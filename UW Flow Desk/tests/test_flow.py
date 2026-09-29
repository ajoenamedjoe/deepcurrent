"""Every test is named after the bug or trap it guards.

Run: python3 -m pytest tests/test_flow.py -q   (or python3 tests/test_flow.py)
"""
import os
import random
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flow  # noqa: E402
from tests.fixtures import sample_flow as F  # noqa: E402

TODAY = date(2026, 10, 16)


# ---------------------------------------------------------------- ARITHMETIC

def test_weights_sum_to_100():
    assert abs(sum(flow.WEIGHTS.values()) - 100.0) < 1e-9


def test_subweights_sum_to_their_parent():
    assert abs((flow.W_SWEEP_SHARE + flow.W_SWEEP_RULE + flow.W_SWEEP_FILL)
               - flow.W_SWEEP) < 1e-9
    assert abs((flow.W_OPEN_VOI + flow.W_OPEN_OI_CONF + flow.W_OPEN_DAYS
                + flow.W_OPEN_ALL_OPENING) - flow.W_OPENING) < 1e-9
    assert abs((flow.W_REL_TICKER_SHARE + flow.W_REL_MCAP)
               - flow.W_RELATIVE) < 1e-9
    assert abs((flow.W_URG_DTE + flow.W_URG_STACK) - flow.W_URGENCY) < 1e-9


def test_confluence_legs_still_sum_to_100_after_adding_flow():
    # Adding a fourth leg is only honest if the other three were rescaled.
    assert abs(sum(flow.CONFLUENCE_LEGS.values()) - 100.0) < 1e-9
    assert flow.CONFLUENCE_LEGS["flow"] == flow.W_FLOW_LEG


def test_confluence_flow_leg_is_bounded():
    assert flow.confluence_flow_leg(0) == 0.0
    assert abs(flow.confluence_flow_leg(100) - flow.W_FLOW_LEG) < 1e-9
    assert flow.confluence_flow_leg(1e9) <= flow.W_FLOW_LEG
    assert flow.confluence_flow_leg(-50) == 0.0
    assert flow.confluence_flow_leg(None) == 0.0


def test_fuzz_no_component_can_overflow_its_weight():
    rnd = random.Random(11)
    for _ in range(4000):
        alert = dict(F.ACME_FLOOR)
        alert["total_premium"] = str(rnd.uniform(0, 5e8))
        alert["total_ask_side_prem"] = str(rnd.uniform(0, float(alert["total_premium"])))
        alert["total_bid_side_prem"] = "0"
        alert["total_size"] = rnd.randint(0, 10**6)
        alert["open_interest"] = rnd.randint(0, 10**6)
        alert["trade_count"] = rnd.randint(0, 5000)
        alert["expiry_count"] = 1
        c = dict(F.FJRD_SCR)
        c["volume"] = rnd.randint(1, 10**6)
        c["open_interest"] = rnd.randint(0, 10**6)
        c["prev_oi"] = rnd.randint(0, 10**6)
        c["ticker_vol"] = rnd.randint(1, 10**7)
        c["ask_side_volume"] = rnd.randint(0, 10**6)
        c["bid_side_volume"] = rnd.randint(0, 10**6)
        c["days_of_oi_increases"] = rnd.randint(0, 40)
        card = flow.build_card("FUZZ", [alert], [c], TODAY)
        for k, v in card["parts"].items():
            assert -1e-9 <= v <= flow.WEIGHTS[k] + 1e-9, (k, v)
        assert 0.0 <= card["score"] <= 100.0


def test_a_maxed_card_can_actually_reach_100():
    # If the theoretical maximum is unreachable the scale is a lie. Confluence
    # Desk shipped a model whose practical ceiling was 77 and had to print its
    # own observed maximum in the footer to stay honest.
    parts = {k: v for k, v in flow.WEIGHTS.items()}
    assert abs(sum(parts.values()) - 100.0) < 1e-9


# ------------------------------------------------------- THE ZERO-OI LANDMINE

