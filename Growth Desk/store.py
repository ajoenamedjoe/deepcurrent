"""
SQLite persistence for the Growth Leaders desk: scan runs, the cards each run produced (written as they
finish, so a restart resumes), an API cache (earnings and 13F change slowly), and breakout alerts.
"""

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session TEXT NOT NULL,             -- the trading day the run describes (YYYY-MM-DD, ET)
  started REAL NOT NULL,
  finished REAL,
  status TEXT NOT NULL,              -- running | done | interrupted | failed
  universe INTEGER DEFAULT 0,
  candidates INTEGER DEFAULT 0,
  api_calls INTEGER DEFAULT 0,
  market TEXT,                       -- json
  ctx TEXT,                          -- json: rs + groups + universe rows needed to resume
  note TEXT
);
CREATE TABLE IF NOT EXISTS cards (
  run_id INTEGER NOT NULL,
  ticker TEXT NOT NULL,
  score INTEGER,
  leader INTEGER DEFAULT 0,
  state TEXT,
  card TEXT NOT NULL,
  PRIMARY KEY (run_id, ticker)
);
CREATE TABLE IF NOT EXISTS cache (
  key TEXT PRIMARY KEY,
  at REAL NOT NULL,
  val TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  session TEXT NOT NULL,
  ticker TEXT NOT NULL,
  kind TEXT NOT NULL,                -- breakout
  pivot REAL,
  score INTEGER,
  leader INTEGER DEFAULT 0,
  market TEXT,
  text TEXT,
  posted INTEGER DEFAULT 0,
  UNIQUE (ticker, kind, pivot)
);
"""


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self.db.executescript(SCHEMA)
        # a run that was going when the desk stopped is resumable, never silently "done"
        self.db.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        self.db.commit()

    # ------------------------------------------------------------------ runs
    def start_run(self, session, universe, candidates, market, ctx):
        with self.lock:
            cur = self.db.execute("INSERT INTO runs(session, started, status, universe, candidates, market, ctx) "
                                  "VALUES (?,?,?,?,?,?,?)", (session, time.time(), "running", universe, candidates,
                                                             json.dumps(market), json.dumps(ctx)))
            self.db.commit()
            return cur.lastrowid

    def resume_run(self, rid):
        with self.lock:
            self.db.execute("UPDATE runs SET status='running' WHERE id=?", (rid,))
            self.db.commit()

    def finish_run(self, rid, status, api_calls, note=None):
        with self.lock:
            self.db.execute("UPDATE runs SET finished=?, status=?, api_calls=?, note=? WHERE id=?",
                            (time.time(), status, api_calls, note, rid))
            # keep the ranking context of the latest few runs only (it is ~0.5 MB)
            self.db.execute("UPDATE runs SET ctx=NULL WHERE id NOT IN (SELECT id FROM runs ORDER BY id DESC LIMIT 3)")
            self.db.commit()

    def last_context(self):
        """(run, ctx, market) of the latest finished run, for /api/ticker after a restart."""
        with self.lock:
            r = self.db.execute("SELECT id, session, ctx, market FROM runs WHERE status='done' AND ctx IS NOT NULL "
                                "ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None, None, None
        return dict(id=r[0], session=r[1]), json.loads(r[2]), (json.loads(r[3]) if r[3] else None)

    def interrupted(self, session):
        with self.lock:
            r = self.db.execute("SELECT * FROM runs WHERE session=? AND status='interrupted' AND ctx IS NOT NULL "
                                "ORDER BY id DESC LIMIT 1", (session,)).fetchone()
            return dict(r) if r else None

    def last_run(self, done_only=True):
        with self.lock:
            q = "SELECT * FROM runs %s ORDER BY id DESC LIMIT 1" % ("WHERE status='done'" if done_only else "")
            r = self.db.execute(q).fetchone()
            if not r:
                return None
            d = dict(r)
            d.pop("ctx", None)
            d["market"] = json.loads(d["market"]) if d.get("market") else None
            return d

    def runs(self, n=10):
        with self.lock:
            return [{k: r[k] for k in ("id", "session", "started", "finished", "status", "universe", "candidates", "api_calls", "note")}
                    for r in self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,))]

    def distinct_days(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(DISTINCT session) FROM runs WHERE status='done'").fetchone()[0]

    # ------------------------------------------------------------------ cards
    def save_card(self, rid, card):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO cards(run_id, ticker, score, leader, state, card) VALUES (?,?,?,?,?,?)",
                            (rid, card["ticker"], card.get("score"), 1 if card.get("leader") else 0,
                             (card.get("base") or {}).get("state"), json.dumps(card)))
            self.db.commit()

    def done_tickers(self, rid):
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT ticker FROM cards WHERE run_id=?", (rid,))}

    def cards(self, rid):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute(
                "SELECT card FROM cards WHERE run_id=? ORDER BY score IS NULL, score DESC", (rid,))]

    def card_history(self, ticker, n=30):
        with self.lock:
            return [{"session": r[0], "score": r[1], "leader": bool(r[2]), "state": r[3]} for r in self.db.execute(
                "SELECT runs.session, cards.score, cards.leader, cards.state FROM cards JOIN runs ON runs.id = cards.run_id "
                "WHERE cards.ticker=? AND runs.status='done' ORDER BY runs.id DESC LIMIT ?", (ticker, n))]

    # ------------------------------------------------------------------ cache
    def cache_get(self, key, max_age):
        with self.lock:
            r = self.db.execute("SELECT at, val FROM cache WHERE key=?", (key,)).fetchone()
        if r and time.time() - r[0] <= max_age:
            return json.loads(r[1])
        return None

    def cache_put(self, key, val):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO cache(key, at, val) VALUES (?,?,?)", (key, time.time(), json.dumps(val)))
            self.db.commit()

    def cache_prune(self, max_age=30 * 86400):
        with self.lock:
            self.db.execute("DELETE FROM cache WHERE at < ?", (time.time() - max_age,))
            self.db.commit()

    # ------------------------------------------------------------------ alerts
    def add_alert(self, session, ticker, kind, pivot, score, leader, market, text):
        """Once per ticker per base (pivot). Returns the new id, or None if already alerted."""
        with self.lock:
            try:
                cur = self.db.execute("INSERT INTO alerts(at, session, ticker, kind, pivot, score, leader, market, text) "
                                      "VALUES (?,?,?,?,?,?,?,?,?)", (time.time(), session, ticker, kind,
                                                                     round(pivot or 0, 2), score, 1 if leader else 0, market, text))
                self.db.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def mark_posted(self, aid):
        with self.lock:
            self.db.execute("UPDATE alerts SET posted=1 WHERE id=?", (aid,))
            self.db.commit()

    def alerts(self, n=40):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (n,))]


def default_path(here):
    return os.environ.get("GDESK_DB") or os.path.join(here, "growth.db")
