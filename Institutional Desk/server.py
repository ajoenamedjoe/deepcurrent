"""
Institutional Desk -- local HTTP server.

The API token never leaves this process; the browser only ever talks to
localhost. Start with START_HERE.bat (or `python server.py`), then open
http://127.0.0.1:8777.

Endpoints:
  GET  /                  the dashboard
  GET  /api/state         scan progress, last run, universe counts, config
  GET  /api/alerts        the alert feed (filtered, scored, joined)
  GET  /api/clusters      alerts rolled up per ticker -- the headline view
  GET  /api/ticker/XYZ    everything known about one name
  GET  /api/funds         the qualifying fund universe
  POST /api/scan          kick off a refresh in the background
  POST /api/stop          ask a running scan to stop after the current fund
"""

import http.server
import json
import os
import socketserver
import threading
import traceback
import urllib.parse
import webbrowser

import scan as scanmod
import store
import uw

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = uw.env_int("IDESK_PORT", 8777)

_scan_lock = threading.Lock()
_scan = None            # current or most recent Scan instance
_scan_thread = None


# ---------------------------------------------------------------- helpers

def _row_to_dict(row):
    d = dict(row)
    for key in ("annual_years", "historical_units", "score_parts", "tags", "people"):
        if key in d:
            d[key] = store.jload(d[key], [])
    return d


def _derive(d):
    """Fields the UI wants that are cheaper to compute than to store."""
    close = uw.num(d.get("close"))
    avg = uw.opt_num(d.get("avg_price"))
    units = uw.num(d.get("cur_units"))
    value = uw.num(d.get("value"))
    d["report_price"] = round(value / units, 4) if units > 0 and value > 0 else None
    d["vs_cost"] = (close / avg - 1.0) if (avg and avg > 0 and close > 0) else None
    if d.get("report_price") and close > 0:
        d["since_report"] = close / d["report_price"] - 1.0
    else:
        d["since_report"] = None
    return d


def _alerts(report_date):
    conn = store.connect()
    rows = conn.execute(
        """SELECT e.report_date, e.cik, e.ticker, e.kind, e.units, e.units_change,
                  e.change_perc, e.value, e.weight, e.trajectory, e.score, e.score_parts,
                  i.name AS fund_name, i.short_name AS fund_short, i.tags, i.people,
                  i.total_value AS fund_aum, i.is_hedge_fund,
                  fs.position_count,
                  t.full_name, t.sector, t.marketcap, t.close,
                  f.profitable_years, f.years_available, f.net_income_latest,
                  f.revenue_latest, f.ni_growth, f.total_assets,
                  f.shareholder_equity, f.annual_years, f.fy_end,
                  h.avg_price, h.first_buy, h.historical_units,
                  h.units AS cur_units
             FROM events e
             JOIN institutions i ON i.cik = e.cik
        LEFT JOIN fund_scan   fs ON fs.cik = e.cik AND fs.report_date = e.report_date
        LEFT JOIN tickers      t ON t.ticker = e.ticker
        LEFT JOIN fundamentals f ON f.ticker = e.ticker
        LEFT JOIN holdings     h ON h.cik = e.cik AND h.ticker = e.ticker
                                AND h.report_date = e.report_date
                                AND h.security_type = 'Share' AND h.put_call = ''
            WHERE e.report_date = ?
            ORDER BY e.score DESC""",
        (report_date,),
    ).fetchall()
    return [_derive(_row_to_dict(r)) for r in rows]