def test_volume_oi_ratio_field_is_zero_when_open_interest_is_zero():
    # Documenting the API's behaviour, so that if it is ever fixed the test
    # fails loudly rather than the desk silently changing its scores.
    assert F.QRTX_ZERO_OI["open_interest"] == 0
    assert F.QRTX_ZERO_OI["volume_oi_ratio"] == "0"
    assert F.BLMP_ZERO_OI2["open_interest"] == 0
    assert F.BLMP_ZERO_OI2["volume_oi_ratio"] == "0"


def test_volume_over_oi_is_computed_locally_not_read_from_the_payload():
    # The most-opening contract on the tape must not score at the bottom.
    assert flow.volume_over_oi(F.QRTX_ZERO_OI) == 31.0
    assert flow.volume_over_oi(F.BLMP_ZERO_OI2) == 34.0
    assert flow.num(F.QRTX_ZERO_OI["volume_oi_ratio"]) == 0.0


def test_zero_oi_contract_maxes_the_measurable_part_of_the_opening_leg():
    pts, d = flow.score_opening([F.QRTX_ZERO_OI], [])
    assert d["volume_over_oi"] == 31.0
    assert d["size_greater_than_oi"] is True
    assert pts > 0.90 * flow.W_OPEN_VOI


def test_opening_is_capped_while_oi_confirmation_is_pending():
    # Today's flow cannot be OI-confirmed until tomorrow morning, so the
    # confirmation sub-components are UNMEASURED and the leg is capped. The
    # card says "awaiting tomorrow's open interest" rather than drawing a bar
    # at zero, which would read as evidence against the name.
    pts, d = flow.score_opening([F.QRTX_ZERO_OI], [])
    assert d["oi_state"] == "pending"
    assert pts <= flow.W_OPEN_VOI + flow.W_OPEN_ALL_OPENING + 1e-9


def test_a_multi_day_oi_build_scores_above_a_single_session_print():
    alone, _ = flow.score_opening([], [F.SNWX_SCR])          # days_of_oi_increases 0
    building, _ = flow.score_opening([], [F.FJRD_SCR])     # days_of_oi_increases 4
    assert building > alone


# --------------------------------------------------- SIDE-COVERAGE ON ALERTS

def test_alert_ask_share_refuses_a_row_whose_sides_do_not_cover_the_premium():
    # ZENO: $41,275 ask + $3,180 bid against $655,310 total. ask/(ask+bid) is
    # 92.8% and would read as overwhelming conviction. It is 93% mid.
    share, coverage, ok = flow.alert_ask_share(F.ZENO_REPEATED)
    assert coverage < flow.MIN_SIDE_COVERAGE
    assert ok is False
    assert share is None


def test_alert_ask_share_uses_total_premium_not_ask_plus_bid():
    share, coverage, ok = flow.alert_ask_share(F.ACME_FLOOR)
    assert ok and coverage == 1.0
    assert abs(share - 1.0) < 1e-9
    # MEGA 0DTE: 99.9% BID side -- calls being sold, not bought.
    share, _, ok = flow.alert_ask_share(F.MEGA_0DTE)
    assert ok and share < 0.01


def test_undetermined_premium_is_reported_not_silently_dropped():
    card = flow.build_card("ZENO", [F.ZENO_REPEATED], [], TODAY)
    assert card["undetermined_premium"] > 600_000
    assert card["detail"]["aggression"]["measured"] is False


# ------------------------------------------------------------ THE SIGN TRAP

def test_a_put_bought_on_the_ask_is_bearish():
    assert flow.direction_of("put", "ask") == flow.BEARISH
    assert flow.direction_of("call", "ask") == flow.BULLISH


def test_a_put_sold_on_the_bid_is_bullish():
    assert flow.direction_of("put", "bid") == flow.BULLISH
    assert flow.direction_of("call", "bid") == flow.BEARISH


def test_direction_of_rejects_junk():
    assert flow.direction_of(None, "ask") is None
    assert flow.direction_of("call", "mid") is None


def test_heavy_ask_side_on_puts_does_not_produce_a_bullish_card():
    card = flow.build_card("DRVX", [F.DRVX_ASCENDING], [], TODAY)
    assert card["direction"] == flow.BEARISH
    assert card["ask_share"] is not None and card["ask_share"] > 0.75


