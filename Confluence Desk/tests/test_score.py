"""
Confluence Desk -- unit tests.

Every test named after the bug or the trap it guards. Run with:
    python -m unittest discover -s tests -v
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import darkpool
import insider
import score
import uw


# ============================================================ arithmetic

class TestWeights(unittest.TestCase):
    def test_weights_sum_to_100(self):
        """
        The composite must not need clipping.

        Institutional Desk bug #1: components summed to 122, clipping at 100
        put SIX different names at exactly 100 and destroyed the ordering at
        the top of the board -- the only part of the board anyone reads.
        """
        self.assertAlmostEqual(sum(score.ALL_WEIGHTS), 100.0, places=9)

    def test_leg_weights_sum_to_100(self):
        self.assertAlmostEqual(
            score.W_INSIDER + score.W_DARKPOOL + score.W_INSTITUTIONAL, 100.0)

    def test_insider_subweights_sum_to_leg(self):
        self.assertAlmostEqual(
            score.WI_CONVICTION + score.WI_CLUSTER + score.WI_RANK + score.WI_SIZE,
            score.W_INSIDER, places=9)

    def test_darkpool_subweights_sum_to_leg(self):
        self.assertAlmostEqual(
            score.WD_BLOCK + score.WD_LEAN + score.WD_SUSTAIN + score.WD_PROXIMITY,
            score.W_DARKPOOL, places=9)

    def test_institutional_subweights_sum_to_leg(self):
        self.assertAlmostEqual(
            score.WN_FUNDS + score.WN_WEIGHT + score.WN_TRAJECTORY,
            score.W_INSTITUTIONAL, places=9)

    def test_a_perfect_card_scores_exactly_100(self):
        perfect_insider = {
            "conviction": 1.0, "distinct_buyers": 9, "top_rank": 1.0,
            "notional": 50e6, "marketcap": 200e6,
            "frac_10b5_1": 0.0, "frac_corporate": 0.0,
        }
        perfect_dark = {
            "max_size_vs_avg30": 0.5, "window_notional": 500e6,
            "marketcap": 200e6, "print_count": 40, "clean_count": 30,
            "pressure": 1.0, "active_sessions": 10,
            "window_sessions": 10, "overlap_days": 3,
            "testable_days": ["2026-09-01", "2026-09-02", "2026-09-03"],
            "insider_days": ["2026-09-01", "2026-09-02", "2026-09-03"],
        }
        perfect_inst = {
            "distinct_funds": 9, "best_position_weight": 0.3,
            "trajectories": ["new_conviction"],
        }
        out = score.composite(perfect_insider, perfect_dark, perfect_inst)
        self.assertAlmostEqual(out["score"], 100.0, places=1)

    def test_no_leg_can_exceed_its_maximum(self):
        """Fuzz every component well past its anchors; nothing may overflow."""
        import random
        random.seed(7)
        for _ in range(4000):
            agg = {
                "conviction": random.uniform(-2, 4),
                "distinct_buyers": random.randint(0, 50),
                "top_rank": random.uniform(-1, 3),
                "notional": random.uniform(0, 1e10),
                "marketcap": random.choice([0, None, random.uniform(1e6, 1e13)]),
                "frac_10b5_1": random.uniform(-1, 2),
                "frac_corporate": random.uniform(-1, 2),
            }
            dp = {
                "max_size_vs_avg30": random.uniform(-1, 10),
                "window_notional": random.uniform(0, 1e11),
                "marketcap": agg["marketcap"], "print_count": random.randint(0, 500),
                "clean_count": random.randint(0, 500),
                "pressure": random.choice([None, random.uniform(-3, 3)]),
                "active_sessions": random.randint(0, 60),
                "window_sessions": random.randint(0, 60),
                "overlap_days": random.randint(0, 60),
                "testable_days": ["2026-09-0%d" % random.randint(1, 9)]
                                 * random.randint(0, 3),
                "insider_days": ["2026-09-0%d" % random.randint(1, 9)],
            }
            inst = {
                "distinct_funds": random.randint(0, 80),
                "best_position_weight": random.uniform(-1, 200),
                "trajectories": [random.choice(
                    ["new_conviction", "building", "steady", "volatile",
                     "harvesting", "closing", "nonsense"])],
            }
            out = score.composite(agg, dp, inst)
            self.assertLessEqual(out["score"], 100.0 + 1e-9)
            self.assertGreaterEqual(out["score"], -1e-9)
            for leg in out["legs"].values():
                self.assertLessEqual(leg["points"], leg["max"] + 1e-9)
                self.assertGreaterEqual(leg["points"], -1e-9)


# ================================================== the 13F backdrop rule

class TestBackdropNotGate(unittest.TestCase):
    def test_no_13f_caps_the_card_at_75(self):
        """
        The design is 'backdrop, not gate'. A perfect insider + dark pool read
        with no 13F support must still produce a card, and it must cap at
        exactly the two legs' combined weight.
        """
        agg = {"conviction": 1.0, "distinct_buyers": 9, "top_rank": 1.0,
               "notional": 50e6, "marketcap": 200e6,
               "frac_10b5_1": 0.0, "frac_corporate": 0.0}
        dp = {"max_size_vs_avg30": 0.5, "window_notional": 500e6,
              "marketcap": 200e6, "print_count": 40, "clean_count": 30,
              "pressure": 1.0, "active_sessions": 10,
              "window_sessions": 10, "overlap_days": 3,
              "testable_days": ["2026-09-01", "2026-09-02", "2026-09-03"],
              "insider_days": ["2026-09-01", "2026-09-02", "2026-09-03"]}
        out = score.composite(agg, dp, None)
        self.assertAlmostEqual(out["score"], 75.0, places=1)
        self.assertFalse(out["legs"]["institutional"]["measured"])
        self.assertFalse(out["all_three"])

    def test_institutional_absent_is_distinguishable_from_zero(self):
        """
        'This desk has no 13F data for the ticker' and 'funds are not buying
        it' are different facts and the card has to be able to say which.
        """
        points, _parts, measured = score.institutional_leg(None)
        self.assertEqual(points, 0.0)
        self.assertFalse(measured)
        points, _parts, measured = score.institutional_leg(
            {"distinct_funds": 1, "best_position_weight": 0.0, "trajectories": []})
        self.assertGreater(points, 0.0)
        self.assertTrue(measured)


# ============================================== the "N of M agree" trap

class TestNoAgreementCount(unittest.TestCase):
    def test_card_does_not_ship_an_agreement_count(self):
        """
        Swing Desk shipped 'N of M signals agree', gave it a materiality floor
        after a card read 5/5 on a score of 33, and the Insider Desk STILL had
        to delete its version because every card in the top 25 read 4 of 4.
        The trap is identical here: the insider leg is a precondition for a
        card existing, so it always 'agrees'. The card reports leg POINTS.
        """
        out = score.composite(
            {"conviction": 0.2, "distinct_buyers": 1, "top_rank": 0.45,
             "notional": 60e3, "marketcap": 1e9,
             "frac_10b5_1": 0.0, "frac_corporate": 0.0},
            {}, None)
        self.assertNotIn("agree", json_keys(out))
        self.assertIn("carried_by", out)
        self.assertIn("legs", out)

    def test_carried_by_names_the_leg_that_actually_paid(self):
        agg = {"conviction": 1.0, "distinct_buyers": 6, "top_rank": 1.0,
               "notional": 20e6, "marketcap": 300e6,
               "frac_10b5_1": 0.0, "frac_corporate": 0.0}
        out = score.composite(agg, {}, None)
        self.assertEqual(out["carried_by"], ["insider"])

    def test_all_three_requires_material_contribution_from_each(self):
        """
        A materiality floor is necessary but not sufficient (that is the whole
        lesson), so this flag is only used for the Discord gate and never as a
        quality claim on the card. It must still not fire on a token leg.
        """
        agg = {"conviction": 0.9, "distinct_buyers": 4, "top_rank": 0.95,
               "notional": 5e6, "marketcap": 500e6,
               "frac_10b5_1": 0.0, "frac_corporate": 0.0}
        barely_dark = {"max_size_vs_avg30": 0.0021, "window_notional": 1.0,
                       "marketcap": 500e6, "print_count": 1, "clean_count": 0,
                       "pressure": None, "active_sessions": 0,
                       "window_sessions": 8, "overlap_days": 0,
                       "testable_days": [], "insider_days": ["2026-09-01"]}
        inst = {"distinct_funds": 4, "best_position_weight": 0.05,
                "trajectories": ["new_conviction"]}
        out = score.composite(agg, barely_dark, inst)
        self.assertFalse(out["all_three"])


def json_keys(obj, prefix=""):
    keys = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(prefix + str(key))
            keys |= json_keys(value, prefix)
    elif isinstance(obj, list):
        for item in obj:
            keys |= json_keys(item, prefix)
    return keys


# ============================================ insider: role parsing order

class TestSeniority(unittest.TestCase):
    def test_vice_president_is_not_president(self):
        """
        `\\bPRESIDENT\\b` sitting above the vice-president patterns scored
        'Executive Vice President' and 'Vice-President' as President 0.82
        instead of EVP 0.64 / VP 0.55 -- about 1% of rows. A hyphen is a
        word boundary, so the hyphenated spelling matches too.
        """
        self.assertAlmostEqual(score.seniority("Executive Vice President"), 0.64)
        self.assertAlmostEqual(score.seniority("Vice-President"), 0.55)
        self.assertAlmostEqual(score.seniority("Vice President, Finance"), 0.55)
        self.assertAlmostEqual(score.seniority("Senior Vice President"), 0.58)
        self.assertAlmostEqual(score.seniority("EVP and CFO"), 0.80)  # CFO wins, listed first
        self.assertAlmostEqual(score.seniority("President"), 0.82)
        self.assertAlmostEqual(score.seniority("President & CEO"), 0.95)

    def test_seniority_is_ordered_the_way_a_reader_expects(self):
        ceo = score.seniority("Chief Executive Officer")
        pres = score.seniority("President")
        cfo = score.seniority("Chief Financial Officer")
        evp = score.seniority("Executive Vice President")
        svp = score.seniority("Senior Vice President")
        vp = score.seniority("Vice President")
        director = score.seniority("", is_director=True)
        self.assertGreater(ceo, pres)
        self.assertGreater(pres, cfo)
        self.assertGreater(cfo, evp)
        self.assertGreater(evp, svp)
        self.assertGreater(svp, vp)
        self.assertGreater(vp, director)

    def test_seniority_never_falls_below_the_floor(self):
        for title in ("", None, "   ", "Widget Wrangler", "???"):
            self.assertGreaterEqual(score.seniority(title), score.RANK_UNKNOWN)

    def test_ten_percent_owner_outranks_a_plain_director(self):
        self.assertGreater(
            score.seniority("", is_ten_percent_owner=True),
            score.seniority("", is_director=True))


# ============================================ insider: data landmines

class TestInsiderDataLandmines(unittest.TestCase):
    def test_negative_shares_owned_before_is_a_new_stake_not_a_negative_ratio(self):
        """
        Synthetic payload in the shape of /api/insider/transactions: a row
        (QRTX / EXAMPLE TRADING LP) with shares_owned_before = -7318. Dividing
        by it yields a NEGATIVE ratio, and sqrt of a negative is a crash.
        Treat it as a new stake.
        """
        value = score.conviction_component(12944, -7318)
        self.assertGreater(value, 0.0)
        self.assertLessEqual(value, 1.0)

    def test_zero_and_null_shares_owned_before_are_new_stakes(self):
        for before in (0, None, 0.0):
            self.assertAlmostEqual(score.conviction_component(5000, before), 0.85)

    def test_a_brand_new_stake_does_not_automatically_max_conviction(self):
        """
        A meaningful share of rows have no prior holding. Scoring those 1.0 would make
        'a director bought their first 100 shares' outrank 'the CEO doubled a
        five-million-share position'.
        """
        self.assertLess(score.conviction_component(100, 0), 1.0)

    def test_absurd_fill_price_falls_back_to_the_quote(self):
        """
        Synthetic payload in the shape of /api/insider/transactions: BLMP,
        'DOE JANE, 1,000 shares @ $487.00' against a stock_price of $4.21 -- a
        ~116x discrepancy that books a $487k purchase into a $390M company and
        rockets the card to the top.
        """
        price, flag = insider.fill_price(
            {"price": "487.0000", "stock_price": "4.21"})
        self.assertAlmostEqual(price, 4.21)
        self.assertEqual(flag, "suspect")

    def test_a_real_drawdown_is_not_treated_as_bad_data(self):
        """
        Synthetic VOLT row: fill 71.84, quote 59.10. That is a genuine ~18%
        decline, not a data error, and must keep the actual fill price.
        """
        price, flag = insider.fill_price(
            {"price": "71.8400", "stock_price": "59.1"})
        self.assertAlmostEqual(price, 71.84)
        self.assertEqual(flag, "")

    def test_null_fill_price_falls_back_to_the_quote(self):
        """
        `price` is null on ~0.5% of rows. Without the fallback the row books
        $0 of notional and drops silently through the dollar floor, which
        reads as 'no insider buying' rather than 'bad row'.
        """
        price, flag = insider.fill_price({"price": None, "stock_price": "12.5"})
        self.assertAlmostEqual(price, 12.5)
        self.assertEqual(flag, "fallback")

    def test_total_return_swaps_are_rejected_despite_common_stock_only(self):
        """
        `common_stock_only=true` is not sufficient: a 90-day sample through
        that filter returned 16 Total Return Swap rows and assorted LP/LLC
        units.
        """
        swap = {"transaction_code": "P", "security_ad_code": "DA",
                "security_title": "Total Return Swap", "ticker": "XYZ",
                "amount": 1000}
        ok, reason = insider.is_eligible(swap)
        self.assertFalse(ok)
        self.assertEqual(reason, "derivative_or_disposition")

    def test_derivative_acquisition_code_is_rejected(self):
        row = {"transaction_code": "P", "security_ad_code": "DA",
               "security_title": "Common Stock", "ticker": "XYZ", "amount": 100}
        self.assertFalse(insider.is_eligible(row)[0])

    def test_plain_common_stock_is_accepted(self):
        for title in ("Common Stock", "Class A Common Stock",
                      "Common Stock, $0.001 par value per share",
                      "COMMON STOCK", "Ordinary Shares"):
            row = {"transaction_code": "P", "security_ad_code": "NA",
                   "security_title": title, "ticker": "XYZ", "amount": 100}
            self.assertTrue(insider.is_eligible(row)[0], title)

    def test_sales_are_rejected(self):
        row = {"transaction_code": "S", "security_ad_code": "ND",
               "security_title": "Common Stock", "ticker": "XYZ", "amount": 100}
        self.assertFalse(insider.is_eligible(row)[0])


# ===================================================== insider aggregate

def _row(ticker, cik, name, shares, price, date, **kw):
    row = {
        "ticker": ticker, "reporter_cik": cik, "owner_name": name,
        "amount": shares, "price": str(price), "stock_price": str(price),
        "transaction_date": date, "transaction_code": "P",
        "security_ad_code": "NA", "security_title": "Common Stock",
        "marketcap": "500000000", "is_director": True, "is_officer": False,
        "is_ten_percent_owner": False, "reporter_is_public_company": False,
        "is_10b5_1": False, "officer_title": None, "shares_owned_before": 10000,
        "sector": "Industrials", "next_earnings_date": "2026-11-01",
    }
    row.update(kw)
    return row


class TestInsiderAggregate(unittest.TestCase):
    def test_filings_are_grouped_by_person_not_by_filing(self):
        """
        One company once carried 27 filings from 2 people. As a filing list that is the
        same two names eleven times over, and the stake percentages cannot be
        summed because each is measured against THAT filing's opening holding.
        """
        rows = [
            _row("ABC", "CIK1", "SMITH JANE", 5000, 10, "2026-09-01",
                 shares_owned_before=10000),
            _row("ABC", "CIK1", "SMITH JANE", 5000, 10, "2026-09-02",
                 shares_owned_before=15000),
            _row("ABC", "CIK1", "SMITH JANE", 5000, 10, "2026-09-03",
                 shares_owned_before=20000),
        ]
        out = insider.aggregate(rows)["ABC"]
        self.assertEqual(out["distinct_buyers"], 1)
        self.assertEqual(out["filings"], 3)
        self.assertEqual(len(out["people"]), 1)
        person = out["people"][0]
        self.assertEqual(person["shares"], 15000)
        # 15,000 bought against the 10,000 held before the FIRST purchase.
        self.assertAlmostEqual(person["stake_growth"], 1.0)

    def test_the_displayed_stake_growth_is_not_capped_at_100_percent(self):
        """
        THE SCORE clamps stake growth at 100% on purpose -- doubling a holding
        is already maximum conviction. THE DISPLAY must not inherit that clamp.

        Reconstructing the card's percentage out of the clamped score made
        every insider who had at least doubled read "+100%": a director who
        tripled their stake, one who quadrupled it, and one who merely doubled
        all showed the same number on the card AND in the share image. Caught
        by rendering the board and reading three identical "+100%" labels in a
        row next to three very different share counts.
        """
        rows = [
            _row("ABC", "CIK1", "DOUBLER", 10000, 10, "2026-09-01",
                 shares_owned_before=10000),
            _row("ABC", "CIK2", "QUADRUPLER", 30000, 10, "2026-09-01",
                 shares_owned_before=10000),
        ]
        people = {p["name"]: p for p in insider.aggregate(rows)["ABC"]["people"]}
        self.assertAlmostEqual(people["Doubler"]["stake_pct"], 100.0)
        self.assertAlmostEqual(people["Quadrupler"]["stake_pct"], 300.0)
        # ...while the SCORE component stays clamped for both
        self.assertAlmostEqual(people["Doubler"]["stake_growth"], 1.0)
        self.assertAlmostEqual(people["Quadrupler"]["stake_growth"], 1.0)

    def test_a_new_stake_has_no_percentage_to_display(self):
        rows = [_row("ABC", "CIK1", "FRESH", 10000, 10, "2026-09-01",
                     shares_owned_before=0)]
        person = insider.aggregate(rows)["ABC"]["people"][0]
        self.assertTrue(person["new_stake"])
        self.assertIsNone(person["stake_pct"])

    def test_the_dollar_floor_drops_token_buys(self):
        rows = [_row("TINY", "CIK1", "A B", 100, 10, "2026-09-01")]
        self.assertNotIn("TINY", insider.aggregate(rows))

    def test_conviction_takes_the_best_buyer_not_the_average(self):
        """
        One person tripling their holding IS the signal; averaging it against
        four directors' token buys erases exactly the thing we are looking for.
        """
        rows = [
            _row("ABC", "CIK1", "BIG BUYER", 30000, 10, "2026-09-01",
                 shares_owned_before=10000),
            _row("ABC", "CIK2", "TOKEN ONE", 100, 10, "2026-09-01",
                 shares_owned_before=1000000),
            _row("ABC", "CIK3", "TOKEN TWO", 100, 10, "2026-09-01",
                 shares_owned_before=1000000),
        ]
        out = insider.aggregate(rows)["ABC"]
        self.assertEqual(out["distinct_buyers"], 3)
        self.assertAlmostEqual(out["conviction"], 1.0)

    def test_a_richer_officer_title_on_a_later_filing_wins(self):
        rows = [
            _row("ABC", "CIK1", "JANE", 10000, 10, "2026-09-01", officer_title=""),
            _row("ABC", "CIK1", "JANE", 10000, 10, "2026-09-02",
                 officer_title="Chief Executive Officer"),
        ]
        out = insider.aggregate(rows)["ABC"]
        self.assertAlmostEqual(out["top_rank"], 0.95)

    def test_vs_fills_is_computed_for_display_and_never_enters_the_score(self):
        """
        'The stock is now 11% below what the CEO paid' is useful on a card and
        is on every one -- but scoring it systematically ranks falling knives
        to the top of the board.
        """
        rows = [_row("ABC", "CIK1", "JANE", 20000, 10, "2026-09-01",
                     stock_price="8")]
        out = insider.aggregate(rows)["ABC"]
        self.assertAlmostEqual(out["vs_fills_pct"], -20.0)
        points_down, _ = score.insider_leg(out)
        rows_up = [_row("ABC", "CIK1", "JANE", 20000, 10, "2026-09-01",
                        stock_price="12")]
        up = insider.aggregate(rows_up)["ABC"]
        points_up, _ = score.insider_leg(up)
        self.assertAlmostEqual(points_down, points_up, places=6)

    def test_modifiers_only_ever_multiply_down_and_in_proportion(self):
        base = {"conviction": 1.0, "distinct_buyers": 4, "top_rank": 1.0,
                "notional": 1e6, "marketcap": 1e9,
                "frac_10b5_1": 0.0, "frac_corporate": 0.0}
        full, _ = score.insider_leg(base)
        half = dict(base, frac_10b5_1=0.5)
        halved, _ = score.insider_leg(half)
        allplan = dict(base, frac_10b5_1=1.0)
        planned, _ = score.insider_leg(allplan)
        self.assertLess(halved, full)
        self.assertLess(planned, halved)
        self.assertAlmostEqual(planned, full * 0.75, places=6)


# ================================================ dark pool: dirty quotes

class TestSpreadPosition(unittest.TestCase):
    def test_a_print_at_the_bid_is_zero_and_at_the_ask_is_one(self):
        self.assertAlmostEqual(score.spread_position(10.00, 10.00, 10.10), 0.0)
        self.assertAlmostEqual(score.spread_position(10.10, 10.00, 10.10), 1.0)
        self.assertAlmostEqual(score.spread_position(10.05, 10.00, 10.10), 0.5)

    def test_a_stale_extended_hours_quote_is_refused(self):
        """
        Synthetic ZENO print, 2026-03-09 21:00Z: bid 53.20 / ask 58.29 -- a 9%
        spread on a stock that traded all day inside 30 cents. Scoring a print
        against that quote is inventing a signal out of quote noise.
        """
        self.assertIsNone(score.spread_position(56.94, 53.20, 58.29))

    def test_a_print_outside_the_nbbo_is_refused(self):
        """
        Synthetic ACME print, 2026-03-10: 5,000 @ 25.0403 against a 25.07
        bid. Below the bid is not 'maximum distribution', it is a print the
        quote does not describe.
        """
        self.assertIsNone(score.spread_position(25.0403, 25.07, 25.18))

    def test_crossed_or_missing_quotes_are_refused(self):
        self.assertIsNone(score.spread_position(10, 10.5, 10.0))
        self.assertIsNone(score.spread_position(10, None, 10.1))
        self.assertIsNone(score.spread_position(10, 0, 0))
        self.assertIsNone(score.spread_position("x", 10, 11))

    def test_prior_reference_price_prints_are_dropped_from_the_lean(self):
        """
        Synthetic ACME print: 3,740 @ 25.06 tagged prior_reference_price,
        against a 24.51/25.83 quote. The attached NBBO relates to an earlier
        moment, not to this fill.
        """
        rows = [{
            "price": "25.06", "nbbo_bid": "24.51", "nbbo_ask": "25.83",
            "sale_cond_codes": "prior_reference_price",
            "ext_hour_sold_codes": None, "canceled": False, "premium": "93724",
        }]
        self.assertEqual(darkpool.clean_prints(rows), [])

    def test_extended_hours_prints_are_dropped_from_the_lean(self):
        rows = [{
            "price": "10.05", "nbbo_bid": "10.00", "nbbo_ask": "10.10",
            "sale_cond_codes": None,
            "ext_hour_sold_codes": "extended_hours_trade",
            "canceled": False, "premium": "100000",
        }]
        self.assertEqual(darkpool.clean_prints(rows), [])

    def test_canceled_prints_are_dropped(self):
        rows = [{
            "price": "10.05", "nbbo_bid": "10.00", "nbbo_ask": "10.10",
            "sale_cond_codes": None, "ext_hour_sold_codes": None,
            "canceled": True, "premium": "100000",
        }]
        self.assertEqual(darkpool.clean_prints(rows), [])


class TestLeanComponent(unittest.TestCase):
    """
    The lean is premium share at the OFFER minus premium share at the BID --
    not a weighted mean spread position. The mean version scores ACME and
    ZENO identically (~0.49) despite completely different distributions and
    had to be thrown away; `test_the_mean_that_was_replaced_...` below is the
    regression test for that.
    """

    def test_too_few_clean_prints_is_unmeasured_not_zero(self):
        """
        A thin name must read 'not enough clean prints' on the card, not
        'distribution'. Zero and unknown are different claims.
        """
        self.assertIs(score.lean_component(0.9, clean_count=2), score.UNMEASURED)
        self.assertIs(score.lean_component(0.9, clean_count=5), score.UNMEASURED)
        self.assertIsNotNone(score.lean_component(0.9, clean_count=6))

    def test_balanced_pressure_earns_almost_nothing(self):
        """
        Equal premium at the bid and the offer is the neutral default, not
        evidence of accumulation. It must score near zero or the component
        pays every ticker a participation fee.
        """
        self.assertLessEqual(score.lean_component(0.0, 10), 0.25)

    def test_bid_heavy_pressure_scores_zero(self):
        self.assertAlmostEqual(score.lean_component(-0.5, 10), 0.0)
        self.assertAlmostEqual(score.lean_component(-1.0, 10), 0.0)

    def test_the_lean_is_monotone_in_pressure(self):
        prev = -1.0
        for pressure in [-1.0, -0.5, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
            value = score.lean_component(pressure, 10)
            self.assertGreaterEqual(value, prev)
            prev = value

    def test_the_mean_that_was_replaced_cannot_separate_acme_from_zeno(self):
        """
        THE BUG THIS COMPONENT WAS REBUILT AROUND.

        ACME (visibly working size, bimodal: a cluster at the offer and a
        cluster at the bid, price 21.26 -> 25.88 across the window) and ZENO
        (flat, lower-middle of the spread, nothing at the offer) return the
        same premium-weighted mean spread position to two decimals. Any curve
        over the mean therefore scores them the same, which is points of
        model that could never pay out.

        The pressure statistic must separate them. This test asserts BOTH
        halves: that the mean really is degenerate on this tape shape, and
        that the replacement is not.
        """
        from fixtures import sample_prints as sp

        acme = darkpool.aggregate(sp.acme(), marketcap=sp.ACME_MARKETCAP)
        zeno = darkpool.aggregate(sp.zeno(), marketcap=sp.ZENO_MARKETCAP)

        self.assertAlmostEqual(acme["weighted_position"],
                               zeno["weighted_position"], places=2)
        self.assertNotAlmostEqual(acme["pressure"], zeno["pressure"], places=2)

    def test_sample_prints_produce_different_dark_pool_legs(self):
        """
        Whatever the lean does, the two names must not end up with the same
        dark pool leg: ACME is active on 5 of 8 sessions and ZENO on 1 of 6.
        """
        from fixtures import sample_prints as sp

        acme = darkpool.aggregate(sp.acme(), marketcap=sp.ACME_MARKETCAP)
        zeno = darkpool.aggregate(sp.zeno(), marketcap=sp.ZENO_MARKETCAP)
        acme_pts, _p, _m = score.darkpool_leg(acme)
        zeno_pts, _p, _m = score.darkpool_leg(zeno)
        self.assertGreater(acme_pts, zeno_pts + 1.5)
        # ...and the separation must come from the components that are
        # supposed to carry it, not from a coin flip on the lean.
        self.assertGreater(acme["active_sessions"], zeno["active_sessions"])
        self.assertGreater(acme["max_size_vs_avg30"], 0.02)

    def test_the_activity_floor_is_reachable_by_an_ordinary_liquid_name(self):
        """
        THE BUG THAT KILLED 14 OF 30 DARK POOL POINTS ON THE FIRST BOARD.

        The floor shipped at 5% of 30-day average volume, eyeballed on the
        most block-heavy kind of name. An ordinary liquid mid-cap runs well
        under that, so the floor was unreachable and `sustain` scored ZERO on
        every card, taking `proximity` with it.

        This asserts the floor is set below a normal ticker's typical session,
        using the synthetic QRTX tape (an ordinary liquid mid-cap).
        """
        from fixtures import sample_prints as sp
        qrtx = darkpool.aggregate(sp.qrtx(), marketcap=sp.QRTX_MARKETCAP)
        self.assertGreater(qrtx["active_sessions"], 0,
                           "an ordinary liquid name must be able to register "
                           "an active session at all")
        self.assertLess(qrtx["active_sessions"], qrtx["window_sessions"],
                        "...but not every session, or it discriminates nothing")

    def test_the_three_sample_names_order_the_way_the_tape_reads(self):
        """
        THE REGRESSION TEST FOR THE WHOLE ACTIVITY-FLOOR SAGA.

        Three synthetic tickers, three different characters, and the component
        has to separate them without saturating at either end:

          ACME  visibly working size, price walking 21.26 -> 25.88
          QRTX  ordinary liquid mid-cap with one genuine standout session
          ZENO  flat, nothing printing at the offer

        v1 (5% of 30-day average) gave 0 active on the whole board.
        v2 (2% of the same) saturated the working name and the flat one alike.
        v3 (1.3x own median) punished consistency.
        """
        from fixtures import sample_prints as sp
        acme = darkpool.aggregate(sp.acme(), marketcap=sp.ACME_MARKETCAP)
        qrtx = darkpool.aggregate(sp.qrtx(), marketcap=sp.QRTX_MARKETCAP)
        zeno = darkpool.aggregate(sp.zeno(), marketcap=sp.ZENO_MARKETCAP)

        p, _pp, _pm = score.darkpool_leg(acme)
        h, _hp, _hm = score.darkpool_leg(qrtx)
        w, _wp, _wm = score.darkpool_leg(zeno)
        self.assertGreater(p, h)
        self.assertGreater(h, w)
        # ~7.3 points of the 24 available here (proximity is UNMEASURED for
        # all three, since no insider dates are passed) -- a real spread, and
        # far from the 1.7 the saturating v2 floor produced.
        self.assertGreater(p - w, 6.0, "the spread must stay meaningful")

        # and nothing saturates
        for agg in (acme, qrtx, zeno):
            self.assertGreater(agg["window_sessions"], 0)
            self.assertLess(agg["active_sessions"], agg["window_sessions"])


class TestSustain(unittest.TestCase):
    def test_sessions_not_prints_drive_the_sustain_component(self):
        """
        A sample count is not an evidence count: the GEX desk once called a
        hit rate meaningful at n=117 when all 117 came from one afternoon of
        overlapping windows -- about one independent observation. Forty prints
        in one frantic hour is one session.
        """
        one_busy_day = score.sustain_component(1, 8)
        four_quiet_days = score.sustain_component(4, 8)
        self.assertGreater(four_quiet_days, one_busy_day * 2)

    def test_no_activity_scores_zero(self):
        self.assertEqual(score.sustain_component(0, 8), 0.0)


class TestProximity(unittest.TestCase):
    def test_nothing_testable_is_unmeasured_not_zero(self):
        """
        The insider window is 45 days; one dark pool call reaches back about 8
        SESSIONS. Most insider dates on a card therefore have no off-exchange
        data anywhere near them. Scoring those as misses reads as "the blocks
        did not line up" when the truth is "there was nothing to line them up
        against" -- and on the first board that silent zero was on every
        card.
        """
        self.assertIs(score.proximity_component(5, []), score.UNMEASURED)
        self.assertIs(score.proximity_component(0, None), score.UNMEASURED)

    def test_full_overlap_scores_one(self):
        self.assertAlmostEqual(
            score.proximity_component(3, ["a", "b", "c"]), 1.0)

    def test_only_insider_dates_inside_the_dark_pool_window_are_testable(self):
        """An insider buy three weeks before the oldest print is not a miss."""
        rows = [{"executed_at": "2026-09-%02dT15:30:00Z" % d, "size": 30000,
                 "price": "40", "premium": "1200000", "avg30_volume": "200000",
                 "nbbo_bid": "39.95", "nbbo_ask": "40.05", "canceled": False,
                 "ext_hour_sold_codes": None, "sale_cond_codes": None}
                for d in (8, 9, 10)]
        near = darkpool.aggregate(rows, marketcap=9e8,
                                  insider_dates=[dt.date(2026, 9, 9)])
        self.assertEqual(near["testable_days"], ["2026-09-09"])
        far = darkpool.aggregate(rows, marketcap=9e8,
                                 insider_dates=[dt.date(2026, 8, 1)])
        self.assertEqual(far["testable_days"], [])
        self.assertIs(score.proximity_component(far["overlap_days"],
                                                far["testable_days"]),
                      score.UNMEASURED)


class TestDarkPoolAggregate(unittest.TestCase):
    def _print(self, day, size, price, premium=None, bid=None, ask=None,
               ext=None, cond=None, avg30=200000, volume=500000):
        return {
            "executed_at": "%sT15:30:00Z" % day, "size": size,
            "price": str(price), "premium": str(premium if premium is not None
                                                else size * price),
            "avg30_volume": str(avg30), "nbbo_bid": str(bid if bid else price - 0.05),
            "nbbo_ask": str(ask if ask else price + 0.05),
            "ext_hour_sold_codes": ext, "sale_cond_codes": cond,
            "canceled": False, "volume": volume,
        }

    def test_a_utc_evening_print_belongs_to_that_days_session(self):
        """
        Synthetic ACME print stamped 2026-03-04T23:52Z -- 6:52pm ET on the
        4th, not the 5th. Bucketing it on the calendar date would scatter one
        session across two and inflate the sustain component.
        """
        row = {"executed_at": "2026-03-04T23:52:40Z"}
        self.assertEqual(darkpool._session_date(row), dt.date(2026, 3, 4))

    def test_a_morning_print_stays_on_its_own_day(self):
        row = {"executed_at": "2026-09-10T13:31:00Z"}
        self.assertEqual(darkpool._session_date(row), dt.date(2026, 9, 10))

    def test_avg30_volume_comes_from_the_rows_and_needs_no_extra_call(self):
        rows = [self._print("2026-09-10", 5000, 40.0, avg30=274913)]
        out = darkpool.aggregate(rows, marketcap=900e6)
        self.assertAlmostEqual(out["avg30_volume"], 274913)
        self.assertAlmostEqual(out["max_size_vs_avg30"], 5000 / 274913.0, places=6)

    def test_multi_session_activity_is_counted_in_distinct_sessions(self):
        rows = [self._print("2026-09-%02d" % d, 60000, 40.0, volume=500000)
                for d in (8, 9, 10)]          # 12% of each day's volume
        out = darkpool.aggregate(rows, marketcap=900e6)
        self.assertEqual(out["window_sessions"], 3)
        self.assertEqual(out["active_sessions"], 3)

    def test_a_session_is_measured_against_its_own_volume(self):
        """
        The same off-exchange size is a big deal on a quiet day and nothing on
        a busy one. Numerator and denominator must describe the SAME session --
        that is the whole fix for the floor that killed sustain on the first
        board.
        """
        rows = [self._print("2026-09-09", 30000, 40.0, volume=200000),
                self._print("2026-09-10", 30000, 40.0, volume=5_000_000)]
        out = darkpool.aggregate(rows, marketcap=900e6)
        by_day = {s["date"]: s for s in out["sessions"]}
        self.assertTrue(by_day["2026-09-09"]["active"])    # 15% of the day
        self.assertFalse(by_day["2026-09-10"]["active"])   # 0.6% of the day
        self.assertEqual(out["active_sessions"], 1)

    def test_the_newest_session_is_flagged_partial(self):
        """
        The current session is still forming, so its volume is partial and its
        share runs high -- a liquid mid-cap can read ~20% mid-morning against
        a ~2% typical.
        It still counts, but the card has to be able to say why it looks hot.
        """
        rows = [self._print("2026-09-%02d" % d, 60000, 40.0) for d in (9, 10)]
        out = darkpool.aggregate(rows, marketcap=900e6)
        newest = out["sessions"][0]
        self.assertEqual(newest["date"], "2026-09-10")
        self.assertTrue(newest["partial"])
        self.assertFalse(out["sessions"][1]["partial"])

    def test_day_volume_falls_back_to_avg30_when_absent(self):
        rows = [self._print("2026-09-10", 30000, 40.0, avg30=200000)]
        for r in rows:
            r.pop("volume")
        out = darkpool.aggregate(rows, marketcap=900e6)
        self.assertTrue(out["sessions"][0]["active"])   # 15% of avg30

    def test_a_dead_session_does_not_count_as_activity(self):
        rows = [self._print("2026-09-10", 100, 40.0, avg30=2_000_000)]
        out = darkpool.aggregate(rows, marketcap=900e6)
        self.assertEqual(out["active_sessions"], 0)

    def test_proximity_matches_insider_dates_to_active_sessions(self):
        rows = [self._print("2026-09-%02d" % d, 60000, 40.0) for d in (8, 9, 10)]
        out = darkpool.aggregate(
            rows, marketcap=900e6, insider_dates=[dt.date(2026, 9, 9)],
            proximity_days=5)
        self.assertEqual(out["overlap_days"], 1)
        self.assertEqual(out["testable_days"], ["2026-09-09"])
        # An insider date far outside the dark pool window is UNMEASURABLE,
        # not a miss -- there is no off-exchange data to compare it against.
        far = darkpool.aggregate(
            rows, marketcap=900e6, insider_dates=[dt.date(2026, 7, 1)],
            proximity_days=5)
        self.assertEqual(far["overlap_days"], 0)
        self.assertEqual(far["testable_days"], [])

    def test_dirty_prints_still_count_for_size_but_not_for_the_lean(self):
        rows = [
            self._print("2026-09-10", 30000, 40.0, cond="prior_reference_price"),
            self._print("2026-09-09", 30000, 40.0, ext="extended_hours_trade"),
        ]
        out = darkpool.aggregate(rows, marketcap=900e6)
        self.assertEqual(out["print_count"], 2)
        self.assertEqual(out["clean_count"], 0)
        self.assertIsNone(out["weighted_position"])

    def test_an_empty_payload_does_not_crash(self):
        out = darkpool.aggregate([], marketcap=900e6)
        self.assertEqual(out["print_count"], 0)
        points, _parts, measured = score.darkpool_leg(out)
        self.assertEqual(points, 0.0)
        self.assertFalse(measured["block"])

    def test_the_window_is_bounded_in_sessions(self):
        rows = [self._print("2026-08-%02d" % d, 30000, 40.0)
                for d in range(10, 29)]
        out = darkpool.aggregate(rows, marketcap=900e6, window_days=5)
        self.assertEqual(out["window_sessions"], 5)


# ============================================== the 13F backdrop reader

class TestInstitutionalCoverage(unittest.TestCase):
    def test_fund_count_is_funds_in_the_backdrop_not_every_filer(self):
        """
        `institutions` holds every 13F filer the Institutional Desk ever paged
        (~8,900 of them). The board tile reporting that number read
        "8,922 small funds", which implies the whole 13F universe was screened
        in -- when the entire point of inheriting that desk's screen is that it
        is narrow. Count the funds that actually appear in the bullish events.
        """
        import idb, sqlite3, tempfile, os
        path = os.path.join(tempfile.mkdtemp(), "i.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE institutions (cik TEXT, name TEXT, short_name TEXT);
            CREATE TABLE events (report_date TEXT, cik TEXT, ticker TEXT,
                kind TEXT, units INT, units_change INT, change_perc REAL,
                value REAL, weight REAL, trajectory TEXT, score REAL);
        """)
        conn.execute("INSERT INTO meta VALUES ('last_report_date','2026-06-30')")
        # 500 filers in the table, but only 2 of them are buying anything
        conn.executemany("INSERT INTO institutions VALUES (?,?,?)",
                         [("c%d" % i, "FUND %d LLC" % i, None) for i in range(500)])
        conn.executemany(
            "INSERT INTO events VALUES ('2026-06-30',?,?,?,1,1,0.1,1e6,0.03,"
            "'new_conviction',70)",
            [("c1", "AAA", "NEW"), ("c2", "AAA", "ADD"),
             ("c1", "BBB", "NEW"), ("c3", "CCC", "EXIT")])
        conn.commit(); conn.close()

        inst = idb.Institutional(path)
        cov = inst.coverage()
        self.assertTrue(cov["available"])
        self.assertEqual(cov["tickers"], 2)      # CCC was an EXIT, not bullish
        self.assertEqual(cov["funds"], 2)        # NOT 500, and NOT 3
        self.assertEqual(inst.get("AAA")["distinct_funds"], 2)
        self.assertIsNone(inst.get("CCC"), "an EXIT is not bullish coverage")
        self.assertIsNone(inst.get("ZZZ"))

    def test_a_missing_database_is_survivable(self):
        import idb
        inst = idb.Institutional("/nonexistent/institutional.db")
        self.assertFalse(inst.available)
        self.assertIsNone(inst.get("AAA"))
        self.assertIn("not found", inst.coverage()["error"])

    def test_an_explicit_path_never_falls_back_to_a_different_database(self):
        """
        Naming a database that is not there must FAIL, not quietly load
        whichever one happens to be sitting next door -- reading a different
        quarter than the one you asked for is worse than reading nothing.

        This test passed in the build container, where no sibling database
        exists and the fallback therefore found nothing, and failed the first
        time the suite ran on a machine where one did. A
        fallback can only be tested somewhere its target exists.
        """
        import idb
        self.assertIsNone(idb.locate("/nonexistent/institutional.db"))

    def test_shouting_fund_names_are_title_cased(self):
        import idb
        self.assertEqual(idb._titlecase("EXAMPLE CAPITAL MANAGEMENT, LLC"),
                         "Example Capital Management, LLC")
        self.assertEqual(idb._titlecase("Placeholder Partners Management, L.P."),
                         "Placeholder Partners Management, L.P.")


