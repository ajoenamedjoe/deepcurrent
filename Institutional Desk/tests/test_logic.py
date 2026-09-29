"""
Unit tests for the pure logic in scan.py -- no network, no database.

Run:  python tests/test_logic.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scan            # noqa: E402
import uw              # noqa: E402

CFG = {
    "min_position_value": 1e6,
    "min_add_perc": 0.25,
    "max_trim_perc": -0.50,
    "min_profit_years": 3,
    "co_max_assets": 10e9,
}
RD = "2026-06-30"


def holding(**kw):
    base = {
        "security_type": "Share", "put_call": None, "units": 100000,
        "units_change": 0, "change_perc": "0.0", "value": "5000000",
        "close": "50.0", "first_buy": "2024-03-31",
    }
    base.update(kw)
    return base


class TestCoercion(unittest.TestCase):
    def test_num_handles_api_shapes(self):
        self.assertEqual(uw.num("36204417.00"), 36204417.0)
        self.assertEqual(uw.num(None), 0.0)
        self.assertEqual(uw.num(""), 0.0)
        self.assertEqual(uw.num("null"), 0.0)
        self.assertEqual(uw.num("1,234"), 1234.0)
        self.assertEqual(uw.num("garbage", -1), -1)

    def test_opt_num_preserves_null(self):
        self.assertIsNone(uw.opt_num(None))
        self.assertIsNone(uw.opt_num(""))
        self.assertEqual(uw.opt_num("-1.0"), -1.0)
        self.assertEqual(uw.opt_num(0), 0.0)   # zero is a value, not a null

    def test_unwrap_both_envelopes(self):
        self.assertEqual(uw.unwrap({"data": [1, 2]}), [1, 2])
        self.assertEqual(uw.unwrap({"result": [3]}), [3])
        self.assertEqual(uw.unwrap([4]), [4])
        self.assertEqual(uw.unwrap({}), [])
        self.assertEqual(uw.unwrap(None), [])


class TestClassifyEvent(unittest.TestCase):
    def test_new_position(self):
        h = holding(first_buy=RD, units_change=100000, change_perc=None)
        self.assertEqual(scan.classify_event(h, RD, CFG), "NEW")

    def test_exit_beats_trim(self):
        # a closed position has units 0 AND a negative change -- order matters
        h = holding(units=0, units_change=-200000, change_perc="-1.0", value="0")
        self.assertEqual(scan.classify_event(h, RD, CFG), "EXIT")

    def test_tiny_exit_ignored(self):
        h = holding(units=0, units_change=-100, change_perc="-1.0",
                    value="0", close="2.0")   # $200 sold
        self.assertIsNone(scan.classify_event(h, RD, CFG))

    def test_add_needs_threshold(self):
        self.assertEqual(scan.classify_event(
            holding(units_change=50000, change_perc="0.40"), RD, CFG), "ADD")
        self.assertIsNone(scan.classify_event(
            holding(units_change=50000, change_perc="0.10"), RD, CFG))

    def test_trim_needs_threshold(self):
        self.assertEqual(scan.classify_event(
            holding(units_change=-90000, change_perc="-0.75"), RD, CFG), "TRIM")
        self.assertIsNone(scan.classify_event(
            holding(units_change=-1000, change_perc="-0.05"), RD, CFG))

    def test_unchanged_is_not_an_event(self):
        self.assertIsNone(scan.classify_event(holding(units_change=0), RD, CFG))

    def test_non_share_security_types_skipped(self):
        for sec in ("Fund", "Option", "Debt", "Warrant", "Pref", "Unidentified", ""):
            h = holding(security_type=sec, first_buy=RD, units_change=100000)
            self.assertIsNone(scan.classify_event(h, RD, CFG), sec)

    def test_option_leg_skipped(self):
        h = holding(put_call="call", first_buy=RD, units_change=100000)
        self.assertIsNone(scan.classify_event(h, RD, CFG))

    def test_small_position_skipped(self):
        h = holding(first_buy=RD, units_change=100, value="250000")
        self.assertIsNone(scan.classify_event(h, RD, CFG))

    def test_first_buy_in_a_prior_quarter_is_not_new(self):
        h = holding(first_buy="2025-12-31", units_change=100000, change_perc="0.9")
        self.assertEqual(scan.classify_event(h, RD, CFG), "ADD")


class TestTrajectory(unittest.TestCase):
    def test_building(self):
        self.assertEqual(
            scan.classify_trajectory([2410, 2185, 1960, 845, 790, 610, 480, 315]),
            "building")

    def test_harvesting(self):
        self.assertEqual(
            scan.classify_trajectory([3720, 4185, 5540, 6010, 7325, 7390, 8120, 8465]),
            "harvesting")

    def test_new_conviction(self):
        self.assertEqual(scan.classify_trajectory([3260, 0, 0, 0, 0, 0, 0, 0]),
                         "new_conviction")

    def test_closing(self):
        self.assertEqual(scan.classify_trajectory([0, 740, 740, 740, 0, 0, 0, 0]),
                         "closing")

    def test_steady(self):
        self.assertEqual(scan.classify_trajectory([100, 100, 100, 100, 100, 0, 0, 0]),
                         "steady")

    def test_volatile(self):
        self.assertEqual(
            scan.classify_trajectory([1640, 690, 910, 470, 1380, 820, 0, 0]),
            "volatile")

    def test_degenerate_inputs(self):
        self.assertEqual(scan.classify_trajectory([]), "unknown")
        self.assertEqual(scan.classify_trajectory(None), "unknown")
        self.assertEqual(scan.classify_trajectory(["100", "50"]), "new_conviction")


class TestScore(unittest.TestCase):
    def test_bounded_and_ordered(self):
        low, _ = scan.score_event("ADD", 0.001, 400, "steady", 1, None, 5.0)
        high, _ = scan.score_event("NEW", 0.30, 8, "building", 9, 0.5, -0.2)
        self.assertGreaterEqual(low, 0)
        self.assertLessEqual(high, 100)
        self.assertGreater(high, low)

    def test_new_beats_add_all_else_equal(self):
        a, _ = scan.score_event("NEW", 0.05, 20, "building", 2, 0.1, 0.05)
        b, _ = scan.score_event("ADD", 0.05, 20, "building", 2, 0.1, 0.05)
        self.assertGreater(a, b)

    def test_weight_is_monotonic(self):
        prev = -1
        for w in (0.0, 0.01, 0.03, 0.08, 0.25):
            s, _ = scan.score_event("ADD", w, 30, "steady", 1, None, None)
            self.assertGreaterEqual(s, prev)
            prev = s

    def test_cluster_capped(self):
        _, p = scan.score_event("NEW", 0.05, 20, "building", 50, 0.1, 0.0)
        self.assertLessEqual(p["cluster"], 20.0)

    def test_score_is_the_normalised_sum_of_its_parts(self):
        s, p = scan.score_event("ADD", 0.01, 400, "steady", 1, None, None)
        self.assertAlmostEqual(s, round(100 * sum(p.values()) / scan.SCORE_MAX, 1), places=1)

    def test_perfect_event_reaches_100(self):
        s, _ = scan.score_event("NEW", 0.5, 5, "building", 20, 1.0, -0.5)
        self.assertAlmostEqual(s, 100.0, places=1)

    def test_top_of_board_is_not_a_wall_of_100s(self):
        # the bug this replaced: clipping made six different names all show 100
        a, _ = scan.score_event("ADD", 0.08, 40, "building", 8, 0.9, -0.03)
        b, _ = scan.score_event("NEW", 0.12, 44, "steady", 6, 0.2, -0.2)
        c, _ = scan.score_event("NEW", 0.19, 20, "new_conviction", 4, 0.5, 0.05)
        self.assertEqual(len({a, b, c}), 3)
        for s in (a, b, c):
            self.assertLess(s, 100.0)

    def test_missing_optionals_do_not_crash(self):
        s, _ = scan.score_event("NEW", None, None, "unknown", None, None, None)
        self.assertGreaterEqual(s, 0)


def inc(year, ni, oi=None, rev=1e9, currency="USD", rtype="annual"):
    return {
        "report_type": rtype, "reported_currency": currency,
        "fiscal_date_ending": "%d-12-31" % year,
        "net_income": str(ni),
        "operating_income": str(oi if oi is not None else ni),
        "total_revenue": str(rev),
    }


def bs(year, assets):
    return {"report_type": "annual", "fiscal_date_ending": "%d-12-31" % year,
            "total_assets": str(assets), "total_liabilities": str(assets * 0.4),
            "total_shareholder_equity": str(assets * 0.6)}


class TestFundamentals(unittest.TestCase):
    def test_three_profitable_years_pass(self):
        out = scan.evaluate_fundamentals(
            [inc(2025, 120e6), inc(2024, 100e6), inc(2023, 80e6)],
            [bs(2025, 900e6)], CFG)
        self.assertEqual(out["passes"], 1)
        self.assertEqual(out["profitable_years"], 3)
        self.assertAlmostEqual(out["ni_growth"], 0.20, places=6)

    def test_recent_loss_fails(self):
        out = scan.evaluate_fundamentals(
            [inc(2025, -47e6), inc(2024, 612e6), inc(2023, 538e6)],
            [bs(2025, 3.3e9)], CFG)
        self.assertEqual(out["passes"], 0)
        self.assertEqual(out["profitable_years"], 0)
        self.assertIn("profitable_0", out["reject_reason"])

    def test_streak_must_be_consecutive_and_recent(self):
        out = scan.evaluate_fundamentals(
            [inc(2025, 10e6), inc(2024, 10e6), inc(2023, -5e6), inc(2022, 50e6)],
            [bs(2025, 500e6)], CFG)
        self.assertEqual(out["profitable_years"], 2)
        self.assertEqual(out["passes"], 0)

    def test_quarterly_rows_are_ignored(self):
        rows = [inc(2025, 30e6, rtype="quarterly"), inc(2024, 30e6, rtype="quarterly")]
        out = scan.evaluate_fundamentals(rows, [], CFG)
        self.assertEqual(out["years_available"], 0)
        self.assertEqual(out["reject_reason"], "no_annual_statements")

    def test_foreign_currency_rejected(self):
        # an IDR filer's net income looks like a trillion -- must not pass
        out = scan.evaluate_fundamentals(
            [inc(2025, 7.9e12, currency="IDR"), inc(2024, 7.1e12, currency="IDR"),
             inc(2023, 6.4e12, currency="IDR")], [bs(2025, 1e9)], CFG)
        self.assertEqual(out["passes"], 0)
        self.assertEqual(out["reject_reason"], "reports_in_IDR")

    def test_missing_currency_accepted_when_scale_is_sane(self):
        # Synthetic payload in the shape of the income-statements endpoint: a
        # plain US filer (fictional ACME) whose reported_currency is "None"
        rows = [inc(y, 17e6, rev=413e6, currency="None") for y in (2025, 2024, 2023)]
        out = scan.evaluate_fundamentals(rows, [bs(2025, 562e6)], CFG, marketcap=455e6)
        self.assertEqual(out["passes"], 1)

    def test_missing_currency_rejected_when_scale_is_absurd(self):
        # Fictional ZENO: reported_currency "None", but ~25 trillion of revenue
        # against a 14B market cap cannot be dollars
        rows = [inc(y, 4.3e12, rev=24.8e12, currency="None") for y in (2025, 2024, 2023)]
        out = scan.evaluate_fundamentals(rows, [bs(2025, 1e9)], CFG, marketcap=14e9)
        self.assertEqual(out["passes"], 0)
        self.assertEqual(out["reject_reason"], "currency_unknown_and_scale_implausible")

    def test_scale_check_skipped_when_currency_is_explicit_usd(self):
        rows = [inc(y, 5e9, rev=90e9, currency="USD") for y in (2025, 2024, 2023)]
        out = scan.evaluate_fundamentals(rows, [bs(2025, 5e9)], CFG, marketcap=200e6)
        self.assertEqual(out["passes"], 1)

    def test_scale_check_skipped_without_marketcap(self):
        rows = [inc(y, 10e6, rev=50e6, currency="None") for y in (2025, 2024, 2023)]
        out = scan.evaluate_fundamentals(rows, [bs(2025, 100e6)], CFG, marketcap=None)
        self.assertEqual(out["passes"], 1)

    def test_too_few_years_rejected(self):
        out = scan.evaluate_fundamentals(
            [inc(2025, 10e6), inc(2024, 10e6)], [bs(2025, 100e6)], CFG)
        self.assertEqual(out["passes"], 0)
        self.assertIn("only_2", out["reject_reason"])

    def test_balance_sheet_cap_enforced(self):
        out = scan.evaluate_fundamentals(
            [inc(2025, 1e9), inc(2024, 1e9), inc(2023, 1e9)],
            [bs(2025, 400e9)], CFG)          # a bank
        self.assertEqual(out["passes"], 0)
        self.assertEqual(out["reject_reason"], "total_assets_above_cap")

    def test_null_net_income_breaks_streak(self):
        rows = [inc(2025, 10e6), inc(2024, 10e6), inc(2023, 10e6)]
        rows[1]["net_income"] = None
        out = scan.evaluate_fundamentals(rows, [bs(2025, 100e6)], CFG)
        self.assertEqual(out["profitable_years"], 1)
        self.assertEqual(out["passes"], 0)

    def test_unsorted_input_is_handled(self):
        out = scan.evaluate_fundamentals(
            [inc(2023, 80e6), inc(2025, 120e6), inc(2024, 100e6)],
            [bs(2023, 100e6), bs(2025, 900e6)], CFG)
        self.assertEqual(out["fy_end"], "2025-12-31")
        self.assertEqual(out["total_assets"], 900e6)
        self.assertEqual(out["passes"], 1)

    def test_missing_balance_sheet_still_passes(self):
        out = scan.evaluate_fundamentals(
            [inc(2025, 10e6), inc(2024, 10e6), inc(2023, 10e6)], [], CFG)
        self.assertEqual(out["passes"], 1)
        self.assertIsNone(out["total_assets"])


class TestEnvParsing(unittest.TestCase):
    def test_env_file_quirks(self):
        import tempfile
        body = (
            "﻿# comment\r\n"
            "UW_API_TOKEN = abc=def==  \r\n"
            'DISCORD_WEBHOOK_URL="https://x/y?z=1"\r\n'
            "\r\n"
            "BARE\r\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(body)
            path = fh.name
        try:
            got = uw._parse_env_file(path)
            self.assertEqual(got["UW_API_TOKEN"], "abc=def==")
            self.assertEqual(got["DISCORD_WEBHOOK_URL"], "https://x/y?z=1")
            self.assertNotIn("BARE", got)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
