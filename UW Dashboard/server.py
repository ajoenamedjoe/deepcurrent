"""
UW Dashboard -- one page that opens every desk, today's events, the
portfolio sheet and the P&L calendar.

    START_HERE.bat  ->  python server.py  ->  http://127.0.0.1:8700/

Stdlib only (no pip). Binds to 127.0.0.1. The UW token stays in this
process; the page never sees it.

Everything the page shows is persisted in dashboard.db next to this file,
so a closed tab or an overnight reboot comes back exactly as it was.
Run INSTALL_AUTOSTART.bat once to start the dashboard when Windows starts.
"""

import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import backup as backup_mod
import changelog
import desks as desks_mod
import deskview
import guide
import env
import events as events_mod
import market as market_mod
import pnl
import portfolio as portfolio_mod
import sizer
import themes
import store as store_mod
import trackrec
import watchlist as watch_mod

HERE = env.HERE
DB_PATH = os.path.join(HERE, "dashboard.db")
VERSION = "1.0"
RELEASE = "r22"            # must be the newest entry in CHANGELOG.md (a test checks)
MAX_BODY = 8 * 1024 * 1024          # settings (logo / images as data URLs) fit comfortably
MAX_UPLOAD = 40 * 1024 * 1024        # broker CSVs (/api/pnl/upload only)
UNC_RE = re.compile(r"^[\\/]{2}")    # \\server\share or //server/share: never a desk or backup folder
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOG_PATH = os.path.join(HERE, "logs", "dashboard.log")

# Seeded from .env on first run (kept out of the source so a share zip never carries it).
DEFAULT_PORTFOLIO = env.env_str("DASH_PORTFOLIO_URL", "")


def default_settings():
    return {
        "menu": [
            {"id": "morning", "type": "page", "label": "Today"},    # r17: Morning + Today's Events in one page
            {"id": "lookup", "type": "page", "label": "Ticker Lookup"},
            {"id": "watchlist", "type": "page", "label": "Watchlist"},
            {"id": "desks", "type": "group", "label": "Desks",
             "children": [d["id"] for d in env.DEFAULT_DESKS]},
            {"id": "trackrecord", "type": "page", "label": "Track Record"},
            {"id": "portfolio", "type": "page", "label": "Portfolio"},
            {"id": "pnl", "type": "page", "label": "Interactive P&L"},
            {"id": "guide", "type": "page", "label": "How it works"},
            {"id": "changelog", "type": "page", "label": "What's new"},
        ],
        "desks": [dict(d, autostart=False, port_override=None) for d in env.DEFAULT_DESKS],
        "keep_alive": False,
        "auto_restart_on_update": True,
        "portfolio_url": DEFAULT_PORTFOLIO,
        "portfolio_view": "visual",
        "portfolio_title": env.env_str("DASH_PORTFOLIO_TITLE", "Stock Portfolio"),
        "portfolio_capital": env.env_int("DASH_STARTING_CAPITAL", 100000),
        "portfolio_image": None,
        "logo": None,
        "logo_fit": "contain",
        "logo_height": 96,
        "brand": "Deep Current",
        "theme": "system",          # light / dark / system: the MODE, for themes that have both
        "theme_id": themes.DEFAULT,  # which built-in theme
        "theme_motion": False,       # Cyberpunk's optional slow animation
        "clock_24h": False,
        "clock_seconds": True,
        "open_browser_on_start": True,
        "open_browser_on_boot": False,
        "earnings_filter": "all",
        "morning_autoopen": True,
        "mode": "all",               # Invest / Trade / All: which side of the menu is shown (r16)
        "sizer_risk_pct": 1.0,
        "sizer_max_pos_pct": 10.0,
        "sizer_stop_pct": 5.0,
        "sizer_side": "long",
        "backup_dir": "",
        "backup_keep": 30,
    }


MODES = ("all", "invest", "trade")
RETIRED_PAGES = {"events"}     # Today's Events became part of Today (r17)
PAGE_IDS = ("morning", "lookup", "watchlist", "trackrecord", "portfolio", "pnl", "guide", "changelog")


def ensure_builtins(menu, desk_ids):
    """Built-in pages can be moved or renamed, never lost. One added in a later version
    lands where it sits in the default menu, not at the bottom."""
    have = {m.get("id") for m in menu}
    for i, req in enumerate(default_settings()["menu"]):
        if req["id"] in have:
            continue
        item = req if req["id"] != "desks" else dict(req, children=sorted(desk_ids))
        menu.insert(min(i, len(menu)), item)
        have.add(req["id"])
    return menu


# ------------------------------------------------------------------ state
STATE = {"store": None, "sup": None, "events": None, "portfolio": None, "pnl_cache": None,
         "frame_cache": {}, "started": time.time()}
LOG_LOCK = threading.Lock()


