"""
Valuation Desk -- screener mode (model 3.2).

Buffett hunts; he does not wait to be handed a ticker. A screen values a LIST
of tickers in the background and ranks them by quality x value, so the
"cheap AND good" corner is visible at a glance. Click a row to run the full
report (docx, card, script) for that one name.

Sources: a pasted list, every ticker in this desk's history, or an ETF's
holdings (/api/etfs/{t}/holdings -- SPY for the S&P 500, QQQ, DIA, XLK ...).

Cost: ~6 API calls per ticker (info, 3 statements, insiders, earnings) at the
client's ~3 calls/sec -> about 2 s per ticker, ~17 minutes for 500. The work
runs on ONE internal thread (skill rule: scans never pull-driven from the
browser), every row is written to SQLite as it finishes, and a screen that
was interrupted (desk closed) RESUMES where it stopped. No per-ticker
payloads are kept -- 500 tickers of raw statements would be ~800 MB.

Every ticker gets the SAME treatment and every failure carries a sentence
(skill rule: nothing disappears silently).
"""

import json
import re
import threading
import time
from datetime import datetime

import valuation as V

SCHEMA = """
CREATE TABLE IF NOT EXISTS screens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    source      TEXT,
    created_at  TEXT,
    finished_at TEXT,
    status      TEXT,
    total       INTEGER,
    tickers     TEXT
);
CREATE TABLE IF NOT EXISTS screen_rows (
    screen_id   INTEGER,
    ticker      TEXT,
    status      TEXT,
    error       TEXT,
    name        TEXT,
    sector      TEXT,
    model_class TEXT,
    price       REAL,
    base_value  REAL,
    mos         REAL,
    score       REAL,
    verdict     TEXT,
    quality     REAL,
    measured    INTEGER,
    irr5        REAL,
    irr10       REAL,
    implied_growth REAL,
    gated       TEXT,
    why         TEXT,
    detail      TEXT,
    done_at     TEXT,
    PRIMARY KEY (screen_id, ticker)
);
"""

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def parse_tickers(text):
    """'aapl, MSFT brk.b\\nGOOGL' -> ['AAPL', 'MSFT', 'BRK.B', 'GOOGL'], order kept, de-duped."""
    out, seen = [], set()
    for tok in re.split(r"[\s,;]+", (text or "").upper()):
        tok = tok.strip().lstrip("$")
        if tok and TICKER_RE.match(tok) and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def etf_tickers(client, etf):
    """
    Holdings of an ETF via /api/etfs/{t}/holdings (path verified in the API docs
    2026-09-23). The row field names were NOT verified against a real response
    (no MCP tool exposes this endpoint), so the ticker is read defensively and
    non-equity rows (cash, futures, blank) are dropped.
    """
    rows = V.rows_of(client.get("/api/etfs/%s/holdings" % etf.upper()))
    out = []
    for r in rows:
        t = (r.get("ticker") or r.get("symbol") or r.get("holding_ticker") or "")
        t = str(t).upper().strip()
        kind = str(r.get("type") or r.get("security_type") or r.get("asset_class") or "equity").lower()
        if t and TICKER_RE.match(t) and not any(k in kind for k in ("cash", "future", "bond", "option")):
            out.append(t)
    return parse_tickers(" ".join(out))


def summarise(res):
    """One screen row from a full result -- small on purpose."""
    f, s, v = res["fin"], res["score"], res["valuation"]
    b = v["scenarios"]["base"]
    q = res.get("quality") or {}
    er = v.get("returns") or {}
    irr = er.get("irr") or {}
    mos = next((g.get("mos") for g in s.get("gates") or [] if g["key"] == "margin"), None)
    gated = ",".join(g["key"] for g in s.get("gates") or [] if g.get("applied"))
    detail = {"quality": [[c["key"], c["value"]] for c in q.get("components") or []],
              "strengths": q.get("strengths") or [], "weaknesses": q.get("weaknesses") or []}
    return {"status": "ok", "error": None, "name": f["name"], "sector": f["sector"],
            "model_class": res["class"], "price": f["price"], "base_value": b["per_share"],
            "mos": mos, "score": s["score"], "verdict": s["verdict"], "quality": q.get("score"),
            "measured": q.get("measured"), "irr5": irr.get(5), "irr10": irr.get(10),
            "implied_growth": er.get("implied_growth"), "gated": gated,
            "why": " ".join(res.get("why") or []), "detail": json.dumps(detail)}


def quadrant(row):
    """cheap + good = the target. Thresholds are the verdict gates themselves."""
    q, m = row.get("quality"), row.get("mos")
    if q is None or m is None:
        return "unknown"
    cheap, good = m >= V.MARGIN_OF_SAFETY, q >= 6.0
    return {(True, True): "cheap_good", (True, False): "value_trap",
            (False, True): "pricey_good", (False, False): "avoid"}[(cheap, good)]


def rank_key(row):
    order = {"cheap_good": 0, "pricey_good": 1, "value_trap": 2, "avoid": 3, "unknown": 4}
    return (0 if row.get("status") == "ok" else 1, order[quadrant(row)],
            -(row.get("score") or 0), -(row.get("quality") or 0))


