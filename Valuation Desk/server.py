"""
Valuation Desk -- HTTP server.

The token never reaches the browser: the page talks to this process, this
process talks to Unusual Whales. Single runs are on demand (one ticker,
~6 API calls). Screens (model 3.2) run on ONE internal background thread and
resume after a restart -- see screen.py.

A missing token or bad config NEVER stops the server starting: the page opens
and says what is wrong (skill rule: config parsing must degrade, not die).
"""

import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import desk
import generate as G
import screen as screen_mod
import store as store_mod
import uw
import valuation as V

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
DB_PATH = os.path.join(HERE, "valuation.db")
PORT = uw.env_int("VDESK_PORT", 8790)
OPEN_BROWSER = uw.env_bool("VDESK_OPEN_BROWSER", True)

STATE = {"store": None, "client": None, "client_error": None, "log": [], "screener": None}
MIME = {"py": "text/x-python; charset=utf-8", "console": "text/plain; charset=utf-8",
        "data": "application/json; charset=utf-8", "png": "image/png",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def log(message):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), message)
    print(line, flush=True)
    STATE["log"].append(line)
    del STATE["log"][:-300]


def client():
    if STATE["client"] is None:
        try:
            STATE["client"] = uw.Client()
            STATE["client_error"] = None
        except RuntimeError as exc:
            STATE["client_error"] = str(exc)
            raise ValueError(str(exc))
    return STATE["client"]


