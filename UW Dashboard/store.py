"""
SQLite persistence for the UW Dashboard.

Everything the dashboard needs to come back exactly as it was after a closed
tab or an overnight reboot lives here: settings (menu order, logo, links,
desk config), P&L imports and their fills, and the dashboard's own log.

WAL first, then TRUNCATE, then DELETE -- WAL fails with a bare
"disk I/O error" on some mounted paths (Institutional Desk lesson 5).
Every open runs the migrations; CREATE TABLE IF NOT EXISTS is not one.
"""

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT,
    broker      TEXT,
    account     TEXT,
    uploaded_at REAL,
    rows_read   INTEGER,
    fills_found INTEGER,
    fills_new   INTEGER,
    notes       TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    id        TEXT PRIMARY KEY,
    import_id INTEGER,
    account   TEXT,
    ts        TEXT,
    seq       INTEGER,
    data      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fills_ts ON fills(ts);
CREATE TABLE IF NOT EXISTS journal (
    day        TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

# (table, column, DDL) -- applied when PRAGMA table_info lacks the column.
MIGRATIONS = [
    ("imports", "notes", "ALTER TABLE imports ADD COLUMN notes TEXT"),
    ("fills", "seq", "ALTER TABLE fills ADD COLUMN seq INTEGER"),
]


def connect(path):
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    for mode in ("WAL", "TRUNCATE", "DELETE"):
        try:
            conn.execute("PRAGMA journal_mode=%s" % mode)
            break
        except sqlite3.OperationalError:
            continue
    return conn


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.conn = connect(path)
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self):
        for table, col, ddl in MIGRATIONS:
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(%s)" % table)}
            if col not in cols:
                self.conn.execute(ddl)

    # ------------------------------------------------------------- kv
    def get(self, key, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set(self, key, value):
        with self.lock:
            self.conn.execute(
                "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), time.time()))
            self.conn.commit()

    # ------------------------------------------------------------- P&L
    def add_import(self, filename, broker, account, rows_read, fills, notes=""):
        """Insert fills, skipping ones already stored. Returns (import_id, new)."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO imports(filename,broker,account,uploaded_at,rows_read,fills_found,fills_new,notes)"
                " VALUES(?,?,?,?,?,?,0,?)",
                (filename, broker, account, time.time(), rows_read, len(fills), notes))
            imp = cur.lastrowid
            new = 0
            for f in fills:
                try:
                    self.conn.execute(
                        "INSERT INTO fills(id,import_id,account,ts,seq,data) VALUES(?,?,?,?,?,?)",
                        (f["id"], imp, f.get("account") or "", f["ts"], f.get("seq", 0), json.dumps(f)))
                    new += 1
                except sqlite3.IntegrityError:
                    pass
            self.conn.execute("UPDATE imports SET fills_new=? WHERE id=?", (new, imp))
            self.conn.commit()
        return imp, new

    def imports(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT i.*, (SELECT COUNT(*) FROM fills f WHERE f.import_id=i.id) AS fills_kept"
                " FROM imports i ORDER BY i.id DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_import(self, imp):
        with self.lock:
            self.conn.execute("DELETE FROM fills WHERE import_id=?", (imp,))
            self.conn.execute("DELETE FROM imports WHERE id=?", (imp,))
            self.conn.commit()

    def all_fills(self):
        with self.lock:
            rows = self.conn.execute("SELECT data FROM fills ORDER BY ts, import_id, seq").fetchall()
        return [json.loads(r["data"]) for r in rows]

    # ------------------------------------------------------------- journal
    def journal_get(self, day):
        with self.lock:
            r = self.conn.execute("SELECT * FROM journal WHERE day=?", (day,)).fetchone()
        return dict(r) if r else None

    def journal_put(self, day, body):
        """Save a day's note. An empty note deletes the day (so it drops out of the list)."""
        now = time.time()
        with self.lock:
            if not body.strip():
                self.conn.execute("DELETE FROM journal WHERE day=?", (day,))
            else:
                self.conn.execute(
                    "INSERT INTO journal(day,body,created_at,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(day) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                    (day, body, now, now))
            self.conn.commit()
        return self.journal_get(day)

    def journal_list(self, q=""):
        sql = "SELECT day, body, updated_at FROM journal"
        args = ()
        if q:
            sql += " WHERE body LIKE ? ESCAPE '\\'"
            args = ("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",)
        with self.lock:
            rows = self.conn.execute(sql + " ORDER BY day DESC", args).fetchall()
        return [dict(r) for r in rows]

    def fill_count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"]