def test_a_bid_side_put_is_not_counted_as_bearish_conviction():
    # PLNK: 100% bid side put = puts SOLD = mildly bullish, and crucially it
    # must contribute zero BEARISH ask-side premium.
    card = flow.build_card("PLNK", [F.PLNK_BID_PUT], [], TODAY)
    assert card["ask_premium"] == 0.0
    assert card["sold_premium"] > 300_000
    assert not flow.qualifies(card)


# ------------------------------------------------- THE MEGACAP 0DTE REJECTION

def test_the_four_biggest_premium_contracts_in_the_market_are_all_rejected():
    # This is the single most important behavioural test in the desk. Ranking
    # by premium returns these four; an unusual-flow board must not.
    for row in (F.MEGA_SCR, F.TITN_SCR, F.GIGA_SCR, F.COLS_SCR):
        share = flow.contract_ask_share(row)
        assert share is not None
        assert share < flow.ASK_GATE, (row["ticker_symbol"], share)


def test_the_genuinely_aggressive_names_are_kept():
    assert flow.contract_ask_share(F.FJRD_SCR) > flow.ASK_GATE
    assert flow.contract_ask_share(F.SNWX_SCR) > flow.ASK_GATE


def test_ask_share_values_match_the_sample_tape():
    approx = {"MEGA": 0.488, "TITN": 0.561, "GIGA": 0.414, "COLS": 0.518,
              "FJRD": 0.824, "SNWX": 0.907, "CRSX": 0.513, "XLGX": 0.604}
    for row in F.CONTRACTS:
        t = row["ticker_symbol"]
        if t in approx:
            assert abs(flow.contract_ask_share(row) - approx[t]) < 0.002, t


# --------------------------------------------------- MULTILEG / CROSS RUBBISH

def test_a_single_cross_does_not_read_as_conviction():
    # CRSX: 5,500 of 6,212 contracts are one multileg cross printed at neutral.
    assert flow.cross_contamination(F.CRSX_SCR) > 0.85
    assert flow.cross_contamination(F.XLGX_SCR) > 0.98
    assert flow.cross_contamination(F.FJRD_SCR) < 0.05


def test_a_spread_leg_is_not_the_same_problem_as_a_cross():
    # The bug this guards: conflating multileg_volume with neutral/cross volume
    # rejected most contracts as "unreliable" when only a few had any
    # neutral volume. A spread leg picks a side; a cross does not.
    spread_only = dict(F.FJRD_SCR)
    spread_only["multileg_volume"] = spread_only["volume"]   # 100% multileg
    spread_only["neutral_volume"] = 0
    spread_only["cross_volume"] = 0
    assert flow.spread_share(spread_only) == 1.0
    assert flow.cross_contamination(spread_only) == 0.0
    card = flow.build_card("SPREAD", [], [spread_only], TODAY)
    assert card["is_spread"] is True
    assert card["reliable"] is True          # ambiguous, NOT unreadable
    assert flow.lane(card) == "spreads"


def test_a_cross_heavy_card_is_unreliable_not_merely_a_spread():
    card = flow.build_card("XLGX", [], [F.XLGX_SCR], TODAY)
    assert card["reliable"] is False
    assert "cross" in [n["kind"] for n in card["modifier_notes"]]
    assert flow.lane(card) == "out"


def test_every_card_lands_in_exactly_one_lane():
    lanes = set()
    for c in F.CONTRACTS:
        card = flow.build_card(c["ticker_symbol"], [], [c], TODAY)
        lane = flow.lane(card)
        assert lane in ("board", "spreads", "near", "out")
        lanes.add(lane)
    for a in F.ALERTS:
        assert flow.lane(flow.build_card(a["ticker"], [a], [], TODAY)) in (
            "board", "spreads", "near", "out")
    assert len(lanes) > 1


def test_contaminated_cards_are_multiplied_down_and_say_why():
    card = flow.build_card("CRSX", [], [F.CRSX_SCR], TODAY)
    assert card["modifier"] < 1.0
    assert "cross" in [n["kind"] for n in card["modifier_notes"]]


def test_multileg_alert_is_flagged_by_two_independent_signals():
    assert flow.is_multileg_contaminated(F.VOLT_MULTILEG) is True
    assert flow.is_multileg_contaminated(F.ACME_FLOOR) is False


