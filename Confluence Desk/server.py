"""
Confluence Desk -- HTTP server.

The token never reaches the browser: the page talks to this process, this
process talks to Unusual Whales.

SCANS ARE INTERNAL, on a background thread, whether or not a browser is open.
Swing Desk's scans are pull-driven by the browser polling `/api/scan`, which
means with no tab open the server does nothing -- no scans, no Discord, and
the daily wrap reports an empty day. That is the wrong shape for something
meant to alert unattended, and Form 4s land in an evening wave long after the user
has closed the tab.
"""

import json
import os
import socketserver
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import notify
import scan
import store as store_mod
import uw

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
DB_PATH = os.path.join(HERE, "confluence.db")

PORT = uw.env_int("CDESK_PORT", 8770)
SCAN_EVERY = uw.env_num("CDESK_SCAN_SECONDS", 600.0)
OPEN_BROWSER = uw.env_bool("CDESK_OPEN_BROWSER", True)

STATE = {"store": None, "scanner": None, "notifier": None, "log": []}


def log(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    line = "[%s] %s" % (stamp, message)
    print(line, flush=True)
    STATE["log"].append(line)
    del STATE["log"][:-400]


# ------------------------------------------------------------ scan thread

def scan_loop():
    scanner = STATE["scanner"]
    notifier = STATE["notifier"]
    while True:
        try:
            log("scan starting")
            result = scanner.run()
            if result.get("error"):
                log("scan error: %s (serving last good board)" % result["error"])
            else:
                log("scan done: %d cards, %d API calls, %.1fs"
                    % (len(result["cards"]), result["api_calls"], result["seconds"]))
                sent, reasons = notifier.process(
                    result["cards"], baseline=result.get("baseline"))
                if sent:
                    log("discord: %d posted" % sent)
                elif reasons:
                    log("discord: none (%s)" % ", ".join(
                        "%s=%d" % kv for kv in sorted(reasons.items())))
        except Exception as exc:
            log("scan loop crashed: %s: %s" % (type(exc).__name__, exc))
        time.sleep(max(60.0, SCAN_EVERY))


# ---------------------------------------------------------------- handler

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, content_type="application/json; charset=utf-8",
              extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, default=str))

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
        try:
            if path in ("/", "/index.html"):
                return self._index()
            if path == "/api/board":
                return self._board()
            if path == "/api/log":
                return self._json({"lines": STATE["log"][-200:]})
            if path == "/api/health":
                return self._health()
            if path.startswith("/api/history/"):
                ticker = path.rsplit("/", 1)[-1].upper()
                return self._json({"ticker": ticker,
                                   "rows": STATE["store"].history(ticker)})
            if path == "/api/test-discord":
                ok = STATE["notifier"].test()
                return self._json({"ok": ok, "enabled": STATE["notifier"].enabled})
            return self._send(404, "not found", "text/plain; charset=utf-8")
        except Exception as exc:
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def _index(self):
        # index.html is read from disk PER REQUEST, so a UI change needs
        # nothing but a browser reload -- no restart.
        try:
            with open(INDEX, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._send(500, "index.html missing", "text/plain; charset=utf-8")
        return self._send(200, body, "text/html; charset=utf-8")

    def _board(self):
        scanner = STATE["scanner"]
        result = scanner.last_result
        if result is None:
            return self._json({
                "pending": True,
                "message": "First scan is running. It takes 40-90 seconds.",
                "log": STATE["log"][-30:],
            })
        payload = dict(result)
        payload["build"] = build_stamp()
        payload["pending"] = False
        return self._json(payload)

    def _health(self):
        scanner = STATE["scanner"]
        return self._json({
            "ok": scanner.last_error is None,
            "error": scanner.last_error,
            "api_calls_total": scanner.client.calls,
            "api_errors": scanner.client.errors,
            "institutional": scanner.inst.coverage(),
            "discord_enabled": STATE["notifier"].enabled,
            "distinct_days": STATE["store"].distinct_days(),
            "build": build_stamp(),
        })


def build_stamp():
    """
    Served index.html mtime + the folder the server was launched from.

    This exists because of a wasted round trip: a new Share button was
    reported missing when the file on disk was correct and the browser tab
    was simply from before the deploy. There was no way to tell a stale tab
    from a failed deploy by looking. It also catches "you are running a second
    copy out of an old extracted zip", which has happened here too -- this
    machine has GEX ES Desk, _1, _2 and _3 sitting side by side.
    """
    try:
        mtime = os.path.getmtime(INDEX)
        stamp = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        stamp = "?"
    return {"index_mtime": stamp, "folder": HERE, "port": PORT}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    log("Confluence Desk starting in %s" % HERE)
    if not uw.env_str("UW_API_TOKEN"):
        print("\n  No UW_API_TOKEN found.\n"
              "  Put it in .env next to this file, or leave a sibling desk's\n"
              "  .env in place next door.\n")
        sys.exit(2)

    STATE["store"] = store_mod.Store(DB_PATH)
    STATE["notifier"] = notify.Notifier(STATE["store"], log=log)
    STATE["scanner"] = scan.Scanner(STATE["store"], log=log)

    coverage = STATE["scanner"].inst.coverage()
    if coverage["available"]:
        log("13F backdrop: %s quarter, %d tickers, %d funds (filed ~%s, %s days ago)"
            % (coverage["report_date"], coverage["tickers"], coverage["funds"],
               coverage["filed_around"], coverage["age_days"]))
    else:
        log("13F backdrop UNAVAILABLE: %s" % coverage["error"])

    if STATE["store"].is_first_scan():
        log("FIRST RUN: this scan records the baseline and posts nothing to "
            "Discord. Alerts begin on the next scan.")

    threading.Thread(target=scan_loop, daemon=True).start()

    httpd = Server(("127.0.0.1", PORT), Handler)
    url = "http://127.0.0.1:%d/" % PORT
    log("serving %s" % url)
    # START_HERE.bat deliberately does NOT open a browser -- two tabs opened
    # on launch when both did. The server wins because it knows the real port.
    if OPEN_BROWSER:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")


if __name__ == "__main__":
    main()
