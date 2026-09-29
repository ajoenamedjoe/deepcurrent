"""
Model tests. Each is named after the trap it guards; most run on synthetic payloads
in the shape of the UW statements endpoints (tests/fixtures), built to carry each trap.

Run:  python -m unittest discover -s tests -v
"""
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import valuation as V                      # noqa: E402
import sample_qrtx_acme as A                 # noqa: E402
import sample_volt_blmp_zeno as B               # noqa: E402

SETS = {
    "QRTX": dict(info=A.QRTX_INFO, is_a=A.QRTX_IS_A, is_q=A.QRTX_IS_Q, bs_q=A.QRTX_BS_Q, cf_q=A.QRTX_CF_Q),
    "ACME": dict(info=A.ACME_INFO, is_a=A.ACME_IS_A, bs_a=A.ACME_BS_A, cf_a=A.ACME_CF_A),
    "VOLT": dict(info=B.VOLT_INFO, is_a=B.VOLT_IS_A, bs_a=B.VOLT_BS_A, cf_a=B.VOLT_CF_A),
    "BLMP": dict(info=B.BLMP_INFO, is_a=B.BLMP_IS_A, cf_a=B.BLMP_CF_A),
    "ZENO": dict(info=B.ZENO_INFO, is_a=B.ZENO_IS_A),
}
FX = {"TWD": 32.0}


def run(t):
    return V.run(SETS[t], fx=FX)


import contextlib  # noqa: E402


@contextlib.contextmanager
def legacy():
    """Model 3.0 behaviour (all 3.1 options off) for tests of the older mechanics."""
    old = dict(V.OPTIONS)
    V.OPTIONS.update(fixed_discount=False, trend_margin=False, cycle_aware=False)
    try:
        yield
    finally:
        V.OPTIONS.clear(); V.OPTIONS.update(old)


class Coercion(unittest.TestCase):
    def test_numbers_arrive_as_strings_and_null(self):
        self.assertEqual(V.num("73104289.00"), 73104289.0)
        self.assertIsNone(V.num(None))
        self.assertIsNone(V.num(""))
        self.assertIsNone(V.num("null"))
        self.assertIsNone(V.num(float("nan")))
        self.assertIsNone(V.num(True))
        self.assertEqual(V.num("1,234"), 1234.0)

    def test_envelopes_data_result_bare(self):
        row = {"fiscal_date_ending": "2025-12-31", "x": "1"}
        for payload in ({"data": [row]}, {"result": [row]}, [row], row, {"data": {"result": [row]}}):
            self.assertEqual(len(V.rows_of(payload)), 1, payload)
        self.assertEqual(V.rows_of(None), [])
        self.assertEqual(V.info_of({"data": {"symbol": "X"}, "price": "2"})["price"], "2")
        self.assertEqual(V.info_of({"symbol": "X", "price": "2"})["symbol"], "X")


class Extraction(unittest.TestCase):
    def test_ttm_used_when_quarters_newer_than_annual(self):
        f = run("QRTX")["fin"]
        self.assertEqual(f["basis"], "TTM to 2026-05-31")
        self.assertAlmostEqual(f["revenue"], 87210000 + 80406000 + 84681000 + 30270000)

    def test_annual_used_without_quarters(self):
        f = run("ACME")["fin"]
        self.assertEqual(f["basis"], "FY2026")
        self.assertAlmostEqual(f["rev_growth"], 742318 / 655207 - 1, places=6)

    def test_ebitda_is_operating_income_plus_da_never_api_ebit(self):
        f = run("QRTX")["fin"]
        # the API's ebitda for these four quarters sums to ~+1.41B; the truth is deeply negative
        self.assertLess(f["ebitda"], 0)
        op = -355850000 - 249853000 - 279526000 - 148502000
        da = 51049000 + 33634000 + 51183000 + 14340000
        self.assertAlmostEqual(f["ebitda"], op + da)

    def test_collapsed_cash_field_adds_short_term_investments(self):
        f = run("QRTX")["fin"]
        self.assertAlmostEqual(f["liquid"], 1015435000 + 1112661000)
        f = run("ACME")["fin"]
        self.assertAlmostEqual(f["liquid"], 95888000000 + 27995000000)

    def test_capex_sign_normalised(self):
        f = run("ACME")["fin"]
        self.assertGreater(f["capex"], 0)
        self.assertAlmostEqual(f["fcf"], 147902e6 - 139655e6)

    def test_owner_fcf_never_charges_more_than_actual_capex(self):
        f = run("ACME")["fin"]
        # maintenance = min(capex, D&A) -> OCF - D&A here
        self.assertLessEqual(f["capex"] - (f["ocf"] - f["owner_fcf"]), f["capex"])
        self.assertGreaterEqual(f["owner_fcf"], f["fcf"])

    def test_non_usd_reporter_refuses_without_fx(self):
        with self.assertRaises(ValueError) as cm:
            V.run(SETS["ZENO"])
        self.assertIn("VDESK_FX_TWD", str(cm.exception))

    def test_fx_converts_statements(self):
        f = run("ZENO")["fin"]
        self.assertAlmostEqual(f["revenue"], 3412806551000 / 32.0)
        self.assertEqual(f["currency"], "TWD")

    def test_adr_shares_are_adr_units_from_info(self):
        f = run("ZENO")["fin"]
        self.assertEqual(f["shares"], 4873219506.0)          # ADR units, not ~24.4B ordinary
        self.assertAlmostEqual(f["marketcap"], 4873219506.0 * 386.2217)

    def test_stale_marketcap_is_recomputed_and_reported(self):
        f = run("QRTX")["fin"]
        # UW cap 10.96B implies 284M shares at the quote; outstanding is 319M
        self.assertEqual(f["shares"], 318774062.0)
        self.assertAlmostEqual(f["marketcap"], 318774062.0 * 38.66)
        self.assertTrue(any("stale" in q for q in f["quality"]))

    def test_missing_cash_flow_is_not_cash_burn(self):
        r = run("ZENO")
        self.assertTrue(r["fin"]["cash_proxy"])
        self.assertNotIn(r["class"], ("growth_burn", "venture", "turnaround"))
        self.assertFalse(any(f["key"] == "runway" for f in r["flags"]))

    def test_missing_balance_sheet_is_unmeasured_not_zero(self):
        f = run("BLMP")["fin"]
        self.assertIsNone(f["net_cash"])
        self.assertIsNone(f["liquid"])
        self.assertTrue(any("UNMEASURED" in q for q in f["quality"]))

    def test_margin_history_normalises_cyclical_fcf(self):
        f = run("VOLT")["fin"]
        reps = [h["rep"] for h in f["margin_hist"]]
        self.assertEqual(len(reps), 3)
        self.assertLess(min(reps), 0.01)     # newest year: 0.8%
        self.assertGreater(max(reps), 0.20)  # oldest year: 24.8%

    def test_invalid_ticker_is_a_sentence_not_a_crash(self):
        with self.assertRaises(ValueError):
            V.run({"info": {}})
        with self.assertRaises(ValueError):
            V.run({"info": A.QRTX_INFO})


