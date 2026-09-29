"""Growth Leaders model tests: every check by name, the base finder, market direction, and the score's spread."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("GDESK_DB", os.path.join(tempfile.mkdtemp(), "g.db"))

import fixture as F   # noqa: E402
import model as M     # noqa: E402
import scan as S      # noqa: E402

TODAY = F.TODAY


def q(eps):
    return M.eps_series(F.earnings(eps), TODAY)


class Weights(unittest.TestCase):
    def test_weights_sum_to_100(self):
        self.assertEqual(sum(M.WEIGHTS.values()), 100)

    def test_method_lists_every_weight(self):
        self.assertEqual(M.method()["weights"], M.WEIGHTS)


class Earnings(unittest.TestCase):
    def test_next_report_row_is_dropped(self):
        rows = F.earnings(F.growth_eps())
        self.assertIsNone(rows[0]["reported_eps"])
        self.assertEqual(len(M.eps_series(rows, TODAY)), 16)

    def test_c_growth_and_acceleration(self):
        c = M.check_c(q(F.growth_eps(1.0, 0.5)), TODAY)
        self.assertTrue(c["passed"])
        self.assertAlmostEqual(c["g0"], 0.5, places=2)
        eps = F.growth_eps(1.0, 0.3)
        eps[0] *= 1.5                                        # latest quarter jumps: speeding up
        fast = M.check_c(q(eps), TODAY)
        self.assertGreater(fast["accel"], 0.05)
        self.assertIn("speeding up", fast["text"])

    def test_c_a_loss_is_never_a_growth_rate(self):
        eps = F.growth_eps(1.0, 0.3)
        eps[0] = -0.2
        c = M.check_c(q(eps), TODAY)
        self.assertEqual((c["passed"], c["points"]), (False, 0))
        self.assertIn("a loss", c["text"])
        eps = F.growth_eps(1.0, 0.3)
        eps[4] = -0.1                                         # year-ago loss, now a profit
        c = M.check_c(q(eps), TODAY)
        self.assertFalse(c["passed"])
        self.assertIn("Turned profitable", c["text"])
        self.assertGreater(c["points"], 0)

    def test_c_stale_or_short_history_is_unmeasured(self):
        self.assertFalse(M.check_c(q([1, 1, 1]), TODAY)["measured"])
        self.assertFalse(M.check_c(q(F.growth_eps()), "2027-06-01")["measured"])
        self.assertFalse(M.check_c([], TODAY)["measured"])

    def test_a_cagr_and_down_years(self):
        a = M.check_a(q(F.growth_eps(1.0, 0.4)))
        self.assertTrue(a["passed"])
        self.assertAlmostEqual(a["cagr"], 0.4, delta=0.03)
        eps = F.growth_eps(1.0, 0.4)
        for k in range(4, 8):
            eps[k] *= 1.8                                     # last year was better than this one
        a = M.check_a(q(eps))
        self.assertFalse(a["passed"])
        self.assertIn("down year", a["text"])
        self.assertFalse(M.check_a(q(F.growth_eps(n=6)))["measured"])


class PriceChecks(unittest.TestCase):
    def test_n_distance_from_high(self):
        at = M.check_n({"close": "99", "week_52_high": "100"})
        far = M.check_n({"close": "70", "week_52_high": "100"})
        self.assertTrue(at["passed"])
        self.assertFalse(far["passed"])
        self.assertGreater(at["points"], far["points"])
        self.assertFalse(M.check_n({"close": None, "week_52_high": "100"})["measured"])

    def test_s_volume_and_share_count(self):
        bars = S.bar_rows(F.base_then("breakout"))
        s = M.check_s({"shares_outstanding_growth_4q": "-0.02"}, bars)
        self.assertTrue(s["measured"])
        self.assertIn("buybacks", s["text"])
        glitch = M.check_s({"shares_outstanding_growth_4q": "999849.4"}, [])
        self.assertFalse(glitch["measured"])                  # a 999,849x share count is a data error, not dilution
        novol = [dict(b, volume=None) for b in bars]
        only_shares = M.check_s({"shares_outstanding_growth_4q": "0.10"}, novol)
        self.assertEqual(only_shares["possible"], 4.0)        # volume unmeasured: that part drops out
        self.assertFalse(only_shares["passed"])

    def test_i_breadth_and_net(self):
        good = M.check_i(M.ownership(F.ownership(adds=60, cuts=20)), TODAY)
        bad = M.check_i(M.ownership(F.ownership(adds=15, cuts=60)), TODAY)
        self.assertTrue(good["passed"])
        self.assertFalse(bad["passed"])
        self.assertGreater(good["points"], bad["points"])
        self.assertIn("new", good["text"])
        self.assertFalse(M.check_i(M.ownership(F.ownership(adds=3, cuts=2)), TODAY)["measured"])
        self.assertFalse(M.check_i(M.ownership(F.ownership(report_date="2025-09-30")), TODAY)["measured"])


class Ranks(unittest.TestCase):
    def test_rs_is_1_to_99_and_monotonic(self):
        rows = F.universe(300)
        rs = M.rs_ratings(rows)
        vals = sorted(v["rs"] for v in rs.values())
        self.assertEqual((vals[0], vals[-1]), (1, 99))
        by = sorted(rows, key=lambda r: M.rs_raw(r)[0])
        self.assertLess(rs[by[0]["ticker"]]["rs"], rs[by[-1]["ticker"]]["rs"])

    def test_groups_rank_the_strong_industry_first(self):
        rows = F.universe(400)
        g = M.group_ranks(rows, M.rs_ratings(rows))
        self.assertEqual(g["Semis"]["rank"], 1)
        self.assertLess(g["Autos"]["pct"], g["Semis"]["pct"])


class Base(unittest.TestCase):
    def state(self, kind, **kw):
        return M.breakout(S.bar_rows(F.base_then(kind)), **kw)

    def test_states(self):
        self.assertEqual(self.state("breakout")["state"], "breakout")
        self.assertEqual(self.state("near")["state"], "near_pivot")
        self.assertEqual(self.state("extended")["state"], "extended")
        self.assertEqual(self.state("in_base")["state"], "in_base")

    def test_quiet_breakout_waits_for_volume(self):
        bars = S.bar_rows(F.base_then("breakout"))
        bars[-1]["volume"] = 1e6
        self.assertEqual(M.breakout(bars)["state"], "weak_breakout")

    def test_levels_and_sell_rules(self):
        b = self.state("breakout")
        self.assertAlmostEqual(b["buy_to"], b["pivot"] * 1.05)
        self.assertAlmostEqual(b["stop"], b["pivot"] * 0.92)
        self.assertAlmostEqual(b["profit_from"], b["pivot"] * 1.20)

    def test_a_stock_at_new_highs_has_no_base(self):
        closes = [50 * 1.004 ** i for i in range(250)]
        b = M.breakout(S.bar_rows(F.bars_series(closes)))
        self.assertEqual(b["state"], "none")
        self.assertIn("No base yet", b["text"])

    def test_breakout_a_few_days_ago_keeps_its_pivot(self):
        bars = S.bar_rows(F.base_then("breakout"))
        p = bars[-1]["close"]
        for i in range(3):
            bars.append(dict(bars[-1], date="2026-09-2%d" % (6 + i), close=p * 1.01, high=p * 1.015, low=p, volume=1e6))
        b = M.breakout(bars)
        self.assertEqual(b["state"], "in_range")
        self.assertIn("Broke out", b["text"])


class Market(unittest.TestCase):
    def st(self, kind):
        return M.index_state(S.bar_rows(F.index_series(kind)))

    def test_states(self):
        self.assertEqual(self.st("uptrend")["state"], "uptrend")
        self.assertEqual(self.st("correction")["state"], "correction")
        p = self.st("pressure")
        self.assertEqual(p["state"], "pressure")
        self.assertGreaterEqual(p["distribution_days"], M.DD_PRESSURE)

    def test_follow_through_day_ends_a_correction(self):
        s = self.st("ftd")
        self.assertEqual(s["state"], "uptrend")
        self.assertIsNotNone(s["follow_through"])

    def test_worse_of_the_two_indexes(self):
        m = M.market_state(S.bar_rows(F.index_series("uptrend")), S.bar_rows(F.index_series("correction")))
        self.assertEqual((m["state"], m["ok"]), ("correction", False))
        m = M.market_state(S.bar_rows(F.index_series("uptrend")), S.bar_rows(F.index_series("uptrend")))
        self.assertTrue(m["ok"])

    def test_no_volume_counts_no_distribution_days(self):
        bars = [dict(b, volume=None) for b in S.bar_rows(F.index_series("pressure"))]
        s = M.index_state(bars)
        self.assertIsNone(s["distribution_days"])
        m = M.market_state(bars, bars)
        self.assertTrue(m["price_only"])
        self.assertIn("Volume unavailable", m["text"])

    def test_no_history_is_unknown_not_bad(self):
        m = M.market_state([], [])
        self.assertIsNone(m["state"])
        self.assertFalse(m["ok"])


class Card(unittest.TestCase):
    def setUp(self):
        self.fc = F.FakeClient(300)
        self.rs = M.rs_ratings(self.fc.rows)
        self.g = M.group_ranks(self.fc.rows, self.rs)
        self.mk = M.market_state(S.bar_rows(self.fc.bars["SPY"]), S.bar_rows(self.fc.bars["QQQ"]))

    def card(self, t, **kw):
        row = next(r for r in self.fc.rows if r["ticker"] == t)
        args = dict(eps_rows=self.fc.eps[t], bars=S.bar_rows(self.fc.bars[t]), own_rows=self.fc.own[t], market=self.mk,
                    today=TODAY)
        args.update(kw)
        return M.score_stock(t, row, self.rs, self.g, **args)

    def test_unmeasured_parts_rescale_and_too_little_data_gives_no_score(self):
        t = self.fc.rows[0]["ticker"]
        full = self.card(t)
        self.assertEqual(full["measured"], 100)
        no13f = self.card(t, own_rows=None)
        self.assertEqual(no13f["measured"], 85)               # I (15) dropped out, the rest re-scale
        self.assertEqual(no13f["score"], int(M.half_up(100 * no13f["earned"] / 85)))
        thin = self.card(t, eps_rows=[], own_rows=None)       # C 25 + A 15 + I 15 missing: 45 measured < 60
        self.assertEqual(thin["measured"], 45)
        self.assertIsNone(thin["score"])

    def test_leader_needs_all_seven(self):
        leaders = [c for c in (self.card(r["ticker"]) for r in self.fc.rows) if c["six_pass"]]
        self.assertTrue(leaders)
        t = leaders[0]["ticker"]
        self.assertTrue(self.card(t)["leader"])
        bad = M.market_state(S.bar_rows(F.index_series("correction")), S.bar_rows(F.index_series("correction")))
        c = self.card(t, market=bad)
        self.assertTrue(c["six_pass"])
        self.assertFalse(c["leader"])                         # the market check fails, so no badge
        self.assertEqual(c["score"], self.card(t)["score"])   # ... but M never moves the score

    def test_score_discriminates_across_fixtures(self):
        scores = sorted(c["score"] for c in (self.card(r["ticker"]) for r in self.fc.rows) if c["score"] is not None)
        n = len(scores)
        self.assertGreater(scores[int(0.9 * (n - 1))] - scores[int(0.1 * (n - 1))], 30)
        self.assertLess(sum(1 for s in scores if s == scores[-1]), max(3, n // 20))   # no pile-up at the top

    def test_every_check_explains_itself(self):
        c = self.card(self.fc.rows[5]["ticker"])
        self.assertEqual([x["key"] for x in c["checks"]], list("CANSLIM"))
        for x in c["checks"]:
            self.assertTrue(x["text"].strip(), x["key"])

    def test_flow_is_context_only(self):
        t = self.fc.rows[0]["ticker"]
        a = self.card(t)
        row = dict(next(r for r in self.fc.rows if r["ticker"] == t), bullish_premium="9e9", bearish_premium="1")
        b = M.score_stock(t, row, self.rs, self.g, eps_rows=self.fc.eps[t], bars=S.bar_rows(self.fc.bars[t]),
                          own_rows=self.fc.own[t], market=self.mk, today=TODAY)
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(b["flow"]["tone"], "good")


class Numbers(unittest.TestCase):
    def test_num_handles_uw_strings(self):
        self.assertEqual(M.num("12.50"), 12.5)
        for v in (None, "None", "", "nan", "abc", True):
            self.assertIsNone(M.num(v))

    def test_half_up(self):
        self.assertEqual(M.half_up(2.5), 3)
        self.assertEqual(M.half_up(0.125, 2), 0.13)


if __name__ == "__main__":
    unittest.main()
