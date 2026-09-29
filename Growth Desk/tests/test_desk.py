"""Growth Leaders desk: the scan pipeline, resume, alerts, Discord gating, the HTTP API and the page's reads."""
import datetime as dt
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp()
os.environ["GDESK_DB"] = os.path.join(TMP, "g.db")
os.environ.setdefault("UW_API_TOKEN", "test-token-not-real")

import calendar_et as cal   # noqa: E402
import fixture as F         # noqa: E402
import model as M           # noqa: E402
import scan as S            # noqa: E402
import store as ST          # noqa: E402

SESSION = dt.date(2026, 9, 25)


class Clock:
    """Pin the calendar: after the close on 2026-09-25, market closed."""
    def __enter__(self):
        self.saved = (cal.last_session, cal.market_open)
        cal.last_session = lambda et=None: SESSION
        cal.market_open = lambda et=None, start_min=0: False
        return self

    def __exit__(self, *a):
        cal.last_session, cal.market_open = self.saved


class FakeNotifier:
    enabled = True

    def __init__(self):
        self.posts = []

    def breakout(self, card, market):
        self.posts.append(card["ticker"])
        return True

    def test(self):
        return True


def fresh(n=300):
    path = os.path.join(tempfile.mkdtemp(dir=TMP), "g.db")
    fc = F.FakeClient(n)
    st = ST.Store(path)
    nt = FakeNotifier()
    return fc, st, nt, S.Scanner(fc, st, log=lambda *a: None, notifier=nt)


