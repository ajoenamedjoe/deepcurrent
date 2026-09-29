"""
SQLite persistence for the Institutional Desk.

Everything the scan learns is written here so that:
  * an interrupted scan resumes instead of restarting (13F scans are long)
  * fundamentals are fetched once per ticker per quarter, not once per run
  * quarter-over-quarter history accumulates for later backtesting

DB file: institutional.db next to this module.
"""

import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("IDESK_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "institutional.db"
)

SCHEMA = """
-- every 13F filer the API knows about
CREATE TABLE IF NOT EXISTS institutions (
    cik            TEXT PRIMARY KEY,
    name           TEXT,
    short_name     TEXT,
    is_hedge_fund  INTEGER,
    tags           TEXT,      -- JSON array
    people         TEXT,      -- JSON array
    report_date    TEXT,
    filing_date    TEXT,
    total_value    REAL,
    share_value    REAL,
    buy_value      REAL,
    sell_value     REAL,
    refreshed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_inst_value ON institutions(total_value);
CREATE INDEX IF NOT EXISTS idx_inst_name  ON institutions(name);

-- which funds passed the universe filter, and what we learned fetching them
CREATE TABLE IF NOT EXISTS fund_scan (
    cik             TEXT NOT NULL,
    report_date     TEXT NOT NULL,
    position_count  INTEGER,
    oversized       INTEGER DEFAULT 0,   -- >500 positions: page was full
    share_positions INTEGER,
    fetched_at      TEXT,
    error           TEXT,
    PRIMARY KEY (cik, report_date)
);

-- position-level 13F rows for qualifying funds only
CREATE TABLE IF NOT EXISTS holdings (
    cik                 TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    report_date         TEXT NOT NULL,
    security_type       TEXT NOT NULL DEFAULT '',
    put_call            TEXT NOT NULL DEFAULT '',
    full_name           TEXT,
    sector              TEXT,
    units               INTEGER,
    units_change        INTEGER,
    change_perc         REAL,
    value               REAL,
    avg_price           REAL,
    close               REAL,
    shares_outstanding  REAL,
    perc_of_share_value REAL,
    first_buy           TEXT,
    historical_units    TEXT,   -- JSON array, index 0 = current quarter
    price_change_since_first_buy_perc REAL,
    PRIMARY KEY (cik, ticker, report_date, security_type, put_call)
);
CREATE INDEX IF NOT EXISTS idx_hold_ticker ON holdings(ticker, report_date);
CREATE INDEX IF NOT EXISTS idx_hold_cik    ON holdings(cik, report_date);

-- per-ticker fundamentals cache (refetched only when stale)
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker            TEXT PRIMARY KEY,
    fetched_at        TEXT,
    currency          TEXT,
    annual_years      TEXT,     -- JSON: [{"year","net_income","operating_income","revenue"}]
    profitable_years  INTEGER,  -- consecutive profitable annual periods, most recent first
    years_available   INTEGER,
    net_income_latest REAL,
    revenue_latest    REAL,
    ni_growth         REAL,     -- latest vs prior year, fraction
    total_assets      REAL,
    total_liabilities REAL,
    shareholder_equity REAL,
    fy_end            TEXT,
    passes            INTEGER,  -- 1 = clears the profitability + size gate
    reject_reason     TEXT
);

-- ticker-level facts derived from holdings (free -- no extra API calls)
CREATE TABLE IF NOT EXISTS tickers (
    ticker      TEXT PRIMARY KEY,
    full_name   TEXT,
    sector      TEXT,
    close       REAL,
    shares_out  REAL,
    marketcap   REAL,
    updated_at  TEXT
);

-- the alert feed
CREATE TABLE IF NOT EXISTS events (
    report_date   TEXT NOT NULL,
    cik           TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    kind          TEXT NOT NULL,      -- NEW | ADD | TRIM | EXIT
    units         INTEGER,
    units_change  INTEGER,
    change_perc   REAL,
    value         REAL,
    weight        REAL,               -- position as fraction of fund equity book
    trajectory    TEXT,               -- building | new_conviction | volatile | steady | harvesting
    score         REAL,
    score_parts   TEXT,               -- JSON breakdown, so the number is auditable
    PRIMARY KEY (report_date, cik, ticker, kind)
);
CREATE INDEX IF NOT EXISTS idx_ev_ticker ON events(report_date, ticker);
CREATE INDEX IF NOT EXISTS idx_ev_score  ON events(report_date, score DESC);

-- scan bookkeeping
CREATE TABLE IF NOT EXISTS scan_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    report_date TEXT,
    stage       TEXT,
    status      TEXT,
    api_calls   INTEGER,
    funds_kept  INTEGER,
    tickers_kept INTEGER,
    events_made INTEGER,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_local = threading.local()


def connect():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        # WAL is faster, but it needs real file locking. On a network share or
        # a mounted folder SQLite raises "disk I/O error" on the pragma alone,
        # so fall back to the portable rollback journal instead of dying.
        for mode in ("WAL", "TRUNCATE", "DELETE"):
            try:
                conn.execute("PRAGMA journal_mode=%s" % mode).fetchone()
                break
            except sqlite3.Error:
                continue
        conn.executescript(SCHEMA)
        conn.commit()
        _local.conn = conn
    return conn


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def jdump(val):
    try:
        return json.dumps(val)
    except (TypeError, ValueError):
        return "[]"


def jload(text, default=None):
    if not text:
        return default if default is not None else []
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default if default is not None else []


# ---------------------------------------------------------------- meta

def set_meta(key, value):
    conn = connect()
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_meta(key, default=None):
    row = connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# ---------------------------------------------------------------- writes

def upsert_institutions(rows):
    conn = connect()
    conn.executemany(
        """INSERT INTO institutions
           (cik,name,short_name,is_hedge_fund,tags,people,report_date,filing_date,
            total_value,share_value,buy_value,sell_value,refreshed_at)
           VALUES (:cik,:name,:short_name,:is_hedge_fund,:tags,:people,:report_date,
                   :filing_date,:total_value,:share_value,:buy_value,:sell_value,:refreshed_at)
           ON CONFLICT(cik) DO UPDATE SET
             name=excluded.name, short_name=excluded.short_name,
             is_hedge_fund=excluded.is_hedge_fund, tags=excluded.tags,
             people=excluded.people, report_date=excluded.report_date,
             filing_date=excluded.filing_date, total_value=excluded.total_value,
             share_value=excluded.share_value, buy_value=excluded.buy_value,
             sell_value=excluded.sell_value, refreshed_at=excluded.refreshed_at""",
        rows,
    )
    conn.commit()


def record_fund_scan(cik, report_date, position_count, oversized, share_positions, error=None):
    conn = connect()
    conn.execute(
        """INSERT INTO fund_scan(cik,report_date,position_count,oversized,
                                 share_positions,fetched_at,error)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(cik,report_date) DO UPDATE SET
             position_count=excluded.position_count, oversized=excluded.oversized,
             share_positions=excluded.share_positions, fetched_at=excluded.fetched_at,
             error=excluded.error""",
        (cik, report_date, position_count, 1 if oversized else 0, share_positions, now(), error),
    )
    conn.commit()


def scanned_ciks(report_date):
    rows = connect().execute(
        "SELECT cik FROM fund_scan WHERE report_date=? AND error IS NULL", (report_date,)
    ).fetchall()
    return {r["cik"] for r in rows}


def upsert_holdings(rows):
    if not rows:
        return
    conn = connect()
    conn.executemany(
        """INSERT INTO holdings
           (cik,ticker,report_date,security_type,put_call,full_name,sector,units,
            units_change,change_perc,value,avg_price,close,shares_outstanding,
            perc_of_share_value,first_buy,historical_units,
            price_change_since_first_buy_perc)
           VALUES (:cik,:ticker,:report_date,:security_type,:put_call,:full_name,:sector,
                   :units,:units_change,:change_perc,:value,:avg_price,:close,
                   :shares_outstanding,:perc_of_share_value,:first_buy,:historical_units,
                   :price_change_since_first_buy_perc)
           ON CONFLICT(cik,ticker,report_date,security_type,put_call) DO UPDATE SET
             units=excluded.units, units_change=excluded.units_change,
             change_perc=excluded.change_perc, value=excluded.value,
             avg_price=excluded.avg_price, close=excluded.close,
             shares_outstanding=excluded.shares_outstanding,
             perc_of_share_value=excluded.perc_of_share_value,
             first_buy=excluded.first_buy, historical_units=excluded.historical_units,
             price_change_since_first_buy_perc=excluded.price_change_since_first_buy_perc,
             full_name=excluded.full_name, sector=excluded.sector""",
        rows,
    )
    conn.commit()


def upsert_tickers(rows):
    if not rows:
        return
    conn = connect()
    conn.executemany(
        """INSERT INTO tickers(ticker,full_name,sector,close,shares_out,marketcap,updated_at)
           VALUES(:ticker,:full_name,:sector,:close,:shares_out,:marketcap,:updated_at)
           ON CONFLICT(ticker) DO UPDATE SET
             full_name=COALESCE(excluded.full_name, tickers.full_name),
             sector=COALESCE(excluded.sector, tickers.sector),
             close=excluded.close, shares_out=excluded.shares_out,
             marketcap=excluded.marketcap, updated_at=excluded.updated_at""",
        rows,
    )
    conn.commit()


def upsert_fundamentals(row):
    conn = connect()
    conn.execute(
        """INSERT INTO fundamentals
           (ticker,fetched_at,currency,annual_years,profitable_years,years_available,
            net_income_latest,revenue_latest,ni_growth,total_assets,total_liabilities,
            shareholder_equity,fy_end,passes,reject_reason)
           VALUES (:ticker,:fetched_at,:currency,:annual_years,:profitable_years,
                   :years_available,:net_income_latest,:revenue_latest,:ni_growth,
                   :total_assets,:total_liabilities,:shareholder_equity,:fy_end,
                   :passes,:reject_reason)
           ON CONFLICT(ticker) DO UPDATE SET
             fetched_at=excluded.fetched_at, currency=excluded.currency,
             annual_years=excluded.annual_years,
             profitable_years=excluded.profitable_years,
             years_available=excluded.years_available,
             net_income_latest=excluded.net_income_latest,
             revenue_latest=excluded.revenue_latest, ni_growth=excluded.ni_growth,
             total_assets=excluded.total_assets,
             total_liabilities=excluded.total_liabilities,
             shareholder_equity=excluded.shareholder_equity, fy_end=excluded.fy_end,
             passes=excluded.passes, reject_reason=excluded.reject_reason""",
        row,
    )
    conn.commit()


def fresh_fundamentals(max_age_days=75):
    """Tickers whose cached fundamentals are recent enough to reuse."""
    cutoff = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - max_age_days * 86400)
    )
    rows = connect().execute(
        "SELECT ticker FROM fundamentals WHERE fetched_at >= ?", (cutoff,)
    ).fetchall()
    return {r["ticker"] for r in rows}


def replace_events(report_date, rows):
    conn = connect()
    conn.execute("DELETE FROM events WHERE report_date=?", (report_date,))
    if rows:
        conn.executemany(
            """INSERT INTO events
               (report_date,cik,ticker,kind,units,units_change,change_perc,value,
                weight,trajectory,score,score_parts)
               VALUES(:report_date,:cik,:ticker,:kind,:units,:units_change,:change_perc,
                      :value,:weight,:trajectory,:score,:score_parts)""",
            rows,
        )
    conn.commit()


def start_run(report_date):
    conn = connect()
    cur = conn.execute(
        "INSERT INTO scan_runs(started_at,report_date,stage,status) VALUES(?,?,?,?)",
        (now(), report_date, "starting", "running"),
    )
    conn.commit()
    return cur.lastrowid


def update_run(run_id, **fields):
    if not fields:
        return
    conn = connect()
    sets = ", ".join("%s=?" % k for k in fields)
    conn.execute(
        "UPDATE scan_runs SET %s WHERE id=?" % sets, list(fields.values()) + [run_id]
    )
    conn.commit()


def last_run():
    row = connect().execute(
        "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None