# ===================================================== env parsing

class TestEnvParsing(unittest.TestCase):
    def test_env_handles_crlf_bom_quotes_padding_and_comments(self):
        import tempfile
        body = ("﻿# comment\r\n"
                "UW_API_TOKEN = abc=def \r\n"
                'DISCORD_WEBHOOK_URL="https://x/y"\r\n'
                "\r\n"
                "EMPTY=\r\n")
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(body)
            path = fh.name
        try:
            parsed = uw._parse_env_file(path)
        finally:
            os.unlink(path)
        # split on the FIRST '=' only -- a token can contain '='
        self.assertEqual(parsed["UW_API_TOKEN"], "abc=def")
        self.assertEqual(parsed["DISCORD_WEBHOOK_URL"], "https://x/y")
        self.assertEqual(parsed["EMPTY"], "")

    def test_the_shipped_env_example_parses_and_coerces(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".env.example")
        if not os.path.isfile(path):
            self.skipTest(".env.example not in this copy (GitHub's web upload leaves out dot-files)")
        parsed = uw._parse_env_file(path)
        self.assertIn("UW_API_TOKEN", parsed)
        for key, value in parsed.items():
            if key.startswith("CDESK_") and value:
                if value.lower() in ("true", "false"):
                    continue
                float(value)      # must coerce, or the app dies on launch


