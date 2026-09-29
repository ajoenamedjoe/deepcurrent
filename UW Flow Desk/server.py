"""Unusual Flow Desk — local server.

Launch by double-clicking START_HERE.bat. Do NOT open flowboard.html directly:
the page fetches /api/flow from this server, and the token never leaves it.

Routes
    /                 the board
    /api/flow         the latest scan  {meta, cards}
    /api/diag         endpoint probe, no secrets
    /api/health       liveness + build stamp

The scan runs on an INTERNAL THREAD, not from the browser. Swing Desk made its
scans pull-driven and with no tab open it did nothing at all: no scans, no
alerts, and a 4pm wrap reporting an empty day. An alerting desk that only runs
while you are watching is not an alerting desk.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import env as envmod
import flow
import flowscan
import tape as tapemod

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = envmod.load_env()
TOKEN = envmod.token(ENV)

PORT = envmod.cfg_int(ENV, "PORT", 8733, 1024, 65535)
SCAN_SECONDS = envmod.cfg_int(ENV, "SCAN_SECONDS", 300, 60, 3600)
PAGES = envmod.cfg_int(ENV, "SCREENER_PAGES", 2, 1, 8)
MIN_PREMIUM = envmod.cfg_int(ENV, "MIN_CONTRACT_PREMIUM", 0, 0, 10_000_000)
# The UW Dashboard starts this desk with OPEN_BROWSER=0 in the environment so a
# background start opens no tab. That must win over the .env file's
# OPEN_BROWSER=true, or every dashboard start / restart pops a second tab.
OPEN_BROWSER = envmod.cfg_bool(
    {"OPEN_BROWSER": os.environ["OPEN_BROWSER"]} if os.environ.get("OPEN_BROWSER", "") != "" else ENV,
    "OPEN_BROWSER", True)
ALWAYS_SCAN = envmod.cfg_bool(ENV, "SCAN_OUTSIDE_MARKET_HOURS", False)


def next_session_text():
    """When the desk will next be scanning, in words.

    An empty board with no explanation is the state a new install spends its
    first weekend in, and "no qualifying flow yet" is the wrong sentence for
    it — the desk is not screening and finding nothing, it is not screening.
    """
    import datetime as _dt
    now = flowscan.et_now()
    if flowscan.market_open():
        return None
    day, mins = now.weekday(), now.hour * 60 + now.minute
    if day < 5 and mins < 9 * 60 + 50:
        return "today at 9:50am ET"
    nxt = now
    while True:
        nxt = nxt + _dt.timedelta(days=1)
        if nxt.weekday() < 5:
            break
    label = "tomorrow" if (nxt.date() - now.date()).days == 1 else nxt.strftime("%A")
    return "%s at 9:50am ET" % label


class Desk:
    """Holds the last good board. Never blanks it on a failure."""

    def __init__(self):
        self.lock = threading.Lock()
        self.board = None
        self.error = None
        self.last_ok = None
        self.scans = 0
        self.stop = threading.Event()

    def snapshot(self):
        with self.lock:
            if self.board is None:
                return {
                    "meta": {
                        "session": str(flowscan.session_today()),
                        "counts": {"board": 0, "spreads": 0, "near": 0, "out": 0},
                        "tickers": 0, "alerts": 0, "contracts": 0,
                        "observed_max": None,
                        "market_open": flowscan.market_open(),
                        "next_session": next_session_text(),
                        "scans": self.scans,
                        "warnings": ([] if not self.error else [{
                            "endpoint": "scan",
                            "shape": self.error,
                            "detail": "no board yet",
                        }]),
                        "status": self.status_line(),
                    },
                    "cards": [],
                }
            board = json.loads(json.dumps(self.board))
            board["meta"]["status"] = self.status_line()
            board["meta"]["market_open"] = flowscan.market_open()
            board["meta"]["next_session"] = next_session_text()
            board["meta"]["scans"] = self.scans
            if self.error:
                board["meta"].setdefault("warnings", []).append({
                    "endpoint": "scan", "shape": self.error,
                    "detail": "last scan failed; showing the last good board",
                })
            return board

    def status_line(self):
        bits = []
        if self.last_ok:
            age = int(time.time() - self.last_ok)
            bits.append("last scan %ds ago" % age)
        bits.append("%d scans" % self.scans)
        if not flowscan.market_open() and not ALWAYS_SCAN:
            bits.append("market closed — holding the last board")
        return " · ".join(bits)

    def loop(self):
        if not TOKEN:
            with self.lock:
                self.error = ("no API token found — copy .env.example to .env "
                              "and paste your token in")
            return
        scan = flowscan.FlowScan(TOKEN, pages=PAGES,
                                 min_premium=MIN_PREMIUM or None)
        while not self.stop.is_set():
            if ALWAYS_SCAN or flowscan.market_open():
                try:
                    board = scan.run()
                    with self.lock:
                        self.board = board
                        self.error = None
                        self.last_ok = time.time()
                        self.scans += 1
                    m = board["meta"]
                    print("[%s] %d tickers  %s  top %.1f"
                          % (datetime.now().strftime("%H:%M:%S"),
                             m["tickers"], m["counts"], m["observed_max"]),
                          flush=True)
                    for w in m.get("warnings", []):
                        print("  WARNING %s -> %s" % (w["endpoint"], w["shape"]),
                              flush=True)
                except Exception as exc:      # never let one bad scan kill the thread
                    with self.lock:
                        self.error = "%s: %s" % (type(exc).__name__, exc)
                    print("[scan failed] " + self.error, flush=True)
                    traceback.print_exc()
            self.stop.wait(SCAN_SECONDS)


DESK = Desk()
TAPE = {"t": None}


def tape_lookup(query):
    """GET /api/tape?alert=<uuid> | ?contract=<OSI>[&date=YYYY-MM-DD]"""
    from urllib.parse import parse_qs
    q = {k: v[0] for k, v in parse_qs(query or "").items() if v}
    if not TOKEN:
        return 503, {"error": "no API token - copy .env.example to .env and paste your token in"}
    if TAPE["t"] is None:
        TAPE["t"] = tapemod.Tape(flowscan.Client(TOKEN))
    return TAPE["t"].lookup(alert=q.get("alert"), contract=q.get("contract"), date=q.get("date"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
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
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/flowboard.html"):
            page = os.path.join(HERE, "flowboard.html")
            try:
                with open(page, "r", encoding="utf-8") as fh:
                    html = fh.read()
            except OSError as exc:
                return self._send(500, "cannot read flowboard.html: %s" % exc,
                                  "text/plain; charset=utf-8")
            # Build stamp. A missing Share button was once reported on the Insider
            # Desk when the browser tab simply predated the deploy, and there was no way
            # to tell a stale tab from a failed deploy by looking.
            stamp = "%s · %s" % (
                datetime.fromtimestamp(os.path.getmtime(page)).strftime("%Y-%m-%d %H:%M"),
                os.path.basename(HERE))                  # the folder name, not the full path
            html = html.replace("</body>",
                                '<div style="max-width:78ch;margin:10px auto 0;'
                                'font:12px system-ui;color:#8d8c82;padding:0 20px">'
                                'build ' + stamp.replace("&", "&amp;").replace("<", "&lt;") +
                                '</div></body>')
            return self._send(200, html, "text/html; charset=utf-8")

        if path == "/api/flow":
            return self._send(200, json.dumps(DESK.snapshot()),
                              "application/json; charset=utf-8")

        if path == "/api/tape":
            code, body = tape_lookup(self.path.split("?", 1)[1] if "?" in self.path else "")
            return self._send(code, json.dumps(body), "application/json; charset=utf-8")

        if path == "/api/health":
            return self._send(200, json.dumps({
                "ok": True,
                "token": bool(TOKEN),
                "env_files": [os.path.basename(f) for f in ENV.get("_files", [])],
                "scans": DESK.scans,
                "error": DESK.error,
                "market_open": flowscan.market_open(),
                "config_warnings": envmod.WARNINGS,
                "port": PORT,
                "scan_seconds": SCAN_SECONDS,
            }), "application/json; charset=utf-8")

        if path == "/api/diag":
            return self._send(200, json.dumps(diag(), indent=1),
                              "application/json; charset=utf-8")

        return self._send(404, "not found", "text/plain; charset=utf-8")


def diag():
    """Probe both endpoints and report what came back. No secrets.

    Ship a diagnostic with anything that talks to a third-party API: GEX ES
    Desk spent two rounds guessing at response shapes before writing one.
    """
    out = {"token_present": bool(TOKEN), "probes": []}
    if not TOKEN:
        out["hint"] = "copy .env.example to .env and paste your token in"
        return out
    client = flowscan.Client(TOKEN)
    probes = [
        ("/api/option-activity/unusual",
         dict(flowscan.CONTRACT_PARAMS, limit=5),
         ("option_symbol", "ticker_symbol")),
        ("/api/option-trades/flow-alerts",
         dict(flowscan.ALERT_PARAMS, limit=5),
         ("option_chain", "alert_rule")),
    ]
    for path, params, keys in probes:
        rec = {"endpoint": path}
        try:
            payload = client.get(path, params)
            rec["envelope"] = flowscan._shape(payload)
            rows = flowscan.dig(payload, *keys)
            if rows is None:
                rec["ok"] = False
                rec["detail"] = "no rows carrying " + " / ".join(keys)
            else:
                rows = rows if isinstance(rows, list) else [rows]
                rec["ok"] = True
                rec["rows"] = len(rows)
                rec["fields_found"] = sorted(rows[0].keys())[:40] if rows else []
        except Exception as exc:
            rec["ok"] = False
            rec["detail"] = "%s: %s" % (type(exc).__name__, exc)
        out["probes"].append(rec)
    return out


def main():
    print("Unusual Flow Desk")
    print("  folder : " + HERE)
    print("  env    : " + (", ".join(ENV.get("_files", [])) or "none found"))
    print("  token  : " + ("found" if TOKEN else "MISSING — see .env.example"))
    print("  scan   : every %ds, market hours%s"
          % (SCAN_SECONDS, " (overridden: always on)" if ALWAYS_SCAN else ""))
    for w in envmod.WARNINGS:
        print("  config warning: " + w)

    threading.Thread(target=DESK.loop, daemon=True).start()

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = "http://127.0.0.1:%d/" % PORT
    print("\n  open %s\n  (Ctrl-C to stop)\n" % url, flush=True)
    if OPEN_BROWSER:
        # The server opens the browser, not START_HERE.bat -- when both did it,
        # the Insider Desk opened two tabs. The server wins because it knows the
        # configured PORT.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        DESK.stop.set()
        srv.server_close()


if __name__ == "__main__":
    main()