class Classification(unittest.TestCase):
    def test_sample_classes(self):
        self.assertEqual(run("QRTX")["class"], "venture")
        self.assertEqual(run("ACME")["class"], "fcf")
        self.assertEqual(run("VOLT")["class"], "utility")
        self.assertEqual(run("BLMP")["class"], "financial")

    def test_bank_is_financial_and_never_gets_runway_flag(self):
        r = run("BLMP")
        self.assertFalse(any(f["key"] in ("runway", "funding") for f in r["flags"]))
        self.assertEqual(r["valuation"]["net_cash_used"], 0.0)

    def test_payment_network_in_financial_sector_is_not_a_bank(self):
        fin = run("ACME")["fin"]
        fin = dict(fin, sector="Financial Services")
        self.assertNotEqual(V.classify(fin)[0], "financial")

    def test_pe_not_meaningful_on_operating_loss(self):
        m = run("QRTX")["multiples"]
        self.assertFalse(m["pe_meaningful"])


class Engine(unittest.TestCase):
    def test_scenarios_never_cross(self):
        rnd = random.Random(7)
        for _ in range(400):
            t = rnd.choice(list(SETS))
            fin = dict(run(t)["fin"])
            fin["rev_growth"] = rnd.uniform(-0.3, 4.0)
            cls = V.classify(fin)[0]
            g, _, _ = V.scenario_growth(fin, cls)
            self.assertGreaterEqual(g["bull"], g["base"])
            self.assertGreaterEqual(g["base"], g["bear"])
            val = V.value(fin, cls)
            s = val["scenarios"]
            self.assertGreaterEqual(s["bull"]["per_share"] + 1e-9, s["base"]["per_share"], (t, fin["rev_growth"]))
            self.assertGreaterEqual(s["base"]["per_share"] + 1e-9, s["bear"]["per_share"], (t, fin["rev_growth"]))

    def test_negative_cash_flow_is_never_compounded(self):
        fin = dict(run("ACME")["fin"], fcf_margin=-0.05, margin_hist=[])
        m = V.start_margins(fin, "fcf")
        self.assertGreaterEqual(m["bear"][1], 0.0)     # ramps to a floor, not -5% forever
        slow = V.dcf(100.0, 0.05, -0.05, -0.05, 0.10, 0.02)
        fast = V.dcf(100.0, 0.50, -0.05, -0.05, 0.10, 0.02)
        # this is what compounding a negative looks like -- and why start_margins never does it
        self.assertLess(fast["ev"], slow["ev"])

    def test_terminal_value_zero_when_final_cf_negative(self):
        d = V.dcf(100.0, 0.1, -0.5, -0.1, 0.1, 0.02)
        self.assertEqual(d["tv"], 0.0)

    def test_dcf_matches_hand_calc(self):
        d = V.dcf(100.0, 0.10, 0.10, 0.10, 0.10, 0.02, fade=0)     # the 1.x 5-year engine
        cf = [100 * 1.1 ** t * 0.1 for t in range(1, 6)]
        pv = sum(c / 1.1 ** t for t, c in enumerate(cf, 1))
        tv = cf[-1] * 1.02 / 0.08
        self.assertAlmostEqual(d["ev"], pv + tv / 1.1 ** 5, places=9)

    def test_per_share_floored_at_zero_and_flagged(self):
        s = run("VOLT")["valuation"]["scenarios"]["bear"]
        self.assertEqual(s["per_share"], 0.0)
        self.assertTrue(s["floored"])
        self.assertLess(s["equity"], 0)

    def test_growth_position_follows_trailing_growth(self):
        fin = dict(run("ACME")["fin"])
        lo = V.scenario_growth(dict(fin, rev_growth=0.10), "fcf")[0]["base"]
        hi = V.scenario_growth(dict(fin, rev_growth=0.15), "fcf")[0]["base"]
        self.assertAlmostEqual(lo, 0.10)
        self.assertAlmostEqual(hi, 0.15)

    def test_discount_rate_stays_in_class_band(self):
        for t in SETS:
            r = run(t)
            lo, hi = V.CLASSES[r["class"]]["discount"]
            self.assertTrue(lo <= r["valuation"]["discount"] <= hi, t)