def log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with LOG_LOCK:
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def settings():
    stored = STATE["store"].get("settings") or {}
    out = default_settings()
    out.update({k: v for k, v in stored.items() if k in out or k.startswith("x_")})
    # a desk added to the defaults later still shows up
    out["desks"] = [d for d in out["desks"] if d.get("id") not in env.RETIRED_DESKS]
    for d in out["desks"]:
        mv = env.MOVED_DESKS.get(d.get("id"))
        if mv and os.path.normcase(str(d.get("folder") or "")) == os.path.normcase(mv[0]):
            new_abs = os.path.join(env.PARENT, mv[1])
            if os.path.isfile(os.path.join(new_abs, "server.py")):
                d["folder"] = mv[1]
    have = {d["id"] for d in out["desks"]}
    added = []
    habit = "desks" in stored and all(d.get("autostart") for d in out["desks"])   # every desk autostarts: so does a new one
    for d in env.DEFAULT_DESKS:
        if d["id"] not in have:
            out["desks"].append(dict(d, autostart=habit, port_override=None))
            added.append(d["id"])
    menu = [dict(m, children=[c for c in m["children"] if c not in env.RETIRED_DESKS])
            if isinstance(m.get("children"), list) else dict(m)
            for m in out["menu"] if m.get("id") not in env.RETIRED_DESKS and m.get("id") not in RETIRED_PAGES]
    for m in menu:
        if m.get("id") == "morning" and m.get("label") == "Morning":
            m["label"] = "Today"           # the old default name; a name the user chose is kept
    if added and "menu" in stored:
        # a newly shipped desk joins the Desks group once; after the desk list is saved,
        # taking it out of the menu sticks
        listed = {m.get("id") for m in menu} | {c for m in menu for c in (m.get("children") or [])}
        for m in menu:
            if m.get("id") == "desks" and m.get("type") == "group":
                m["children"] = list(m.get("children") or []) + [i for i in added if i not in listed]
    out["menu"] = ensure_builtins(menu, {d["id"] for d in out["desks"]})
    return out


def sanitize_settings(new):
    """Accept only known keys with sane types; the menu is validated item by item."""
    cur = settings()
    base = default_settings()
    for k, v in (new or {}).items():
        if k not in base:
            continue
        if k == "menu":
            cur[k] = sanitize_menu(v, cur)
        elif k == "desks":
            cur[k] = sanitize_desks(v, cur["desks"])
        elif k in ("logo", "portfolio_image"):
            if v is None or (isinstance(v, str) and v.startswith("data:image/") and len(v) < 4_000_000):
                cur[k] = v
        elif isinstance(base[k], bool):
            cur[k] = bool(v)
        elif isinstance(base[k], float):
            try:
                cur[k] = max(0.0, min(100.0, float(v)))
            except (TypeError, ValueError):
                pass
        elif isinstance(base[k], int) and not isinstance(base[k], bool):
            try:
                if k == "logo_height":
                    cur[k] = max(40, min(260, int(v)))
                elif k == "portfolio_capital":
                    cur[k] = max(1, int(float(v)))
                elif k == "backup_keep":
                    cur[k] = backup_mod.clamp_keep(v)
                else:
                    cur[k] = int(v)
            except (TypeError, ValueError):
                pass
        elif k == "backup_dir":
            continue                            # only /api/backup/folder (validated, local) may change it
        elif k == "portfolio_url":
            v = str(v or "").strip()[:2000]
            if not v or portfolio_mod.allowed_url(v):
                cur[k] = v
        elif k == "theme_id":
            if v in themes.BY_ID:
                cur[k] = v
        elif k == "theme":
            if v in ("system", "light", "dark"):
                cur[k] = v
        elif k == "mode":
            if v in MODES:
                cur[k] = v
        elif isinstance(base[k], str) or base[k] is None:
            cur[k] = str(v)[:2000] if v is not None else None
    return cur


def sanitize_menu(menu, cur):
    desk_ids = {d["id"] for d in cur["desks"]}
    out, seen = [], set()
    for it in menu if isinstance(menu, list) else []:
        if not isinstance(it, dict) or it.get("id") in seen:
            continue
        typ = it.get("type")
        label = str(it.get("label") or "")[:60]
        if typ == "page" and it.get("id") in PAGE_IDS:
            out.append({"id": it["id"], "type": "page", "label": label or it["id"]})
        elif typ == "group" and it.get("id") == "desks":
            kids = [c for c in it.get("children", []) if c in desk_ids]
            kids += [d for d in desk_ids if d not in kids]
            out.append({"id": "desks", "type": "group", "label": label or "Desks", "children": kids})
        elif typ == "link":
            url = str(it.get("url") or "").strip()
            if not re.match(r"^https?://", url, re.I):
                continue
            out.append({"id": re.sub(r"[^\w-]", "", str(it.get("id")))[:40] or "link-%d" % len(out),
                        "type": "link", "label": label or url, "url": url[:2000],
                        "mode": "tab" if it.get("mode") == "tab" else "frame"})
        else:
            continue
        seen.add(out[-1]["id"])
    return ensure_builtins(out, desk_ids)


def sanitize_desks(new, cur):
    by = {d["id"]: d for d in cur}
    for d in new if isinstance(new, list) else []:
        if not isinstance(d, dict) or d.get("id") not in by:
            continue
        t = by[d["id"]]
        if "name" in d:
            t["name"] = str(d["name"])[:60] or t["name"]
        if "folder" in d:
            f = str(d["folder"])[:500].strip()
            if not UNC_RE.match(f):             # a network share is refused: keep the old folder
                t["folder"] = f
        if "autostart" in d:
            t["autostart"] = bool(d["autostart"])
        if "port_override" in d:
            try:
                p = int(d["port_override"]) if d["port_override"] not in (None, "") else None
                t["port_override"] = p if p is None or 1024 <= p <= 65535 else t.get("port_override")
            except (TypeError, ValueError):
                pass
    return list(by.values())


# ------------------------------------------------------------------ backups
PICKER = {"pick": backup_mod.pick_folder, "open": backup_mod.open_folder, "lock": threading.Lock()}


