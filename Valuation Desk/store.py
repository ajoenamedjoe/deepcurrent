"""
Valuation Desk -- SQLite history.

One row per run. The full result JSON is kept, so any past report can be
re-opened exactly as it was, and `history(ticker)` can show how the price,
the base value and the score moved between runs.

`CREATE TABLE IF NOT EXISTS` is not a migration (Confluence Desk lesson 11):
new columns go in MIGRATIONS and are applied with a PRAGMA table_info check on
every open, so an old database never breaks an insert.
"""

import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL,
    run_at        TEXT NOT NULL,
    price         REAL,
    base_value    REAL,
    bull_value    REAL,
    bear_value    REAL,
    score         REAL,
    verdict       TEXT,
    model_class   TEXT,
    model_version TEXT,
    out_dir       TEXT,
    result_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_ticker ON runs(ticker, run_at);
"""

MIGRATIONS = [
    ("runs", "overrides", "TEXT"),
    ("runs", "card_saved", "INTEGER DEFAULT 0"),
    ("runs", "price_source", "TEXT"),
    ("runs", "scale", "INTEGER DEFAULT 1"),
]


class Store:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            for table, col, decl in MIGRATIONS:
                have = {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}
                if col not in have:
                    c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
        self._convert_scale()

    def _convert_scale(self):
        """
        One-time: model 1.x stored 0 = buy / 10 = avoid; 2.0 inverted it.
        Mirror every old row so the history never mixes scales.
        A copy of the database is taken first, and each row is converted by
        the model's own upgrade_result() -- the valuations are untouched.
        """
        import valuation as V
        with self._conn() as c:
            rows = c.execute("SELECT id, result_json FROM runs WHERE COALESCE(scale, 1) < ?",
                             (V.SCALE_VERSION,)).fetchall()
        if not rows:
            return 0
        backup = self.path + ".before-scale-2.bak"
        if os.path.exists(self.path) and not os.path.exists(backup):
            shutil.copy2(self.path, backup)
        n = 0
        with self._lock, self._conn() as c:
            for r in rows:
                try:
                    res = V.upgrade_result(json.loads(r["result_json"]))
                except Exception:
                    continue          # leave an unreadable row alone rather than guess
                c.execute("UPDATE runs SET score = ?, verdict = ?, result_json = ?, scale = ? WHERE id = ?",
                          (res["score"]["score"], res["score"]["verdict"], json.dumps(res, default=str),
                           V.SCALE_VERSION, r["id"]))
                n += 1
        return n

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def add(self, res, out_dir, price_source=None):
        s = res["valuation"]["scenarios"]
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO runs (ticker, run_at, price, base_value, bull_value, bear_value, score,"
                " verdict, model_class, model_version, out_dir, result_json, overrides, price_source, scale)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (res["fin"]["ticker"], datetime.now().isoformat(timespec="seconds"),
                 res["fin"]["price"], s["base"]["per_share"], s["bull"]["per_share"],
                 s["bear"]["per_share"], res["score"]["score"], res["score"]["verdict"],
                 res["class"], res["model_version"], out_dir,
                 json.dumps(res, default=str), json.dumps(res.get("overrides") or {}),
                 price_source, res["score"].get("scale", 1)))
            return cur.lastrowid

    def mark_card(self, run_id):
        with self._lock, self._conn() as c:
            c.execute("UPDATE runs SET card_saved = 1 WHERE id = ?", (run_id,))

    def get(self, run_id):
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(r) if r else None

    def history(self, ticker=None, limit=200):
        q = ("SELECT id, ticker, run_at, price, base_value, bull_value, bear_value, score, verdict,"
             " model_class, model_version, overrides, card_saved FROM runs")
        args = []
        if ticker:
            q += " WHERE ticker = ?"
            args.append(ticker.upper())
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def tickers(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT ticker, COUNT(*) AS runs, MAX(run_at) AS last_run,"
                " (SELECT score FROM runs r2 WHERE r2.ticker = r.ticker ORDER BY id DESC LIMIT 1) AS last_score,"
                " (SELECT verdict FROM runs r2 WHERE r2.ticker = r.ticker ORDER BY id DESC LIMIT 1) AS last_verdict"
                " FROM runs r GROUP BY ticker ORDER BY last_run DESC")]