class Scoring(unittest.TestCase):
    def _val(self, bull, base, bear):
        # per_share is implied by the upside, so the 3.2 margin-of-safety gate sees a real value
        p = run("ACME")["fin"]["price"]
        return {"scenarios": {k: {"upside": u, "per_share": p * (1 + u)}
                              for k, u in (("bull", bull), ("base", base), ("bear", bear))}}

    def test_score_slope_uses_whole_downside(self):
        f = run("ACME")["fin"]
        # scale 2.0: 10 = deeply undervalued, 0 = avoid
        self.assertEqual(V.score(f, "fcf", self._val(0.5, -1.0, -1.0), [])["score"], 0.0)
        self.assertEqual(V.score(f, "fcf", self._val(0.5, 0.0, -0.5), [])["score"], 5.0)
        self.assertEqual(V.score(f, "fcf", self._val(2.0, 1.0, -0.2), [])["score"], 10.0)
        # the first draft's slope of 10 made these identical
        a = V.score(f, "fcf", self._val(0.5, -0.56, -0.9), [])["score"]
        b = V.score(f, "fcf", self._val(0.5, -0.73, -0.9), [])["score"]
        self.assertNotEqual(a, b)

    def test_red_flags_only_push_toward_caution(self):
        for t in SETS:
            r = run(t)
            clean = V.score(r["fin"], r["class"], r["valuation"], [])
            self.assertLessEqual(r["score"]["score"], clean["score"], t)
            self.assertTrue(all(f["points"] >= 0 for f in r["flags"]))

    def test_flag_points_capped(self):
        f = run("ACME")["fin"]
        many = [{"key": str(i), "bad": True, "text": "x", "points": 1.0} for i in range(6)]
        sc = V.score(f, "fcf", self._val(1, 0.0, -0.5), many)
        self.assertEqual(sc["flag_points"], V.FLAG_POINTS_CAP)
        self.assertEqual(sc["score"], 5.0 - V.FLAG_POINTS_CAP)

    def test_structural_cap_when_bull_below_price(self):
        f = run("ACME")["fin"]
        self.assertLessEqual(V.score(f, "fcf", self._val(-0.01, -0.02, -0.3), [])["score"], 3.0)

    def test_structural_floor_when_bear_above_price(self):
        f = run("ACME")["fin"]
        # 3.2: the floor still lifts the VALUATION to 6, but a 1% upside is no margin
        # of safety, so the Buffett gate caps the final score at HOLD -- and says why
        sc = V.score(f, "fcf", self._val(0.2, 0.01, 0.005), [])
        self.assertGreaterEqual(sc["before_gates"], 6.0)
        self.assertEqual(sc["score"], 5.5)
        self.assertTrue(any(g["key"] == "margin" and g["applied"] for g in sc["gates"]))

    def test_verdict_colour_mapping(self):
        # the 1.x colours (0-4 green, 5-6 yellow, 7-10 red), mirrored exactly:
        # 0-3 red, 4-5 yellow, 6-10 green -- and every old verdict maps to the same verdict
        old = {0: "STRONG BUY", 1: "STRONG BUY", 2: "STRONG BUY", 3: "BUY", 4: "BUY", 5: "HOLD",
               6: "HOLD", 7: "REDUCE", 8: "REDUCE", 9: "AVOID", 10: "AVOID"}
        for b in range(0, 11):
            label, colour = V.verdict_for(b)
            self.assertEqual(label, old[10 - b], b)
            want = "bad" if b <= 3 else ("warn" if b <= 5 else "good")
            self.assertEqual(colour, want, b)

    def test_score_discriminates_across_sample_fixtures(self):
        # compare BEFORE the 0..10 clamp: two names both honestly at the cap (QRTX, VOLT
        # in model 1.2) are allowed; two identical uncapped builds would mean a dead input
        scores = [run(t)["score"]["score"] for t in SETS]
        raw = [round(run(t)["score"]["valuation_points"] - run(t)["score"]["flag_points"], 3) for t in SETS]
        self.assertEqual(len(set(raw)), len(raw), raw)
        # 3.1's fixed discount compresses the five fixtures toward the low end (mostly overvalued
        # at their fixture prices); the spread requirement is about a dead input, not a target range
        self.assertGreater(max(scores) - min(scores), 3.0, scores)
        self.assertGreater(len({run(t)["score"]["verdict"] for t in SETS}), 1)

    def test_carried_by_names_components_and_is_not_a_count(self):
        seen = set()
        for t in SETS:
            sc = run(t)["score"]
            self.assertNotIn("agree", " ".join(c["what"] for c in sc["carried_by"]).lower())
            self.assertTrue(sc["carried_by"][0]["what"].startswith("Valuation"))
            seen.add(tuple(c["what"] for c in sc["carried_by"]))
        self.assertEqual(len(seen), len(SETS))


