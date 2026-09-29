"""
Growth Leaders (O'Neil-style) desk -- local HTTP server.

The token never reaches the browser: the page talks to this process, this process talks to Unusual Whales.
Scans run on an internal thread (after every close, and at start-up when the last session was never scanned);
the intraday breakout watch runs every 15 minutes in market hours. Nothing depends on a tab being open.
"""

import json
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import calendar_et as cal
import model as M
import notify
import scan
import store as store_mod
import uw

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")

PORT = uw.env_int("GDESK_PORT", 8760)
OPEN_BROWSER = uw.env_bool("GDESK_OPEN_BROWSER", True)
WATCH_EVERY = uw.env_num("GDESK_WATCH_SECONDS", 900.0)

STATE = {"store": None, "scanner": None, "notifier": None, "log": [], "ticker_cache": {}}
TICKER_TTL = 3600


def log(message):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), message)
    print(line, flush=True)
    STATE["log"].append(line)
    del STATE["log"][:-400]


# ------------------------------------------------------------------ scheduler
def due():
    """A daily run is due when no finished run started after the last session's after-close time."""
    last = STATE["store"].last_run()
    if not last:
        return True
    return last["started"] < cal.scan_due_after(cal.last_session())


def loop():
    last_watch = 0.0
    while True:
        sc = STATE["scanner"]
        try:
            if not sc.running and due():
                sc.run()
            elif not sc.running and cal.market_open() and time.time() - last_watch >= WATCH_EVERY:
                last_watch = time.time()
                n = sc.watch_once()
                if n:
                    log("intraday watch: %d names near a pivot checked" % n)
        except Exception as exc:              # noqa: BLE001
            log("scheduler: %s: %s" % (type(exc).__name__, exc))
        time.sleep(60)


# ------------------------------------------------------------------ payloads
def board():
    st, sc = STATE["store"], STATE["scanner"]
    last = st.last_run()
    if not last:
        return {"pending": True, "scanning": sc.running, "progress": sc.progress, "error": sc.last_error,
                "message": "The first scan is running: it ranks ~3,000 stocks and checks the leaders one by one "
                           "(about 10 minutes the first time).", "log": STATE["log"][-20:], "method": M.method()}
    cards = st.cards(last["id"])
    scored = [c for c in cards if c.get("score") is not None]
    top = max((c["score"] for c in scored), default=None)
    for c in cards:
        w = sc.watch.get(c["ticker"])
        if w and w["at"] > (last.get("finished") or 0):
            c["live"] = w
    return {
        "pending": False, "run": {k: last.get(k) for k in ("id", "session", "started", "finished", "universe",
                                                              "candidates", "api_calls", "note")},
        "market": last.get("market") or sc.market, "cards": cards,
        "counts": {"cards": len(cards), "scored": len(scored), "leaders": sum(1 for c in cards if c.get("leader")),
                   "six_pass": sum(1 for c in cards if c.get("six_pass")),
                   "breakouts": sum(1 for c in cards if (c.get("base") or {}).get("state") == "breakout"),
                   "near_pivot": sum(1 for c in cards if (c.get("base") or {}).get("state") == "near_pivot")},
        "observed_max": top, "alerts": st.alerts(30), "scanning": sc.running, "progress": sc.progress,
        "error": sc.last_error, "method": M.method(), "build": build_stamp(),
        "distinct_days": st.distinct_days(),
    }


def health():
    st, sc = STATE["store"], STATE["scanner"]
    last = st.last_run()
    return {"ok": sc.last_error is None, "error": sc.last_error, "scanning": sc.running, "progress": sc.progress,
            "api_calls_total": sc.client.calls, "api_errors": sc.client.errors, "distinct_days": st.distinct_days(),
            "last_session": last and last["session"], "market": (last or {}).get("market", {}) and last["market"].get("label"),
            "discord_enabled": STATE["notifier"].enabled, "build": build_stamp(), "model": M.MODEL_VERSION}