def set_backup_folder(body):
    """Validate + save the backup folder. Empty path = back to the default."""
    raw = str(body.get("path") or "").strip()
    old = STATE["backup"].status()["dest"]
    if not raw:
        path = ""
        dest = backup_mod.destination(STATE["backup"].here)[0]
    else:
        path, err = backup_mod.check_folder(raw)
        if err:
            return 400, {"error": err}
        dest = path
    copied = 0
    if body.get("copy_existing", True):
        try:
            os.makedirs(dest, exist_ok=True)
            copied = backup_mod.copy_backups(old, dest)
        except OSError as exc:
            return 400, {"error": "Saved nothing: couldn't copy the old backups (%s)." % (exc.strerror or exc)}
    s = settings()
    s["backup_dir"] = path
    STATE["store"].set("settings", s)
    log("backup folder -> %s (%d old backups copied)" % (dest, copied))
    return 200, {"ok": True, "settings": s, "copied": copied, "backup": STATE["backup"].status()}


def pick_backup_folder():
    if not PICKER["lock"].acquire(blocking=False):
        return {"error": "A folder picker is already open on this PC - look behind the browser window."}
    try:
        chosen = PICKER["pick"](STATE["backup"].status()["dest"])
    except backup_mod.PickerUnavailable as exc:
        return {"error": str(exc)}
    finally:
        PICKER["lock"].release()
    return {"path": chosen, "cancelled": not chosen}


# ------------------------------------------------------------------ P&L
def _today():
    """Local date as YYYY-MM-DD. Tests replace this to pin the clock."""
    return time.strftime("%Y-%m-%d")


def pnl_payload(account=None):
    st = STATE["store"]
    fills = st.all_fills()
    today = _today()
    key = (len(fills), st.get("pnl_rev", 0), account, today)
    cached = STATE["pnl_cache"]
    if cached and cached[0] == key:
        return cached[1]
    res = pnl.compute(fills, today=today, account=account or None)
    days = pnl.daily(res["closed"], res["unmatched"])
    payload = {
        "days": days, "stats": pnl.stats(res["closed"], days), "closed": res["closed"],
        "unmatched": res["unmatched"], "open": res["open"],
        "accounts": sorted({f["account"] for f in fills}), "account": account or "",
        "imports": st.imports(), "fills": len(fills),
        "first_fill": fills[0]["ts"][:10] if fills else None,
        "last_fill": fills[-1]["ts"][:10] if fills else None,
    }
    STATE["pnl_cache"] = (key, payload)
    return payload


def pnl_upload(body):
    text = body.get("text") or ""
    if not text.strip():
        return 400, {"error": "The file is empty."}
    mapping = body.get("mapping") or None
    try:
        broker, fills, nrows, notes = pnl.parse(text, account_label=body.get("account", ""), mapping=mapping)
    except pnl.ParseError as exc:
        return 422, {"error": str(exc), "columns": exc.columns,
                     "guess": pnl.guess_mapping(exc.columns) if exc.columns else {}}
    if not fills:
        return 422, {"error": "Read the file as %s but found no trades in it. If this is the right file, "
                              "check the date range you exported." % pnl.BROKER_NAMES.get(broker, broker),
                     "columns": []}
    st = STATE["store"]
    imp, new = st.add_import(body.get("filename") or "upload.csv", broker,
                             fills[0]["account"], nrows, fills, "\n".join(notes))
    st.set("pnl_rev", (st.get("pnl_rev", 0) or 0) + 1)
    log("P&L import #%d: %s, %s, %d fills (%d new)" % (imp, body.get("filename"), broker, len(fills), new))
    return 200, {"import_id": imp, "broker": pnl.BROKER_NAMES.get(broker, broker), "fills": len(fills),
                 "new": new, "duplicates": len(fills) - new, "notes": notes,
                 "range": [fills[0]["ts"][:10], fills[-1]["ts"][:10]]}


# ------------------------------------------------------------------ frames
def frame_check(url):
    """Can this URL be shown inside the dashboard, or will the site refuse?"""
    if len(STATE["frame_cache"]) > 200:
        STATE["frame_cache"].clear()            # bounded: one entry per link you've checked
    hit = STATE["frame_cache"].get(url)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    res = {"url": url, "frameable": None, "reason": None}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 uw-dashboard"})
        with urllib.request.urlopen(req, timeout=8) as r:
            xfo = (r.headers.get("X-Frame-Options") or "").lower()
            csp = (r.headers.get("Content-Security-Policy") or "").lower()
    except urllib.error.HTTPError as exc:
        xfo = (exc.headers.get("X-Frame-Options") or "").lower() if exc.headers else ""
        csp = (exc.headers.get("Content-Security-Policy") or "").lower() if exc.headers else ""
    except Exception as exc:      # noqa: BLE001
        res["reason"] = "Could not reach the site to check (%s)." % exc.__class__.__name__
        STATE["frame_cache"][url] = (time.time(), res)
        return res
    fa = re.search(r"frame-ancestors([^;]*)", csp)
    if xfo in ("deny", "sameorigin"):
        res.update(frameable=False, reason="The site sends X-Frame-Options: %s." % xfo.upper())
    elif fa and "*" not in fa.group(1):
        res.update(frameable=False, reason="The site only allows framing by: %s." % fa.group(1).strip())
    else:
        res["frameable"] = True
    STATE["frame_cache"][url] = (time.time(), res)
    return res