class Report(unittest.TestCase):
    def test_console_report_has_every_section(self):
        for t in SETS:
            text = V.console_report(run(t))
            for h in ("CURRENT METRICS", "REVENUE & MARGINS", "FREE CASH FLOW", "BALANCE SHEET",
                      "VALUATION MULTIPLES", "MODEL:", "RED FLAGS", "VERDICT:", "Not financial advice"):
                self.assertIn(h, text, (t, h))

    def test_runway_printed_for_cash_burner(self):
        r = run("QRTX")
        self.assertIsNotNone(r["runway_years"])
        self.assertGreater(r["runway_years"], 3)       # ~5y on cash + ST investments
        self.assertIn("Cash runway", V.console_report(r))

    def test_unmeasured_risks_are_listed_not_skipped(self):
        self.assertTrue(any("concentration" in u for u in run("ACME")["unmeasured"]))


if __name__ == "__main__":
    unittest.main()


class Overrides(unittest.TestCase):
    def test_override_moves_value_and_is_recorded(self):
        base = run("ACME")
        r = V.run(SETS["ACME"], overrides={"discount": 0.08})   # 9% is 3.1's fixed fcf rate
        self.assertGreater(r["valuation"]["scenarios"]["base"]["per_share"],
                           base["valuation"]["scenarios"]["base"]["per_share"])
        self.assertIn("OVERRIDE", r["valuation"]["discount_how"])
        self.assertIn("OVERRIDES", V.console_report(r))

    def test_override_bounds(self):
        with self.assertRaises(ValueError):
            V.run(SETS["ACME"], overrides={"discount": 0.02})
        with self.assertRaises(ValueError):
            V.run(SETS["ACME"], overrides={"growth": 9})


class Currency(unittest.TestCase):
    def _acme_with(self, label):
        import copy
        p = copy.deepcopy(SETS["ACME"])
        for r in p["is_a"]["result"]:
            r["reported_currency"] = label
        return p

    def test_literal_none_label_is_not_a_currency(self):
        # a literal "None" label once made the model refuse with "reports in NONE"
        for label in ("None", "none", "", None, "null", "N/A"):
            r = V.run(self._acme_with(label))
            self.assertEqual(r["fin"]["currency"], "USD", label)

    def test_junk_label_falls_back_to_other_filings(self):
        import copy
        p = copy.deepcopy(SETS["ZENO"])
        p["is_a"]["result"][0]["reported_currency"] = "None"
        r = V.run(p, fx=FX)
        self.assertEqual(r["fin"]["currency"], "TWD")

    def test_mislabelled_cash_flow_rows_are_flagged_not_mixed(self):
        import copy
        p = copy.deepcopy(SETS["ACME"])
        p["cf_a"]["result"][0]["reported_currency"] = "RUB"   # cash-flow rows labelled in another currency
        r = V.run(p)
        self.assertEqual(r["fin"]["currency"], "USD")
        self.assertTrue(any("label conflict" in q for q in r["fin"]["quality"]))

    def test_refusal_names_the_file_and_folder(self):
        with self.assertRaises(ValueError) as cm:
            V.run(SETS["ZENO"])
        self.assertIn("Valuation Desk folder", str(cm.exception))

    def test_half_rounds_up(self):
        self.assertEqual(V.half_up(6.5), 7)
        self.assertEqual(V.half_up(7.5), 8)
        # 2.0 mirror: a .5 tie goes to caution
        self.assertEqual(V.verdict_for(3.5)[0], "REDUCE")
        self.assertEqual(V.badge_for(3.5), 3)
        self.assertEqual(V.verdict_for(5.5)[0], "HOLD")
        self.assertEqual(V.verdict_for(5.6)[0], "BUY")

    def test_verdict_follows_displayed_score(self):
        f = run("ACME")["fin"]
        val = {"scenarios": {"bull": {"upside": 0.5}, "base": {"upside": -0.294}, "bear": {"upside": -0.5}}}
        sc = V.score(f, "fcf", val, [])       # 5 - 1.47 = 3.53 -> shown 3.5
        self.assertEqual(sc["score"], 3.5)
        self.assertEqual(sc["verdict"], "REDUCE")


