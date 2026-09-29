"""Watchlist, buy zones, theses, smart money on your names, re-valuation after earnings."""
import datetime as dt
import os
import tempfile
import unittest

from _path import ROOT  # noqa: F401
import store
import watchlist as W


class FakeView:
    """The Valuation desk (history, one run's card, method), plus the other desks' views."""

    def __init__(self):
        self.runs = {"ACME": [{"id": 7, "ticker": "ACME", "run_at": "2026-09-01 10:00:00", "price": 90.0,
                               "base_value": 200.0, "bull_value": 260.0, "bear_value": 150.0, "score": 7.0, "verdict": "BUY"}]}
        self.cards = {7: {"q": 7.5, "period": "2026-06-30"}}
        self.cache = {}
        self.posts = []
        self.running = {"valuation": 8790, "swing": 8787}
        self.next_id = 100

    def desk_ports(self):
        return dict(self.running)

    def _get(self, did, path, ttl=60):
        if did not in self.running:
            return None, "not running"
        if path == "/api/method":
            return {"margin_of_safety": 0.25}, None
        if path.startswith("/api/history?ticker="):
            return {"runs": sorted(self.runs.get(path.rsplit("=", 1)[1], []), key=lambda r: -r["id"])}, None
        if path.startswith("/api/run/"):
            c = self.cards[int(path.rsplit("/", 1)[1])]
            return {"card": {"quality": {"score": c["q"], "tier": "high"}, "synopsis": "s"},
                    "result": {"fin": {"period_end": c["period"], "basis": "TTM"}}}, None
        if path == "/api/scan":
            return {"alerts": [{"ticker": "ACME", "score": 48, "direction": "BULL", "drivers": ["flow"]}]}, None
        return None, "nothing"

    def for_ticker(self, t):
        return {"confluence": {"card": {"score": 61.0, "band": "elevated"}},
                "flow": {"card": {"lane": "board", "direction": "bullish", "score": 72}},
                "institutional": {"cluster": {"n_buyers": 3}}}

    def post(self, did, path, body, timeout=60):
        self.posts.append((did, path, body))
        t = body["ticker"]
        self.next_id += 1
        new = dict(self.runs[t][-1], id=self.next_id, run_at="2026-10-30 10:00:00", base_value=150.0,
                   verdict="HOLD", score=5.0)
        self.runs[t].append(new)
        self.cards[self.next_id] = {"q": 6.0, "period": self.period_after}
        return 200, {"run_id": self.next_id}

    period_after = "2026-09-30"


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None, **kw):
        self.calls.append(path)
        if path == "/api/insider/transactions":
            return 200, {"data": [
                {"id": 1, "transaction_code": "P", "formtype": "4", "amount": "1000", "price": "90", "owner_name": "A"},
                {"id": 2, "transaction_code": "P", "formtype": "4", "amount": "500", "price": "91", "owner_name": "B"},
                {"id": 3, "transaction_code": "P", "formtype": "144", "amount": "9999", "price": "90", "owner_name": "C"}]}
        if path.startswith("/api/institution/"):
            q = "2026-06-30"
            return 200, {"data": [
                {"report_date": q, "units": "0", "units_changed": "-400"},
                {"report_date": q, "units": "0", "units_changed": "-300"},
                {"report_date": q, "units": "0", "units_changed": "-200"},
                {"report_date": q, "units": "1000", "units_changed": "-100"},
                {"report_date": q, "units": "500", "units_changed": "50"},
                {"report_date": "2026-03-31", "units": "999999", "units_changed": "0"}]}
        return 200, {"data": []}


class FakeMarket:
    def __init__(self):
        self.client = FakeClient()
        self.price = {"ACME": 140.0, "ZENO": 50.0}
        self.er = {"ACME": {"date": "2026-10-28", "days": 5, "time": "postmarket"}}
        self.sell_usd = 0

    def quote(self, t):
        return {"price": self.price[t]}

    def holding(self, t):
        return {"earnings": self.er.get(t), "insider": {"nonplan_usd": self.sell_usd, "days": 30}}

    def _cached(self, key, ttl, fn):
        return fn()


class Base(unittest.TestCase):
    def setUp(self):
        self.st = store.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.view, self.mkt = FakeView(), FakeMarket()
        self.day = [dt.date(2026, 10, 23)]
        self.hold = [{"ticker": "ZENO", "amt": 10, "entry": 40.0, "mark": 50.0, "weight": 0.05}]
        self.w = W.Watchlist(self.st, self.mkt, self.view, holdings=lambda: self.hold, log=lambda m: None,
                             today=lambda: self.day[0])