def test_expiry_count_above_one_counts_as_multileg_even_if_flags_disagree():
    row = dict(F.ACME_FLOOR)
    row["expiry_count"] = 2
    assert flow.is_multileg_contaminated(row) is True


def test_modifiers_never_multiply_up():
    for alerts, contracts in [([F.ACME_FLOOR], []), ([], [F.FJRD_SCR]),
                              ([F.VOLT_MULTILEG], [F.CRSX_SCR]), ([], [])]:
        mod, _ = flow.modifiers(alerts, contracts, TODAY)
        assert 0.0 < mod <= 1.0


# ------------------------------------------------------------- THE DOUBLE-COUNT

def test_a_subsuming_alert_drops_the_alert_it_contains():
    kept = flow.dedupe_alerts([F.ZENO_REPEATED, F.ZENO_FLOOR])
    assert len(kept) == 1
    assert kept[0]["alert_rule"] == "SweepsFollowedByFloor"


def test_dedupe_is_order_independent():
    a = flow.dedupe_alerts([F.ZENO_REPEATED, F.ZENO_FLOOR])
    b = flow.dedupe_alerts([F.ZENO_FLOOR, F.ZENO_REPEATED])
    assert [x["id"] for x in a] == [x["id"] for x in b]


def test_dedupe_does_not_merge_different_contracts():
    kept = flow.dedupe_alerts([F.ZENO_FLOOR, F.ACME_FLOOR, F.QRTX_ZERO_OI])
    assert len(kept) == 3


def test_summing_the_undeduped_alerts_would_overstate_premium():
    naive = sum(flow.num(a["total_premium"])
                for a in (F.ZENO_REPEATED, F.ZENO_FLOOR))
    deduped = sum(flow.num(a["total_premium"])
                  for a in flow.dedupe_alerts([F.ZENO_REPEATED, F.ZENO_FLOOR]))
    assert naive > 1_300_000
    assert deduped == 712840.0


# ---------------------------------------------------------- THE SWEEP LEG LIVES

def test_sweep_share_of_volume_would_have_been_a_dead_component():
    # The reason the sweep leg reads the alerts endpoint. If this ever stops
    # being true the screener-based approach becomes viable again.
    assert max(F.SAMPLE_DISTRIBUTIONS["sweep_share"]) < 0.10
    worst = max(row["sweep_volume"] / row["volume"] for row in F.CONTRACTS)
    assert worst < 0.10


def test_the_sweep_component_actually_pays_out_on_sample_rows():
    pts, d = flow.score_sweep([F.ZENO_FLOOR], flow.BEARISH)
    assert pts > 0.5 * flow.W_SWEEP
    assert d["strongest_rule"] == "SweepsFollowedByFloor"


def test_sweep_component_discriminates_between_sample_tickers():
    strong, _ = flow.score_sweep([F.ZENO_FLOOR], flow.BEARISH)
    weak, _ = flow.score_sweep([F.MEGA_0DTE], flow.BULLISH)
    assert strong - weak > 0.25 * flow.W_SWEEP


def test_ascending_fill_only_counts_when_it_agrees_with_the_direction():
    up, _ = flow.score_sweep([F.DRVX_ASCENDING], flow.BEARISH)   # put, paying up
    wrong, _ = flow.score_sweep([F.DRVX_ASCENDING], flow.BULLISH)
    assert up > wrong


# ----------------------------------------------------- THE 0DTE URGENCY CURVE

def test_zero_dte_is_not_scored_as_maximum_urgency():
    zero, _ = flow.score_urgency([F.MEGA_0DTE], [], TODAY)
    week = dict(F.MEGA_0DTE)
    week["expiry"] = "2026-10-23"
    wk, _ = flow.score_urgency([week], [], TODAY)
    assert wk > zero


def test_urgency_peaks_in_the_near_term_and_decays_for_leaps():
    def u(expiry):
        row = dict(F.ACME_FLOOR)
        row["expiry"] = expiry
        return flow.score_urgency([row], [], TODAY)[0]
    assert u("2026-10-23") > u("2026-10-16")     # 7d beats 0DTE
    assert u("2026-10-23") > u("2027-01-22")     # 7d beats 3mo
    assert u("2027-01-22") > u("2028-02-18")     # 3mo beats LEAP


# ------------------------------------------------------------ CALENDAR TRAPS