class StockComp(unittest.TestCase):
    def test_sbc_is_charged_in_base_and_bear_not_bull(self):
        fin = dict(run("ACME")["fin"])
        with legacy():
            plain = V.start_margins(dict(fin, sbc_pct=0.0, margin_hist=[dict(h, sbc=0.0) for h in fin["margin_hist"]]), "fcf")
            heavy = V.start_margins(dict(fin, sbc_pct=0.20, margin_hist=[dict(h, sbc=0.20) for h in fin["margin_hist"]]), "fcf")
        self.assertAlmostEqual(plain["base"][0] - heavy["base"][0], 0.20)

        self.assertAlmostEqual(plain["bull"][0], heavy["bull"][0])
        self.assertLess(heavy["bear"][0], plain["bear"][0])

    def test_improving_margins_are_not_anchored_to_the_worst_year(self):
        # improving shape: 3.1% -> 5.8% -> 10.4%, current 9.1%
        fin = dict(run("ACME")["fin"], fcf_margin=0.091, owner_margin=0.093, sbc_pct=0.0,
                   margin_hist=[{"fy": "2025", "rep": .104, "own": .106, "sbc": 0},
                                {"fy": "2024", "rep": .058, "own": .059, "sbc": 0},
                                {"fy": "2023", "rep": .031, "own": .033, "sbc": 0}])
        with legacy():
            m = V.start_margins(fin, "fcf")
        avg = (.106 + .059 + .033) / 3
        self.assertAlmostEqual(m["base"][0], (0.093 + avg) / 2)

    def test_sbc_keeps_scenario_order(self):
        fin = dict(run("ACME")["fin"], sbc_pct=0.25)
        fin["margin_hist"] = [dict(h, sbc=0.25) for h in fin["margin_hist"]]
        m = V.start_margins(fin, "fcf")
        self.assertGreaterEqual(m["bull"][0], m["base"][0])
        self.assertGreaterEqual(m["base"][0], m["bear"][0])


class ScriptSnapshot(unittest.TestCase):
    def test_script_uses_the_loaded_model_not_the_file_on_disk(self):
        import generate as G
        self.assertIn("MODEL_VERSION = \"%s\"" % V.MODEL_VERSION, G._MODEL_SRC)


class ScaleInversion(unittest.TestCase):
    def test_higher_is_better(self):
        with legacy():
            self._higher_is_better()

    def _higher_is_better(self):
        self.assertGreater(run("BLMP")["score"]["score"], run("QRTX")["score"]["score"] + 3)
        self.assertNotIn(run("BLMP")["score"]["verdict"], ("AVOID", "REDUCE"))
        self.assertEqual(run("QRTX")["score"]["verdict"], "AVOID")

    def test_upgrade_mirrors_an_old_result_exactly(self):
        import copy
        for t in SETS:
            new = run(t)
            old = copy.deepcopy(new)
            sc = old["score"]                          # rebuild what 1.x stored
            sc["score"] = round(10 - sc["score"], 1)
            sc["valuation_points"] = round(10 - sc["valuation_points"], 2)
            sc["badge"] = V.half_up(sc["score"])
            sc["verdict"] = {"AVOID": "AVOID", "REDUCE": "REDUCE", "HOLD": "HOLD",
                             "BUY": "BUY", "STRONG BUY": "STRONG BUY"}[sc["verdict"]]
            sc.pop("scale")
            up = V.upgrade_result(old)
            self.assertAlmostEqual(up["score"]["score"], new["score"]["score"], places=6, msg=t)
            self.assertEqual(up["score"]["verdict"], new["score"]["verdict"], t)
            self.assertEqual(up["score"]["scale"], V.SCALE_VERSION)
            self.assertIsNot(V.upgrade_result(up), None)   # idempotent: a 2.0 result is left alone
            self.assertEqual(V.upgrade_result(up)["score"]["score"], up["score"]["score"])