def _clusters(alerts):
    """Roll the feed up per ticker. Bullish events drive the ranking."""
    by_ticker = {}
    for a in alerts:
        slot = by_ticker.setdefault(a["ticker"], {
            "ticker": a["ticker"],
            "full_name": a.get("full_name"),
            "sector": a.get("sector"),
            "marketcap": a.get("marketcap"),
            "close": a.get("close"),
            "profitable_years": a.get("profitable_years"),
            "years_available": a.get("years_available"),
            "annual_years": a.get("annual_years") or [],
            "ni_growth": a.get("ni_growth"),
            "total_assets": a.get("total_assets"),
            "revenue_latest": a.get("revenue_latest"),
            "fy_end": a.get("fy_end"),
            "buyers": [], "sellers": [],
            "buy_value": 0.0, "sell_value": 0.0,
            "top_score": 0.0, "score": 0.0,
        })
        entry = {
            "cik": a["cik"], "fund": a.get("fund_short") or a.get("fund_name"),
            "fund_name": a.get("fund_name"), "fund_aum": a.get("fund_aum"),
            "positions": a.get("position_count"), "kind": a["kind"],
            "weight": a.get("weight"), "value": a.get("value"),
            "change_perc": a.get("change_perc"), "units_change": a.get("units_change"),
            "trajectory": a.get("trajectory"), "score": a.get("score"),
            "historical_units": a.get("historical_units") or [],
            "avg_price": a.get("avg_price"), "vs_cost": a.get("vs_cost"),
            "first_buy": a.get("first_buy"), "people": a.get("people") or [],
            "tags": a.get("tags") or [],
        }
        if a["kind"] in scanmod.BULLISH:
            slot["buyers"].append(entry)
            slot["buy_value"] += uw.num(a.get("value"))
            slot["top_score"] = max(slot["top_score"], uw.num(a.get("score")))
        else:
            slot["sellers"].append(entry)
            slot["sell_value"] += abs(uw.num(a.get("units_change"))) * uw.num(a.get("close"))

    out = []
    for slot in by_ticker.values():
        if not slot["buyers"]:
            continue
        slot["buyers"].sort(key=lambda e: uw.num(e["score"]), reverse=True)
        slot["sellers"].sort(key=lambda e: uw.num(e["value"]), reverse=True)
        slot["n_buyers"] = len(slot["buyers"])
        slot["n_sellers"] = len(slot["sellers"])
        slot["n_new"] = sum(1 for e in slot["buyers"] if e["kind"] == "NEW")
        # a cluster's headline score is its best single event, nudged by breadth
        slot["score"] = round(
            min(100.0, slot["top_score"] + min(8.0, 2.0 * (slot["n_buyers"] - 1))), 1
        )
        # best (lowest) discount to any buyer's cost basis
        costs = [e["vs_cost"] for e in slot["buyers"] if e["vs_cost"] is not None]
        slot["best_vs_cost"] = min(costs) if costs else None
        out.append(slot)
    out.sort(key=lambda s: (s["score"], s["buy_value"]), reverse=True)
    return out


def _state():
    conn = store.connect()
    report_date = store.get_meta("last_report_date")
    cfg = scanmod.config()
    counts = {}
    if report_date:
        counts["funds_scanned"] = conn.execute(
            "SELECT COUNT(*) c FROM fund_scan WHERE report_date=? AND error IS NULL",
            (report_date,)).fetchone()["c"]
        counts["funds_kept"] = conn.execute(
            "SELECT COUNT(*) c FROM fund_scan WHERE report_date=? AND error IS NULL "
            "AND oversized=0 AND position_count<=?",
            (report_date, cfg["fund_max_positions"])).fetchone()["c"]
        counts["events"] = conn.execute(
            "SELECT COUNT(*) c FROM events WHERE report_date=?",
            (report_date,)).fetchone()["c"]
    counts["institutions"] = conn.execute(
        "SELECT COUNT(*) c FROM institutions").fetchone()["c"]
    counts["fundamentals_cached"] = conn.execute(
        "SELECT COUNT(*) c FROM fundamentals").fetchone()["c"]
    counts["fundamentals_passing"] = conn.execute(
        "SELECT COUNT(*) c FROM fundamentals WHERE passes=1").fetchone()["c"]

    live = dict(_scan.state) if _scan else {"stage": "idle", "detail": "", "done": 0,
                                            "total": 0, "error": None, "api_calls": 0}
    live["running"] = bool(_scan_thread and _scan_thread.is_alive())
    return {
        "report_date": report_date,
        "last_scan_finished": store.get_meta("last_scan_finished"),
        "scan": live,
        "counts": counts,
        "config": cfg,
    }


def _ticker_detail(ticker, report_date):
    conn = store.connect()
    tkr = ticker.upper()
    fund = conn.execute("SELECT * FROM fundamentals WHERE ticker=?", (tkr,)).fetchone()
    meta = conn.execute("SELECT * FROM tickers WHERE ticker=?", (tkr,)).fetchone()
    holders = conn.execute(
        """SELECT h.*, i.name AS fund_name, i.short_name AS fund_short,
                  i.total_value AS fund_aum, i.people, i.tags, fs.position_count
             FROM holdings h
             JOIN institutions i ON i.cik = h.cik
        LEFT JOIN fund_scan   fs ON fs.cik = h.cik AND fs.report_date = h.report_date
            WHERE h.ticker = ? AND h.report_date = ? AND h.security_type='Share'
            ORDER BY h.value DESC""",
        (tkr, report_date),
    ).fetchall()
    return {
        "ticker": tkr,
        "meta": _row_to_dict(meta) if meta else None,
        "fundamentals": _row_to_dict(fund) if fund else None,
        "holders": [_row_to_dict(h) for h in holders],
    }


def _start_scan():
    global _scan, _scan_thread
    with _scan_lock:
        if _scan_thread and _scan_thread.is_alive():
            return {"started": False, "reason": "a scan is already running"}
        _scan = scanmod.Scan()

        def work():
            try:
                _scan.run()
            except Exception:                       # noqa: BLE001
                traceback.print_exc()

        _scan_thread = threading.Thread(target=work, daemon=True, name="idesk-scan")
        _scan_thread.start()
        return {"started": True}


