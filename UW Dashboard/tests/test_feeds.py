"""Today's Events and the portfolio sheet."""
import unittest

from _path import ROOT  # noqa: F401
from fixtures.sample_events import EARNINGS, MARKET_EVENTS
import events
import portfolio


class Econ(unittest.TestCase):
    def test_duplicated_events_are_collapsed(self):
        rows = events.norm_econ(events.rows(MARKET_EVENTS))
        self.assertEqual(len(MARKET_EVENTS["data"]), 12)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["time"], "2026-10-14T12:30:00Z")

    def test_null_forecast_stays_null(self):
        rows = events.norm_econ(events.rows(MARKET_EVENTS))
        kc = [r for r in rows if r["event"] == "Regional Fed Manufacturing Survey"][0]
        self.assertIsNone(kc["forecast"])
        self.assertEqual(kc["prev"], "-4")


class Earnings(unittest.TestCase):
    def test_expected_move_is_dollars_so_percent_is_derived(self):
        rows = {r["symbol"]: r for r in events.norm_earn(events.rows(EARNINGS), "premarket")}
        self.assertAlmostEqual(rows["ZMRT"]["move_pct"], 18.4127 / 742.5, places=6)
        self.assertAlmostEqual(rows["QRTX"]["move"], 1.2375)

    def test_sorted_by_market_cap_and_flags(self):
        rows = events.norm_earn(events.rows(EARNINGS), "premarket")
        self.assertEqual(rows[0]["symbol"], "ZMRT")
        self.assertTrue(rows[0]["sp500"])
        self.assertFalse([r for r in rows if r["symbol"] == "QRTX"][0]["sp500"])

    def test_missing_estimate_is_none_not_zero(self):
        vfs = [r for r in events.norm_earn(events.rows(EARNINGS), "x") if r["symbol"] == "NULX"][0]
        self.assertIsNone(vfs["eps_est"])
        self.assertIsNone(vfs["surprise"])

    def test_num_handles_strings_nulls_and_literal_none(self):
        self.assertEqual(events.num("1,234.5"), 1234.5)
        self.assertIsNone(events.num("None"))
        self.assertAlmostEqual(events.num("-0.3%"), -0.003)

    def test_envelopes(self):
        self.assertEqual(len(events.rows([{"a": 1}])), 1)
        self.assertEqual(len(events.rows({"data": [{"a": 1}, {"a": 2}]})), 2)
        self.assertEqual(len(events.rows({"x": {"result": [{"a": 1}]}})), 1)

    def test_events_keeps_last_good_copy_on_failure(self):
        calls = {"n": 0}

        def opener(url):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("boom")
            return 200, MARKET_EVENTS
        ev = events.Events(events.Client(token="t", opener=opener))
        first = ev.calendar()
        ev.cache[("cal",)]["at"] = 0          # expire it
        second = ev.calendar()
        self.assertFalse(first["stale"])
        self.assertTrue(second["stale"])
        self.assertEqual(len(second["rows"]), 6)

    def test_no_token_is_a_sentence_not_a_crash(self):
        ev = events.Events(events.Client(token=None, opener=lambda u: (200, {})))
        ev.client.token = ""
        out = ev.calendar()
        self.assertIn("UW_API_TOKEN", out["error"])


class Sheet(unittest.TestCase):
    URL = ("https://docs.google.com/spreadsheets/d/e/2PACX-abc_DEF-1/pub?gid=0&single=true&output=csv")

    def test_urls_from_a_published_csv_link(self):
        u = portfolio.sheet_urls(self.URL)
        self.assertTrue(u["csv"].endswith("pub?gid=0&single=true&output=csv"))
        self.assertIn("pubhtml?gid=0", u["html"])
        self.assertIn("widget=true", u["html"])

    def test_urls_from_a_normal_share_link(self):
        u = portfolio.sheet_urls("https://docs.google.com/spreadsheets/d/1AbC/edit#gid=12")
        self.assertIn("export?format=csv&gid=12", u["csv"])

    def test_parse_skips_title_rows_and_types_columns(self):
        csv_text = ("My Portfolio,,,,\n,,,,\nTicker,Shares,Price,Day Change,Gain %\n"
                    "AAPL,10,$225.00,($3.10),12.5%\nMSFT,5,\"$430.00\",$1.20,(4.0%)\n")
        d = portfolio.parse(csv_text)
        self.assertEqual(d["columns"], ["Ticker", "Shares", "Price", "Day Change", "Gain %"])
        self.assertEqual(len(d["rows"]), 2)
        self.assertEqual(d["types"][0]["kind"], "text")
        self.assertTrue(d["types"][3]["signed"])
        self.assertFalse(d["types"][1]["signed"])
        self.assertTrue(d["types"][4]["pct"])

    def test_parse_cell(self):
        self.assertEqual(portfolio.parse_cell("($1,234.50)"), -1234.5)
        self.assertAlmostEqual(portfolio.parse_cell("-12.5%"), -0.125)
        self.assertIsNone(portfolio.parse_cell("AAPL"))

    def test_unpublished_sheet_says_how_to_publish(self):
        p = portfolio.Portfolio(fetch=lambda u: "<!DOCTYPE html><html>sign in</html>")
        out = p.get(self.URL)
        self.assertIn("Publish to web", out["error"])