class TenYearFunded(unittest.TestCase):
    """Model 3.0: 10-year projection with a growth fade, and growth that must be funded."""

    def test_projection_is_ten_years_and_fades_to_terminal(self):
        b = run("ACME")["valuation"]["scenarios"]["base"]
        self.assertEqual(len(b["rows"]), 10)
        g = [r["growth"] for r in b["rows"]]
        self.assertTrue(all(x == g[0] for x in g[:5]))
        self.assertTrue(all(g[i] > g[i + 1] for i in range(4, 9)))
        self.assertGreater(g[9], b["terminal"])

    def test_fade_starts_from_at_most_25pct(self):
        # an 80% venture case compounding through year 10 was worth 40x more
        p = V.fade_path(0.80, 0.025)
        self.assertEqual(p[:5], [0.80] * 5)
        self.assertLessEqual(p[5], V.FADE_START_CAP)
        self.assertEqual(V.fade_path(0.10, 0.025)[5] > 0.08, True)   # a 10% grower fades from 10%

    def test_growth_is_never_worth_negative(self):
        # low sales-to-capital and a thin margin: growth must not LOWER value
        lo = V.dcf(100.0, 0.02, 0.05, 0.05, 0.10, 0.02, s2c=0.8)["ev"]
        hi = V.dcf(100.0, 0.30, 0.05, 0.05, 0.10, 0.02, s2c=0.8)["ev"]
        self.assertGreaterEqual(hi, lo - 1e-9)
        lo = V.dcf(100.0, 0.02, 0.2, 0.2, 0.10, 0.02, roe=0.05)["ev"]
        hi = V.dcf(100.0, 0.30, 0.2, 0.2, 0.10, 0.02, roe=0.05)["ev"]
        self.assertGreaterEqual(hi, lo - 1e-9)

    def test_funding_costs_capital_heavy_growth_more(self):
        light = V.dcf(100.0, 0.20, 0.15, 0.15, 0.10, 0.025, s2c=5.0)["ev"]
        heavy = V.dcf(100.0, 0.20, 0.15, 0.15, 0.10, 0.025, s2c=1.0)["ev"]
        free = V.dcf(100.0, 0.20, 0.15, 0.15, 0.10, 0.025)["ev"]
        self.assertGreater(free, light)
        self.assertGreater(light, heavy)

    def test_goodwill_is_not_capital_growth_needs(self):
        fin = dict(run("ACME")["fin"])
        with_gw = V.capital_efficiency(dict(fin, goodwill_intang=0.0), "fcf")[1]
        ex_gw = V.capital_efficiency(fin, "fcf")[1]
        self.assertGreater(ex_gw, with_gw)

    def test_financials_fund_growth_from_roe(self):
        v = run("BLMP")["valuation"]
        self.assertEqual(v["capital"]["kind"], "roe")
        self.assertTrue(v["capital"]["charged"])

    def test_loss_makers_are_charged_from_year_6_not_twice(self):
        # years 1-5: capex already in their reported cash burn; years 6-10: funded like anyone
        v = run("QRTX")["valuation"]
        rows = v["scenarios"]["bull"]["rows"]
        self.assertEqual(v["capital"]["from_year"], 6)
        self.assertTrue(all(r["reinvest"] == 0 for r in rows[:5]))
        self.assertTrue(any(r["reinvest"] > 0 for r in rows[5:]))


class DebtFree(unittest.TestCase):
    def test_all_null_debt_on_a_present_balance_sheet_means_zero_debt(self):
        # every debt field null -> cash was once ignored
        import copy
        p = copy.deepcopy(SETS["ACME"])
        for r in p["bs_a"]["result"]:
            for k in ("short_long_term_debt_total", "short_term_debt", "long_term_debt",
                      "capital_lease_obligations"):
                r[k] = None
        f = V.run(p)["fin"]
        self.assertEqual(f["debt"], 0.0)
        self.assertAlmostEqual(f["net_cash"], f["liquid"])
        self.assertNotIn("no balance sheet", V.run(p)["valuation"]["capital"]["how"])

    def test_no_balance_sheet_at_all_is_still_unmeasured(self):
        self.assertIsNone(run("BLMP")["fin"]["net_cash"])


def _hist(adj, revs):
    """newest-first margin_hist_long rows from oldest-first series"""
    return [{"fy": str(2016 + i), "rev": r, "adj": a, "own": a + 0.01}
            for i, (a, r) in enumerate(zip(adj, revs))][::-1]


class Model31(unittest.TestCase):
    def test_fixed_discount_is_the_default_and_overrides_still_win(self):
        self.assertTrue(V.OPTIONS["fixed_discount"])
        r = run("ACME")
        self.assertAlmostEqual(r["valuation"]["discount"], V.FIXED_DISCOUNT[r["class"]])
        o = V.run(SETS["ACME"], fx=FX, overrides={"discount": 0.11})
        self.assertAlmostEqual(o["valuation"]["discount"], 0.11)

    def test_cycle_check_is_shown_but_not_scored(self):
        self.assertFalse(V.OPTIONS["cycle_aware"])
        fin = dict(run("ACME")["fin"], owner_margin=0.45, sbc_pct=0.0,
                   margin_hist_long=_hist([.20, .30, .10, .25, .05, .28, .12, .45],
                                          [10, 12, 9, 13, 11, 15, 12, 20]))
        fin["_cycle"] = V.cycle_profile(fin)
        self.assertTrue(fin["_cycle"]["cyclical"])
        fl = [f for f in V.red_flags(fin, "fcf", run("ACME")["valuation"]) if f["key"] == "cycle_peak"]
        self.assertTrue(fl and fl[0]["bad"])
        self.assertEqual(fl[0]["points"], 0.0)
        self.assertIn("warning only", fl[0]["text"])

    def test_cycle_median_is_robust_to_one_disaster_year(self):
        fin = {"margin_hist_long": _hist([.15, .16, -.60, .14, .15, .02, .16], [10, 9, 8, 10, 11, 9, 12])}
        c = V.cycle_profile(fin)
        self.assertAlmostEqual(c["median_adj"], .15)
        self.assertLess(c["mean_adj"], .03)

    def test_lenders_filed_outside_financials_are_caught(self):
        self.assertTrue(V.is_lender({"sector": "Technology", "description":
            "Example Pay Inc. ... is a financial technology company offering a buy now, pay later service"}))
        self.assertTrue(V.is_lender({"sector": "Financial Services", "description": ""}))
        self.assertFalse(V.is_lender({"sector": "Communication Services",
                                      "description": "Example Search Inc. is a multinational technology conglomerate"}))

    def test_steady_improver_uses_todays_margin(self):
        base = dict(run("ACME")["fin"], fcf_margin=0.12, owner_margin=0.12, sbc_pct=0.0,
                    description="", sector="Technology",
                    margin_hist=[{"fy": "2025", "rep": .12, "own": .12, "sbc": 0},
                                 {"fy": "2024", "rep": .08, "own": .08, "sbc": 0},
                                 {"fy": "2023", "rep": .04, "own": .04, "sbc": 0}],
                    margin_hist_long=_hist([.04, .08, .12], [10, 12, 15]))
        base["_cycle"] = {"cyclical": False}
        m = V.start_margins(base, "fcf")
        self.assertAlmostEqual(m["base"][0], 0.12)
        with legacy():
            self.assertLess(V.start_margins(base, "fcf")["base"][0], 0.12)

    def test_scenarios_stay_ordered_under_every_option_mix(self):
        import itertools
        for combo in itertools.product((False, True), repeat=3):
            old = dict(V.OPTIONS)
            V.OPTIONS.update(zip(("fixed_discount", "trend_margin", "cycle_aware"), combo))
            try:
                for t in SETS:
                    sc = run(t)["valuation"]["scenarios"]
                    b, m, w = (sc[k]["per_share"] or 0 for k in ("bull", "base", "bear"))
                    self.assertGreaterEqual(b + 1e-9, m, (t, combo))
                    self.assertGreaterEqual(m + 1e-9, w, (t, combo))
            finally:
                V.OPTIONS.clear(); V.OPTIONS.update(old)


