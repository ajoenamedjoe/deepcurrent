"""The server module, a stub UW client fed synthetic payloads, real HTTP -- every route the page calls."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from _path import ROOT, sample
from fixtures.sample_events import EARNINGS, MARKET_EVENTS
import desks
import env
import events
import portfolio
import server
import store


WIDGET = ("Open/Closed,Date,Close Date,Ticker,Amt,Entry,Mark,% P&L (Unreal),$$ P&L (Unreal),Thoughts\n"
          "Open,8/11/2026,,ACME,30,412.40,405.80,-1.60%,-198.00,Weekly flag breakout\n"
          "Open,7/14/2026,,ZENO,12,142.50,163.10,14.46%,247.2,Channel breakout\n")


class FakeMarket:
    def quote(self, t):
        if t == "NOPE":
            raise RuntimeError("No price for NOPE from UW.")
        return {"price": 50.0, "source": "UW quote", "name": t + " Inc"}

    def holding(self, t):
        import market
        from fixtures.sample_market import ACME_FLOW, ACME_INSIDER
        import datetime as dt
        if t == "ACME":
            return market.warnings_for(t, {}, [], ACME_INSIDER, ACME_FLOW, dt.date(2026, 10, 14),
                                       now_ms=ACME_FLOW[0]["start_time"])
        return market.warnings_for(t, {}, [], [], [], dt.date(2026, 10, 14))

    def opening_flow(self, t):
        import datetime as dt
        import openflow
        a = {"option_chain": t + "311219C00100000", "type": "call", "strike": "100", "expiry": "2031-12-19",
             "total_premium": "120000", "total_ask_side_prem": "110000", "total_bid_side_prem": "0", "total_size": 300,
             "open_interest": 120, "volume": 900, "start_time": 1, "has_singleleg": True, "has_multileg": False}
        return openflow.summarize([a], dt.date(2031, 6, 13), 0)

    def sectors(self):
        return [{"ticker": "SPY", "name": "S&P 500", "last": 702.44, "chg": -0.0038, "bull": 1, "bear": 2, "lean": -0.3}]

    def tide(self):
        return [{"t": "2026-10-14T09:30:00-04:00", "call": 1.0, "put": 2.0, "vol": 1}]

    def daily(self, t, timeframe="1Y"):
        return []


def desk_fetch(port, path):
    if port == 8790 and path.startswith("/api/history"):
        return {"runs": [{"id": 1, "ticker": "ACME", "run_at": "2026-10-14 10:00:00", "price": 412,
                          "base_value": 468, "score": 6.8, "verdict": "BUY"}]}
    if port == 8790 and path == "/api/method":
        return {"margin_of_safety": 0.25}
    if port == 8790 and path == "/api/run/1":
        return {"card": {"quality": {"score": 7.2, "tier": "high"}, "synopsis": "$ACME: cheap."},
                "result": {"fin": {"period_end": "2026-06-30", "basis": "TTM"}}}
    if port == 8790 and path == "/api/health":
        return {"ok": True, "client_error": None, "calls": 12}
    raise OSError("no such route in the stub")


class StubSup:
    def __init__(self):
        self.calls = []
        self.state = {}

    def snapshot(self):
        return [{"id": "valuation", "name": "Valuation", "status": "running", "port": 8790,
                 "url": "http://127.0.0.1:8790/", "updated": False, "page_mtime": 1.0}]

    def ensure(self, did):
        self.calls.append(("ensure", did))
        return "starting"

    start = restart = ensure

    def stop(self, did):
        self.calls.append(("stop", did))
        return True

    def log_tail(self, did, n=25):
        return "log"


def opener(url):
    if "economic-calendar" in url:
        return 200, MARKET_EVENTS
    if "premarket" in url and "page=0" in url:
        return 200, {"data": EARNINGS["result"]}
    return 200, {"data": []}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = os.path.join(tempfile.mkdtemp(), "d.db")
        server.LOG_PATH = os.path.join(os.path.dirname(db), "dashboard.log")   # never the user's own log
        cls.sup = StubSup()
        server.init(db, events=events.Events(events.Client(token="t", opener=opener)),
                    portfolio=portfolio.Portfolio(fetch=lambda u: WIDGET if "widget" in u else "Ticker,Shares,Gain\nAAPL,1,$2.00\n"),
                    supervisor=cls.sup, market=FakeMarket(), desk_fetch=desk_fetch)
        cls.bkdir = os.path.join(os.path.dirname(db), "bk")
        events.MIN_INTERVAL = 0
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, path, body=None, headers=None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                   data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw


class Smoke(Base):
    def test_page_and_script_are_served(self):
        c, body = self.req("/")
        self.assertEqual(c, 200)
        self.assertIn(b'src="/app.js"', body)
        c, js = self.req("/app.js")
        self.assertEqual(c, 200)
        self.assertIn(b"PAGES.pnl", js)

    def test_state_never_contains_the_token(self):
        c, st = self.req("/api/state")
        self.assertEqual(c, 200)
        tok, _ = env.find_token()
        if tok:
            self.assertNotIn(tok, json.dumps(st))
        self.assertIn("settings", st)
        self.assertEqual([m["id"] for m in st["settings"]["menu"]],
                         ["morning", "lookup", "watchlist", "desks", "trackrecord", "portfolio", "pnl", "guide", "changelog"])
        self.assertEqual(st["settings"]["menu"][0]["label"], "Today")
        self.assertEqual(st["settings"]["menu"][3]["children"], ["valuation", "flow", "confluence", "institutional", "swing", "growth"])

    def test_lookup_carries_single_leg_opening_flow(self):
        c, d = self.req("/api/lookup?t=ACME")
        of = d["opening_flow"]
        self.assertEqual(of["contracts"][0]["lean"], "bullish")
        self.assertEqual(of["contracts"][0]["reasons"], ["size > OI", "vol > OI"])
        with open(os.path.join(ROOT, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        for k in ("contract", "dte", "side", "ask_share", "premium", "size", "volume", "oi", "reasons", "lean", "sweep", "floor"):
            self.assertIn(k, of["contracts"][0])
            self.assertIn("c." + k, js)

    def test_events_route(self):
        c, d = self.req("/api/events?date=2026-10-14")
        self.assertEqual(c, 200)
        self.assertIsInstance(d["mine"], dict)                           # your names, for the earnings list
        self.assertEqual(len(d["calendar"]["rows"]), 6)
        self.assertEqual(len(d["earnings"]["rows"]), 4)
        c, _ = self.req("/api/events?date=nope")
        self.assertEqual(c, 400)

    def test_settings_roundtrip_and_sanitising(self):
        c, st = self.req("/api/state")
        s = st["settings"]
        s["menu"] = list(reversed(s["menu"]))[:-1]           # drop a built-in: it must come back
        s["menu"].insert(0, {"id": "link-x", "type": "link", "label": "TV", "url": "https://tradingview.com", "mode": "frame"})
        s["menu"].append({"id": "bad", "type": "link", "label": "js", "url": "javascript:alert(1)"})
        s["logo"] = "https://evil.example/x.png"               # only data: images are stored
        s["brand"] = "Demo Desk"
        c, out = self.req("/api/settings", s)
        self.assertEqual(c, 200)
        ids = [m["id"] for m in out["settings"]["menu"]]
        self.assertLess(ids.index("link-x"), ids.index("pnl"))          # the user's order is kept
        self.assertNotIn("bad", ids)
        self.assertEqual(sorted(i for i in ids if i != "link-x"),
                         sorted(["desks", "pnl", "portfolio", "morning", "lookup", "watchlist", "trackrecord", "guide", "changelog"]))
        self.assertIsNone(out["settings"]["logo"])
        c, st2 = self.req("/api/state")
        self.assertEqual(st2["settings"]["brand"], "Demo Desk")
        self.req("/api/settings/reset", {})

    def test_menu_saved_by_an_older_version_gains_new_pages_in_place(self):
        old = [{"id": "pnl", "type": "page", "label": "My P&L"},
               {"id": "events", "type": "page", "label": "Today's Events"},
               {"id": "desks", "type": "group", "label": "Desks", "children": ["flow", "valuation", "confluence", "institutional"]},
               {"id": "portfolio", "type": "page", "label": "Portfolio"}]
        server.STATE["store"].set("settings", dict(server.settings(), menu=old))
        c, st = self.req("/api/state")
        ids = [m["id"] for m in st["settings"]["menu"]]
        self.assertEqual(ids[0], "morning")                              # new page at its default spot
        self.assertEqual(next(m for m in st["settings"]["menu"] if m["id"] == "pnl")["label"], "My P&L")   # renamed item kept
        self.assertLess(ids.index("pnl"), ids.index("portfolio"))        # ... and the user's order too
        self.assertEqual(st["settings"]["menu"][[m["id"] for m in st["settings"]["menu"]].index("desks")]["children"][0], "flow")
        self.assertNotIn("events", ids)                                  # Today's Events retired into Today (r17)
        self.assertEqual(len(ids), 9)
        self.assertEqual(ids[-2:], ["guide", "changelog"])              # added at the end, next to Settings
        self.req("/api/settings/reset", {})

    def test_old_morning_and_events_menu_becomes_today(self):
        old = [{"id": "morning", "type": "page", "label": "Morning"},
               {"id": "events", "type": "page", "label": "Today's Events"},
               {"id": "pnl", "type": "page", "label": "P&L"}]
        server.STATE["store"].set("settings", dict(server.settings(), menu=old))
        c, st = self.req("/api/state")
        m = st["settings"]["menu"]
        self.assertEqual((m[0]["id"], m[0]["label"]), ("morning", "Today"))
        self.assertNotIn("events", [x["id"] for x in m])
        renamed = [{"id": "morning", "type": "page", "label": "Pre-market"}]
        server.STATE["store"].set("settings", dict(server.settings(), menu=renamed))
        c, st = self.req("/api/state")
        self.assertEqual(st["settings"]["menu"][0]["label"], "Pre-market")   # a name you chose is kept
        self.req("/api/settings/reset", {})

    def test_changelog_route(self):
        c, d = self.req("/api/changelog")
        self.assertEqual(c, 200)
        self.assertEqual(d["release"], server.RELEASE)
        self.assertEqual(d["releases"][0]["id"], server.RELEASE)
        c, st = self.req("/api/state")
        self.assertEqual(st["release"], server.RELEASE)

    def test_theme_routes(self):
        c, t = self.req("/api/theme")
        self.assertEqual((c, t["id"], t["css"]), (200, "paper", ""))              # default: nothing changes
        self.assertEqual(len(t["themes"]), 8)
        c, page = self.req("/")
        self.assertIn(b'<style id="themeCss"></style>', page)
        c, out = self.req("/api/settings", {"theme_id": "cyberpunk", "theme_motion": True})
        self.assertEqual(out["settings"]["theme_id"], "cyberpunk")
        c, out = self.req("/api/settings", {"theme_id": "../../evil", "theme": "purple"})
        self.assertEqual((out["settings"]["theme_id"], out["settings"]["theme"]), ("cyberpunk", "system"))  # refused
        c, page = self.req("/")
        self.assertIn(b'data-theme="dark"', page)                                 # dark-only: no flash
        self.assertIn(b"@keyframes uwgrid", page)
        c, js = self.req("/theme.js?desk=swing")
        self.assertEqual(c, 200)
        self.assertIn(b'"cyberpunk"', js)
        r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/theme/desk?desk=flow" % self.port,
                                   headers={"Origin": "http://127.0.0.1:8733"}), timeout=10)
        self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:8733")   # r21: not "*"
        self.assertIn("--ground:", json.loads(r.read())["css"])
        r = urllib.request.urlopen("http://127.0.0.1:%d/fonts/orbitron-latin-700-normal.woff2" % self.port, timeout=10)
        self.assertEqual((r.status, r.headers.get("Access-Control-Allow-Origin")), (200, "*"))
        c, _ = self.req("/fonts/..%2Fserver.py")
        self.assertEqual(c, 404)
        self.req("/api/settings/reset", {})

    def test_brand_names_every_desk(self):
        """r16: the desks call themselves "<brand> · <desk name in Settings>"; Unusual Whales stays the data credit."""
        c, b = self.req("/api/brand?desk=valuation")
        self.assertEqual((c, b["brand"], b["desk"]), (200, "Deep Current", "Valuation"))
        self.assertEqual(b["label"], "Deep Current \u00b7 Valuation")
        c, st = self.req("/api/state")
        desks = [dict(d, name="Possible <b>Swings</b>\x07") if d["id"] == "swing" else d for d in st["settings"]["desks"]]
        self.req("/api/settings", {"brand": "Tide\nPool", "desks": desks})
        c, b = self.req("/api/brand?desk=swing")
        self.assertEqual(b["label"], "TidePool \u00b7 Possible bSwings/b")           # no markup, no control chars
        c, js = self.req("/theme.js?desk=swing")
        self.assertIn("TidePool", js.decode())
        self.assertIn("data-uw-name", js.decode())
        c, page = self.req("/")
        self.assertIn(b"<title>TidePool</title>", page)
        c, b = self.req("/api/brand?desk=nosuch")
        self.assertEqual(b["label"], "TidePool")
        # a desk's brandname.py (sibling folder, if present) asks this server
        sib = os.path.join(ROOT, "..", "Valuation Desk", "brandname.py")
        if os.path.exists(sib):
            import importlib.util
            spec = importlib.util.spec_from_file_location("brandname_t", sib)
            bn = importlib.util.module_from_spec(spec); spec.loader.exec_module(bn)
            old = os.environ.get("UW_DASHBOARD_URL")
            os.environ["UW_DASHBOARD_URL"] = "http://127.0.0.1:%d" % self.port
            try:
                self.assertEqual(bn.label("swing", "Swing Desk"), "TidePool \u00b7 Possible bSwings/b")
                os.environ["UW_DASHBOARD_URL"] = "http://evil.example:80"   # only this PC is ever asked
                bn._CACHE.clear()
                self.assertEqual(bn.label("swing", "Swing Desk"), "Swing Desk")
            finally:
                if old is None: os.environ.pop("UW_DASHBOARD_URL", None)
                else: os.environ["UW_DASHBOARD_URL"] = old
        self.req("/api/settings/reset", {})

    def test_invest_trade_mode_and_growth_routes(self):
        c, out = self.req("/api/settings", {"mode": "trade"})
        self.assertEqual(out["settings"]["mode"], "trade")
        c, out = self.req("/api/settings", {"mode": "yolo"})
        self.assertEqual(out["settings"]["mode"], "trade")                  # unknown modes are refused
        c, _ = self.req("/api/growth?t=%3Cb%3E")
        self.assertEqual(c, 400)
        c, g = self.req("/api/growth?t=ACME")
        self.assertEqual(c, 200)
        self.assertFalse(g["found"])
        c, d = self.req("/api/direction")
        self.assertEqual(c, 200)
        self.assertIn("error", d)
        self.req("/api/settings/reset", {})

    def test_security_review_2026_09_26_attacks_are_refused(self):
        """Replays of the review's proofs: each one worked before the fix."""
        port = self.port
        # 1) another local port (a desk page) can't change settings; neither can a form post or text/plain
        for hdrs in ({"Origin": "http://127.0.0.1:8790"}, {"Origin": "http://localhost:8733"}, {"Origin": "null"},
                     {"Origin": "http://127.0.0.1:%d" % port, "Content-Type": "text/plain"},
                     {"Origin": "http://127.0.0.1:%d" % port, "Content-Type": "application/x-www-form-urlencoded"}):
            c, _ = self.req("/api/settings", {"brand": "pwned"}, headers=hdrs)
            self.assertEqual(c, 403, hdrs)
        c, _ = self.req("/api/settings", {"brand": "ok"}, headers={"Origin": "http://localhost:%d" % port})
        self.assertEqual(c, 200)                                   # the page itself still works
        # 2) a desk folder on a network share is refused (the supervisor would run its server.py)
        c, st = self.req("/api/state")
        desks_ = st["settings"]["desks"]
        before = desks_[0]["folder"]
        desks_[0]["folder"] = "\\\\attacker.example@SSL\\dav\\x"
        c, out = self.req("/api/settings", {"desks": desks_})
        self.assertEqual(out["settings"]["desks"][0]["folder"], before)
        import desks as desks_mod
        self.assertEqual(desks_mod.resolve("//attacker/share/x"), "")
        # 3) the sheet link can't read local files or reach this PC / the LAN
        for bad in ("file:///C:/Users/x/Valuation%20Desk/.env", "http://docs.google.com/x", "https://127.0.0.1:8790/api/health",
                    "javascript:alert(1)//https://docs.google.com/spreadsheets/d/e/X/pub"):
            c, out = self.req("/api/settings", {"portfolio_url": bad})
            self.assertNotEqual(out["settings"]["portfolio_url"], bad, bad)
        # 4) the backup folder can't be moved through settings, nor to a network share
        c, out = self.req("/api/settings", {"backup_dir": "C:/elsewhere"})
        self.assertNotEqual(out["settings"]["backup_dir"], "C:/elsewhere")
        c, out = self.req("/api/backup/folder", {"path": "\\\\attacker\\drop"})
        self.assertEqual(c, 400)
        # 5) another site's <img> can't make the dashboard spend UW calls
        c, _ = self.req("/api/lookup?t=ACME", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(c, 403)
        c, _ = self.req("/api/lookup?t=ACME", headers={"Sec-Fetch-Site": "same-site"})   # another localhost port
        self.assertEqual(c, 403)
        c, _ = self.req("/api/theme/desk?desk=swing", headers={"Sec-Fetch-Site": "same-site"})
        self.assertEqual(c, 200)                                   # the desks' theme hook stays open
        # r21 security check: the palette names the dashboard, so another website may not read it
        for u in ("/api/theme/desk?desk=swing", "/theme.js?desk=swing"):
            c, _ = self.req(u, headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"})
            self.assertEqual(c, 403, u)
            c, _ = self.req(u, headers={"Sec-Fetch-Site": "cross-site", "Referer": "http://localhost:8790/"})
            self.assertEqual(c, 200, u)                            # a desk opened as localhost still themes
        r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/theme/desk" % port,
                                   headers={"Origin": "https://evil.example"}), timeout=10)
        self.assertEqual(r.headers.get("Access-Control-Allow-Origin"), "null")
        # r21: a "ticker" of dots can't walk UW paths (/api/stock/../info)
        for bad in ("..", "-", ".A"):
            c, out = self.req("/api/lookup?t=" + bad)
            self.assertIn("error", out)
            self.assertNotIn("quote", out)
        # 6) no other site may frame the dashboard; bodies are capped, negative lengths refused
        r = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=10)
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", r.headers.get("Content-Security-Policy"))
        import http.client
        h = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h.putrequest("POST", "/api/settings"); h.putheader("Content-Type", "application/json")
        h.putheader("Content-Length", "-1"); h.endheaders()
        self.assertEqual(h.getresponse().status, 413)
        self.req("/api/settings/reset", {})

    def test_cross_site_post_is_refused(self):
        c, _ = self.req("/api/settings", {"brand": "x"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(c, 403)
        c, _ = self.req("/api/state", headers={"Host": "evil.example"})
        self.assertEqual(c, 403)

    def test_desk_controls(self):
        c, d = self.req("/api/desks/valuation/ensure", {})
        self.assertEqual(c, 200)
        self.assertIn(("ensure", "valuation"), self.sup.calls)
        c, d = self.req("/api/desks")
        self.assertEqual(d["desks"][0]["status"], "running")

    def test_pnl_upload_dedupe_and_delete(self):
        c, r = self.req("/api/pnl/upload", {"filename": "tos.csv", "text": sample("tos.csv"), "account": ""})
        self.assertEqual(c, 200, r)
        self.assertEqual(r["broker"], "thinkorswim")
        c, r2 = self.req("/api/pnl/upload", {"filename": "tos.csv", "text": sample("tos.csv")})
        self.assertEqual(r2["new"], 0)
        real_today = server._today
        try:
            # pinned to the statement's end date: the QQQ spread (26 SEP 26) is still open
            server._today = lambda: "2026-09-24"
            c, p = self.req("/api/pnl")
            self.assertEqual(p["stats"]["trades"], 3)
            self.assertEqual(len(p["unmatched"]), 1)
            self.assertEqual(p["accounts"], ["TOS 5678"])
            # after its expiry both legs close at 0 as "expired (inferred)"
            server._today = lambda: "2026-10-01"
            c, p = self.req("/api/pnl")
            self.assertEqual(p["stats"]["trades"], 5)
        finally:
            server._today = real_today
        for imp in p["imports"]:
            self.req("/api/pnl/import/%d/delete" % imp["id"], {})
        c, p = self.req("/api/pnl")
        self.assertEqual(p["fills"], 0)

    def test_journal_save_reload_list_search_and_delete(self):
        c, e = self.req("/api/journal?date=2026-09-01")
        self.assertEqual((c, e["body"], e["updated_at"]), (200, "", None))
        c, e = self.req("/api/journal", {"date": "2026-09-01", "body": "Bias long.\nWatch CPI at 7:30"})
        self.assertEqual(c, 200)
        self.assertTrue(e["updated_at"])
        self.req("/api/journal", {"date": "2026-09-02", "body": "Chopped around; stayed flat. 50% off_the_highs"})
        c, e = self.req("/api/journal?date=2026-09-01")
        self.assertEqual(e["body"], "Bias long.\nWatch CPI at 7:30")
        c, l = self.req("/api/journal/list")
        self.assertEqual([x["day"] for x in l["entries"]][:2], ["2026-09-02", "2026-09-01"])
        c, l = self.req("/api/journal/list?q=cpi")
        self.assertEqual([x["day"] for x in l["entries"]], ["2026-09-01"])
        c, l = self.req("/api/journal/list?q=50%25")           # % and _ are literal, not wildcards
        self.assertEqual([x["day"] for x in l["entries"]], ["2026-09-02"])
        c, l = self.req("/api/journal/list?q=off_the")
        self.assertEqual(len(l["entries"]), 1)
        c, l = self.req("/api/journal/list?q=f_t")
        self.assertEqual(len(l["entries"]), 1)
        c, l = self.req("/api/journal/list?q=o%25e")
        self.assertEqual(len(l["entries"]), 0)
        self.req("/api/journal", {"date": "2026-09-02", "body": "   "})   # emptied = removed
        c, l = self.req("/api/journal/list")
        self.assertNotIn("2026-09-02", [x["day"] for x in l["entries"]])
        c, _ = self.req("/api/journal", {"date": "Sept 1", "body": "x"})
        self.assertEqual(c, 400)
        c, _ = self.req("/api/journal", {"date": "2026-09-03", "body": "x"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(c, 403)
        self.req("/api/journal", {"date": "2026-09-01", "body": ""})

    def test_pnl_unknown_format_returns_columns_for_mapping(self):
        c, r = self.req("/api/pnl/upload", {"filename": "u.csv", "text": sample("unknown.csv")})
        self.assertEqual(c, 422)
        self.assertEqual(r["columns"], ["When", "What", "How many", "At"])

    def test_portfolio_route(self):
        c, st = self.req("/api/state")
        s = st["settings"]
        s["portfolio_url"] = ""
        self.req("/api/settings", s)
        c, d = self.req("/api/portfolio")
        self.assertIn("Settings", d["error"])                 # no link: says where to put one
        s["portfolio_url"] = "https://docs.google.com/spreadsheets/d/e/2PACX-x/pub?gid=0&single=true&output=csv"
        self.req("/api/settings", s)
        c, d = self.req("/api/portfolio")
        self.assertEqual(d["columns"], ["Ticker", "Shares", "Gain"])
        self.assertTrue(d["html"].startswith("https://docs.google.com/"))
        self.assertIn("model", d)
        self.assertFalse(d["model"]["ok"])                      # stub sheet has no Entry/Mark
        self.assertIn("entry", d["model"]["missing"])
        self.req("/api/settings/reset", {})


class Features(Base):
    """Routes added in deploy-r4, against the same synthetic stubs."""

    def use_widget_sheet(self):
        c, st = self.req("/api/state")
        s = st["settings"]
        s["portfolio_url"] = "https://docs.google.com/spreadsheets/d/e/2PACX-widget/pub?gid=0&single=true&output=csv"
        self.req("/api/settings", s)
        self.req("/api/backup/folder", {"path": self.bkdir})    # the only way in (settings can't set it)

    def test_watchlist_routes(self):
        self.use_widget_sheet()                                        # holdings ACME + ZENO
        c, r = self.req("/api/watchlist/add", {"ticker": "nova"})
        self.assertEqual((c, r["ticker"]), (200, "NOVA"))
        c, r = self.req("/api/watchlist/add", {"ticker": "../x"})
        self.assertEqual(c, 400)
        c, d = self.req("/api/watchlist")
        by = {i["ticker"]: i for i in d["items"]}
        self.assertEqual(sorted(by), ["ACME", "NOVA", "ZENO"])
        self.assertTrue(by["ACME"]["holding"] and not by["NOVA"]["holding"])
        self.assertEqual(by["ACME"]["buy"], 351.0)                    # 468 x 0.75
        self.assertEqual(by["ACME"]["zone"], "in")                     # stub price 50
        c, r = self.req("/api/watchlist/thesis", {"ticker": "ACME", "thesis": "cheap", "sell_target": "500"})
        self.assertEqual(c, 200)
        self.assertEqual((r["item"]["baseline"]["verdict"], r["item"]["baseline"]["quality"]), ("BUY", 7.2))
        c, it = self.req("/api/watchlist/item?t=ACME")
        self.assertEqual(it["thesis"], "cheap")
        self.assertTrue(any(e["kind"] == "thesis" for e in it["events"]))
        c, _ = self.req("/api/watchlist/item?t=%3Cimg%3E")
        self.assertEqual(c, 400)
        c, m = self.req("/api/morning")
        self.assertEqual(m["names"]["count"], 3)
        self.assertEqual(self.req("/api/watchlist/remove", {"ticker": "NOVA"})[0], 200)

    def test_size_uses_capital_from_settings_and_cash_from_the_sheet(self):
        self.use_widget_sheet()
        c, r = self.req("/api/size", {"entry": 50, "risk_pct": 1, "max_pos_pct": 20, "stop_pct": 5, "side": "long"})
        self.assertEqual(c, 200)
        self.assertEqual(r["shares"], 400)
        self.assertEqual(r["capital"], 100000)
        c, r = self.req("/api/size", {"entry": 50, "risk_pct": 1, "max_pos_pct": 99, "stop_pct": 0.5})
        self.assertTrue(r.get("cash_short"))                           # $99k position vs ~$86k cash
        c, r = self.req("/api/size", {"entry": 50, "risk_pct": 2, "max_pos_pct": 15, "stop_pct": 8, "side": "short",
                                      "remember": True})
        c, st = self.req("/api/state")
        self.assertEqual((st["settings"]["sizer_risk_pct"], st["settings"]["sizer_side"]), (2.0, "short"))

    def test_lookup_joins_desks_position_and_warnings(self):
        self.use_widget_sheet()
        c, d = self.req("/api/lookup?t=acme")
        self.assertEqual(d["ticker"], "ACME")
        self.assertEqual(d["quote"]["price"], 50.0)
        self.assertEqual(d["desks"]["valuation"]["run"]["verdict"], "BUY")
        self.assertEqual(d["desks"]["confluence"]["error"], "not running")
        self.assertEqual(d["position"]["amt"], 30)
        self.assertEqual([w["kind"] for w in d["holding"]["warnings"]], ["insider"])
        c, d = self.req("/api/lookup?t=NOPE")
        self.assertIn("No price", d["quote"]["error"])

    def test_warnings_cover_every_holding(self):
        self.use_widget_sheet()
        c, w = self.req("/api/warnings")
        self.assertEqual(sorted(h["ticker"] for h in w["holdings"]), ["ACME", "ZENO"])
        self.assertIn("insider_usd", w["thresholds"])

    def test_morning_market_track_and_health(self):
        self.use_widget_sheet()
        for path in ("/api/morning", "/api/market", "/api/trackrecord", "/api/healthz"):
            c, d = self.req(path)
            self.assertEqual(c, 200, path)
        c, d = self.req("/api/morning")
        self.assertEqual(d["picks"]["valuation"]["error"], None)
        c, h = self.req("/api/healthz")
        val = [x for x in h["desks"] if x["id"] == "valuation"][0]
        self.assertTrue(val["health"]["ok"])

    def test_backup_now_writes_a_checked_copy(self):
        self.use_widget_sheet()
        self.req("/api/journal", {"date": "2026-09-20", "body": "backed up"})
        c, r = self.req("/api/backup", {})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["where"], "custom folder")
        self.assertTrue(os.path.isfile(r["file"]))

    def test_backup_folder_route_validates_copies_and_resets(self):
        self.use_widget_sheet()
        self.req("/api/backup", {})
        c, r = self.req("/api/backup/folder", {"path": "not/absolute"})
        self.assertEqual(c, 400)
        self.assertIn("full path", r["error"])
        new = os.path.join(tempfile.mkdtemp(), "moved here")
        c, r = self.req("/api/backup/folder", {"path": new, "copy_existing": True})
        self.assertEqual(c, 200, r)
        self.assertEqual(r["settings"]["backup_dir"], os.path.normpath(new))
        self.assertGreaterEqual(r["copied"], 1)
        self.assertEqual(r["backup"]["dest"], os.path.normpath(new))
        c, r = self.req("/api/backup", {})
        self.assertTrue(r["file"].startswith(os.path.normpath(new)))
        c, r = self.req("/api/backup/folder", {"path": "", "copy_existing": False})
        self.assertEqual(r["settings"]["backup_dir"], "")
        self.assertNotEqual(r["backup"]["where"], "custom folder")
        self.use_widget_sheet()

    def test_backup_picker_and_open_are_stubbable(self):
        saved = dict(server.PICKER)
        opened = []
        server.PICKER.update(pick=lambda start: "", open=lambda p: opened.append(p))
        try:
            c, r = self.req("/api/backup/pick", {})
            self.assertTrue(r["cancelled"])
            server.PICKER["pick"] = lambda start: "/somewhere/picked"
            c, r = self.req("/api/backup/pick", {})
            self.assertEqual(r["path"], "/somewhere/picked")
            def boom(start):
                raise server.backup_mod.PickerUnavailable("no picker")
            server.PICKER["pick"] = boom
            c, r = self.req("/api/backup/pick", {})
            self.assertEqual(r["error"], "no picker")
            self.use_widget_sheet()
            c, r = self.req("/api/backup/open", {})
            self.assertEqual(opened, [self.bkdir])
        finally:
            server.PICKER.update(saved)

    def test_backup_keep_setting_round_trips_clamped(self):
        c, st = self.req("/api/state")
        s = st["settings"]
        s["backup_keep"] = 1000
        c, r = self.req("/api/settings", s)
        self.assertEqual(r["settings"]["backup_keep"], 365)
        s["backup_keep"] = 30
        self.req("/api/settings", s)


class Shipped(unittest.TestCase):
    def test_batch_files_are_crlf(self):
        for name in ("START_HERE.bat", "INSTALL_AUTOSTART.bat", "REMOVE_AUTOSTART.bat", "RUN_TESTS.bat", "MAKE_SHARE_ZIP.bat"):
            with open(os.path.join(ROOT, name), "rb") as fh:
                data = fh.read()
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), name)

    def test_shipped_env_example_parses(self):
        if not os.path.isfile(os.path.join(ROOT, ".env.example")):
            self.skipTest(".env.example not in this copy (GitHub's web upload leaves out dot-files)")
        e = env.parse_env_file(os.path.join(ROOT, ".env.example"))
        self.assertEqual(int(e["DASH_PORT"]), 8700)

    def test_default_ports_do_not_collide_with_desks(self):
        ports = {d["port"] for d in env.DEFAULT_DESKS if d["port"]}
        self.assertNotIn(env.PORT, ports)

    def test_every_page_the_menu_can_route_to_exists(self):
        with open(os.path.join(ROOT, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        for page in ("morning", "events", "desk", "link", "portfolio", "pnl", "settings"):
            self.assertIn("PAGES.%s = " % page, js)


class Upgrade(unittest.TestCase):
    """Settings saved by an older version that knew fewer desks."""

    def setUp(self):
        self.saved = server.STATE.get("store")
        self.addCleanup(lambda: server.STATE.__setitem__("store", self.saved))
        server.STATE["store"] = store.Store(os.path.join(tempfile.mkdtemp(), "u.db"))
        old = server.default_settings()
        old["desks"] = [d for d in old["desks"] if d["id"] != "swing"] + [
            {"id": "gex", "name": "GEX / ES", "folder": "GEX ES Desk", "port": 8848, "autostart": True}]
        for m in old["menu"]:
            if m["id"] == "desks":
                m["children"] = ["valuation", "confluence", "gex"]   # user dropped two desks; gex since retired
        server.STATE["store"].set("settings", old)

    def group(self, s):
        return [m for m in s["menu"] if m["id"] == "desks"][0]["children"]

    def test_new_desks_join_the_menu_but_removed_ones_stay_removed(self):
        kids = self.group(server.settings())
        self.assertEqual(kids, ["valuation", "confluence", "swing"])
        self.assertNotIn("gex", [d["id"] for d in server.settings()["desks"]])   # retired desks are dropped

    def test_after_saving_a_removed_new_desk_stays_removed(self):
        s = server.settings()
        for m in s["menu"]:
            if m["id"] == "desks":
                m["children"] = ["valuation", "swing"]
        server.STATE["store"].set("settings", s)
        self.assertEqual(self.group(server.settings()), ["valuation", "swing"])


class MovedDesk(unittest.TestCase):
    """The Flow Desk moved from D:\\UW-Desks to a folder beside the dashboard."""

    def setUp(self):
        self.saved = (server.STATE.get("store"), env.PARENT)
        self.addCleanup(self.restore)
        server.STATE["store"] = store.Store(os.path.join(tempfile.mkdtemp(), "m.db"))
        env.PARENT = tempfile.mkdtemp()
        s = server.default_settings()
        for d in s["desks"]:
            if d["id"] == "flow":
                d["folder"] = r"D:\UW-Desks\UW Flow Desk"
        server.STATE["store"].set("settings", s)

    def restore(self):
        server.STATE["store"], env.PARENT = self.saved

    def flow_folder(self):
        return [d for d in server.settings()["desks"] if d["id"] == "flow"][0]["folder"]

    def test_old_default_stays_until_the_new_folder_has_the_desk(self):
        self.assertEqual(self.flow_folder(), r"D:\UW-Desks\UW Flow Desk")
        os.makedirs(os.path.join(env.PARENT, "UW Flow Desk"))
        self.assertEqual(self.flow_folder(), r"D:\UW-Desks\UW Flow Desk")     # empty folder: not yet
        open(os.path.join(env.PARENT, "UW Flow Desk", "server.py"), "w").close()
        self.assertEqual(self.flow_folder(), "UW Flow Desk")

    def test_a_folder_the_user_chose_is_never_moved(self):
        s = server.settings()
        for d in s["desks"]:
            if d["id"] == "flow":
                d["folder"] = r"E:\Mine\Flow"
        server.STATE["store"].set("settings", s)
        os.makedirs(os.path.join(env.PARENT, "UW Flow Desk"))
        open(os.path.join(env.PARENT, "UW Flow Desk", "server.py"), "w").close()
        self.assertEqual(self.flow_folder(), r"E:\Mine\Flow")


class TokenSources(unittest.TestCase):
    def test_uw_token_key_and_placeholders(self):
        d = tempfile.mkdtemp()
        dash = os.path.join(d, "UW Dashboard")
        os.makedirs(dash)
        gex = os.path.join(d, "UW Flow Desk")
        os.makedirs(gex)
        with open(os.path.join(gex, ".env"), "w") as fh:
            fh.write("UW_TOKEN=paste_your_unusual_whales_api_token_here\n")
        saved = (env.HERE, env.ENV)
        env.HERE, env.ENV = dash, {}
        try:
            self.assertEqual(env.find_token(), ("", None))
            with open(os.path.join(gex, ".env"), "w") as fh:
                fh.write("UW_TOKEN=abc123\n")
            self.assertEqual(env.find_token()[0], "abc123")
        finally:
            env.HERE, env.ENV = saved


if __name__ == "__main__":
    unittest.main()