class Pipeline(unittest.TestCase):
    def test_run_scores_every_candidate(self):
        with Clock():
            fc, st, nt, sc = fresh()
            r = sc.run()
        self.assertNotIn("error", r)
        last = st.last_run()
        self.assertEqual(last["session"], "2026-09-25")
        cards = st.cards(r["run"])
        rs = M.rs_ratings(fc.rows)
        expected = [x["ticker"] for x in fc.rows if rs[x["ticker"]]["rs"] >= S.CANDIDATE_RS
                    and float(x["close"]) >= S.CANDIDATE_PRICE and float(x["close"]) / float(x["week_52_high"]) - 1 >= -0.25]
        self.assertEqual(sorted(c["ticker"] for c in cards), sorted(expected))      # every candidate, no top-N slice
        self.assertEqual(last["market"]["state"], "uptrend")

    def test_cache_saves_calls_on_the_second_run(self):
        with Clock():
            fc, st, nt, sc = fresh()
            sc.run()
            first = fc.calls
            sc.run(force=True)
        self.assertLess(fc.calls - first, first * 0.5)        # earnings, 13F and today's bars all cached

    def test_one_bad_ticker_never_stops_the_run(self):
        with Clock():
            fc, st, nt, sc = fresh()
            rs = M.rs_ratings(fc.rows)
            bad = next(t for t, v in rs.items() if v["rs"] >= 90)
            fc.fail.add(bad)
            r = sc.run()
        self.assertNotIn("error", r)
        self.assertIn(bad, st.last_run()["note"])

    def test_interrupted_run_resumes(self):
        with Clock():
            fc, st, nt, sc = fresh()
            real = sc.card
            calls = {"n": 0}

            def dies(*a, **k):
                calls["n"] += 1
                if calls["n"] > 10:
                    raise KeyboardInterrupt
                return real(*a, **k)
            sc.card = dies
            with self.assertRaises(KeyboardInterrupt):
                sc.run()
            st2 = ST.Store(st.path)                           # the desk restarts
            self.assertIsNotNone(st2.interrupted("2026-09-25"))
            rid = st2.interrupted("2026-09-25")["id"]
            kept = len(st2.done_tickers(rid))
            self.assertGreater(kept, 0)
            sc2 = S.Scanner(fc, st2, log=lambda *a: None, notifier=nt)
            pages = []
            real_page = fc.screener_page
            fc.screener_page = lambda *a, **k: pages.append(1) or real_page(*a, **k)
            r = sc2.run()
        self.assertNotIn("error", r)
        self.assertEqual(r["run"], rid)                       # the same run finished
        self.assertEqual(pages, [])                           # the universe was not fetched again

    def test_breakout_alerts_once_and_discord_only_for_leaders_in_an_uptrend(self):
        with Clock():
            fc, st, nt, sc = fresh()
            sc.run()
            alerts = st.alerts(500)
            self.assertTrue(alerts)
            self.assertEqual(sorted(nt.posts), sorted(a["ticker"] for a in alerts if a["leader"]))
            n = len(alerts)
            sc.run(force=True)
            self.assertEqual(len(st.alerts(500)), n)          # same pivot: no second alert
        with Clock():
            fc, st, nt, sc = fresh()
            fc.bars["SPY"] = F.index_series("correction")
            fc.bars["QQQ"] = F.index_series("correction")
            sc.run()
            self.assertTrue(st.alerts(500))                   # still shown on the dashboard...
            self.assertEqual(nt.posts, [])                    # ... but nothing posted in a correction

    def test_intraday_watch_uses_live_price(self):
        with Clock():
            fc, st, nt, sc = fresh()
            sc.run()
            n = sc.watch_once()
        self.assertGreater(n, 0)
        self.assertTrue(all("state" in v for v in sc.watch.values()))

    def test_any_ticker_is_ranked_against_the_last_universe(self):
        with Clock():
            fc, st, nt, sc = fresh()
            sc.run()
            outsider = F.screener_row("NEWCO", 0, __import__("random").Random(1), "Semis", 0.5, 0.8, 1.2, 50, 52)
            fc.rows.append(outsider)
            fc.eps["NEWCO"] = F.earnings(F.growth_eps(1, 0.6))
            fc.own["NEWCO"] = F.ownership()
            fc.bars["NEWCO"] = F.base_then("near")
            sc.ctx["rs"].pop("NEWCO", None)
            card = sc.ticker("NEWCO")
        self.assertGreaterEqual(card["rs"], 90)
        self.assertFalse(card["in_universe"])


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        with Clock():
            server.STATE["store"] = ST.Store(os.path.join(tempfile.mkdtemp(dir=TMP), "h.db"))
            server.STATE["notifier"] = FakeNotifier()
            server.STATE["scanner"] = S.Scanner(F.FakeClient(250), server.STATE["store"], log=lambda *a: None,
                                                notifier=server.STATE["notifier"])
            server.STATE["scanner"].run()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Host": "127.0.0.1:%d" % self.port}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        raw = r.read()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def test_board_shape_for_the_dashboard(self):
        c, b = self.req("GET", "/api/board")
        self.assertEqual(c, 200)
        self.assertFalse(b["pending"])
        for k in ("market", "cards", "counts", "run", "alerts", "method", "observed_max"):
            self.assertIn(k, b)
        card = b["cards"][0]
        for k in ("ticker", "score", "leader", "six_pass", "checks", "base", "rs", "carried_by"):
            self.assertIn(k, card)

    def test_ticker_route_and_validation(self):
        c, b = self.req("GET", "/api/board")
        t = b["cards"][0]["ticker"]
        c, card = self.req("GET", "/api/ticker?t=" + t.lower())
        self.assertEqual((c, card["ticker"]), (200, t))
        self.assertIn("history", card)
        c, _ = self.req("GET", "/api/ticker?t=%3Cscript%3E")
        self.assertEqual(c, 400)

    def test_health_and_method(self):
        c, h = self.req("GET", "/api/health")
        self.assertEqual(c, 200)
        self.assertIn("api_calls_total", h)
        c, m = self.req("GET", "/api/method")
        self.assertEqual(sum(m["weights"].values()), 100)

    def test_guard(self):
        self.assertEqual(self.req("GET", "/api/board", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req("GET", "/api/board", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.req("POST", "/api/scan", "{}", {"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.req("POST", "/api/scan", "{}", {"Content-Type": "application/json",
                                                             "Origin": "http://127.0.0.1:8700"})[0], 403)
        c, raw = self.req("GET", "/")
        self.assertEqual(c, 200)

    def test_page_reads_only_keys_the_payload_has(self):
        """Every c.<key> the page reads exists on a real card (the Flow Desk once read a key that was never sent)."""
        page = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        c, b = self.req("GET", "/api/board")
        card = b["cards"][0]
        reads = set(re.findall(r"\bc\.([a-z_]+)\b", page))
        missing = [k for k in reads if k not in card and k not in ("live", "history")]
        self.assertEqual(missing, [])
        base_reads = set(re.findall(r"\bb\.([a-z_]+)\b", page)) - {"onclick", "dataset", "classList"}
        full = next(x["base"] for x in b["cards"] if x["base"].get("pivot"))
        self.assertEqual([k for k in base_reads if k not in full], [])
        self.assertIn('theme.js?desk=growth', page)
        self.assertIn("data-uw-name", page)
        self.assertIn("Data: Unusual Whales", page)


class Files(unittest.TestCase):
    def test_batch_files_are_crlf(self):
        for name in ("START_HERE.bat", "RUN_TESTS.bat", "DIAG.bat"):
            data = open(os.path.join(ROOT, name), "rb").read()
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), name)

    def test_dashboard_can_find_the_port_and_browser_switch(self):
        src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
        self.assertRegex(src, r"""["'](\w*PORT)["']\s*,\s*["']?(\d{4,5})""")
        self.assertRegex(src, r"""["'](\w*OPEN_BROWSER)["']""")

    def test_shipped_env_example_parses(self):
        import uw
        if not os.path.isfile(os.path.join(ROOT, ".env.example")):
            self.skipTest(".env.example not in this copy (GitHub's web upload leaves out dot-files)")
        env = uw._parse_env_file(os.path.join(ROOT, ".env.example"))
        self.assertEqual(env.get("GDESK_PORT"), "8760")

    def test_calendar(self):
        self.assertEqual(cal.last_session(dt.datetime(2026, 9, 28, 10, 0)).isoformat(), "2026-09-25")   # Mon morning
        self.assertEqual(cal.last_session(dt.datetime(2026, 9, 28, 16, 30)).isoformat(), "2026-09-28")
        self.assertEqual(cal.last_session(dt.datetime(2026, 9, 8, 9, 0)).isoformat(), "2026-09-04")     # Labor Day
        self.assertTrue(cal.market_open(dt.datetime(2026, 9, 28, 10, 0)))
        self.assertFalse(cal.market_open(dt.datetime(2026, 9, 28, 9, 40)))


if __name__ == "__main__":
    unittest.main()