import sample_mgmt as M  # noqa: E402


def run_mgmt(t="ACME"):
    p = dict(SETS[t], insiders=M.ACME_INSIDERS, earnings=M.ACME_EARNINGS, as_of="2026-06-30")
    return V.run(p, fx=FX)


class Quality32(unittest.TestCase):
    def test_quality_weights_sum_to_100(self):
        self.assertEqual(sum(V.QUALITY_WEIGHTS.values()), 100)

    def test_duplicate_grouped_insider_rows_counted_once(self):
        # the same ROE filing twice, ids in a different order, one with cik null
        s = V.insider_summary(V.rows_of(M.ACME_INSIDERS), 183.12, 2.07e12, 11.3e9, today="2026-06-30")
        self.assertEqual(s["rows"], 5)

    def test_plan_sales_do_not_count_against_management(self):
        s = V.insider_summary(V.rows_of(M.ACME_INSIDERS), 183.12, 2.07e12, 11.3e9, today="2026-06-30")
        self.assertEqual(s["sell_usd_12m"], 0)          # every sample ACME sale is a 10b5-1 plan
        self.assertGreater(s["owned_pct"], 0.08)       # STONE 1.10B of ~11.3B

    def test_discretionary_sale_and_wrong_fill_price(self):
        rows = [{"ids": ["a"], "transaction_code": "S", "amount": -100000, "price": "25000.00",
                 "stock_price": "250", "transaction_date": "2026-06-01", "is_10b5_1": False,
                 "reporter_cik": "1", "shares_owned_after": 5, "security_ad_code": "ND"}]
        s = V.insider_summary(rows, 250.0, 1e10, 4e7, today="2026-06-30")
        # a 100x-wrong fill falls back to the quote: $25M, not $2.5B
        self.assertAlmostEqual(s["sell_usd_12m"], 100000 * 250.0)

    def test_next_report_row_is_not_a_miss(self):
        e = V.earnings_summary(V.rows_of(M.ACME_EARNINGS))
        self.assertEqual(e["quarters"], 12)
        self.assertEqual(e["beats"], 12)          # 2023-01 miss is the 13th-newest, outside the window

    def test_unmeasured_components_rescale_not_zero(self):
        q = run("ACME")["quality"]                  # fixture: no insiders, no earnings, no goodwill field
        self.assertLess(q["measured"], 100)
        self.assertIsNotNone(q["score"])
        unm = [c for c in q["components"] if c["value"] is None]
        self.assertTrue(any(c["key"] == "insiders" for c in unm))
        self.assertTrue(any(c["key"] == "acquisitions" for c in unm))   # absent field != no goodwill
        self.assertGreater(run_mgmt()["quality"]["measured"], q["measured"])

    def test_too_little_data_means_unknown_and_no_gate(self):
        fin = dict(run("ACME")["fin"], annual=[])
        q = V.quality(fin, "fcf")
        self.assertIsNone(q["score"])
        val = run("ACME")["valuation"]
        gates = V.gate_checks(fin, val, q, 9.0)
        self.assertTrue(all(g["cap"] is None for g in gates if g["key"] == "quality"))

    def test_share_trend_cuts_pre_ipo_rows(self):
        # a pre-IPO row: 2018 = 203M between two ~744M years
        rows = [{"fy": str(2026 - i), "shares": s} for i, s in
                enumerate([1142e6, 1088e6, 1061e6, 1019e6, 996e6, 958e6, 931e6, 744e6, 203e6, 744e6])]
        cg, yrs = V.share_trend(rows)
        self.assertEqual(yrs, 5)
        self.assertAlmostEqual(cg, (1142 / 958) ** 0.2 - 1, places=6)