def test_next_earnings_date_in_the_past_is_not_treated_as_upcoming():
    # QRTX's next_earnings_date is the day before the session.
    assert flow.days_to_earnings("2026-10-15", TODAY) == -1
    card = flow.build_card("QRTX", [F.QRTX_ZERO_OI], [], TODAY)
    assert "earnings" not in [n["kind"] for n in card["modifier_notes"]]


def test_null_earnings_date_does_not_raise():
    assert flow.days_to_earnings(None, TODAY) is None
    card = flow.build_card("KWLX", [F.KWLX_NULL_ER], [], TODAY)
    assert card["score"] >= 0


def test_upcoming_earnings_applies_the_haircut():
    row = dict(F.ACME_FLOOR)
    row["next_earnings_date"] = "2026-10-20"
    card = flow.build_card("ACME", [row], [], TODAY)
    assert "earnings" in [n["kind"] for n in card["modifier_notes"]]
    assert card["modifier"] <= 0.85 + 1e-9


def test_session_bucketing_shifts_utc_evening_prints_back_a_day():
    # 2026-10-16T23:58Z is 18:58 ET on the 16th, not the 17th.
    assert flow.session_day(1792195080000).isoformat() == "2026-10-16"
    # 09:30 ET the same day, and 04:00 / 20:00 ET, all land on the 16th.
    assert flow.session_day(1792157400000).isoformat() == "2026-10-16"   # 13:30Z
    assert flow.session_day(1792137600000).isoformat() == "2026-10-16"   # 08:00Z
    assert flow.session_day(1792195200000).isoformat() == "2026-10-16"   # 00:00Z+1d


def test_dte_handles_a_malformed_expiry():
    assert flow.dte("not-a-date", TODAY) is None
    assert flow.dte(None, TODAY) is None


# --------------------------------------------------------- THE SIDE INVARIANT

def test_screener_side_volumes_usually_sum_to_volume():
    ok = [r for r in F.CONTRACTS if flow.contract_sides_reconcile(r)]
    assert len(ok) == len(F.CONTRACTS) - 1


def test_the_one_sample_row_where_ask_volume_exceeds_total_volume():
    assert F.STVX_SCR["ask_side_volume"] > F.STVX_SCR["volume"]
    assert flow.contract_sides_reconcile(F.STVX_SCR) is False
    # It must still produce a share in [0, 1] rather than 1.03.
    s = flow.contract_ask_share(F.STVX_SCR)
    assert 0.0 <= s <= 1.0


def test_contract_ask_share_is_none_when_nobody_took_a_side():
    row = dict(F.FJRD_SCR)
    row["ask_side_volume"] = 0
    row["bid_side_volume"] = 0
    assert flow.contract_ask_share(row) is None


# ------------------------------------------------- NO DATA vs MEASURED ZERO

def test_open_interest_state_is_pending_not_zero_without_screener_rows():
    _, d = flow.score_opening([F.ACME_FLOOR], [])
    assert d["oi_state"] == "pending"
    assert d["oi_growth"] is None
    assert d["days_of_oi_increases"] is None


def test_open_interest_state_distinguishes_confirmed_from_contradicted():
    _, up = flow.score_opening([], [F.FJRD_SCR])
    assert up["oi_state"] == "confirmed"
    falling = dict(F.FJRD_SCR)
    falling["open_interest"] = 200
    falling["prev_oi"] = 400
    _, down = flow.score_opening([], [falling])
    assert down["oi_state"] == "contradicted"


def test_relative_detail_is_none_rather_than_zero_without_contracts():
    _, d = flow.score_relative([], [F.ACME_FLOOR], 146380.0)
    assert d["contract_share_of_ticker_volume"] is None


def test_aggression_is_unmeasured_rather_than_zero():
    assert flow.score_aggression(None) is None
    card = flow.build_card("ZENO", [F.ZENO_REPEATED], [], TODAY)
    assert card["detail"]["aggression"]["measured"] is False
    assert card["ask_share"] is None


# -------------------------------------------------------- NO "N OF M AGREE"

def test_card_does_not_ship_an_agreement_count():
    card = flow.build_card("FJRD", [], [F.FJRD_SCR], TODAY)
    for key in ("agree", "agree_count", "signals_agreeing", "n_of_m"):
        assert key not in card


