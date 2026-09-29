"""The "How it works" page: live numbers win, written numbers stand in, and the written
numbers match each desk's own source whenever the desk folders sit next to the dashboard."""
import ast
import os
import unittest

from _path import ROOT
import deskview
import guide

PARENT = os.path.dirname(ROOT)


def consts(path):
    """Module-level constant assignments of a desk file, evaluated without importing it."""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                ns[node.targets[0].id] = eval(compile(ast.Expression(node.value), path, "eval"),
                                              {"__builtins__": {}}, dict(ns))
            except Exception:           # noqa: BLE001 -- anything that isn't a constant is skipped
                pass
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for n, v in zip(t.elts, node.value.elts):
                        if isinstance(n, ast.Name):
                            try:
                                ns[n.id] = ast.literal_eval(v)
                            except ValueError:
                                pass
    return ns


def desk_file(folder, name):
    p = os.path.join(PARENT, folder, name)
    return p if os.path.exists(p) else None


def weights(did):
    return {label.strip(): w for label, w in guide.WRITTEN[did]["parts"]}


class GuideLive(unittest.TestCase):
    PAYLOADS = {
        (8790, "/api/method"): {"model_version": "3.2", "margin_of_safety": 0.30, "flag_cap": 2.5,
                                "quality": [["Return on capital", 40], ["Insider behaviour", 60]],
                                "quality_gates": [[4.0, 5.5, "HOLD"], [6.0, 7.5, "BUY"]],
                                "quality_min_measured": 50,
                                "verdicts": [[1.5, "AVOID"], [3.5, "REDUCE"], [5.5, "HOLD"], [7.5, "BUY"], [10.0, "STRONG BUY"]]},
        (8733, "/api/flow"): {"meta": {"gate": 0.8, "near_gate": 0.6, "min_premium": 150000, "observed_max": 71.26,
                                       "weights": {"aggression": 30, "sweep": 18, "opening": 20, "relative": 12,
                                                   "urgency": 12, "conviction": 8}}, "cards": []},
        (8787, "/api/health"): {"config": {"w_flow": 35, "w_oi": 20, "w_dark": 10, "w_gamma": 15, "w_tech": 20,
                                           "min_score": 42, "min_agreeing": 3, "min_rr": 1.3, "agree_threshold": 0.15,
                                           "earnings_haircut": 0.65, "high_iv_rank": 85, "high_iv_haircut": 0.85,
                                           "thin_haircut": 0.7, "scan_start": "09:35", "scan_end": "16:00", "ttl_scan": 45},
                                "discord": {"enabled": False, "min_score": 55, "min_agreeing": 4, "min_streak": 3,
                                            "cooldown_hours": 6}},
        (8777, "/api/state"): {"report_date": "2031-06-30", "config": {
            "fund_min_aum": 50e6, "fund_max_aum": 2.5e9, "fund_max_positions": 75, "require_hedge_fund": True,
            "co_max_marketcap": 10e9, "co_max_assets": 10e9, "min_profit_years": 3,
            "min_position_value": 1e6, "min_add_perc": 0.25, "cluster_min_funds": 3}},
    }
    PORTS = {"valuation": 8790, "confluence": 8770, "flow": 8733, "institutional": 8777, "swing": 8787}

    def view(self, running):
        def fetch(port, path):
            if (port, path) in self.PAYLOADS:
                return self.PAYLOADS[(port, path)]
            raise OSError("nothing")
        return deskview.Desks(lambda: {k: v for k, v in self.PORTS.items() if k in running}, fetch=fetch)

    def by_id(self, out):
        return {x["id"]: x for x in out["desks"] + out["pages"]}

    def test_running_desks_publish_their_own_numbers(self):
        g = self.by_id(guide.build(self.view(guide.ORDER), {}))
        self.assertEqual(g["valuation"]["source"], "live")
        self.assertEqual(g["valuation"]["parts"], [["Return on capital", 40.0], ["Insider behaviour", 60.0]])
        self.assertIn("70% of base value (a 30% margin)", g["valuation"]["rules"][0][1])
        self.assertIn("at most 2.5", g["valuation"]["score"]["formula"])
        self.assertEqual(g["valuation"]["score"]["bands"][0], ["0 - 1.5", "AVOID"])
        self.assertEqual(g["valuation"]["score"]["bands"][1], ["1.6 - 3.5", "REDUCE"])
        self.assertEqual(g["flow"]["parts"][0], ["Aggression: how hard the buyer lifts the offer", 30.0])
        self.assertEqual(g["flow"]["rules"][0][1], "At least 80% of premium on the ask, with at least $150k bought")
        self.assertIn("71.3", g["flow"]["note_live"])
        self.assertEqual(g["swing"]["parts"][0], ["Options flow", 35.0])
        self.assertIn("Score 42+", g["swing"]["rules"][0][1])
        self.assertIn("Score 55+", g["swing"]["rules"][1][1])
        self.assertIn("Discord not set up", g["swing"]["rules"][1][1])
        self.assertEqual(g["institutional"]["source"], "live")
        self.assertIn("$50M to $2.5B", g["institutional"]["rules"][0][1])
        self.assertIn("2031-06-30", g["institutional"]["note_live"])
        # Confluence doesn't publish weights: always written, and it says so
        self.assertEqual(g["confluence"]["source"], "written")
        self.assertIn("doesn't publish", g["confluence"]["source_note"])

    def test_stopped_desks_fall_back_to_the_written_values(self):
        g = self.by_id(guide.build(self.view(()), {}))
        for did in ("valuation", "flow", "swing", "institutional"):
            self.assertEqual(g[did]["source"], "written", did)
            self.assertIn("not running", g[did]["source_note"])
            self.assertEqual(g[did]["parts"], guide.WRITTEN[did]["parts"])

    def test_a_desk_answering_without_numbers_does_not_break_the_page(self):
        self.PAYLOADS = dict(self.PAYLOADS)
        self.PAYLOADS[(8733, "/api/flow")] = {"meta": {"counts": {}}, "cards": []}   # no board yet
        self.PAYLOADS[(8787, "/api/health")] = ["not", "a", "dict"]
        g = self.by_id(guide.build(self.view(("flow", "swing")), {}))
        self.assertEqual(g["flow"]["source"], "written")
        self.assertIn("no board yet", g["flow"]["source_note"])
        self.assertEqual(g["swing"]["source"], "written")
        self.assertEqual(g["swing"]["parts"], guide.WRITTEN["swing"]["parts"])

    def test_written_values_are_never_mutated_by_a_live_read(self):
        before = repr(guide.WRITTEN)
        guide.build(self.view(guide.ORDER), {})
        self.assertEqual(repr(guide.WRITTEN), before)

    def test_only_enabled_desks_are_listed(self):
        out = guide.build(self.view(()), {}, enabled=["flow", "swing", "gex"])
        self.assertEqual([x["id"] for x in out["desks"]], ["flow", "swing"])

    def test_dashboard_pages_use_the_dashboard_settings(self):
        g = self.by_id(guide.build(None, {"sizer_risk_pct": 0.5, "sizer_max_pos_pct": 12, "sizer_stop_pct": 7,
                                          "portfolio_capital": 250000}))
        sz = dict(g["sizer"]["rules"])
        self.assertEqual(sz["Risk per trade"], "0.5% of capital (Settings)")
        self.assertEqual(sz["Max position"], "12% of capital")
        self.assertIn("$250k", sz["Capital"])
        self.assertIn("5, 20, 60 sessions", " ".join(g["trackrecord"]["how"]))
        self.assertIn("within 7 days", g["morning"]["rules"][0][1])
        self.assertIn("thinkorswim", g["pnl"]["rules"][0][1])

    def test_written_weights_add_up(self):
        for did in ("flow", "swing", "growth"):
            self.assertAlmostEqual(sum(weights(did).values()), 100.0, msg=did)
        self.assertAlmostEqual(sum(weights("valuation").values()), 100.0)
        w = weights("confluence")
        legs = [w[k] for k in w if not k[0].islower()]
        self.assertAlmostEqual(sum(legs), 100.0)
        self.assertEqual(sum(weights("institutional").values()), 122.0)   # rescaled to 100 by the desk