class Gates32(unittest.TestCase):
    def _val(self, up):
        p = run("ACME")["fin"]["price"]
        return {"scenarios": {k: {"upside": u, "per_share": p * (1 + u)}
                              for k, u in (("bull", up + 0.3), ("base", up), ("bear", up - 0.3))}}

    def _q(self, score):
        return {"score": score, "measured": 80, "tier": "x", "weaknesses": ["Return on capital: weak"],
                "strengths": []}

    def test_margin_of_safety_caps_a_thin_discount_at_hold(self):
        f = run("ACME")["fin"]
        sc = V.score(f, "fcf", self._val(0.20), [], self._q(8))    # 20% upside = 16.7% margin
        self.assertEqual(sc["score"], 5.5)
        self.assertEqual(sc["verdict"], "HOLD")
        ok = V.score(f, "fcf", self._val(0.40), [], self._q(8))    # 28.6% margin
        self.assertGreater(ok["score"], 5.5)

    def test_low_quality_is_a_value_trap_capped_at_hold(self):
        f = run("ACME")["fin"]
        sc = V.score(f, "fcf", self._val(1.0), [], self._q(3.0))
        self.assertEqual(sc["before_gates"], 10.0)
        self.assertEqual(sc["score"], 5.5)

    def test_average_quality_cannot_be_strong_buy(self):
        f = run("ACME")["fin"]
        sc = V.score(f, "fcf", self._val(1.0), [], self._q(5.0))
        self.assertEqual(sc["score"], 7.5)
        self.assertEqual(sc["verdict"], "BUY")
        self.assertEqual(V.score(f, "fcf", self._val(1.0), [], self._q(6.5))["score"], 10.0)

    def test_gates_only_lower(self):
        for t in SETS:
            r = run(t)
            self.assertLessEqual(r["score"]["score"], r["score"]["before_gates"], t)

    def test_why_names_the_cap(self):
        f = run("ACME")["fin"]
        val = self._val(1.0)
        q = self._q(3.0)
        sc = V.score(f, "fcf", val, [], q)
        why = " ".join(V.why_verdict(f, val, sc, q))
        self.assertIn("caps this at HOLD", why)
        self.assertIn("value trap", why)


class Returns32(unittest.TestCase):
    def test_irr_equals_discount_rate_at_fair_value(self):
        r = run("ACME")
        fin = dict(r["fin"], price=r["valuation"]["scenarios"]["base"]["per_share"])
        er = V.expected_returns(fin, r["class"], r["valuation"])
        d = r["valuation"]["discount"]
        self.assertAlmostEqual(er["irr"][5], d, places=4)
        self.assertAlmostEqual(er["irr"][10], d, places=4)
        self.assertAlmostEqual(er["implied_return"], d, places=3)
        self.assertAlmostEqual(er["implied_growth"], r["valuation"]["engine"]["growth"], places=3)

    def test_cheaper_price_means_higher_return(self):
        r = run("ACME")
        bv = r["valuation"]["scenarios"]["base"]["per_share"]
        lo = V.expected_returns(dict(r["fin"], price=bv * 0.6), r["class"], r["valuation"])
        hi = V.expected_returns(dict(r["fin"], price=bv * 1.2), r["class"], r["valuation"])
        self.assertGreater(lo["irr"][5], hi["irr"][5])
        self.assertLess(lo["implied_growth"], hi["implied_growth"])

    def test_no_positive_value_no_irr(self):
        r = run("QRTX")
        if r["valuation"]["scenarios"]["base"]["floored"]:
            self.assertEqual(r["valuation"]["returns"]["irr"], {})


class Synopsis32(unittest.TestCase):
    def test_fits_in_a_tweet_and_uses_the_model_numbers(self):
        for t in SETS:
            r = run(t)
            syn = r["synopsis"]
            self.assertLessEqual(len(syn), V.SYNOPSIS_MAX, t)
            self.assertTrue(syn.startswith("$" + t), syn)
            self.assertIn(r["score"]["verdict"], syn)
            b = r["valuation"]["scenarios"]["base"]
            if b["per_share"] and not b["floored"]:
                self.assertIn(V.px(b["per_share"]), syn)

    def test_heavy_debt_is_named(self):
        r = run("VOLT")
        fin = dict(r["fin"], debt=(r["fin"]["ebitda"] or 1) * 8 + (r["fin"]["liquid"] or 0))
        syn = V.synopsis(dict(r, fin=fin))
        self.assertIn("EBITDA", syn)

    def test_gate_reason_is_in_the_tweet(self):
        r = run("ACME")
        sc = dict(r["score"], verdict="HOLD", gates=[{"key": "margin", "applied": True}])
        self.assertIn("no 25% margin of safety", V.synopsis(dict(r, score=sc)))