class Zones(Base):
    def test_buy_zone_comes_from_the_valuation_margin_of_safety(self):
        self.w.add("acme")
        it = self.w.item("ACME")
        self.assertAlmostEqual(it["buy_auto"], 150.0)            # 200 x (1 - 0.25)
        self.assertEqual(it["zone"], "in")                       # 140 <= 150
        self.assertAlmostEqual(it["to_buy"], 140 / 150 - 1)
        self.mkt.price["ACME"] = 160.0
        self.assertEqual(self.w.item("ACME")["zone"], "near")    # within 10% above
        self.mkt.price["ACME"] = 180.0
        self.assertEqual(self.w.item("ACME")["zone"], "above")

    def test_your_own_buy_price_wins(self):
        self.w.add("ACME")
        self.w.save_thesis("ACME", {"buy_override": "120"})
        it = self.w.item("ACME")
        self.assertEqual((it["buy"], it["buy_is_override"], it["zone"]), (120.0, True, "above"))
        self.w.save_thesis("ACME", {"buy_override": ""})         # cleared -> back to Valuation's price
        self.assertEqual(self.w.item("ACME")["buy"], 150.0)

    def test_holdings_are_always_in_and_the_watchlist_follows(self):
        self.w.add("ACME")
        items = self.w.view_all()["items"]
        self.assertEqual(sorted(i["ticker"] for i in items), ["ACME", "ZENO"])
        z = [i for i in items if i["ticker"] == "ZENO"][0]
        self.assertTrue(z["holding"])
        self.assertIsNone(z["valuation"])                        # never valued: no zone, says so
        self.assertEqual(z["checks"][0]["status"], "n/a")         # no value and no rules to check against
        self.assertEqual(z["position"]["entry"], 40.0)

    def test_bad_tickers_are_refused(self):
        for bad in ("", "1ABC", "A;B", "../x", "TOOLONGTICKER"):
            self.assertEqual(self.w.add(bad)[0], 400, bad)

    def test_removing_keeps_a_written_thesis(self):
        self.w.add("ACME")
        self.w.save_thesis("ACME", {"thesis": "cheap, compounding"})
        self.w.remove("ACME")
        self.assertEqual(self.w._row("ACME")["thesis"], "cheap, compounding")
        self.assertFalse(self.w._row("ACME")["on_list"])
        self.w.add("NOVA")
        self.w.remove("NOVA")
        self.assertIsNone(self.w._row("NOVA"))


class Theses(Base):
    def test_baseline_is_taken_when_the_thesis_is_first_saved(self):
        self.w.save_thesis("ACME", {"thesis": "why", "add": True})
        b = self.w.item("ACME")["baseline"]
        self.assertEqual((b["verdict"], b["quality"], b["value"], b["run_id"]), ("BUY", 7.5, 200.0, 7))

    def test_price_checks(self):
        self.w.save_thesis("ACME", {"thesis": "x", "add": True, "sell_target": "130", "stop": "100"})
        c = {x["key"]: x for x in self.w.item("ACME")["checks"]}
        self.assertEqual(c["price"]["status"], "break")
        self.assertIn("sell target", c["price"]["detail"])
        self.mkt.price["ACME"] = 95.0
        c = {x["key"]: x for x in self.w.item("ACME")["checks"]}
        self.assertIn("stop", c["price"]["detail"])
        self.mkt.price["ACME"] = 210.0
        self.assertIn("above intrinsic value", {x["key"]: x for x in self.w.item("ACME")["checks"]}["price"]["detail"])

    def test_business_got_worse_against_the_baseline(self):
        self.w.save_thesis("ACME", {"thesis": "x", "add": True})
        c = {x["key"]: x for x in self.w.item("ACME")["checks"]}
        self.assertEqual(c["business"]["status"], "ok")
        self.view.post("valuation", "/api/run", {"ticker": "ACME"})      # BUY 7.5 -> HOLD 6.0
        c = {x["key"]: x for x in self.w.item("ACME")["checks"]}
        self.assertEqual(c["business"]["status"], "break")
        self.assertIn("BUY to HOLD", c["business"]["detail"])
        self.assertIn("7.5 to 6.0", c["business"]["detail"])
        self.w.save_thesis("ACME", {"rebaseline": True})               # "I've reconsidered": new baseline
        self.assertEqual({x["key"]: x for x in self.w.item("ACME")["checks"]}["business"]["status"], "ok")

    def test_business_check_needs_a_thesis(self):
        self.w.add("ACME")
        self.assertEqual({x["key"]: x for x in self.w.item("ACME")["checks"]}["business"]["status"], "n/a")