# ------------------------------------------------------------------ HTTP
def build_stamp():
    """page_mtime: the tab reloads itself when it grows. code_mtime > started: restart needed."""
    def newest(names):
        m = 0
        for name in names:
            try:
                m = max(m, os.path.getmtime(os.path.join(HERE, name)))
            except OSError:
                pass
        return m
    page = newest(("index.html", "app.js"))
    code = newest(("server.py", "pnl.py", "desks.py", "events.py", "portfolio.py", "store.py", "env.py",
                   "market.py", "sizer.py", "deskview.py", "trackrec.py", "backup.py", "guide.py", "themes.py", "changelog.py", "watchlist.py"))
    m = max(page, code)
    return {"version": VERSION, "page_mtime": page, "code_mtime": code, "started": STATE["started"],
            "built": time.strftime("%Y-%m-%d %H:%M", time.localtime(m)) if m else "?"}


class Handler(BaseHTTPRequestHandler):
    server_version = "UWDashboard/" + VERSION

    def log_message(self, fmt, *args):   # quiet
        pass

    # --- helpers
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # no other site may frame the dashboard (clickjacking); the dashboard frames the desks, not the reverse
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _local_host(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "")

    def _origins(self):
        port = self.server.server_address[1]
        return {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port}

    def _same_origin(self):
        """A state-changing request must come from THIS page: exact origin (scheme, host AND port), and
        JSON only, so a cross-site form or text/plain POST can't even be sent without a preflight
        (which this server never answers). Before 2026-09-26 any 127.0.0.1 port passed, so script on a
        desk page could rewrite a desk folder and have the supervisor run it."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return False
        origin = self.headers.get("Origin")
        if origin is None:                      # browsers always send Origin on POST; local tools may not
            ref = self.headers.get("Referer")
            if not ref:
                return True
            u = urllib.parse.urlparse(ref)
            origin = "%s://%s" % (u.scheme, u.netloc)
        return origin in self._origins()

    def _cross_site_get(self, path):
        """A GET from another site (an <img> tag, a link) must not make the dashboard spend UW calls or
        fetch URLs. Browsers label every request; same-origin, typed URLs and bookmarks pass. The theme
        hook and fonts are meant for the desks' pages, so they stay open."""
        if not path.startswith("/api/") or path == "/api/theme/desk":
            return False
        site = self.headers.get("Sec-Fetch-Site")
        return site is not None and site not in ("same-origin", "none")

    LOCAL_ORIGIN_RE = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d{1,5})?$")

    def _page_origin(self):
        o = self.headers.get("Origin")
        if o:
            return o
        ref = urllib.parse.urlsplit(self.headers.get("Referer") or "")
        return "%s://%s" % (ref.scheme, ref.netloc) if ref.scheme else ""

    def _local_origin(self):
        o = self._page_origin()
        return o if self.LOCAL_ORIGIN_RE.match(o) else "null"

    def _foreign_page(self):
        """The theme hook is for the desks' own pages (other ports on this PC). A page on another
        site may not load it: it names the dashboard (r21 security check)."""
        site = self.headers.get("Sec-Fetch-Site")
        if site in (None, "same-origin", "same-site", "none"):
            return False
        return not self.LOCAL_ORIGIN_RE.match(self._page_origin())

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Bad Content-Length.")
        cap = MAX_UPLOAD if urllib.parse.urlparse(self.path).path == "/api/pnl/upload" else MAX_BODY
        if n < 0 or n > cap:
            raise ValueError("Upload too large (limit %d MB)." % (cap // 1048576))
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    # --- routes
    def do_GET(self):
        if not self._local_host():
            return self._send(403, "local only", "text/plain")
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        if self._cross_site_get(path):
            return self._send(403, "same-site only", "text/plain")
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as fh:
                    page = fh.read().decode("utf-8")
                return self._send(200, themed_page(page), "text/html; charset=utf-8")
            if path == "/api/theme":
                return self._json(theme_state())
            if path == "/theme.js":
                if self._foreign_page():
                    return self._send(403, "local pages only", "text/plain")
                desk = re.sub(r"[^a-z]", "", (q.get("desk") or [""])[0])[:20]
                js = themes.theme_js(desk, desk_theme(desk), "http://127.0.0.1:%d" % env.PORT)
                return self._send(200, js, "application/javascript; charset=utf-8")
            m = re.match(r"^/fonts/([\w.-]+\.woff2)$", path)
            if m and m.group(1) in themes.FONT_FILES:
                with open(os.path.join(HERE, "fonts", m.group(1)), "rb") as fh:
                    return self._send(200, fh.read(), "font/woff2", {"Access-Control-Allow-Origin": "*"})
            if path == "/api/theme/desk":
                desk = re.sub(r"[^a-z]", "", (q.get("desk") or [""])[0])[:20]
                if self._foreign_page():
                    return self._send(403, "local pages only", "text/plain")
                # r21: the palette now carries the brand name, so only pages on this PC may read it
                return self._send(200, json.dumps(desk_theme(desk)), "application/json; charset=utf-8",
                                  {"Access-Control-Allow-Origin": self._local_origin(), "Vary": "Origin"})
            if path == "/api/brand":
                desk = re.sub(r"[^a-z]", "", (q.get("desk") or [""])[0])[:20]
                return self._json(brand_info(desk))
            if path == "/app.js":
                with open(os.path.join(HERE, "app.js"), "rb") as fh:
                    return self._send(200, fh.read(), "application/javascript; charset=utf-8")
            if path == "/api/state":
                cl = STATE["events"].client
                return self._json({"settings": settings(), "build": build_stamp(), "warnings": env.WARNINGS,
                                   "token": bool(cl.token), "token_source": cl.token_src, "port": env.PORT,
                                   "folder": HERE, "started": STATE["started"], "release": RELEASE})
            if path == "/api/desks":
                return self._json({"desks": STATE["sup"].snapshot(), "build": build_stamp()})
            m = re.match(r"^/api/desks/([\w-]+)/log$", path)
            if m:
                return self._json({"log": STATE["sup"].log_tail(m.group(1), 200) or ""})
            if path == "/api/events":
                d = (q.get("date") or [time.strftime("%Y-%m-%d")])[0]
                if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                    return self._json({"error": "bad date"}, 400)
                ev = STATE["events"]
                return self._json({"date": d, "calendar": ev.calendar(), "earnings": ev.earnings(d), "mine": my_names()})
            if path == "/api/diag":
                d = (q.get("date") or [time.strftime("%Y-%m-%d")])[0]
                out = STATE["events"].diag(d)
                out["checks"] += market_mod.diag(STATE["events"].client, (q.get("t") or ["AAPL"])[0].upper())
                out["portfolio"] = {k: v for k, v in STATE["portfolio"].get(settings()["portfolio_url"], True).items()
                                    if k in ("csv", "error", "columns")}
                out["desks"] = STATE["sup"].snapshot()
                return self._json(out)
            if path == "/api/portfolio":
                s = settings()
                d = STATE["portfolio"].get(s["portfolio_url"], bool(q.get("force")))
                d = dict(d, model=portfolio_mod.model(d, float(s["portfolio_capital"] or 100000)))
                return self._json(d)
            if path == "/portfolio-image":
                img = os.path.join(HERE, "portfolio_center.jpg")
                if not os.path.isfile(img):
                    return self._send(404, "no image", "text/plain")
                with open(img, "rb") as fh:
                    return self._send(200, fh.read(), "image/jpeg")
            if path == "/api/portfolio/urls":
                return self._json(portfolio_mod.sheet_urls(settings()["portfolio_url"]))
            if path == "/api/framecheck":
                url = (q.get("url") or [""])[0]
                if not re.match(r"^https?://", url, re.I):
                    return self._json({"error": "bad url"}, 400)
                return self._json(frame_check(url))
            if path == "/api/pnl":
                return self._json(pnl_payload((q.get("account") or [""])[0]))
            if path == "/api/journal":
                d = (q.get("date") or [""])[0]
                if not DAY_RE.match(d):
                    return self._json({"error": "bad date"}, 400)
                return self._json(STATE["store"].journal_get(d) or {"day": d, "body": "", "updated_at": None})
            if path == "/api/journal/list":
                qq = (q.get("q") or [""])[0].strip()[:100]
                out = []
                for r in STATE["store"].journal_list(qq):
                    body = r["body"]
                    i = body.lower().find(qq.lower()) if qq else 0
                    start = 0
                    if qq and i > 60:                    # open the snippet on a word boundary
                        cut = body.rfind(" ", 0, i - 40)
                        start = cut + 1 if cut > 0 else i
                    out.append({"day": r["day"], "updated_at": r["updated_at"], "words": len(body.split()),
                                "preview": ("\u2026" if start else "") + body[start:start + 220].strip()})
                return self._json({"entries": out, "q": qq})
            if path == "/api/health":
                return self._json({"ok": True, "build": build_stamp()})
            if path == "/api/quote":
                t = re.sub(r"[^A-Za-z0-9.\-]", "", (q.get("t") or [""])[0]).upper()[:12]
                try:
                    return self._json(dict(STATE["market"].quote(t), ticker=t))
                except Exception as exc:      # noqa: BLE001
                    return self._json({"ticker": t, "error": str(exc)}, 502)
            if path == "/api/growth":
                t = watch_mod.clean_ticker((q.get("t") or [""])[0])
                if not t:
                    return self._json({"error": "bad ticker"}, 400)
                return self._json(STATE["deskview"].growth_card(t))
            if path == "/api/direction":
                return self._json(STATE["deskview"].growth_market())
            if path == "/api/lookup":
                return self._json(lookup((q.get("t") or [""])[0]))
            if path == "/api/flowtape":
                code, body = STATE["deskview"].flow_tape(**{k: (q.get(k) or [None])[0] for k in ("alert", "contract", "date")})
                return self._json(body, code)
            if path == "/api/warnings":
                return self._json(holdings_warnings())
            if path == "/api/market":
                return self._json(market_overview())
            if path == "/api/morning":
                return self._json(morning())
            if path == "/api/watchlist":
                return self._json(STATE["watch"].view_all())
            if path in ("/api/watchlist/item", "/api/watchlist/signals"):
                t = watch_mod.clean_ticker((q.get("t") or [""])[0])
                if not t:
                    return self._json({"error": "That isn't a ticker."}, 400)
                if path.endswith("/signals"):
                    return self._json(STATE["watch"].signals(t))
                it = STATE["watch"].item(t)
                it["events"] = STATE["watch"].events(90, t)
                return self._json(it)
            if path == "/api/changelog":
                return self._json(dict(changelog.load(), release=RELEASE))
            if path == "/api/guide":
                return self._json(guide_page())
            if path == "/api/trackrecord":
                tr = STATE["track"]
                if time.time() - tr.last_score > 3600:
                    threading.Thread(target=tr.score, daemon=True).start()
                return self._json(tr.report())
            if path == "/api/healthz":
                return self._json(health())
            return self._send(404, "not found", "text/plain")
        except Exception as exc:      # noqa: BLE001
            log("GET %s failed: %r" % (path, exc))
            return self._json({"error": str(exc)}, 500)

    def do_POST(self):
        if not self._local_host() or not self._same_origin():
            return self._send(403, "local only", "text/plain")
        path = urllib.parse.urlparse(self.path).path
        try:
            body = self._body()
        except ValueError as exc:
            return self._json({"error": str(exc)}, 413)
        try:
            if path == "/api/settings":
                s = sanitize_settings(body)
                STATE["store"].set("settings", s)
                return self._json({"settings": s})
            if path == "/api/settings/reset":
                STATE["store"].set("settings", default_settings())
                return self._json({"settings": settings()})
            m = re.match(r"^/api/desks/([\w-]+)/(ensure|start|stop|restart)$", path)
            if m:
                sup = STATE["sup"]
                did, act = m.groups()
                res = {"ensure": sup.ensure, "start": sup.start, "restart": sup.restart,
                       "stop": sup.stop}[act](did)
                return self._json({"result": res, "desks": sup.snapshot()})
            if path == "/api/journal":
                d = str(body.get("date") or "")
                text = body.get("body")
                if not DAY_RE.match(d) or not isinstance(text, str):
                    return self._json({"error": "bad journal entry"}, 400)
                if len(text) > 200000:
                    return self._json({"error": "That note is over 200,000 characters."}, 413)
                saved = STATE["store"].journal_put(d, text)
                return self._json(saved or {"day": d, "body": "", "updated_at": None})
            if path == "/api/size":
                return self._json(size_request(body))
            if path == "/api/backup":
                return self._json(STATE["backup"].backup_now("manual"))
            if path == "/api/backup/folder":
                code, res = set_backup_folder(body)
                return self._json(res, code)
            if path == "/api/backup/pick":
                return self._json(pick_backup_folder())
            if path == "/api/backup/open":
                try:
                    dest = STATE["backup"].status()["dest"]
                    os.makedirs(dest, exist_ok=True)
                    PICKER["open"](dest)
                    return self._json({"ok": True, "dest": dest})
                except OSError as exc:
                    return self._json({"error": str(exc)}, 400)
            if path == "/api/restart":
                return self._json(restart_dashboard())
            if path == "/api/trackrecord/record":
                n = STATE["track"].record()
                threading.Thread(target=lambda: STATE["track"].score(force=True), daemon=True).start()
                return self._json({"added": n})
            m = re.match(r"^/api/watchlist/(add|remove|thesis|rerun)$", path)
            if m:
                w, t = STATE["watch"], body.get("ticker")
                code, res = (w.add(t) if m.group(1) == "add" else w.remove(t) if m.group(1) == "remove"
                             else w.save_thesis(t, body) if m.group(1) == "thesis" else w.rerun_now(t))
                return self._json(res, code)
            if path == "/api/pnl/upload":
                code, res = pnl_upload(body)
                return self._json(res, code)
            m = re.match(r"^/api/pnl/import/(\d+)/delete$", path)
            if m:
                STATE["store"].delete_import(int(m.group(1)))
                STATE["store"].set("pnl_rev", (STATE["store"].get("pnl_rev", 0) or 0) + 1)
                return self._json({"ok": True})
            return self._send(404, "not found", "text/plain")
        except Exception as exc:      # noqa: BLE001
            log("POST %s failed: %r" % (path, exc))
            return self._json({"error": str(exc)}, 500)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows: SO_REUSEADDR would let a SECOND copy share the port, so it stays off there
    # (Windows lets a listener bind over TIME_WAIT connections anyway). Linux needs it on,
    # or a restart can't bind for 60 s after any connection was served.
    allow_reuse_address = os.name != "nt"


def port_in_use(port):
    return desks_mod.probe(port, timeout=1.0)


def running_ports():
    return {d["id"]: d["port"] for d in STATE["sup"].snapshot() if d.get("status") == "running" and d.get("port")}


def init(db_path=DB_PATH, events=None, portfolio=None, supervisor=None, desk_fetch=None, market=None):
    STATE["db_path"] = db_path
    STATE["store"] = store_mod.Store(db_path)
    STATE["events"] = events or events_mod.Events()
    STATE["portfolio"] = portfolio or portfolio_mod.Portfolio()
    STATE["sup"] = supervisor or desks_mod.Supervisor(settings, log=log)
    STATE["market"] = market or market_mod.Market(STATE["events"].client)
    STATE["deskview"] = deskview.Desks(running_ports, fetch=desk_fetch)
    STATE["track"] = trackrec.TrackRecord(STATE["store"], STATE["deskview"], STATE["market"], log=log)
    STATE["watch"] = watch_mod.Watchlist(STATE["store"], STATE["market"], STATE["deskview"],
                                         holdings=_open_positions, supervisor=STATE["sup"], log=log)
    STATE["backup"] = backup_mod.Nightly(db_path, os.path.dirname(os.path.abspath(db_path)), STATE["store"],
                                         log=log, get_override=lambda: settings().get("backup_dir") or None,
                                         get_keep=lambda: settings().get("backup_keep", backup_mod.KEEP))
    return STATE


# ------------------------------------------------------------------ features
def portfolio_model(force=False):
    s = settings()
    d = STATE["portfolio"].get(s["portfolio_url"], force)
    return portfolio_mod.model(d, float(s["portfolio_capital"] or 100000)), d


def my_names():
    """{ticker: "held" | "watching"} for Today's earnings list: your names go first."""
    out = {}
    try:
        for t, held, on_list, _, _ in STATE["watch"].names():
            out[t] = "held" if held else "watching"
    except Exception:                  # noqa: BLE001 -- the list still works without it
        pass
    return out


def _open_positions():
    m, _ = portfolio_model()
    return m.get("open") or [] if m.get("ok") else []


def holdings_warnings():
    m, _ = portfolio_model()
    if not m.get("ok"):
        return {"ok": False, "holdings": [], "error": "Portfolio sheet unavailable."}
    out, errors = [], []
    for p in m["open"]:
        try:
            h = STATE["market"].holding(p["ticker"])
            out.append(dict(h, color=p.get("color")))
        except Exception as exc:      # noqa: BLE001
            errors.append("%s: %s" % (p["ticker"], exc))
    return {"ok": True, "holdings": out, "errors": errors,
            "thresholds": {"earnings_days": market_mod.EARNINGS_DAYS,
                           "insider_usd": market_mod.INSIDER_MIN_USD, "insider_days": market_mod.INSIDER_DAYS,
                           "put_usd": market_mod.FLOW_MIN_PUT_USD, "put_call": market_mod.FLOW_PUT_CALL_RATIO,
                           "flow_days": market_mod.FLOW_DAYS}}


def theme_state():
    st = settings()
    t = themes.get(st.get("theme_id"))
    return {"id": t["id"], "mode": themes.resolve_mode(t, st.get("theme")), "modes": list(t["modes"]),
            "motion": bool(st.get("theme_motion")),
            "css": themes.dashboard_css(t["id"], st.get("theme"), bool(st.get("theme_motion"))),
            "themes": themes.public_list()}


def desk_theme(desk):
    st = settings()
    p = themes.desk_payload(st.get("theme_id"), desk, st.get("theme"), bool(st.get("theme_motion")),
                            "http://127.0.0.1:%d" % env.PORT)
    p["brand"] = brand_info(desk)
    return p


def _plain(s, n=60):
    """A name for other programs to print: no control characters, no markup, bounded."""
    s = re.sub(r"[\x00-\x1f\x7f<>]", "", str(s or "")).strip()
    return s[:n]


def brand_info(desk):
    """What a desk calls itself on its page, share cards, Word reports and Discord posts:
    "<brand> · <the desk's name in Settings>". The data credit (Unusual Whales) is separate."""
    st = settings()
    brand = _plain(st.get("brand")) or "Deep Current"
    d = next((x for x in st.get("desks", []) if x.get("id") == desk), None)
    name = _plain(d.get("name")) if d else ""
    return {"brand": brand, "desk": name, "label": ("%s \u00b7 %s" % (brand, name)) if name else brand}


def themed_page(page):
    """Put the theme CSS into the page itself, after the built-in palette, so the first paint is
    already themed. Paper adds nothing."""
    st = theme_state()
    tag = '<style id="themeCss">%s</style>' % st["css"].replace("</", "<\\/")
    page = page.replace("</style>", "</style>\n" + tag, 1)
    page = page.replace("<title>Dashboard</title>", "<title>%s</title>" % html.escape(brand_info("")["brand"]), 1)
    if st["mode"]:
        page = page.replace('<html lang="en">', '<html lang="en" data-theme="%s">' % st["mode"], 1)
    return page


def guide_page():
    st = settings()
    enabled = [d["id"] for d in st.get("desks", [])]
    return guide.build(STATE.get("deskview"), st, enabled=enabled)


def lookup(t):
    t = re.sub(r"[^A-Z0-9.\-]", "", t.upper())[:12]
    if not t or not t[0].isalnum():     # r21: "..", "-" etc. would land in UW paths (/api/stock/../info)
        return {"error": "Type a ticker."}
    out = {"ticker": t}
    try:
        out["quote"] = STATE["market"].quote(t)
    except Exception as exc:          # noqa: BLE001
        out["quote"] = {"error": str(exc)}
    out["desks"] = STATE["deskview"].for_ticker(t)
    try:
        out["opening_flow"] = STATE["market"].opening_flow(t)
    except Exception as exc:          # noqa: BLE001 -- the rest of the lookup still works
        out["opening_flow"] = {"error": str(exc)}
    try:
        out["holding"] = STATE["market"].holding(t)
    except Exception as exc:          # noqa: BLE001
        out["holding"] = {"error": str(exc)}
    m, _ = portfolio_model()
    out["position"] = next((p for p in (m.get("open") or []) if p["ticker"] == t), None)
    out["closed_in_sheet"] = [c for c in (m.get("closed") or []) if c["ticker"] == t]
    out["cash"] = (m.get("summary") or {}).get("cash")
    pl = pnl_payload()
    mine = [x for x in pl["closed"] if x["underlying"] == t]
    out["my_trades"] = {"count": len(mine), "net": sum(x["net"] for x in mine),
                        "wins": sum(1 for x in mine if x["net"] > 0), "recent": mine[-8:][::-1]}
    notes = STATE["store"].journal_list(t)
    out["journal"] = [{"day": n["day"], "snippet": _snippet(n["body"], t)} for n in notes[:6]]
    return out


def _snippet(body, q):
    i = body.lower().find(q.lower())
    start = 0
    if i > 60:
        cut = body.rfind(" ", 0, i - 40)
        start = cut + 1 if cut > 0 else i
    return ("\u2026" if start else "") + body[start:start + 200].strip()


def size_request(body):
    s = settings()
    m, _ = portfolio_model()
    cap = body.get("capital") or s["portfolio_capital"]
    cash = None
    if m.get("ok") and m["summary"].get("cash") is not None:
        cash = float(cap) * m["summary"]["cash"]
    res = sizer.size(cap, body.get("entry"), float(body.get("risk_pct", 0)) / 100,
                     float(body.get("max_pos_pct", 0)) / 100, float(body.get("stop_pct", 0)) / 100,
                     body.get("side", "long"), cash)
    if body.get("remember"):
        STATE["store"].set("settings", sanitize_settings({
            "sizer_risk_pct": body.get("risk_pct"), "sizer_max_pos_pct": body.get("max_pos_pct"),
            "sizer_stop_pct": body.get("stop_pct"), "sizer_side": res.get("side", "long")}))
    return res


def market_overview():
    out = {}
    for k, fn in (("sectors", STATE["market"].sectors), ("tide", STATE["market"].tide)):
        try:
            out[k] = fn()
        except Exception as exc:      # noqa: BLE001
            out[k] = []
            out[k + "_error"] = str(exc)
    return out


def morning():
    today = time.strftime("%Y-%m-%d")
    out = {"date": today, "et": trackrec.et_now().isoformat(timespec="minutes")}
    out["calendar"] = STATE["events"].calendar()
    out["market"] = market_overview()
    out["warnings"] = holdings_warnings()
    try:
        out["direction"] = STATE["deskview"].growth_market()
    except Exception as exc:          # noqa: BLE001
        out["direction"] = {"error": str(exc)}
    try:
        out["picks"] = STATE["deskview"].picks()
    except Exception as exc:          # noqa: BLE001
        out["picks"] = {"error": str(exc)}

    prev = [e for e in STATE["store"].journal_list() if e["day"] < today]
    out["journal_prev"] = {"day": prev[0]["day"], "body": prev[0]["body"][:1500]} if prev else None
    out["journal_today"] = bool(STATE["store"].journal_get(today))
    try:
        out["names"] = STATE["watch"].morning()
    except Exception as exc:          # noqa: BLE001
        out["names"] = {"error": str(exc)}
    out["desks"] = [{k: d.get(k) for k in ("id", "name", "status", "updated", "error")} for d in STATE["sup"].snapshot()]
    b = STATE["store"].get("backup_last") or {}
    out["backup"] = {"ok": b.get("ok"), "at": b.get("at"), "error": b.get("error")}
    return out


def health():
    snap = STATE["sup"].snapshot()
    out = []
    for d in snap:
        h = STATE["deskview"].health(d["id"]) if d["status"] == "running" else {"error": d["status"]}
        tail = STATE["sup"].log_tail(d["id"], 6)
        log_path = os.path.join(desks_mod.LOG_DIR, "%s.log" % d["id"])
        out.append(dict(d, health=h, log_tail=tail,
                        log_mtime=os.path.getmtime(log_path) if os.path.isfile(log_path) else None,
                        started_at=(STATE["sup"].state.get(d["id"]) or {}).get("launched_at")))
    return {"desks": out, "dashboard": {"started": STATE["started"], "build": build_stamp(),
                                       "uw_calls": getattr(STATE["events"].client, "calls", None),
                                       "restart_needed": build_stamp()["code_mtime"] > STATE["started"] + 1},
            "backup": STATE["backup"].status()}


def restart_dashboard():
    """Start a fresh copy of the dashboard, then exit this one. The new copy waits for the port."""
    import subprocess
    args = [sys.executable, os.path.join(HERE, "server.py"), "--restarted"]
    kwargs = {"cwd": HERE}
    subprocess.Popen(args, **kwargs)
    log("restarting: new process launched, this one exits")
    threading.Timer(0.6, lambda: os._exit(0)).start()
    return {"ok": True}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    boot = "--boot" in argv
    restarted = "--restarted" in argv
    url = "http://127.0.0.1:%d/" % env.PORT
    if restarted:                       # the old copy is exiting; give it a moment to free the port
        for _ in range(40):
            if not port_in_use(env.PORT):
                break
            time.sleep(0.25)
    if port_in_use(env.PORT):
        print("\n  The dashboard is already running at %s -- opening it.\n" % url)
        if not boot:
            webbrowser.open(url)
        return
    init()
    log("UW Dashboard %s starting in %s" % (VERSION, HERE))
    tok, src = env.find_token()
    log("UW token: %s" % ("found in %s" % src if tok else "NOT FOUND -- Today's Events will say so"))
    for w in env.WARNINGS:
        log("WARNING: " + w)
    httpd = None
    for _ in range(60):
        try:
            httpd = Server(("127.0.0.1", env.PORT), Handler)
            break
        except OSError:
            time.sleep(1.0)
    if httpd is None:
        log("could not bind port %d" % env.PORT)
        return
    STATE["sup"].run()
    STATE["backup"].run_forever()
    STATE["track"].run_forever()
    STATE["watch"].run_forever()
    s = settings()
    log("serving %s" % url)
    if not restarted and (s["open_browser_on_boot"] if boot else s["open_browser_on_start"]) and env.OPEN_BROWSER:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down (desks keep running -- stop them from the dashboard)")


if __name__ == "__main__":
    main()