def ticker_card(t):
    hit = STATE["ticker_cache"].get(t)
    if hit and time.time() - hit[0] < TICKER_TTL:
        return hit[1]
    st = STATE["store"]
    last = st.last_run()
    if last:
        for c in st.cards(last["id"]):
            if c["ticker"] == t:
                c["history"] = st.card_history(t)
                STATE["ticker_cache"][t] = (time.time(), c)
                return c
    card = STATE["scanner"].ticker(t)
    if "error" not in card:
        card["history"] = st.card_history(t)
        STATE["ticker_cache"][t] = (time.time(), card)
    return card


def build_stamp():
    try:
        stamp = datetime.fromtimestamp(os.path.getmtime(INDEX)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        stamp = "?"
    return {"index_mtime": stamp, "port": PORT, "model": M.MODEL_VERSION}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GrowthDesk/1.0"

    def log_message(self, fmt, *args):
        pass

    # security guard (same as every desk since the 2026-09-26 review): this PC only (Host), other sites may not
    # drive the API (Sec-Fetch-Site), and anything that changes state must be same-origin JSON.
    def parse_request(self):
        if not super().parse_request():
            return False
        why = self._uw_guard()
        if why:
            self.send_error(403, why)
            return False
        return True

    def _uw_guard(self):
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
        self.send_header("Content-Security-Policy", "frame-ancestors 'self' http://127.0.0.1:* http://localhost:*")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

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

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        path = u.path
        try:
            if path in ("/", "/index.html"):
                with open(INDEX, "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if path == "/api/board":
                return self._json(board())
            if path == "/api/health":
                return self._json(health())
            if path == "/api/method":
                return self._json(M.method())
            if path == "/api/market":
                last = STATE["store"].last_run()
                return self._json((last or {}).get("market") or STATE["scanner"].market or {})
            if path == "/api/log":
                return self._json({"lines": STATE["log"][-200:], "runs": STATE["store"].runs()})
            if path == "/api/ticker":
                t = scan_ticker((q.get("t") or [""])[0])
                if not t:
                    return self._json({"error": "bad ticker"}, 400)
                return self._json(ticker_card(t))
            return self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as exc:              # noqa: BLE001
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > 65536:
            return self._json({"error": "too large"}, 413)
        try:
            json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        if path == "/api/scan":
            sc = STATE["scanner"]
            if sc.running:
                return self._json({"ok": False, "error": "a scan is already running"})
            threading.Thread(target=sc.run, kwargs={"force": True}, daemon=True).start()
            return self._json({"ok": True})
        if path == "/api/test-discord":
            return self._json({"ok": STATE["notifier"].test(), "enabled": STATE["notifier"].enabled})
        return self._json({"error": "not found"}, 404)


def scan_ticker(s):
    import re
    s = str(s or "").strip().upper()
    return s if re.match(r"^[A-Z][A-Z0-9.\-]{0,9}$", s) else None


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"     # Windows would let two copies share the port


def setup(client=None):
    STATE["store"] = store_mod.Store(store_mod.default_path(HERE))
    STATE["notifier"] = notify.Notifier(log=log)
    STATE["scanner"] = scan.Scanner(client or uw.Client(), STATE["store"], log=log, notifier=STATE["notifier"])
    STATE["scanner"].load_last()


def main():
    log("Growth Leaders desk starting in %s (model %s)" % (HERE, M.MODEL_VERSION))
    if not uw.env_str("UW_API_TOKEN"):
        print("\n  No UW_API_TOKEN found.\n  Put it in .env next to this file, or keep a sibling desk's .env next door.\n")
        sys.exit(2)
    setup()
    threading.Thread(target=loop, daemon=True).start()
    httpd = Server(("127.0.0.1", PORT), Handler)
    url = "http://127.0.0.1:%d/" % PORT
    log("serving %s" % url)
    if OPEN_BROWSER:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")


if __name__ == "__main__":
    main()