def test_card_names_which_components_carried_the_score():
    card = flow.build_card("FJRD", [], [F.FJRD_SCR], TODAY)
    assert card["carried_by"]
    assert all(k in flow.WEIGHTS for k in card["carried_by"])


def test_carried_by_is_not_unanimous_across_different_tickers():
    # If every card is carried by the same two components the display is
    # decoration -- the exact failure the Insider Desk found in its top 25.
    seen = set()
    for t, a, c in [("FJRD", [], [F.FJRD_SCR]), ("SNWX", [], [F.SNWX_SCR]),
                    ("ACME", [F.ACME_FLOOR], []),
                    ("ZENO", [F.ZENO_FLOOR], []), ("QRTX", [F.QRTX_ZERO_OI], [])]:
        seen.add(tuple(flow.build_card(t, a, c, TODAY)["carried_by"]))
    assert len(seen) > 1


# ----------------------------------------------------------- BOARD BEHAVIOUR

def test_the_board_gate_is_the_75_percent_ask_side():
    assert flow.ASK_GATE == 0.75
    card = flow.build_card("FJRD", [], [F.FJRD_SCR], TODAY)
    assert card["meets_ask_gate"] is True


def test_a_near_miss_is_a_separate_lane_not_a_disappearance():
    row = dict(F.FJRD_SCR)
    row["ask_side_volume"] = 680
    row["bid_side_volume"] = 320          # 0.68
    card = flow.build_card("FJRD", [], [row], TODAY)
    assert card["meets_ask_gate"] is False
    assert card["near_gate"] is True


def test_a_qualifying_card_needs_real_premium_not_just_a_clean_ratio():
    tiny = dict(F.ACME_FLOOR)
    tiny["total_premium"] = "9000"
    tiny["total_ask_side_prem"] = "9000"
    tiny["total_bid_side_prem"] = "0"
    card = flow.build_card("TINY", [tiny], [], TODAY)
    assert card["meets_ask_gate"] is True
    assert flow.qualifies(card) is False


def test_a_cross_contaminated_card_is_kept_off_the_board_with_a_reason():
    # STVX clears the 75% gate with $1.56M while 85% of its volume is a cross
    # and its side volumes did not reconcile.
    card = flow.build_card("STVX", [], [F.STVX_SCR], TODAY)
    assert card["meets_ask_gate"] is True
    assert card["reliable"] is False
    assert flow.qualifies(card) is False
    assert "reliable" in flow.exclusion_reason(card)


def test_every_rejected_card_states_a_reason():
    for a in F.ALERTS:
        card = flow.build_card(a["ticker"], [a], [], TODAY)
        if not flow.qualifies(card):
            assert flow.exclusion_reason(card)
    for c in F.CONTRACTS:
        card = flow.build_card(c["ticker_symbol"], [], [c], TODAY)
        if not flow.qualifies(card):
            assert flow.exclusion_reason(card)


def test_a_qualifying_card_has_no_exclusion_reason():
    card = flow.build_card("FJRD", [], [F.FJRD_SCR], TODAY)
    assert flow.qualifies(card) is True
    assert flow.exclusion_reason(card) is None


def test_a_thin_ticket_is_multiplied_down_so_it_does_not_top_the_board():
    # TNYX: a clean 100% ask share and a sweep on only $14,310. Without the
    # thin-ticket haircut a print this small would rank near the top.
    card = flow.build_card("TNYX", [F.TNYX_TINY_OI], [], TODAY)
    assert "thin" in [n["kind"] for n in card["modifier_notes"]]
    assert card["modifier"] < 1.0


def test_sample_tickers_order_the_way_the_tape_reads():
    fjrd = flow.build_card("FJRD", [], [F.FJRD_SCR], TODAY)
    mega = flow.build_card("MEGA", [F.MEGA_0DTE], [F.MEGA_SCR], TODAY)
    crsx = flow.build_card("CRSX", [], [F.CRSX_SCR], TODAY)
    assert fjrd["score"] > mega["score"]
    assert fjrd["score"] > crsx["score"]