if __name__ == "__main__":
    unittest.main()


WIDGET_CSV = ("Open/Closed,Date,Close Date,Ticker,Amt,Entry,Mark,% P&L (Unreal),$$ P&L (Unreal),Thoughts\n"
              "Open,7/14/2026,,ZENO,12,142.50,163.10,14.46%,247.2,\"Channel breakout, held 50 day\"\n"
              "Open,8/12/2026,,BLMP,150,14.20,14.36,1.13%,24.00,Supply squeeze thesis\n"
              "Open,8/11/2026,,ACME,30,412.40,405.80,-1.60%,-198.00,Weekly flag breakout\n"
              "Open,6/2/2026,,QRTX,40,61.20,44.85,-26.72%,-654.00,\"Base failure, early entry, sector buzz\"\n"
              "Closed,8/3/2026,8/19/2026,VOLT,0,9.40,10.71,13.94%,0,\"Supply squeeze, pullback entry, earnings run-up\"\n")


class PortfolioModel(unittest.TestCase):
    """Synthetic portfolio sheet in the shape of a published trade-log widget."""

    def m(self, text=WIDGET_CSV, capital=100000):
        return portfolio.model(portfolio.parse(text), capital)

    def test_matches_the_widget_summary_line(self):
        s = self.m()["summary"]
        self.assertEqual(s["positions"], 4)
        self.assertAlmostEqual(s["deployed"] * 100, 18.1, places=1)
        self.assertAlmostEqual(s["cash"] * 100, 81.9, places=1)
        self.assertAlmostEqual(s["unrealized"] * 100, -3.11, places=2)
        self.assertAlmostEqual(s["realized"] * 100, 13.94, places=2)

    def test_weights_against_capital_and_ring_against_deployed(self):
        o = {p["ticker"]: p for p in self.m()["open"]}
        self.assertAlmostEqual(o["ACME"]["weight"] * 100, 12.2, places=1)
        self.assertAlmostEqual(o["QRTX"]["weight"] * 100, 1.8, places=1)
        self.assertAlmostEqual(sum(p["share"] for p in o.values()), 1.0)

    def test_table_is_in_open_date_order_and_colours_follow_the_position(self):
        m = self.m()
        self.assertEqual([p["ticker"] for p in m["open"]], ["QRTX", "ZENO", "ACME", "BLMP"])
        self.assertEqual(len({p["color"] for p in m["open"]}), 4)
        # tripling ACME's size must not repaint anything
        bigger = self.m(WIDGET_CSV.replace("ACME,30,", "ACME,90,"))
        self.assertEqual({p["ticker"]: p["color"] for p in m["open"]},
                         {p["ticker"]: p["color"] for p in bigger["open"]})

    def test_closed_trades_and_notes(self):
        c = self.m()["closed"]
        self.assertEqual(c[0]["ticker"], "VOLT")
        self.assertEqual(c[0]["close_date"], "8/19/2026")
        self.assertIn("Supply squeeze", c[0]["notes"])

    def test_capital_changes_weights_not_the_ring(self):
        a, b = self.m(capital=100000), self.m(capital=50000)
        self.assertAlmostEqual(b["summary"]["deployed"], 2 * a["summary"]["deployed"])
        self.assertEqual([p["share"] for p in a["open"]], [p["share"] for p in b["open"]])

    def test_missing_mark_column_is_named(self):
        m = self.m("Ticker,Amt,Entry\nXOM,10,138\n")
        self.assertFalse(m["ok"])
        self.assertIn("mark", m["missing"])

    def test_open_row_without_size_is_listed_not_zeroed(self):
        m = self.m(WIDGET_CSV.replace("Open,8/12/2026,,BLMP,150,", "Open,8/12/2026,,BLMP,,"))
        self.assertEqual(len(m["open"]), 3)
        self.assertTrue(any("BLMP" in s for s in m["skipped"]))

    def test_percent_computed_when_the_sheet_has_no_pct_column(self):
        m = self.m("Ticker,Amt,Entry,Mark\nAAA,10,100,110\n")
        self.assertAlmostEqual(m["open"][0]["pct"], 0.10)
        self.assertIsNone(m["summary"]["realized"])
