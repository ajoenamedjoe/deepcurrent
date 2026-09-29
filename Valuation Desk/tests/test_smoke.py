"""
End-to-end smoke test: the actual server, a stub API returning synthetic payloads
in the UW endpoint shapes, every HTTP route, the generated script EXECUTED, the docx
opened back up, a PNG card POSTed and written, and a failing API.

Run:  python -m unittest discover -s tests -v
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import desk          # noqa: E402
import server        # noqa: E402
import store as store_mod  # noqa: E402
import uw            # noqa: E402
import sample_qrtx_acme as A   # noqa: E402
import sample_volt_blmp_zeno as B   # noqa: E402
import sample_mgmt as M   # noqa: E402
import screen as screen_mod   # noqa: E402

# REST returns annual + quarterly in ONE payload per statement. Rebuild that shape.
def merged(*payloads):
    rows = []
    for p in payloads:
        rows += p["result"] if p else []
    return {"data": rows}


REST = {
    "QRTX": {"info": A.QRTX_INFO, "is": merged(A.QRTX_IS_A, A.QRTX_IS_Q), "bs": merged(A.QRTX_BS_Q),
             "cf": merged(A.QRTX_CF_Q)},
    "ACME": {"info": A.ACME_INFO, "is": merged(A.ACME_IS_A), "bs": merged(A.ACME_BS_A), "cf": merged(A.ACME_CF_A)},
    "VOLT": {"info": B.VOLT_INFO, "is": merged(B.VOLT_IS_A), "bs": merged(B.VOLT_BS_A), "cf": merged(B.VOLT_CF_A)},
    "BLMP": {"info": B.BLMP_INFO, "is": merged(B.BLMP_IS_A), "bs": {"data": []}, "cf": merged(B.BLMP_CF_A)},
    "ZENO": {"info": B.ZENO_INFO, "is": merged(B.ZENO_IS_A), "bs": {"data": []}, "cf": {"data": []}},
}


class StubClient:
    """Answers like uw.Client, from fixtures. `price_on_info=False` forces the fallback."""
    def __init__(self, price_on_info=True, fail=False):
        self.calls = 0
        self.price_on_info = price_on_info
        self.fail = fail

    def _t(self, t):
        self.calls += 1
        if self.fail:
            raise RuntimeError("GET failed: HTTP 503")
        return REST.get(t.upper())

    def info(self, t):
        d = self._t(t)
        if not d:
            return {}
        info = json.loads(json.dumps(d["info"]))
        if not self.price_on_info:
            info.pop("price", None)
        return info

    def income_statements(self, t):
        return self._t(t)["is"]

    def balance_sheets(self, t):
        return self._t(t)["bs"]

    def cash_flows(self, t):
        return self._t(t)["cf"]

    def screener_row(self, t):
        self.calls += 1
        return {"ticker": t, "close": REST[t]["info"]["price"]}

    def ohlc_daily(self, t, timeframe="1M"):
        self.calls += 1
        return []

    # model 3.2 endpoints -- ACME has synthetic insider/earnings payloads, the rest answer empty
    def insider_trades(self, t, start_date):
        self.calls += 1
        return M.ACME_INSIDERS if t.upper() == "ACME" else {"data": []}

    def earnings(self, t):
        self.calls += 1
        return M.ACME_EARNINGS if t.upper() == "ACME" else {"data": []}

    def get(self, path, params=None):
        self.calls += 1
        if path == "/api/etfs/TEST/holdings":
            return {"data": [{"ticker": "ACME", "type": "equity"}, {"ticker": "USD", "type": "cash"},
                             {"ticker": "VOLT"}]}
        return {}


PNG_1PX = ("data:image/png;base64," + base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode())


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        desk.REPORTS = os.path.join(cls.tmp, "reports")
        server.STATE["store"] = store_mod.Store(os.path.join(cls.tmp, "t.db"))
        server.STATE["client"] = StubClient()
        server.STATE["screener"] = screen_mod.Screener(server.STATE["store"], server.client, uw.fetch_all,
                                                       lambda: {"TWD": 32.0}, log=lambda m: None)
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.fx_orig = uw.fx_table
        uw.fx_table = lambda: {"TWD": 32.0}

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        uw.fx_table = cls.fx_orig
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, body=None, method=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                return resp.status, (json.loads(raw) if "json" in ctype else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def _wait_screen(self, sid, timeout=60):
        import time
        t0 = time.time()
        while time.time() - t0 < timeout:
            code, out = self.req("/api/screen/%d" % sid)
            if out["screen"]["status"] != "running":
                return out
            time.sleep(0.2)
        self.fail("screen did not finish")

    def test_screen_ranks_every_ticker_and_explains_failures(self):
        code, out = self.req("/api/screen", {"source": "list", "tickers": "acme, QRTX blmp $volt NOPE zeno"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["total"], 6)
        res = self._wait_screen(out["screen_id"])
        rows = {r["ticker"]: r for r in res["rows"]}
        self.assertEqual(set(rows), {"ACME", "QRTX", "BLMP", "VOLT", "NOPE", "ZENO"})
        self.assertEqual(rows["NOPE"]["status"], "skipped")
        self.assertTrue(rows["NOPE"]["error"])                   # a sentence, never a silent drop
        self.assertEqual(rows["ACME"]["status"], "ok")
        self.assertIsNotNone(rows["ACME"]["quality"])
        self.assertIn(rows["ACME"]["quadrant"], ("cheap_good", "value_trap", "pricey_good", "avoid", "unknown"))
        ok = [r for r in res["rows"] if r["status"] == "ok"]
        self.assertEqual(res["rows"][:len(ok)], ok)             # failures sort last
        code, lst = self.req("/api/screens")
        self.assertTrue(any(s["id"] == out["screen_id"] and s["done"] == 6 for s in lst["screens"]))

    def test_screen_resume_skips_done_rows(self):
        sc = server.STATE["screener"]
        code, out = self.req("/api/screen", {"source": "list", "tickers": "ACME VOLT"})
        self._wait_screen(out["screen_id"])
        before = server.STATE["client"].calls
        code, r = self.req("/api/screen/%d/resume" % out["screen_id"], {})
        self.assertEqual(code, 200, r)
        self._wait_screen(out["screen_id"])
        self.assertEqual(server.STATE["client"].calls, before)   # nothing left to fetch
        self.assertEqual(len(sc.rows(out["screen_id"])), 2)

    def test_screen_from_etf_holdings_drops_cash_rows(self):
        code, out = self.req("/api/screen", {"source": "etf", "etf": "test"})
        self.assertEqual(code, 200, out)
        self.assertEqual(out["total"], 2)
        self._wait_screen(out["screen_id"])

    def test_method_panel_reads_the_loaded_model(self):
        import valuation as V
        code, m = self.req("/api/method")
        self.assertEqual(code, 200)
        self.assertEqual(m["model_version"], V.MODEL_VERSION)
        self.assertEqual(m["margin_of_safety"], V.MARGIN_OF_SAFETY)
        self.assertEqual(sum(w for _l, w in m["quality"]), 100)
        self.assertEqual(len(m["discounts"]), len(V.CLASSES))
        code, page = self.req("/")
        # the explainer moved to the dashboard's How it works page (2026-09-26)
        self.assertNotIn(b'id="howPanel"', page)
        self.assertIn(b'id="cardMode"', page)

    def test_full_run_every_route_and_every_file(self):
        for t in ("QRTX", "ACME", "VOLT", "BLMP", "ZENO"):
            code, out = self.req("/api/run", {"ticker": t.lower()})
            self.assertEqual(code, 200, (t, out))
            rid = out["run_id"]
            for k in ("py", "docx", "console", "data"):
                self.assertIn(k, out["files"], (t, k))
            self.assertIn("VERDICT:", out["console"])
            # every key the canvas reads survives into the card payload
            for key in ("ticker", "badge", "verdict", "colour", "stance", "thesis", "metrics", "scenarios",
                        "bullets", "valuation_points", "flag_points", "explain", "summary", "basis",
                        "price_num", "marketcap", "name", "sector", "class_label", "overrides", "model_version",
                        "quality", "returns", "gates", "why", "synopsis"):
                self.assertIn(key, out["card"], (t, key))
            self.assertEqual(len(out["card"]["metrics"]), 8)
            self.assertEqual(len(out["card"]["scenarios"]), 3)
            self.assertLessEqual(len(out["card"]["bullets"]), 4)
            # card save
            code, sv = self.req("/api/card/%d" % rid, {"png": PNG_1PX})
            self.assertEqual(code, 200, sv)
            self.assertTrue(os.path.isfile(sv["path"]))
            self.assertTrue(sv["path"].endswith("%s_Summary_Card.png" % t))
            # stored run re-opens with the png listed
            code, got = self.req("/api/run/%d" % rid)
            self.assertEqual(code, 200)
            self.assertIn("png", got["files"])
            self.assertNotIn("short", got["files"])
            # the summary card saves beside it and is listed and served
            code, sv = self.req("/api/card/%d" % rid, {"png": PNG_1PX, "kind": "short"})
            self.assertEqual(code, 200, sv)
            self.assertTrue(sv["path"].endswith("%s_Short_Card.png" % t))
            code, got = self.req("/api/run/%d" % rid)
            self.assertEqual(got["files"]["short"], "%s_Short_Card.png" % t)
            code, raw = self.req("/api/file/%d/short" % rid)
            self.assertEqual(code, 200)
            # docx is a real package with the 11 sections
            code, raw = self.req("/api/file/%d/docx" % rid)
            self.assertEqual(code, 200)
            path = os.path.join(out["out_dir"], "%s_Valuation_Analysis.docx" % t)
            with zipfile.ZipFile(path) as z:
                doc = z.read("word/document.xml").decode()
            for n in range(1, 12):
                self.assertIn("%d. " % n, doc, (t, n))
            self.assertNotIn("&lt;w:r&gt;", doc, "XML escaped twice -- the first-draft bug")
        code, h = self.req("/api/history?ticker=ACME")
        self.assertEqual(code, 200)
        self.assertTrue(h["runs"])
        code, tk = self.req("/api/tickers")
        self.assertEqual({x["ticker"] for x in tk["tickers"]} >= {"QRTX", "ACME", "VOLT", "BLMP", "ZENO"}, True)
        code, hl = self.req("/api/health")
        self.assertEqual(code, 200)
        self.assertIn("build", hl)
        code, page = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Valuation Desk", page)

    def test_runs_saved_by_older_models_still_open(self):
        # when model 3.0 shipped, the desk's opening page re-opened a
        # 2.0 run with no "capital" block -> KeyError: 'capital'
        import generate as G
        code, out = self.req("/api/run", {"ticker": "ACME"})
        self.assertEqual(code, 200, out)
        old = json.loads(json.dumps(out["result"]))
        old["valuation"].pop("capital")
        old["valuation"].pop("years")
        c = G.card(old)
        self.assertIn("5-year projection", c["explain"])
        self.assertIn("10-year projection", G.card(out["result"])["explain"])
        st = server.STATE["store"]
        with st._conn() as cx:
            cx.execute("UPDATE runs SET result_json = ? WHERE id = ?", (json.dumps(old), out["run_id"]))
        code, got = self.req("/api/run/%d" % out["run_id"])
        self.assertEqual(code, 200, got)

    def test_overrides_arrive_as_percent_and_are_recorded(self):
        code, out = self.req("/api/run", {"ticker": "ACME", "discount": "9", "growth": ""})
        self.assertEqual(code, 200, out)
        self.assertAlmostEqual(out["result"]["overrides"]["discount"], 0.09)
        self.assertIn("OVERRIDES", out["console"])

    def test_bad_inputs_are_sentences(self):
        code, out = self.req("/api/run", {"ticker": "not a ticker!"})
        self.assertEqual(code, 400)
        self.assertIn("not a ticker", out["error"])
        code, out = self.req("/api/run", {"ticker": "ZZZZ"})
        self.assertEqual(code, 400)
        self.assertIn("no ticker called ZZZZ", out["error"])
        code, out = self.req("/api/card/999999", {"png": PNG_1PX})
        self.assertEqual(code, 400)
        code, out = self.req("/api/card/1", {"png": "data:image/png;base64,AAAA"})
        self.assertEqual(code, 400)

    def test_price_fallback_when_info_has_none(self):
        old = server.STATE["client"]
        server.STATE["client"] = StubClient(price_on_info=False)
        try:
            code, out = self.req("/api/run", {"ticker": "VOLT"})
            self.assertEqual(code, 200, out)
            self.assertTrue(any("screener" in n for n in out["result"]["fin"]["notes"]))
        finally:
            server.STATE["client"] = old

    def test_api_failure_is_reported_not_crashed(self):
        old = server.STATE["client"]
        server.STATE["client"] = StubClient(fail=True)
        try:
            code, out = self.req("/api/run", {"ticker": "ACME"})
            self.assertEqual(code, 502)
            self.assertIn("503", out["error"])
        finally:
            server.STATE["client"] = old

    def test_non_usd_without_fx_refuses_with_instructions(self):
        uw.fx_table = lambda: {}
        try:
            code, out = self.req("/api/run", {"ticker": "ZENO"})
            self.assertEqual(code, 400)
            self.assertIn("VDESK_FX_TWD", out["error"])
        finally:
            uw.fx_table = lambda: {"TWD": 32.0}


class Config(unittest.TestCase):
    def test_the_shipped_env_example_parses(self):
        # A user may rename .env.example -> .env (reasonable!). Test whichever exists;
        # the token may legitimately live in a sibling desk instead.
        path = os.path.join(ROOT, ".env.example")
        if not os.path.exists(path):
            path = os.path.join(ROOT, ".env")
        if not os.path.exists(path):
            self.skipTest("no .env.example or .env in this folder")
        env = uw._parse_env_file(path)
        self.assertTrue(all(k.startswith(("UW_", "VDESK_")) for k in env))
        if os.path.basename(path) != ".env.example":
            return
        self.assertIn("UW_API_TOKEN", env)
        self.assertEqual(uw.num(env.get("VDESK_PORT")), 8790)
        self.assertTrue(all(k.startswith(("UW_", "VDESK_")) for k in env))

    def test_start_here_is_crlf(self):
        with open(os.path.join(ROOT, "START_HERE.bat"), "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_store_migrates_an_old_database(self):
        import sqlite3
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "old.db")
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, run_at TEXT NOT NULL,"
                  " price REAL, base_value REAL, bull_value REAL, bear_value REAL, score REAL, verdict TEXT,"
                  " model_class TEXT, model_version TEXT, out_dir TEXT, result_json TEXT)")
        c.commit(); c.close()
        s = store_mod.Store(p)
        cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(runs)")}
        self.assertTrue({"overrides", "card_saved", "price_source", "scale"} <= cols)

    def test_store_converts_old_scale_rows_once_and_backs_up(self):
        import sqlite3, valuation as V
        sys.path.insert(0, HERE)
        import test_valuation as TV
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "v.db")
        st = store_mod.Store(p)
        res = TV.run("BLMP")                       # rebuild a 2.0-scale result from it
        old = json.loads(json.dumps(res, default=str))
        old["score"]["score"] = round(10 - res["score"]["score"], 1)
        old["score"]["valuation_points"] = round(10 - res["score"]["valuation_points"], 2)
        old["score"]["badge"] = V.half_up(old["score"]["score"])
        old["score"].pop("scale")
        rid = st.add(old, tmp)
        sqlite3.connect(p).execute("UPDATE runs SET scale = 1").connection.commit()
        store_mod.Store(p)                         # reopen -> converts
        row = store_mod.Store(p).get(rid)          # reopen again -> must NOT flip back
        self.assertAlmostEqual(row["score"], res["score"]["score"])
        self.assertEqual(row["verdict"], res["score"]["verdict"])
        self.assertTrue(os.path.exists(p + ".before-scale-2.bak"))
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