class GuideMatchesDeskCode(unittest.TestCase):
    """When the desk folders sit beside the dashboard (the GitHub suite, or Downloads), the
    written numbers must equal the constants in each desk's source."""

    def need(self, folder, name):
        p = desk_file(folder, name)
        if not p:
            self.skipTest("%s not next to the dashboard" % folder)
        return consts(p)

    def test_confluence(self):
        c = self.need("Confluence Desk", "score.py")
        w = weights("confluence")
        self.assertEqual(w["Insider buying (Form 4, open-market)"], c["W_INSIDER"])
        self.assertEqual(w["Dark pool (off-exchange prints)"], c["W_DARKPOOL"])
        self.assertEqual(w["Institutional (13F, small concentrated funds)"], c["W_INSTITUTIONAL"])
        subs = [v for k, v in guide.WRITTEN["confluence"]["parts"] if k.startswith("  ")]
        desk = [c[k] for k in ("WI_CONVICTION", "WI_CLUSTER", "WI_RANK", "WI_SIZE", "WD_BLOCK", "WD_SUSTAIN",
                               "WD_PROXIMITY", "WD_LEAN", "WN_FUNDS", "WN_WEIGHT", "WN_TRAJECTORY")]
        for a, b in zip(subs, desk):
            self.assertAlmostEqual(a, b)

    def test_flow(self):
        c = self.need("UW Flow Desk", "flow.py")
        self.assertEqual(sorted(weights("flow").values()), sorted(c["WEIGHTS"].values()))
        self.assertEqual((c["ASK_GATE"], c["ASK_NEAR_GATE"], c["MIN_TICKER_PREMIUM"]), (0.75, 0.60, 100_000.0))

    def test_valuation(self):
        c = self.need("Valuation Desk", "valuation.py")
        self.assertEqual(sorted(weights("valuation").values()), sorted(c["QUALITY_WEIGHTS"].values()))
        self.assertEqual([b[0] for b in c["VERDICTS"]], [1.5, 3.5, 5.5, 7.5, 10.0])
        self.assertEqual((c["MARGIN_OF_SAFETY"], c["FLAG_POINTS_CAP"]), (0.25, 2.0))

    def test_swing(self):
        c = self.need("Swing Desk", "server.py")["CFG"]
        self.assertEqual(sorted(weights("swing").values()), sorted(c[k] for k in ("w_flow", "w_oi", "w_dark", "w_gamma", "w_tech")))
        self.assertEqual((c["min_score"], c["min_agreeing"]), (40.0, 3))
        n = self.need("Swing Desk", "notify.py")["NOTIFY"]
        self.assertEqual((n["min_score"], n["min_agreeing"], n["min_streak"]), (50.0, 4, 3))

    def test_growth(self):
        c = self.need("Growth Desk", "model.py")
        w = {k[0]: v for k, v in weights("growth").items()}
        self.assertEqual(w, c["WEIGHTS"])
        self.assertEqual((c["C_PASS"], c["A_PASS"], c["N_PASS"], c["L_PASS"], c["BREAKOUT_VOL"], c["STOP_LOSS"]),
                         (0.25, 0.25, -0.15, 80, 1.4, 0.08))

    def test_institutional(self):
        c = self.need("Institutional Desk", "scan.py")
        self.assertEqual(sum(weights("institutional").values()), c["SCORE_MAX"])


if __name__ == "__main__":
    unittest.main()