# ============================================================ coercion

class TestCoercion(unittest.TestCase):
    def test_numeric_strings_coerce(self):
        self.assertAlmostEqual(uw.num("73204418.00"), 73204418.0)
        self.assertAlmostEqual(uw.num("1,234.5"), 1234.5)

    def test_nulls_and_junk_coerce_to_the_default(self):
        for junk in (None, "", "none", "null", "NaN", "abc", [], {}):
            self.assertEqual(uw.num(junk, -1.0), -1.0)

    def test_opt_num_preserves_the_null_distinction(self):
        self.assertIsNone(uw.opt_num(None))
        self.assertIsNone(uw.opt_num(""))
        self.assertEqual(uw.opt_num("0"), 0.0)

    def test_unwrap_handles_every_envelope_shape(self):
        self.assertEqual(uw.unwrap({"data": [1, 2]}), [1, 2])
        self.assertEqual(uw.unwrap({"result": [1]}), [1])
        self.assertEqual(uw.unwrap({"results": [3]}), [3])
        self.assertEqual(uw.unwrap([1, 2, 3]), [1, 2, 3])
        self.assertEqual(uw.unwrap({}), [])
        self.assertEqual(uw.unwrap(None), [])
        self.assertEqual(uw.unwrap({"ticker": "X"}), [{"ticker": "X"}])
        self.assertEqual(len(uw.unwrap({"data": list(range(400))})), 400)