class Screener:
    def __init__(self, store, client_fn, fetch_fn, fx_fn, log=print):
        self.store = store
        self.client_fn, self.fetch_fn, self.fx_fn, self.log = client_fn, fetch_fn, fx_fn, log
        self._thread = None
        self._stop = threading.Event()
        self.current = None           # (screen_id, ticker)
        with store._conn() as c:
            c.executescript(SCHEMA)
            # a screen that was running when the desk closed is resumable, not "running"
            c.execute("UPDATE screens SET status = 'interrupted' WHERE status = 'running'")

    # ------------------------------------------------------------ control
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, tickers, name, source):
        if self.busy():
            raise ValueError("A screen is already running -- stop it first.")
        if not tickers:
            raise ValueError("No valid tickers to screen.")
        if len(tickers) > 1500:
            raise ValueError("%d tickers is more than one screen should hold (max 1,500)." % len(tickers))
        with self.store._lock, self.store._conn() as c:
            cur = c.execute("INSERT INTO screens (name, source, created_at, status, total, tickers)"
                            " VALUES (?,?,?,?,?,?)",
                            (name or source, source, datetime.now().isoformat(timespec="seconds"),
                             "running", len(tickers), json.dumps(tickers)))
            sid = cur.lastrowid
        self._launch(sid)
        return sid

    def resume(self, sid):
        if self.busy():
            raise ValueError("A screen is already running.")
        row = self.get(sid)
        if not row:
            raise ValueError("No screen %s." % sid)
        with self.store._lock, self.store._conn() as c:
            c.execute("UPDATE screens SET status = 'running', finished_at = NULL WHERE id = ?", (sid,))
        self._launch(sid)
        return sid

    def stop(self):
        self._stop.set()

    def _launch(self, sid):
        self._stop.clear()
        self._thread = threading.Thread(target=self._work, args=(sid,), daemon=True, name="screen-%d" % sid)
        self._thread.start()

    # ------------------------------------------------------------ worker
    def _work(self, sid):
        try:
            tickers = json.loads(self.get(sid)["tickers"])
            done = {r["ticker"] for r in self.rows(sid)}
            todo = [t for t in tickers if t not in done]
            self.log("screen %d: %d to value (%d already done)" % (sid, len(todo), len(done)))
            client = self.client_fn()
            fx = self.fx_fn()
            for t in todo:
                if self._stop.is_set():
                    self._set_status(sid, "stopped")
                    self.log("screen %d stopped by user" % sid)
                    return
                self.current = (sid, t)
                try:
                    payloads, _meta = self.fetch_fn(client, t)
                    row = summarise(V.run(payloads, fx=fx))
                except ValueError as exc:            # a sentence: ETF, FX, no statements ...
                    row = {"status": "skipped", "error": str(exc)}
                except RuntimeError as exc:          # API trouble after retries
                    row = {"status": "error", "error": str(exc)}
                    if "rejected the token" in str(exc):
                        self._put(sid, t, row)
                        self._set_status(sid, "failed")
                        self.log("screen %d: token rejected -- stopped" % sid)
                        return
                except Exception as exc:             # a model bug must not kill the screen
                    row = {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}
                self._put(sid, t, row)
            self._set_status(sid, "done")
            self.log("screen %d finished" % sid)
        finally:
            self.current = None

    def _put(self, sid, t, row):
        cols = ["screen_id", "ticker", "done_at"] + list(row.keys())
        vals = [sid, t, datetime.now().isoformat(timespec="seconds")] + list(row.values())
        with self.store._lock, self.store._conn() as c:
            c.execute("INSERT OR REPLACE INTO screen_rows (%s) VALUES (%s)"
                      % (",".join(cols), ",".join("?" * len(cols))), vals)

    def _set_status(self, sid, status):
        with self.store._lock, self.store._conn() as c:
            c.execute("UPDATE screens SET status = ?, finished_at = ? WHERE id = ?",
                      (status, datetime.now().isoformat(timespec="seconds"), sid))

    # ------------------------------------------------------------ reads
    def get(self, sid):
        with self.store._conn() as c:
            r = c.execute("SELECT * FROM screens WHERE id = ?", (sid,)).fetchone()
        return dict(r) if r else None

    def rows(self, sid):
        with self.store._conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM screen_rows WHERE screen_id = ?", (sid,))]
        for r in rows:
            r["quadrant"] = quadrant(r)
            r["detail"] = json.loads(r["detail"]) if r.get("detail") else None
        rows.sort(key=rank_key)
        return rows

    def list(self, limit=30):
        with self.store._conn() as c:
            out = [dict(r) for r in c.execute(
                "SELECT s.id, s.name, s.source, s.created_at, s.finished_at, s.status, s.total,"
                " (SELECT COUNT(*) FROM screen_rows r WHERE r.screen_id = s.id) AS done"
                " FROM screens s ORDER BY s.id DESC LIMIT ?", (limit,))]
        return out

    def status(self):
        cur = self.current
        return {"busy": self.busy(), "screen_id": cur[0] if cur else None,
                "ticker": cur[1] if cur else None}
