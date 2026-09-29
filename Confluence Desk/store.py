"""
Confluence Desk -- SQLite tape.

Three jobs:
  1. `observations` records every card's score and component breakdown on
     every scan, BEFORE its outcome exists. That is the only honest raw
     material for ever fitting the weights, which are currently reasoned and
     not fitted -- the same open position as all four sibling desks.
  2. `outcomes` records the price at a card's first appearance so the desk can
     mark it up later. Insider + 13F signal is a multi-month effect, so this
     needs a quarter before it says anything; recording from day one is the
     only way to have that quarter.
  3. `alert_state` drives Discord: streak, cooldown, and the cold-start
     baseline.
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    api_calls   INTEGER,
    candidates  INTEGER,
    carded      INTEGER,
    error       TEXT,
    baseline    INTEGER DEFAULT 0   -- 1 = first-ever scan, alerts suppressed
);

CREATE TABLE IF NOT EXISTS observations (
    scan_id     INTEGER NOT NULL,
    ticker      TEXT NOT NULL,
    observed_at TEXT,
    score       REAL,
    insider_pts REAL,
    dark_pts    REAL,
    inst_pts    REAL,
    price       REAL,
    score_parts TEXT,               -- JSON, so every number stays auditable
    dark_sessions TEXT,             -- JSON [{date, share_of_day, active}]
    PRIMARY KEY (scan_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_obs_ticker ON observations(ticker, observed_at);

CREATE TABLE IF NOT EXISTS outcomes (
    ticker        TEXT PRIMARY KEY,
    first_seen    TEXT,
    first_score   REAL,
    first_price   REAL,
    peak_score    REAL,
    last_seen     TEXT,
    last_price    REAL
);

CREATE TABLE IF NOT EXISTS alert_state (
    ticker        TEXT PRIMARY KEY,
    first_seen    TEXT,
    last_seen     TEXT,
    last_scan_id  INTEGER,
    streak        INTEGER DEFAULT 0,
    total_seen    INTEGER DEFAULT 0,
    best_score    REAL DEFAULT 0,
    last_alert_at TEXT,
    alert_count   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS seen_filings (
    filing_id  TEXT PRIMARY KEY,
    ticker     TEXT,
    first_seen TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow():
    return datetime.now(timezone.utc)


def iso(when=None):
    return (when or utcnow()).isoformat(timespec="seconds")


class Store:
    # Columns added after the first release. `CREATE TABLE IF NOT EXISTS` does
    # NOT add a column to a table that already exists, so an existing
    # confluence.db would start failing every insert the moment the schema
    # grew. The live board had already been running overnight when
    # `dark_sessions` was added.
    MIGRATIONS = (
        ("observations", "dark_sessions", "TEXT"),
    )

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn):
        for table, column, decl in self.MIGRATIONS:
            have = {r["name"] for r in conn.execute(
                "PRAGMA table_info(%s)" % table)}
            if column not in have:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s"
                             % (table, column, decl))

    def connect(self):
        """
        WAL first, then TRUNCATE, then DELETE.

        SQLite WAL fails on mounted/network paths with a bare `disk I/O error`
        raised by the PRAGMA itself -- not by any query -- so the fallback has
        to wrap the pragma, not the work. Fine on real NTFS; this only bites
        during remote development over the Cowork mount, which is exactly
        where this desk gets built.
        """
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        for mode in ("WAL", "TRUNCATE", "DELETE"):
            try:
                conn.execute("PRAGMA journal_mode=%s" % mode).fetchone()
                break
            except sqlite3.Error:
                continue
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # -------------------------------------------------------------- scans

    def is_first_scan(self):
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM scans WHERE error IS NULL"
            ).fetchone()
            return (row["n"] or 0) == 0

    def start_scan(self, baseline=False):
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO scans (started_at, baseline) VALUES (?, ?)",
                (iso(), 1 if baseline else 0),
            )
            return cur.lastrowid

    def finish_scan(self, scan_id, api_calls, candidates, carded, error=None):
        with self._lock, self.connect() as conn:
            conn.execute(
                "UPDATE scans SET finished_at=?, api_calls=?, candidates=?, "
                "carded=?, error=? WHERE id=?",
                (iso(), api_calls, candidates, carded, error, scan_id),
            )

    # ------------------------------------------------------- observations

    def record(self, scan_id, cards):
        now = iso()
        with self._lock, self.connect() as conn:
            for card in cards:
                legs = card["legs"]
                conn.execute(
                    "INSERT OR REPLACE INTO observations "
                    "(scan_id, ticker, observed_at, score, insider_pts, dark_pts, "
                    " inst_pts, price, score_parts, dark_sessions) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (scan_id, card["ticker"], now, card["score"],
                     legs["insider"]["points"], legs["darkpool"]["points"],
                     legs["institutional"]["points"], card.get("quote"),
                     json.dumps({k: v["parts"] for k, v in legs.items()}),
                     # Per-session shares, so the activity floor can be set
                     # from a real distribution instead of by eye on one
                     # ticker -- which is exactly how it broke the first time.
                     json.dumps([
                         {"d": s["date"], "s": round(s["share_of_day"], 5),
                          "a": 1 if s["active"] else 0,
                          "p": 1 if s.get("partial") else 0}
                         for s in (card.get("dark") or {}).get("sessions", [])
                     ])),
                )
                conn.execute(
                    "INSERT INTO outcomes (ticker, first_seen, first_score, "
                    "  first_price, peak_score, last_seen, last_price) "
                    "VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(ticker) DO UPDATE SET "
                    "  peak_score = MAX(peak_score, excluded.peak_score), "
                    "  last_seen = excluded.last_seen, "
                    "  last_price = excluded.last_price",
                    (card["ticker"], now, card["score"], card.get("quote"),
                     card["score"], now, card.get("quote")),
                )

    def distinct_days(self):
        """
        Distinct DAYS of observations, never row count.

        A sample count is not an evidence count: 117 rows from one afternoon
        of overlapping windows is about one independent observation, and this
        desk scans every few minutes. The footer shows this number so nobody
        reads "4,000 observations" as four thousand pieces of evidence.
        """
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT substr(observed_at,1,10)) AS n FROM observations"
            ).fetchone()
            return row["n"] or 0

    def best_ever(self):
        """
        The highest score this desk has ever recorded.

        The board footer prints this next to the score, because the practical
        ceiling is nowhere near 100: reaching 100 needs every one of eleven
        components maxed at the same moment, and a replay over plausible live
        inputs never got past about 77. Without this number a reader has no
        way to tell whether a 72 is remarkable or routine, and a fixed
        "realistic max" comment would go stale. The scale calibrates itself
        against the desk's own tape instead.
        """
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT MAX(peak_score) AS m FROM outcomes").fetchone()
            return row["m"]

    def history(self, ticker, limit=60):
        with self._lock, self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT observed_at, score, insider_pts, dark_pts, inst_pts, "
                "       price, score_parts, dark_sessions "
                "  FROM observations WHERE ticker=? "
                " ORDER BY observed_at DESC LIMIT ?", (ticker.upper(), limit))]

    # --------------------------------------------------------- alerting

    def bump(self, scan_id, cards):
        """
        Update streak/first_seen/total per card, then reset the streak of
        everything that did NOT appear in this scan.

        The reset MUST run after the loop, not before it. Running it first
        needs an exemption for the previous scan_id, and that exemption is
        what let dropped-out names keep their streak and stay alert-eligible
        (Swing Desk bug #4).
        """
        now = iso()
        with self._lock, self.connect() as conn:
            for card in cards:
                conn.execute(
                    "INSERT INTO alert_state (ticker, first_seen, last_seen, "
                    "  last_scan_id, streak, total_seen, best_score) "
                    "VALUES (?,?,?,?,1,1,?) "
                    "ON CONFLICT(ticker) DO UPDATE SET "
                    "  last_seen=excluded.last_seen, "
                    "  last_scan_id=excluded.last_scan_id, "
                    "  streak=alert_state.streak+1, "
                    "  total_seen=alert_state.total_seen+1, "
                    "  best_score=MAX(alert_state.best_score, excluded.best_score)",
                    (card["ticker"], now, now, scan_id, card["score"]),
                )
            conn.execute(
                "UPDATE alert_state SET streak=0 WHERE last_scan_id IS NOT ?",
                (scan_id,),
            )

    def state(self, ticker):
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM alert_state WHERE ticker=?", (ticker.upper(),)
            ).fetchone()
            return dict(row) if row else None

    def mark_alerted(self, ticker):
        with self._lock, self.connect() as conn:
            conn.execute(
                "UPDATE alert_state SET last_alert_at=?, "
                "alert_count=alert_count+1 WHERE ticker=?", (iso(), ticker.upper()))

    # ------------------------------------------------------ new filings

    def register_filings(self, filing_ids, ticker_by_id, baseline=False):
        """
        Returns the ids never seen before.

        On a cold start the entire lookback window is "new" -- hundreds of filings on
        the Insider Desk's first run, which would have made Discord post its
        per-scan maximum about filings that were weeks old. The first scan
        records the baseline and returns NOTHING, and the UI says so in a
        banner. Any "notify me about things I haven't seen" design needs this.
        """
        now = iso()
        fresh = []
        with self._lock, self.connect() as conn:
            known = {
                r["filing_id"] for r in conn.execute(
                    "SELECT filing_id FROM seen_filings")
            }
            for fid in filing_ids:
                if fid in known:
                    continue
                fresh.append(fid)
                conn.execute(
                    "INSERT OR IGNORE INTO seen_filings (filing_id, ticker, first_seen) "
                    "VALUES (?,?,?)", (fid, ticker_by_id.get(fid), now))
        return [] if baseline else fresh

    # -------------------------------------------------------------- meta

    def get_meta(self, key, default=None):
        with self._lock, self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_meta(self, key, value):
        with self._lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