# ===================================================== curve sanity

class TestCurves(unittest.TestCase):
    def test_lerp_never_extrapolates(self):
        curve = [(1.0, 0.0), (10.0, 0.5), (100.0, 1.0)]
        self.assertEqual(score.lerp_curve(-5, curve), 0.0)
        self.assertEqual(score.lerp_curve(1e9, curve), 1.0)
        self.assertAlmostEqual(score.lerp_curve(5.5, curve), 0.25)

    def test_every_component_is_monotone_in_its_input(self):
        checks = [
            (score.conviction_component, [(100, 10000), (1000, 10000),
                                          (5000, 10000), (10000, 10000)]),
            (score.size_component, [(50e3, 1e9), (500e3, 1e9),
                                    (5e6, 1e9), (50e6, 1e9)]),
            (score.block_component, [(0.001, 0, 1e9), (0.01, 0, 1e9),
                                     (0.05, 0, 1e9), (0.2, 0, 1e9)]),
            (score.weight_component, [(0.001,), (0.01,), (0.05,), (0.2,)]),
        ]
        for fn, args_list in checks:
            prev = -1.0
            for args in args_list:
                value = fn(*args)
                self.assertGreaterEqual(value, prev - 1e-12, "%s %s" % (fn, args))
                prev = value

    def test_cluster_table_is_monotone_and_caps_at_one(self):
        prev = -1.0
        for n in range(0, 12):
            value = score.cluster_component(n)
            self.assertGreaterEqual(value, prev)
            self.assertLessEqual(value, 1.0)
            prev = value


if __name__ == "__main__":
    unittest.main(verbosity=2)