# ---------------------------------------------------------------- http

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "InstitutionalDesk/1.0"

    def log_message(self, fmt, *args):
        if uw.env_bool("IDESK_VERBOSE", False):
            super().log_message(fmt, *args)

    # -- plumbing --------------------------------------------------

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _file(self, name, ctype):
        path = os.path.join(HERE, name)
        try:
            with open(path, "rb") as fh:
                self._send(200, fh.read(), ctype)
        except OSError:
            self._send(404, "not found", "text/plain; charset=utf-8")

    # -- routes ----------------------------------------------------

    # ---- security guard (2026-09-26 review) -----------------------------------------
    # Runs before every request. Only this PC may talk to the desk (Host check: no DNS rebinding);
    # another website may open the page but never drive the API (Sec-Fetch-Site), and anything
    # that changes state must be same-origin JSON (a cross-site form can't send that).
    def parse_request(self):
        if not super().parse_request():
            return False
        why = self._uw_guard()
        if why:
            self.send_error(403, why)
            return False
        return True

    def _uw_guard(self):
        from urllib.parse import urlsplit
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in ("", "127.0.0.1", "localhost", "127.0.0.1:%d" % port, "localhost:%d" % port):
            return "local only"
        path = urlsplit(self.path).path
        if self.command in ("GET", "HEAD"):
            site = self.headers.get("Sec-Fetch-Site")
            if path.startswith("/api/") and site not in (None, "same-origin", "none"):
                return "same-site only"
            return None
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return "JSON only"
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
            return "same-origin only"
        return None

    def end_headers(self):
        # the dashboard (any local port) may frame this desk; no other site may (clickjacking)
        self.send_header("Content-Security-Policy", "frame-ancestors 'self' http://127.0.0.1:* http://localhost:*")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._file("index.html", "text/html; charset=utf-8")
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")

            if path == "/api/state":
                return self._json(_state())

            if path == "/api/alerts":
                rd = (qs.get("date") or [store.get_meta("last_report_date")])[0]
                if not rd:
                    return self._json({"report_date": None, "alerts": []})
                return self._json({"report_date": rd, "alerts": _alerts(rd)})

            if path == "/api/clusters":
                rd = (qs.get("date") or [store.get_meta("last_report_date")])[0]
                if not rd:
                    return self._json({"report_date": None, "clusters": []})
                return self._json({"report_date": rd, "clusters": _clusters(_alerts(rd))})

            if path.startswith("/api/ticker/"):
                rd = store.get_meta("last_report_date")
                tkr = urllib.parse.unquote(path[len("/api/ticker/"):])
                return self._json(_ticker_detail(tkr, rd))

            if path == "/api/funds":
                rd = store.get_meta("last_report_date")
                rows = store.connect().execute(
                    """SELECT i.cik,i.name,i.short_name,i.total_value,i.is_hedge_fund,
                              i.tags,i.people,fs.position_count,fs.share_positions
                         FROM fund_scan fs JOIN institutions i ON i.cik=fs.cik
                        WHERE fs.report_date=? AND fs.error IS NULL AND fs.oversized=0
                          AND fs.position_count<=?
                        ORDER BY fs.position_count ASC""",
                    (rd, scanmod.config()["fund_max_positions"]),
                ).fetchall()
                return self._json({"funds": [_row_to_dict(r) for r in rows]})

            return self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as exc:                    # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)

    def do_POST(self):
        try:
            if self.path == "/api/scan":
                return self._json(_start_scan())
            if self.path == "/api/stop":
                if _scan:
                    _scan.stop()
                return self._json({"stopping": True})
            return self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as exc:                    # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": str(exc)}, 500)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    store.connect()          # fail fast if the DB cannot be opened
    try:
        uw.Client()          # fail fast if the token is missing
    except RuntimeError as exc:
        print("\n  !! %s\n" % exc)

    url = "http://127.0.0.1:%d" % PORT
    print("Institutional Desk running at %s" % url)
    print("Press Ctrl+C to stop.\n")
    if uw.env_bool("IDESK_OPEN_BROWSER", True):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # On a brand-new install there is nothing to look at, and the first scan
    # takes 15-25 minutes. Start it automatically rather than showing an empty
    # board behind a button. After that, refreshing is a deliberate click.
    first_run = not store.get_meta("last_report_date")
    if first_run or uw.env_bool("IDESK_SCAN_ON_START", False):
        if first_run:
            print("First run: no filings loaded yet, starting a scan.")
            print("It takes 15-25 minutes. Progress shows in the browser;")
            print("you can close and reopen the page, the scan keeps going.\n")
        threading.Timer(2.0, _start_scan).start()

    srv = Server(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
