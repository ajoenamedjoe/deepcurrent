"""
Persistence for UW Swing Desk.

Every scan is recorded, so an alert stops being a thing that blinks in and out
and becomes something with a history: how long it has held, when it was first
seen, how many of the recent scans it survived.

That history is what makes Discord notifications sane (fire once, on a setup
that has actually held) and it's the raw material for checking, later, whether
these scores predict anything at all.

SQLite via the stdlib. No install, one file on disk: swing_history.db
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# SWING_DB lets the tests point at a temp file - they must never touch the real history.
DB_PATH = os.environ.get("SWING_DB") or os.path.join(HERE, "swing_history.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    n_alerts  INTEGER DEFAULT 0,
    n_scanned INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observations (
    scan_id   INTEGER NOT NULL,
    ts        TEXT NOT NULL,
    ticker    TEXT NOT NULL,
    direction TEXT,
    score     REAL,
    agreeing  INTEGER,
    qualified INTEGER NOT NULL DEFAULT 0,
    price     REAL,
    horizon   TEXT,
    rr        REAL,
    parts     TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_ticker ON observations(ticker, scan_id);
CREATE INDEX IF NOT EXISTS ix_obs_scan   ON observations(scan_id);

-- One row per (ticker, direction) tracking the life of a setup.
CREATE TABLE IF NOT EXISTS alert_state (
    ticker         TEXT NOT NULL,
    direction      TEXT NOT NULL,
    first_seen     TEXT,
    last_seen      TEXT,
    last_scan_id   INTEGER,
    consecutive    INTEGER DEFAULT 0,
    total_seen     INTEGER DEFAULT 0,
    best_score     REAL DEFAULT 0,
    notified_at    TEXT,
    notified_score REAL,
    PRIMARY KEY (ticker, direction)
);

CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    ticker    TEXT,
    direction TEXT,
    score     REAL,
    payload   TEXT
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or utcnow()).isoformat(timespec="seconds")


class Store:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            # CREATE TABLE IF NOT EXISTS is not a migration: add columns a db from
            # an older version is missing.
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(observations)")}
            if "parts" not in cols:
                self.conn.execute("ALTER TABLE observations ADD COLUMN parts TEXT")
            self.conn.commit()

    # ---------------------------------------------------------------- meta
    def get_meta(self, k, default=None):
        with self._lock:
            r = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, k, v):
        with self._lock:
            self.conn.execute(
                "INSERT INTO meta(k,v) VALUES(?,?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
            self.conn.commit()

    # -------------------------------------------------------------- record
    def record_scan(self, alerts, watch, n_scanned):
        """Write one scan and update every setup's streak.

        Returns {ticker: persistence dict} for the UI, and the list of setups
        that just became eligible to notify.
        """
        now = iso()
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO scans(ts, n_alerts, n_scanned) VALUES(?,?,?)",
                (now, len(alerts), n_scanned))
            scan_id = cur.lastrowid
            prev_id = scan_id - 1   # a streak only continues from the immediately prior scan

            def parts(a):
                # component values (-1..1) so later calibration uses the tape, not a guess
                return json.dumps({k: c.get("value") for k, c in (a.get("components") or {}).items()})

            rows = []
            for flag, group in ((1, alerts), (0, watch)):
                for a in group:
                    rows.append((scan_id, now, a["ticker"], a["direction"], a["score"],
                                 a["agreeing"], flag, a["price"],
                                 a["horizon"]["label"], a["levels"]["rr"], parts(a)))
            self.conn.executemany(
                "INSERT INTO observations"
                "(scan_id, ts, ticker, direction, score, agreeing, qualified, price, horizon, rr, parts)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)

            qualifying = {(a["ticker"], a["direction"]): a for a in alerts}

            for (tkr, direction), a in qualifying.items():
                st = self.conn.execute(
                    "SELECT * FROM alert_state WHERE ticker=? AND direction=?",
                    (tkr, direction)).fetchone()
                score = abs(a["score"])
                if st is None:
                    self.conn.execute(
                        "INSERT INTO alert_state(ticker, direction, first_seen, last_seen,"
                        " last_scan_id, consecutive, total_seen, best_score)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (tkr, direction, now, now, scan_id, 1, 1, score))
                else:
                    # Consecutive only if it also qualified in the scan just before.
                    streak = (st["consecutive"] + 1) if st["last_scan_id"] == prev_id else 1
                    self.conn.execute(
                        "UPDATE alert_state SET last_seen=?, last_scan_id=?, consecutive=?,"
                        " total_seen=total_seen+1, best_score=MAX(best_score, ?)"
                        " WHERE ticker=? AND direction=?",
                        (now, scan_id, streak, score, tkr, direction))

            # Any setup NOT seen in this scan has broken its streak. This must
            # run after the loop above, so rows updated this scan are exempt by
            # virtue of carrying the current scan_id.
            self.conn.execute(
                "UPDATE alert_state SET consecutive=0 "
                "WHERE consecutive>0 AND last_scan_id IS NOT ?", (scan_id,))

            self.conn.commit()

        return self.persistence_for([a["ticker"] for a in alerts] +
                                    [a["ticker"] for a in watch], scan_id)

    # ---------------------------------------------------------- read back
    def persistence_for(self, tickers, scan_id=None, window=20):
        """held-in-last-N and streak, per ticker."""
        if not tickers:
            return {}
        out = {}
        with self._lock:
            if scan_id is None:
                r = self.conn.execute("SELECT MAX(id) m FROM scans").fetchone()
                scan_id = (r["m"] or 0)
            lo = max(scan_id - window + 1, 0)
            marks = ",".join("?" * len(tickers))
            seen = self.conn.execute(
                f"SELECT ticker, COUNT(*) n FROM observations "
                f"WHERE qualified=1 AND scan_id>=? AND ticker IN ({marks}) "
                f"GROUP BY ticker", (lo, *tickers)).fetchall()
            total = self.conn.execute(
                "SELECT COUNT(*) n FROM scans WHERE id>=?", (lo,)).fetchone()["n"]
            states = self.conn.execute(
                f"SELECT * FROM alert_state WHERE ticker IN ({marks})", tuple(tickers)).fetchall()

        held = {r["ticker"]: r["n"] for r in seen}
        st_by = {}
        for s in states:
            # Keep the most recently active direction for display.
            cur = st_by.get(s["ticker"])
            if cur is None or (s["last_seen"] or "") > (cur["last_seen"] or ""):
                st_by[s["ticker"]] = s

        for t in set(tickers):
            s = st_by.get(t)
            out[t] = {
                "held": held.get(t, 0),
                "window": max(total, 1),
                "streak": (s["consecutive"] if s else 0),
                "first_seen": (s["first_seen"] if s else None),
                "total_seen": (s["total_seen"] if s else 0),
                "best_score": (s["best_score"] if s else 0),
                "notified_at": (s["notified_at"] if s else None),
            }
        return out

    def state(self, ticker, direction):
        with self._lock:
            r = self.conn.execute(
                "SELECT * FROM alert_state WHERE ticker=? AND direction=?",
                (ticker, direction)).fetchone()
        return dict(r) if r else None

    def mark_notified(self, ticker, direction, score, kind="new", payload=""):
        now = iso()
        with self._lock:
            self.conn.execute(
                "UPDATE alert_state SET notified_at=?, notified_score=? "
                "WHERE ticker=? AND direction=?", (now, score, ticker, direction))
            self.conn.execute(
                "INSERT INTO notifications(ts, kind, ticker, direction, score, payload)"
                " VALUES(?,?,?,?,?,?)", (now, kind, ticker, direction, score, payload[:2000]))
            self.conn.commit()

    def notified_since(self, hours):
        cutoff = iso(utcnow() - timedelta(hours=hours))
        with self._lock:
            rows = self.conn.execute(
                "SELECT ticker, direction, MAX(ts) ts FROM notifications "
                "WHERE ts>=? AND kind='new' GROUP BY ticker, direction", (cutoff,)).fetchall()
        return {(r["ticker"], r["direction"]): r["ts"] for r in rows}

    def todays_notifications(self, since_iso):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM notifications WHERE ts>=? AND kind='new' ORDER BY ts",
                (since_iso,)).fetchall()
        return [dict(r) for r in rows]

    def outcome_for(self, ticker, direction, since_iso):
        """How a fired setup behaved after it fired - for the daily wrap."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT price, score, qualified, ts FROM observations "
                "WHERE ticker=? AND direction=? AND ts>=? ORDER BY ts",
                (ticker, direction, since_iso)).fetchall()
        if not rows:
            return None
        first, last = rows[0], rows[-1]
        held = sum(1 for r in rows if r["qualified"])
        move = 0.0
        if first["price"]:
            move = (last["price"] - first["price"]) / first["price"] * 100.0
            if direction == "BEAR":
                move = -move          # express as move in the trade's favour
        return {
            "entry_price": first["price"],
            "last_price": last["price"],
            "favourable_move_pct": move,
            "held_scans": held,
            "total_scans": len(rows),
            "still_up": bool(last["qualified"]),
            "last_score": last["score"],
        }

    def stats(self):
        with self._lock:
            s = self.conn.execute(
                "SELECT COUNT(*) n, MIN(ts) first, MAX(ts) last FROM scans").fetchone()
            n = self.conn.execute("SELECT COUNT(*) n FROM notifications").fetchone()
            days = self.conn.execute(
                "SELECT COUNT(DISTINCT substr(ts,1,10)) d FROM scans").fetchone()["d"]
            top = self.conn.execute("SELECT MAX(ABS(score)) m FROM observations").fetchone()["m"]
            today = self.conn.execute(
                "SELECT MAX(ABS(score)) m FROM observations WHERE substr(ts,1,10)=?",
                (iso()[:10],)).fetchone()["m"]
        return {"scans": s["n"], "first_scan": s["first"], "last_scan": s["last"],
                "notifications": n["n"], "db": os.path.basename(self.path), "scan_days": days,
                "max_score_ever": top, "max_score_today": today}