def build_stamp():
    """index.html mtime + launch folder: tells a stale tab from a failed deploy."""
    try:
        stamp = datetime.fromtimestamp(os.path.getmtime(INDEX)).strftime("%Y-%m-%d %H:%M")
    except OSError:
        stamp = "?"
    return {"index_mtime": stamp, "folder": HERE, "port": PORT, "model": V.MODEL_VERSION}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, default=str))

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Bad Content-Length.")
        if n < 0 or n > 10_000_000:
            raise ValueError("Request too large.")
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise ValueError("Request body is not JSON.")

    # ------------------------------------------------------------ GET
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
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path in ("/", "/index.html"):
                with open(INDEX, "rb") as fh:          # per request: a reload picks up a new UI
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if path == "/api/health":
                tok = bool(uw.env_str("UW_API_TOKEN"))
                return self._json({"ok": tok, "token": tok, "client_error": STATE["client_error"],
                                   "fx": uw.fx_table(), "build": build_stamp(),
                                   "calls": getattr(STATE["client"], "calls", 0)})
            if path == "/api/method":
                return self._json(V.method_info())
            if path == "/api/tickers":
                return self._json({"tickers": STATE["store"].tickers()})
            if path == "/api/history":
                t = (qs.get("ticker") or [None])[0]
                return self._json({"runs": STATE["store"].history(t)})
            if path.startswith("/api/run/"):
                return self._get_run(int(path.rsplit("/", 1)[-1]))
            if path.startswith("/api/file/"):
                _, _, _, rid, kind = path.split("/", 4)
                return self._file(int(rid), kind)
            if path == "/api/screens":
                sc = STATE["screener"]
                return self._json({"screens": sc.list(), "status": sc.status()})
            if path.startswith("/api/screen/"):
                sid = int(path.rsplit("/", 1)[-1])
                sc = STATE["screener"]
                meta = sc.get(sid)
                if not meta:
                    return self._json({"error": "No screen %d." % sid}, 404)
                meta.pop("tickers", None)
                return self._json({"screen": meta, "rows": sc.rows(sid), "status": sc.status()})
            if path == "/api/log":
                return self._json({"lines": STATE["log"][-150:]})
            if path == "/api/diag":
                import diag
                return self._json(diag.run_checks(client(), (qs.get("ticker") or ["AAPL"])[0]))
            return self._send(404, "not found", "text/plain; charset=utf-8")
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            log("GET %s failed: %s: %s" % (path, type(exc).__name__, exc))
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)

    def _get_run(self, rid):
        row = STATE["store"].get(rid)
        if not row:
            return self._json({"error": "No run %d." % rid}, 404)
        res = json.loads(row["result_json"])
        files = desk.run_files(row)
        console = ""
        if "console" in files:
            with open(os.path.join(row["out_dir"], files["console"]), encoding="utf-8") as fh:
                console = fh.read()
        return self._json({"run_id": rid, "run_at": row["run_at"], "out_dir": row["out_dir"],
                           "files": files, "card": G.card(res), "result": res, "console": console})

    def _file(self, rid, kind):
        row = STATE["store"].get(rid)
        if not row:
            return self._send(404, "no such run", "text/plain; charset=utf-8")
        name = desk.short_card_name(row["ticker"]) if kind == "short" else G.names(row["ticker"]).get(kind)
        path = os.path.join(row["out_dir"], name or "")
        if not name or not os.path.isfile(path):
            return self._send(404, "file not written", "text/plain; charset=utf-8")
        with open(path, "rb") as fh:
            data = fh.read()
        disp = "inline" if kind in ("png", "short", "console") else "attachment"
        return self._send(200, data, MIME["png" if kind == "short" else kind],
                          {"Content-Disposition": '%s; filename="%s"' % (disp, name)})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/run":
                ov = {}
                for key in ("discount", "growth"):
                    if body.get(key) not in (None, ""):
                        ov[key] = float(body[key]) / 100.0      # UI sends percent
                out = desk.run_ticker(client(), STATE["store"], body.get("ticker"),
                                      overrides=ov, fx=uw.fx_table(), log=log)
                return self._json(out)
            if path == "/api/screen":
                src = (body.get("source") or "list").lower()
                if src == "history":
                    tickers = [r["ticker"] for r in STATE["store"].tickers()]
                    name = "My history (%d)" % len(tickers)
                elif src == "etf":
                    etf = (body.get("etf") or "").strip().upper()
                    if not screen_mod.TICKER_RE.match(etf):
                        raise ValueError("Type an ETF ticker, e.g. SPY.")
                    tickers = screen_mod.etf_tickers(client(), etf)
                    if not tickers:
                        raise ValueError("Unusual Whales returned no holdings for %s." % etf)
                    name = "%s holdings (%d)" % (etf, len(tickers))
                else:
                    tickers = screen_mod.parse_tickers(body.get("tickers"))
                    name = body.get("name") or "List (%d)" % len(tickers)
                client()                                   # fail now, not in the thread, on a bad token
                sid = STATE["screener"].start(tickers, name, src)
                log("screen %d started: %s" % (sid, name))
                return self._json({"screen_id": sid, "total": len(tickers)})
            if path == "/api/screen/stop":
                STATE["screener"].stop()
                return self._json({"ok": True})
            if path.startswith("/api/screen/") and path.endswith("/resume"):
                sid = int(path.split("/")[3])
                STATE["screener"].resume(sid)
                return self._json({"ok": True, "screen_id": sid})
            if path.startswith("/api/card/"):
                rid = int(path.rsplit("/", 1)[-1])
                p = desk.save_card(STATE["store"], rid, body.get("png") or "",
                                   "short" if body.get("kind") == "short" else "full")
                log("card saved: %s" % p)
                return self._json({"ok": True, "path": p})
            if path.startswith("/api/open/"):
                row = STATE["store"].get(int(path.rsplit("/", 1)[-1]))
                if not row:
                    return self._json({"error": "No such run."}, 404)
                if sys.platform.startswith("win"):
                    os.startfile(row["out_dir"])            # noqa -- Windows only
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", row["out_dir"]])
                else:
                    subprocess.Popen(["xdg-open", row["out_dir"]])
                return self._json({"ok": True, "path": row["out_dir"]})
            return self._json({"error": "not found"}, 404)
        except ValueError as exc:
            log("refused: %s" % exc)
            return self._json({"error": str(exc)}, 400)
        except RuntimeError as exc:
            log("failed: %s" % exc)
            return self._json({"error": str(exc)}, 502)
        except Exception as exc:
            log("POST %s crashed: %s: %s" % (path, type(exc).__name__, exc))
            return self._json({"error": "%s: %s" % (type(exc).__name__, exc)}, 500)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(port=None, open_browser=None):
    log("Valuation Desk starting in %s (model %s)" % (HERE, V.MODEL_VERSION))
    STATE["store"] = store_mod.Store(DB_PATH)
    STATE["screener"] = screen_mod.Screener(STATE["store"], client, uw.fetch_all, uw.fx_table, log=log)
    if not uw.env_str("UW_API_TOKEN"):
        log("WARNING: no UW_API_TOKEN found -- the page will open and say so.")
    fx = uw.fx_table()
    if fx:
        log("FX rates from .env: %s" % ", ".join("%s %.4f" % kv for kv in sorted(fx.items())))
    port = port or PORT
    httpd = Server(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    log("serving %s" % url)
    if OPEN_BROWSER if open_browser is None else open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