class SmartMoney(Base):
    def test_signals_on_one_name(self):
        s = self.w.signals("ACME")
        self.assertEqual(s["insider_buy"]["people"], 2)                 # the Form 144 row is not a purchase
        self.assertAlmostEqual(s["insider_buy"]["usd"], 1000 * 90 + 500 * 91)
        f = s["funds"]
        self.assertEqual((f["exits"], f["adds"], f["cuts"], f["holders"]), (3, 1, 4, 5))
        self.assertAlmostEqual(f["net"], 1500 / 2450 - 1)              # only the latest quarter counts
        kinds = {l["kind"] for l in s["lights"]}
        self.assertTrue({"insider", "confluence", "cluster", "flow", "swing"} <= kinds)
        self.assertEqual(s["check"]["status"], "break")                  # 3 exits and -39% net
        self.assertIn("3 funds exited", s["check"]["detail"])

    def test_heavy_insider_selling_breaks_the_thesis(self):
        self.mkt.client.get = lambda path, params=None, **k: (200, {"data": []})
        self.mkt.sell_usd = 2_000_000
        s = self.w.signals("ACME")
        self.assertEqual(s["check"]["status"], "break")
        self.assertIn("outside 10b5-1", s["check"]["detail"])


class AfterEarnings(Base):
    def test_revalues_the_day_after_the_report_and_logs_the_change(self):
        self.w.add("ACME")
        self.assertEqual(self.w.refresh(), 0)                            # records 2026-10-28, nothing to do
        self.assertEqual(self.w._row("ACME")["next_er"], "2026-10-28")
        self.day[0] = dt.date(2026, 10, 28)
        self.mkt.er["ACME"] = {"date": "2027-01-27", "days": 90}          # the data moved on to next quarter
        self.assertEqual(self.w.refresh(), 0)                            # report day itself: wait
        self.assertEqual(self.w._row("ACME")["next_er"], "2026-10-28")    # the passed date isn't overwritten
        self.day[0] = dt.date(2026, 10, 29)
        self.assertEqual(self.w.refresh(), 1)
        self.assertEqual(self.view.posts[-1], ("valuation", "/api/run", {"ticker": "ACME"}))
        ev = [e for e in self.w.events(30) if e["kind"] == "revalued"][0]
        self.assertIn("value $200.00 -> $150.00 (-25%)", ev["text"])
        self.assertIn("verdict BUY -> HOLD", ev["text"])
        self.assertIsNone(self.w._row("ACME")["next_er"])
        self.assertEqual(self.w.refresh(), 0)                            # done for this quarter

    def test_no_new_filing_yet_retries_then_gives_up(self):
        self.w.add("ACME")
        self.w.refresh()
        self.view.period_after = "2026-06-30"                             # same period: statements not filed
        self.day[0] = dt.date(2026, 10, 29)
        self.assertEqual(self.w.refresh(), 1)
        self.assertEqual(self.w._row("ACME")["next_er"], "2026-10-28")    # still pending
        self.day[0] = dt.date(2026, 10, 30)
        self.assertEqual(self.w.refresh(), 0)                            # waits RETRY_DAYS
        self.day[0] = dt.date(2026, 11, 1)
        self.assertEqual(self.w.refresh(), 1)
        self.day[0] = dt.date(2026, 12, 20)                              # 53 days on: stop, say so
        self.assertEqual(self.w.refresh(), 1)
        self.assertIsNone(self.w._row("ACME")["next_er"])
        self.assertTrue(any(e["kind"] == "stale" for e in self.w.events(90)))

    def test_a_run_you_already_did_counts(self):
        self.w.add("ACME")
        self.w.refresh()
        self.view.runs["ACME"].append(dict(self.view.runs["ACME"][0], id=50, run_at="2026-10-29 09:00:00"))
        self.view.cards[50] = {"q": 7.0, "period": "2026-09-30"}
        self.day[0] = dt.date(2026, 10, 30)
        self.assertEqual(self.w.refresh(), 0)
        self.assertEqual(self.view.posts, [])
        self.assertIsNone(self.w._row("ACME")["next_er"])

    def test_holdings_without_a_row_get_one(self):
        self.w.refresh()
        self.assertIsNotNone(self.w._row("ZENO"))
        self.assertFalse(self.w._row("ZENO")["on_list"])                 # a holding, not a watchlist add


if __name__ == "__main__":
    unittest.main()