def test_the_board_separates_a_real_name_from_megacap_noise_by_a_wide_margin():
    fjrd = flow.build_card("FJRD", [], [F.FJRD_SCR], TODAY)
    mega = flow.build_card("MEGA", [F.MEGA_0DTE], [F.MEGA_SCR], TODAY)
    # Confluence Desk's 2% over-correction collapsed a 12.6 point gap to 1.7.
    # A gate that barely separates is not a gate.
    assert fjrd["score"] - mega["score"] > 15.0


# ----------------------------------------------------------------- COERCION

def test_num_survives_every_shape_the_endpoints_produce():
    assert flow.num("58591655.00") == 58591655.0
    assert flow.num(None) == 0.0
    assert flow.num("") == 0.0
    assert flow.num("None") == 0.0
    assert flow.num(float("nan")) == 0.0
    assert flow.num(float("inf")) == 0.0
    assert flow.num(True) == 1.0
    assert flow.num("0") == 0.0


def test_ramp_and_logramp_are_bounded_and_monotone():
    assert flow.ramp(-1e9, 0, 1) == 0.0
    assert flow.ramp(1e9, 0, 1) == 1.0
    assert flow.logramp(0, 1, 10) == 0.0
    assert flow.logramp(-5, 1, 10) == 0.0
    assert flow.logramp(1e9, 1, 10) == 1.0
    prev = -1
    for x in range(1, 200):
        v = flow.logramp(x, 1, 100)
        assert v >= prev
        prev = v


def test_inverted_ramp_works_for_decaying_curves():
    assert flow.ramp(21, 120, 21) == 1.0
    assert flow.ramp(120, 120, 21) == 0.0
    assert 0 < flow.ramp(60, 120, 21) < 1


# ------------------------------------------------------------- EMPTY / JUNK

def test_an_empty_card_does_not_raise_and_scores_zero_ish():
    card = flow.build_card("NONE", [], [], TODAY)
    assert card["score"] >= 0.0
    assert card["ask_share"] is None
    assert flow.qualifies(card) is False


def test_every_fixture_row_builds_a_card_without_raising():
    for a in F.ALERTS:
        flow.build_card(a["ticker"], [a], [], TODAY)
    for c in F.CONTRACTS:
        flow.build_card(c["ticker_symbol"], [], [c], TODAY)


def test_card_payload_carries_every_key_the_ui_reads():
    # Confluence Desk lost `pressure` and then `testable_days` between the
    # aggregate and the card, twice, and nothing failed loudly because the
    # scoring path reads the aggregate while only the display reads the card.
    card = flow.build_card("FJRD", [F.ACME_FLOOR], [F.FJRD_SCR], TODAY)
    for key in ("ticker", "score", "direction", "parts", "max_parts", "detail",
                "ask_premium", "sold_premium", "undetermined_premium",
                "ask_share", "meets_ask_gate", "near_gate", "carried_by",
                "modifier", "modifier_notes", "alert_count", "contract_count",
                "session", "raw_score", "opposing_premium"):
        assert key in card, key
    for key in ("aggression", "sweep", "opening", "relative", "urgency",
                "conviction"):
        assert key in card["parts"], key
        assert key in card["max_parts"], key
    for key in ("ask_share", "measured", "contracts_measured",
                "best_contract_ask_share", "worst_contract_ask_share"):
        assert key in card["detail"]["aggression"], key
    for key in ("volume_over_oi", "oi_state", "oi_growth",
                "days_of_oi_increases", "size_greater_than_oi",
                "all_opening_trades"):
        assert key in card["detail"]["opening"], key
    for key in ("sweep_premium_share", "strongest_rule", "fill_urgency"):
        assert key in card["detail"]["sweep"], key
    for key in ("contract_share_of_ticker_volume", "premium_bp_of_marketcap"):
        assert key in card["detail"]["relative"], key
    for key in ("min_dte", "distinct_contracts", "transactions"):
        assert key in card["detail"]["urgency"], key


def test_display_values_are_not_derived_from_clamped_scores():
    # Insider/Confluence lesson: a clamp that belongs in the score must not
    # leak into the display. A 2861x volume/OI must still READ as 2861.
    row = dict(F.FJRD_SCR)
    row["volume"] = 286100
    row["open_interest"] = 100
    _, d = flow.score_opening([], [row])
    assert d["volume_over_oi"] == 2861.0


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
