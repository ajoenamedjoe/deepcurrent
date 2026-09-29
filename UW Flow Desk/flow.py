"""Unusual options flow — scoring for the Flow board and the Confluence flow leg.

Sixth desk. Folds into Confluence Desk: this module has two consumers.

  1. The FLOW BOARD scores a ticker's session flow 0-100 on its own terms and
     is entered by the flow itself.
  2. The CONFLUENCE COMPOSITE takes `flow_score / 100 * W_FLOW_LEG` as a fourth
     leg beside insider / dark pool / 13F.

Those are different entry criteria on purpose. A name with screaming flow and
no insider buy still deserves a card on the Flow board; it just is not a
confluence card.

------------------------------------------------------------------ DESIGN NOTES

Everything below that looks like a magic number was set against the
distribution of one real session (a ~50-row screener sample plus ~50 alerts),
not by eye on one ticker. tests/fixtures/sample_flow.py reproduces the shapes
and edge cases with synthetic data. That is the direct lesson of Confluence
Desk design lesson 9, where a 5% floor tuned by looking at one ticker scored
zero on every card for a week.

Three findings from that distribution drove the whole model:

  * Ranking by raw premium returns megacap 0DTE churn. The largest OTM
    vol>OI contracts in the market were megacap calls expiring the same day. An unusual-flow desk that surfaces those is broken, so premium
    carries the SMALLEST weight here (8) and everything else is normalised by
    the ticker's own activity.

  * sweep_volume / volume never exceeds 0.062 across the sample. A sweep
    component scored on that share is dead on arrival — the exact failure mode
    Confluence hit. The sweep leg therefore reads the flow-alerts endpoint,
    where a sweep is a per-alert boolean, not a volume share.

  * ask_side_volume / (ask_side_volume + bid_side_volume) at a 0.75 floor keeps
    a small slice of an already-filtered population, rejects every megacap 0DTE
    row (all near 50/50) and the multileg-cross rows (50-60%), and keeps the
    genuinely aggressive names (80-90%). That single
    statistic does most of the work of this desk.

NUMBERS THAT ARE FOR DISPLAY AND NEVER FOR SCORING (Insider Desk lesson 4):
  * `stock_price` vs strike distance — informative, but scoring it would rank
    the most out-of-the-money lottery tickets to the top of the board.
  * `iv_start` / `iv_end` — reported on the card; IV rising while premium is
    lifted is corroboration, but it is also mechanically caused by the buying,
    so scoring it double-counts the aggression component.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

# --------------------------------------------------------------------- WEIGHTS
# Sum to EXACTLY 100 so the composite never needs clipping. Institutional Desk
# clipped at 100 and put six different names at the same score; Swing Desk
# records the same bug. test_weights_sum_to_100 guards this.
W_AGGRESSION = 24.0   # how hard the buyer is lifting  -- the ">75% ask side" brief
W_SWEEP = 18.0        # sweep / floor urgency          -- the "sweeps" brief
W_OPENING = 20.0      # is this a NEW position
W_RELATIVE = 18.0     # unusual FOR THIS TICKER
W_URGENCY = 12.0      # tenor choice + repeat stacking
W_CONVICTION = 8.0    # absolute dollars

WEIGHTS = {
    "aggression": W_AGGRESSION,
    "sweep": W_SWEEP,
    "opening": W_OPENING,
    "relative": W_RELATIVE,
    "urgency": W_URGENCY,
    "conviction": W_CONVICTION,
}

# Sub-weights, each summing to its parent.
W_SWEEP_SHARE, W_SWEEP_RULE, W_SWEEP_FILL = 10.0, 5.0, 3.0
W_OPEN_VOI, W_OPEN_OI_CONF, W_OPEN_DAYS, W_OPEN_ALL_OPENING = 8.0, 6.0, 4.0, 2.0
W_REL_TICKER_SHARE, W_REL_MCAP = 10.0, 8.0
W_URG_DTE, W_URG_STACK = 5.0, 7.0

# The flow leg's share of the rescaled Confluence composite. The other three
# legs rescale 45/30/25 -> 35/25/20 so the four still total exactly 100.
W_FLOW_LEG = 20.0
CONFLUENCE_LEGS = {"insider": 35.0, "dark": 25.0, "institutional": 20.0,
                   "flow": W_FLOW_LEG}

# ------------------------------------------------------------------- THRESHOLDS
# The brief: gate the board at heavy ask side, over 75%.
ASK_GATE = 0.75
# Names between NEAR_GATE and ASK_GATE go to a separate "near miss" lane rather
# than vanishing. A hard threshold with nothing below it is how Swing Desk made
# setups flicker in and out of existence.
ASK_NEAR_GATE = 0.60
MIN_TICKER_PREMIUM = 100_000.0   # qualifying ask-side premium per ticker
# Below this much bought premium a card has no defensible direction. Found by
# replaying a real session: a megacap came back "BULLISH" off a few hundred
# dollars of ask-side premium, and another name came back "BEARISH" off a small
# put purchase while more than ten times that in puts were being SOLD. A direction label is a claim; it needs evidence.
MIN_DIRECTION_PREMIUM = 25_000.0
# A card whose evidence has been multiplied down this far is not weak, it is
# UNRELIABLE, and the two are different. One name cleared the 75% ask gate
# with over $1M of premium while most of its volume was a cross and its
# side volumes did not sum to its volume. It should not be on the board at all,
# and it should say why rather than disappearing.
RELIABILITY_FLOOR = 0.60
# Above this share of multi-leg volume a card goes to the spreads lane instead
# of the directional board. Set above the observed median (~0.25): below it
# the single-leg prints still dominate the contract.
SPREAD_LANE = 0.50
# If ask+bid premium is less than this share of total premium on an alert, the
# direction is UNMEASURED, not neutral. A SweepsFollowedByFloor row can print
# a total of which only ~6% is attributed to a side; reading 94% ask off
# ask/(ask+bid) would call a mid-printed block a screaming conviction buy.
MIN_SIDE_COVERAGE = 0.25

# ------------------------------------------------------------------- COERCION


def num(x, default=0.0):
    """Every numeric field on these endpoints can arrive as a string or null."""
    if x is None:
        return default
    if isinstance(x, bool):
        return float(x)
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return v


def ramp(x, lo, hi):
    """Linear 0->1 between lo and hi, clipped. hi may be below lo (inverted)."""
    if hi == lo:
        return 0.0 if x < lo else 1.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def logramp(x, lo, hi):
    """0->1 between lo and hi on a log scale. For quantities that span decades.

    volume/OI spans from well under 1 to the thousands in a single session,
    and OI growth spans a similar range. A linear ramp on either is 0 or 1 almost everywhere.
    """
    if x <= 0 or lo <= 0 or hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (math.log(x) - math.log(lo)) /
                        (math.log(hi) - math.log(lo))))


# ------------------------------------------------------------------ DIRECTION

BULLISH, BEARISH = "bullish", "bearish"


def direction_of(option_type, side):
    """The sign convention, written once and tested by name.

    GEX ES Desk correction 4 and the Swing Desk note both say the same thing:
    sign conventions in this domain are the single richest source of silent
    bugs. A put bought on the ask is BEARISH, and it is trivially easy to let
    ">75% ask side" mean "bullish" everywhere in a codebase.

      call bought (ask)  -> bullish
      put  bought (ask)  -> bearish
      call sold   (bid)  -> bearish
      put  sold   (bid)  -> bullish
    """
    t = (option_type or "").lower()
    if t not in ("call", "put"):
        return None
    if side == "ask":
        return BULLISH if t == "call" else BEARISH
    if side == "bid":
        return BEARISH if t == "call" else BULLISH
    return None


# ------------------------------------------------------- PER-ROW MEASUREMENTS


def contract_ask_share(row):
    """Ask share for a SCREENER contract row: ask / (ask + bid), by volume.

    Mid and neutral volume are excluded deliberately. On this endpoint
    neutral_volume tracks cross_volume almost exactly (e.g. 5000/5000), and a
    cross is a spread leg or a roll, not a side. Including it would let one
    huge cross drown out the few hundred contracts that actually chose a side.

    Returns None when no contract chose a side at all — "no data", which the
    card must render differently from a measured 0.0 (Confluence lesson 8).
    """
    a = max(0.0, num(row.get("ask_side_volume")))
    b = max(0.0, num(row.get("bid_side_volume")))
    if a + b <= 0:
        return None
    share = a / (a + b)
    # A row can print ask_side_volume ABOVE its total volume: the side fields
    # do not always sum to volume on this endpoint. Observed about once in ~60
    # rows, which is rare enough to be trusted by mistake.
    return max(0.0, min(1.0, share))


def contract_ask_premium(row):
    """Dollar premium that printed on the ask, for a SCREENER contract row.

    `premium` is the contract's whole session dollar premium, so the ask-side
    portion is apportioned by the ask share of volume. Approximate -- premium
    per contract varies through the session -- but it is the only ask-side
    dollar figure available without the full tape, and the alternative (leaving
    it at zero) is far worse: it silently made the two cleanest names in
    the calibration sample fail the board's premium gate while a single floor
    ticket in another name passed. That is an architecture artifact that looks
    exactly like a real fade.
    """
    v = num(row.get("volume"))
    if v <= 0:
        return 0.0
    a = max(0.0, min(v, num(row.get("ask_side_volume"))))
    return num(row.get("premium")) * (a / v)


def contract_sides_reconcile(row):
    """True when ask+bid+mid+neutral == volume, as the endpoint usually holds."""
    v = num(row.get("volume"))
    s = sum(num(row.get(k)) for k in
            ("ask_side_volume", "bid_side_volume", "mid_volume",
             "neutral_volume"))
    return abs(s - v) < 1e-9


def alert_ask_share(row):
    """Ask share for a FLOW-ALERT row -- a DIFFERENT denominator on purpose.

    On the alerts endpoint `total_ask_side_prem + total_bid_side_prem` does not
    reconcile to `total_premium`, and the gap can be almost everything: a
    SweepsFollowedByFloor alert can report ask + bid that cover most of the
    total while its RepeatedHits sibling on the same contract attributes ~94%
    of the money to neither side.

    So the share is measured against `total_premium`, and a row whose two sides
    cover less than MIN_SIDE_COVERAGE of the total is reported as UNMEASURED.

    Returns (share, coverage, determinable).
    """
    ask = max(0.0, num(row.get("total_ask_side_prem")))
    bid = max(0.0, num(row.get("total_bid_side_prem")))
    total = num(row.get("total_premium"))
    if total <= 0:
        return (None, 0.0, False)
    coverage = min(1.0, (ask + bid) / total)
    if coverage < MIN_SIDE_COVERAGE:
        return (None, coverage, False)
    return (min(1.0, ask / total), coverage, True)


def volume_over_oi(row):
    """Computed LOCALLY. Never read `volume_oi_ratio` off the payload.

    A contract with open_interest 0 and a few dozen contracts traded comes back
    with volume_oi_ratio "0". A contract with zero open interest is the most
    unambiguously OPENING thing on the tape, and the payload reports it at the
    bottom of the scale. Other contracts did the same in the same session,
    so this is the documented behaviour and not a one-off.

    The endpoint's own filters treat OI 0 as 1; the returned field does not.
    """
    return num(row.get("volume")) / max(num(row.get("open_interest")), 1.0)


def is_multileg_contaminated(alert):
    """Spread / roll legs must not be read as directional conviction.

    An alert can print has_singleleg False, has_multileg True, expiry_count 2 --
    a two-expiry floor trade, i.e. a roll. On the screener side some names
    have 90%+ of their volume in a single cross.
    """
    if alert.get("has_multileg") and not alert.get("has_singleleg"):
        return True
    if num(alert.get("expiry_count"), 1) > 1:
        return True
    return False


def cross_contamination(contract):
    """Share of volume that took NO side: neutral and cross prints.

    Deliberately NOT including multileg_volume, which is a different problem --
    see spread_share. Conflating them was a real bug: the first live board
    rejected 29 of 50 contracts as "unreliable" when only 6 of them had any
    neutral volume at all. A contract that is ~99% multileg and 0% neutral,
    with thousands of contracts on the ask against a handful on the bid, is an
    enormous spread, not an unreadable print, and calling it contaminated was wrong.

    Live distribution of neutral/cross share: zero through the 90th percentile,
    0.504 at the 90th, 0.987 max. It is genuinely rare, which is what a
    reliability killer should be.
    """
    v = num(contract.get("volume"))
    if v <= 0:
        return 0.0
    worst = max(num(contract.get("neutral_volume")),
                num(contract.get("cross_volume")))
    return max(0.0, min(1.0, worst / v))


def spread_share(contract):
    """Share of volume that is a leg of a multi-leg order.

    A spread leg picks a side -- it can be 100% ask -- so it is not
    *unreadable*. It is directionally AMBIGUOUS: a call bought on the ask is
    bullish on its own and means nothing until you know whether it is the long
    leg of a debit spread, the short leg of someone else's, or half a collar.

    So these do not get haircut into oblivion, they go to their own lane. UW's
    own `unusual=true` preset takes the same position by requiring single-leg.

    Live distribution: median 0.249, 70th 0.872, max 1.0. Spread flow is the
    norm, not the exception, which is exactly why it needs a lane rather than a
    penalty.
    """
    v = num(contract.get("volume"))
    if v <= 0:
        return 0.0
    return max(0.0, min(1.0, num(contract.get("multileg_volume")) / v))


# ------------------------------------------------------------------ DE-DUPING


def dedupe_alerts(alerts):
    """Drop alerts that another alert on the same contract already contains.

    One put contract produced a RepeatedHits alert and a SweepsFollowedByFloor
    alert with the IDENTICAL start_time, the second being the first plus the
    floor prints that followed. Summing them books nearly double the real
    premium.

    Same family as the Insider Desk's "27 filings from 2 people": the tape
    hands you overlapping aggregations and the naive sum flatters the card.

    Rule: within one contract, an alert is dropped when another alert on that
    contract starts at or before it, ends at or after it, and carries at least
    as much premium.
    """
    by_contract = {}
    for a in alerts:
        by_contract.setdefault(a.get("option_chain"), []).append(a)

    kept = []
    for _, group in by_contract.items():
        for a in group:
            a_start, a_end = num(a.get("start_time")), num(a.get("end_time"))
            a_prem = num(a.get("total_premium"))
            subsumed = False
            for b in group:
                if b is a or b.get("id") == a.get("id"):
                    continue
                b_start, b_end = num(b.get("start_time")), num(b.get("end_time"))
                b_prem = num(b.get("total_premium"))
                covers = b_start <= a_start and b_end >= a_end
                bigger = b_prem > a_prem or (
                    b_prem == a_prem and str(b.get("id")) < str(a.get("id")))
                if covers and bigger:
                    subsumed = True
                    break
            if not subsumed:
                kept.append(a)
    kept.sort(key=lambda r: (-num(r.get("total_premium")), str(r.get("id"))))
    return kept


# ------------------------------------------------------------------- CALENDAR


def _parse_day(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def dte(expiry, today):
    d = _parse_day(expiry)
    if d is None:
        return None
    return (d - today).days


def days_to_earnings(next_earnings_date, today):
    """May be None, and may be NEGATIVE.

    A ticker can report a next_earnings_date of YESTERDAY -- the field had not
    rolled forward after the event. Several names did the same. A naive
    `0 <= days <= 7` earnings-week test passes a date in the past only if the
    sign is checked, and a naive `days <= 7` passes it always.
    Some names report null.
    """
    d = _parse_day(next_earnings_date)
    if d is None:
        return None
    return (d - today).days


ET_OFFSET_HOURS = -5
# Deliberately a FIXED -5, and deliberately different from flowscan.et_offset,
# which implements the real DST rule. They are not inconsistent -- they answer
# different questions, and a future reader should not "fix" one to match the
# other:
#   * bucketing a print into its session (here) only needs an offset that puts
#     the whole 04:00-20:00 ET window on one calendar date. -5 does that in
#     both DST and standard time, with no tzdata -- which is not installed on
#     Windows and would break the no-install promise.
#   * deciding whether the market is open right now (flowscan) needs the real
#     wall-clock offset, because 09:30 ET is 13:30Z in summer and 14:30Z in
#     winter. A fixed -5 there would open the desk an hour late for eight
#     months of the year.


def session_date(ms_epoch):
    """Bucket a millisecond UTC timestamp into its US trading session date."""
    dt = datetime.fromtimestamp(num(ms_epoch) / 1000.0, tz=timezone.utc)
    return (dt.timestamp() + ET_OFFSET_HOURS * 3600)


def session_day(ms_epoch):
    ts = session_date(ms_epoch)
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


# ------------------------------------------------------------------ COMPONENTS


def score_aggression(ask_share):
    """0 at the midpoint, 1 at 0.95. Returns None when unmeasured.

    Anchored at 0.50 because below it the premium belongs to the OTHER
    direction, not to a weaker version of this one. 0.75 -- the board's gate -- lands
    at 0.56, which is deliberately mid-scale: clearing the gate should not by
    itself max the component.
    """
    if ask_share is None:
        return None
    return ramp(ask_share, 0.50, 0.95)


_STRONG_RULES = {
    # SweepsFollowedByFloor is the strongest single rule on the endpoint: an
    # electronic sweep and then size crossed on the floor is one participant
    # finishing what they started.
    "SweepsFollowedByFloor": 1.0,
    "LowHistoricVolumeFloor": 0.85,   # size in a name that normally has none
    "FloorTradeSmallCap": 0.80,
    "FloorTradeMidCap": 0.65,
    "OtmEarningsFloor": 0.60,
    "FloorTradeLargeCap": 0.45,
    "RepeatedHitsAscendingFill": 0.40,
    "RepeatedHitsDescendingFill": 0.40,
    "RepeatedHits": 0.30,
}


def score_sweep(alerts, direction):
    """Sweep and floor urgency, read from the ALERTS endpoint.

    Not from the screener's sweep_volume/volume: that share tops out around 0.06
    across a whole session's sample, so a component built on it pays nothing on
    any ticker ever. Finding that in the distribution BEFORE shipping is the
    entire point of Confluence Desk design lesson 9.

    Three parts: how much of the qualifying premium arrived via sweeps, the
    strongest rule that fired, and whether the fill sequence shows the buyer
    paying up.
    """
    if not alerts:
        return 0.0, {}
    total = sum(num(a.get("total_premium")) for a in alerts)
    if total <= 0:
        return 0.0, {}

    swept = sum(num(a.get("total_premium")) for a in alerts if a.get("has_sweep"))
    floored = sum(num(a.get("total_premium")) for a in alerts if a.get("has_floor"))
    share = (swept + floored) / total
    # 0.6 not 1.0: requiring every dollar to arrive swept would make the top of
    # the component unreachable, which is how a leg quietly stops discriminating.
    share_pts = ramp(share, 0.0, 0.60) * W_SWEEP_SHARE

    best_rule, best_val = None, 0.0
    for a in alerts:
        v = _STRONG_RULES.get(a.get("alert_rule"), 0.25)
        if v > best_val:
            best_val, best_rule = v, a.get("alert_rule")
    rule_pts = best_val * W_SWEEP_RULE

    # AscendingFill means each successive fill printed at or above the last:
    # the buyer walked up the offers. That is urgency in the direction of the
    # trade, so it only counts when the fill direction AGREES with the read.
    # Descending fills on a put being bought are the same thing for a seller
    # hitting bids -- the mirror, not the opposite.
    fill = 0.0
    for a in alerts:
        rule = a.get("alert_rule")
        t = (a.get("type") or "").lower()
        if rule == "RepeatedHitsAscendingFill":
            paying_up = (t == "call" and direction == BULLISH) or \
                        (t == "put" and direction == BEARISH)
            if paying_up:
                fill = 1.0
        elif rule == "RepeatedHitsDescendingFill":
            fill = max(fill, 0.35)
    fill_pts = fill * W_SWEEP_FILL

    return share_pts + rule_pts + fill_pts, {
        "sweep_premium_share": round(share, 4),
        "strongest_rule": best_rule,
        "fill_urgency": round(fill, 2),
    }


def score_opening(alerts, contracts):
    """Is this a NEW position, or someone closing / rolling one?

    The honest position here, which the card states in words: today's flow is
    UNCONFIRMED. Open interest updates once the following morning, so
    `open_interest` vs `prev_oi` on the screener confirms YESTERDAY's
    positioning. The UW tool docs say this outright and it is the one caveat
    most flow readers skip.
    """
    detail = {}

    voi = max((volume_over_oi(c) for c in contracts), default=0.0)
    if not contracts:
        voi = max((volume_over_oi(a) for a in alerts), default=0.0)
    # Live deciles: median 2.16, 70th 6.4, 90th 15.9. Anchoring at 1x-30x puts
    # the median near 0.45 and keeps the top reachable without rewarding the
    # 2861x outlier any more than the 30x one.
    voi_pts = logramp(voi, 1.0, 30.0) * W_OPEN_VOI
    detail["volume_over_oi"] = round(voi, 2)

    # size > OI on the alert: the single order was larger than everything
    # already outstanding, so it cannot be purely a close.
    size_gt_oi = any(num(a.get("total_size")) > num(a.get("open_interest"))
                     for a in alerts)
    detail["size_greater_than_oi"] = bool(size_gt_oi)

    if contracts:
        growth = max((num(c.get("open_interest")) - num(c.get("prev_oi"))) /
                     max(num(c.get("prev_oi")), 1.0) for c in contracts)
        detail["oi_growth"] = round(growth, 3)
        if growth > 0:
            detail["oi_state"] = "confirmed"
        elif growth < 0:
            detail["oi_state"] = "contradicted"
        else:
            detail["oi_state"] = "flat"
        conf_pts = logramp(1.0 + max(growth, 0.0), 1.05, 3.0) * W_OPEN_OI_CONF
        days = max(num(c.get("days_of_oi_increases")) for c in contracts)
        detail["days_of_oi_increases"] = int(days)
        days_pts = ramp(days, 0.0, 5.0) * W_OPEN_DAYS
    else:
        # Not measurable rather than zero. The card renders this as "awaiting
        # tomorrow's open interest", never as a bar at zero, because a bar at
        # zero reads as evidence AGAINST the name.
        detail["oi_growth"] = None
        detail["oi_state"] = "pending"
        detail["days_of_oi_increases"] = None
        conf_pts = 0.0
        days_pts = 0.0

    all_open = any(a.get("all_opening_trades") for a in alerts)
    detail["all_opening_trades"] = bool(all_open)
    # A bonus, never a gate. The endpoint docs warn that all_opening_trades is
    # almost never true for the RepeatedHits family, so gating on it would
    # empty the board.
    open_pts = (1.0 if all_open else 0.0) * W_OPEN_ALL_OPENING

    if size_gt_oi:
        voi_pts = max(voi_pts, 0.55 * W_OPEN_VOI)

    return voi_pts + conf_pts + days_pts + open_pts, detail


def score_relative(contracts, alerts, ask_premium):
    """Unusual FOR THIS TICKER. The component that kills megacap 0DTE noise.

    Both halves of every ratio here are filtered the same way, which is the
    generalised form of Confluence Desk design lesson 9: a ratio is only
    comparable across tickers when its numerator and denominator describe the
    same population.
    """
    detail = {}

    # Contract volume as a share of the ticker's WHOLE option session, which
    # the screener payload carries as ticker_vol. Live deciles: median 0.032,
    # 90th 0.144. 0.02 -> 0 and 0.25 -> 1 puts the median at 0.05 and leaves
    # the top of the component genuinely reachable.
    share = 0.0
    for c in contracts:
        tv = num(c.get("ticker_vol"))
        if tv > 0:
            share = max(share, num(c.get("volume")) / tv)
    detail["contract_share_of_ticker_volume"] = round(share, 4) if contracts else None
    share_pts = ramp(share, 0.02, 0.25) * W_REL_TICKER_SHARE

    # Premium against market cap. A $500k print into a $2B company is a
    # different event from the same print into a $5T one -- this is the Insider
    # Desk's `size` component reused, and it is what separates a modest ticket
    # in a mid-cap from an identical ticket in a megacap.
    mcap = 0.0
    for r in list(alerts) + list(contracts):
        mcap = max(mcap, num(r.get("marketcap")))
    if mcap > 0:
        bp = ask_premium / mcap * 10_000.0
        detail["premium_bp_of_marketcap"] = round(bp, 3)
        mcap_pts = logramp(bp, 0.05, 5.0) * W_REL_MCAP
    else:
        detail["premium_bp_of_marketcap"] = None
        mcap_pts = 0.0

    return share_pts + mcap_pts, detail


def score_urgency(alerts, contracts, today):
    """Tenor choice and repeat stacking.

    The DTE curve is an INVERTED U and that is deliberate. "Shorter dated means
    more urgent" is the intuition and it is wrong at the short end: 27 of the
    50 highest-premium contracts in the live sample were 0DTE, and 0DTE flow is
    overwhelmingly market-making, hedging and lottery tickets rather than
    positioning. Peak urgency sits at roughly 1-21 days, decays into LEAPs.
    """
    detail = {}
    dtes = [d for d in (dte(a.get("expiry"), today) for a in alerts)
            if d is not None]
    dtes += [d for d in (dte(c.get("expiry"), today) for c in contracts)
             if d is not None]
    if dtes:
        best = 0.0
        for d in dtes:
            if d <= 0:
                v = 0.25          # 0DTE: explicitly NOT maximum urgency
            elif d <= 21:
                v = 1.0
            elif d <= 60:
                v = ramp(d, 120.0, 21.0)
            else:
                v = ramp(d, 400.0, 60.0) * 0.5
            best = max(best, v)
        detail["min_dte"] = min(dtes)
        dte_pts = best * W_URG_DTE
    else:
        detail["min_dte"] = None
        dte_pts = 0.0

    # Stacking: distinct contracts hit in the same direction, and how many
    # separate transactions made up the alerts. One 1,000-lot is a decision;
    # thirty-seven fills walking a strike is a campaign.
    chains = {a.get("option_chain") for a in alerts if a.get("option_chain")}
    chains |= {c.get("option_symbol") for c in contracts if c.get("option_symbol")}
    trades = sum(int(num(a.get("trade_count"))) for a in alerts)
    detail["distinct_contracts"] = len(chains)
    detail["transactions"] = trades
    stack = 0.6 * ramp(len(chains), 1.0, 4.0) + 0.4 * ramp(trades, 5.0, 60.0)
    return dte_pts + stack * W_URG_STACK, detail


def score_conviction(ask_premium):
    """Absolute dollars. The SMALLEST weight in the model, on purpose.

    Premium is the number every flow product leads with and it is the least
    discriminating thing on the tape: it ranks the largest, most liquid names
    first no matter what they are doing. $50k -> 0, $5M -> 1.
    """
    return logramp(ask_premium, 50_000.0, 5_000_000.0) * W_CONVICTION


# ------------------------------------------------------------------ MODIFIERS


def modifiers(alerts, contracts, today, ask_premium=None):
    """Multiply DOWN only, as on the Insider and Confluence desks.

    A modifier that can multiply up is a weight in disguise and escapes the
    weights-sum-to-100 test.
    """
    mods, notes = 1.0, []

    contam = max([cross_contamination(c) for c in contracts] or [0.0])
    if contam > 0.25:
        m = max(0.35, 1.0 - contam)
        mods *= m
        notes.append({"kind": "cross", "factor": round(m, 3),
                      "detail": f"{contam:.0%} of volume printed with no side"})

    spread = max([spread_share(c) for c in contracts] or [0.0])
    if any(is_multileg_contaminated(a) for a in alerts):
        spread = max(spread, 0.75)
    if spread > SPREAD_LANE:
        mods *= 0.85
        notes.append({"kind": "spread", "factor": 0.85,
                      "detail": f"{spread:.0%} of volume is a multi-leg order"})

    # Thin cards. Conviction carries only 8 points on purpose, which means a
    # ~$13k ticket with a clean 100% ask share and a sweep scored in the 40s on
    # the first replay -- fifth on the board. Size is not what makes flow
    # unusual, but a card this small is not evidence of anything either.
    if ask_premium is not None and 0 < ask_premium < MIN_DIRECTION_PREMIUM:
        m = 0.5 + 0.5 * (ask_premium / MIN_DIRECTION_PREMIUM)
        mods *= m
        notes.append({"kind": "thin", "factor": round(m, 3),
                      "detail": f"only ${ask_premium:,.0f} of premium bought"})

    # A row whose side volumes do not reconcile to its volume cannot support a
    # directional claim. A row can print ask_side_volume ABOVE its total volume
    # -- about once in sixty rows, which is rare
    # enough to be trusted by mistake and common enough to reach the board.
    broken = [c for c in contracts if not contract_sides_reconcile(c)]
    if broken and len(broken) == len(contracts):
        mods *= 0.6
        notes.append({"kind": "unreconciled", "factor": 0.6,
                      "detail": "side volumes do not sum to contract volume"})

    d = None
    for r in list(alerts) + list(contracts):
        v = days_to_earnings(r.get("next_earnings_date"), today)
        if v is not None and v >= 0 and (d is None or v < d):
            d = v
    if d is not None and d <= 7:
        mods *= 0.85
        notes.append({"kind": "earnings", "factor": 0.85,
                      "detail": f"earnings in {d} day(s)"})

    return mods, notes


# ------------------------------------------------------------------- THE CARD


def build_card(ticker, alerts, contracts, today=None, name=None):
    """Score one ticker's session flow 0-100.

    `alerts`    de-duped /api/option-trades/flow-alerts rows for this ticker
    `contracts` /api/screener/option-contracts rows for this ticker
    """
    today = today or date.today()
    alerts = list(alerts or [])
    contracts = list(contracts or [])

    # --- direction and the ask-side premium that defines it -----------------
    # Only BOUGHT premium is scored. Sold-side premium (a put sold, a call
    # written) is real information and is carried on the card, but the brief is
    # about ask-side aggression and mixing a weaker signal into the same number
    # would need a weight I cannot defend from two days of tape.
    bull_ask = bear_ask = bull_bid = bear_bid = 0.0
    undetermined = 0.0
    alert_ask_prem = alert_directional_total = 0.0
    for a in alerts:
        _, coverage, ok = alert_ask_share(a)
        total = num(a.get("total_premium"))
        t = (a.get("type") or "").lower()
        if not ok:
            undetermined += total
            continue
        ask_prem = num(a.get("total_ask_side_prem"))
        bid_prem = num(a.get("total_bid_side_prem"))
        alert_ask_prem += ask_prem
        alert_directional_total += total
        if t == "call":
            bull_ask += ask_prem
            bear_bid += bid_prem
        elif t == "put":
            bear_ask += ask_prem
            bull_bid += bid_prem

    for c in contracts:
        ap = contract_ask_premium(c)
        bp = max(0.0, num(c.get("premium")) - ap)
        t = (c.get("option_type") or "").lower()
        if t == "call":
            bull_ask += ap
            bear_bid += bp
        elif t == "put":
            bear_ask += ap
            bull_bid += bp

    # Direction is a CLAIM and needs evidence. Without enough bought premium on
    # either side the card is explicitly undirected rather than defaulting to
    # bullish, which is what the first replay did on seven of eighteen
    # tickers -- including a megacap off a few hundred dollars.
    if max(bull_ask, bear_ask) < MIN_DIRECTION_PREMIUM:
        direction = None
        ask_premium = max(bull_ask, bear_ask)
        opposing = min(bull_ask, bear_ask)
        sold_premium = max(bull_bid, bear_bid)
    else:
        direction = BULLISH if bull_ask >= bear_ask else BEARISH
        ask_premium = bull_ask if direction == BULLISH else bear_ask
        opposing = bear_ask if direction == BULLISH else bull_ask
        # Premium SOLD that argues the same way as the bought premium: puts
        # written under a bullish read, calls written under a bearish one.
        sold_premium = bull_bid if direction == BULLISH else bear_bid
    opposing_sold = bear_bid if direction == BULLISH else bull_bid

    # Ask share across the ticker's qualifying contracts, premium-weighted.
    # Confluence Desk design lesson 1: a premium-weighted MEAN of a bimodal
    # distribution returns its dead centre. Here the distribution is not
    # bimodal per ticker (it is one participant's behaviour on a handful of
    # contracts) but the same caution applies, so the card prints the contract
    # count the share was measured over and the worst and best contract share,
    # not only the mean.
    #
    # The two sources are NEVER blended into one number. A screener share is
    # ask/(ask+bid) by VOLUME with crosses excluded; an alert share is ask
    # premium over TOTAL premium. They answer different questions against
    # different denominators, and averaging them would produce a statistic that
    # describes neither -- the generalised form of Confluence Desk lesson 1.
    shares = [s for s in (contract_ask_share(c) for c in contracts)
              if s is not None]
    contract_share = None
    if shares:
        wsum = tot = 0.0
        for c in contracts:
            s = contract_ask_share(c)
            if s is None:
                continue
            w = num(c.get("premium")) or num(c.get("volume"))
            wsum += s * w
            tot += w
        contract_share = wsum / tot if tot > 0 else sum(shares) / len(shares)

    alert_share = (alert_ask_prem / alert_directional_total
                   if alert_directional_total > 0 else None)

    if contract_share is not None:
        ask_share, ask_share_source = contract_share, "contract_volume"
    elif alert_share is not None:
        ask_share, ask_share_source = alert_share, "alert_premium"
    else:
        ask_share, ask_share_source = None, None

    # --- components ----------------------------------------------------------
    parts, detail = {}, {}

    agg = score_aggression(ask_share)
    parts["aggression"] = 0.0 if agg is None else agg * W_AGGRESSION
    detail["aggression"] = {
        "ask_share": None if ask_share is None else round(ask_share, 4),
        "ask_share_source": ask_share_source,
        "contract_ask_share": (None if contract_share is None
                               else round(contract_share, 4)),
        "alert_ask_premium_share": (None if alert_share is None
                                    else round(alert_share, 4)),
        "measured": agg is not None,
        "contracts_measured": len(shares),
        "best_contract_ask_share": round(max(shares), 4) if shares else None,
        "worst_contract_ask_share": round(min(shares), 4) if shares else None,
        "undetermined_premium": round(undetermined, 2),
    }

    parts["sweep"], detail["sweep"] = score_sweep(alerts, direction)
    parts["opening"], detail["opening"] = score_opening(alerts, contracts)
    parts["relative"], detail["relative"] = score_relative(
        contracts, alerts, ask_premium)
    parts["urgency"], detail["urgency"] = score_urgency(alerts, contracts, today)
    parts["conviction"] = score_conviction(ask_premium)
    detail["conviction"] = {"ask_premium": round(ask_premium, 2)}

    raw = sum(parts.values())
    mod, mod_notes = modifiers(alerts, contracts, today, ask_premium)
    score = max(0.0, min(100.0, raw * mod))

    # Which two components carried the score. NOT an "N of M agree" count:
    # Swing Desk shipped one, Insider Desk found it read 4-of-4 on every card in
    # the top 25, and Confluence Desk avoided it by construction. The honest
    # display is where the points actually came from.
    ranked = sorted(parts.items(), key=lambda kv: -kv[1])
    carried = [k for k, v in ranked[:2] if v > 0]

    return {
        "ticker": ticker,
        "name": name,
        "score": round(score, 1),
        "raw_score": round(raw, 1),
        "modifier": round(mod, 3),
        "modifier_notes": mod_notes,
        "direction": direction,
        "direction_measured": direction is not None,
        "bought_call_premium": round(bull_ask, 2),
        "bought_put_premium": round(bear_ask, 2),
        "sold_call_premium": round(bear_bid, 2),
        "sold_put_premium": round(bull_bid, 2),
        "carried_by": carried,
        "parts": {k: round(v, 2) for k, v in parts.items()},
        "max_parts": dict(WEIGHTS),
        "detail": detail,
        "ask_premium": round(ask_premium, 2),
        "opposing_premium": round(opposing, 2),
        "sold_premium": round(sold_premium, 2),
        "opposing_sold_premium": round(opposing_sold, 2),
        "undetermined_premium": round(undetermined, 2),
        "ask_share": None if ask_share is None else round(ask_share, 4),
        "ask_share_source": ask_share_source,
        "meets_ask_gate": ask_share is not None and ask_share >= ASK_GATE,
        "near_gate": (ask_share is not None
                      and ASK_NEAR_GATE <= ask_share < ASK_GATE),
        "reliable": mod >= RELIABILITY_FLOOR,
        "is_spread": any(n["kind"] == "spread" for n in mod_notes),
        "spread_share": round(max([spread_share(c) for c in contracts] or [0.0]), 4),
        "cross_share": round(max([cross_contamination(c) for c in contracts] or [0.0]), 4),
        "alert_count": len(alerts),
        "contract_count": len(contracts),
        # What the tape panel opens: the largest alerts (their ids fetch the exact
        # prints) and the largest contracts (their OSI symbols fetch the day's tape).
        "alerts": slim_alerts(alerts),
        "contracts": slim_contracts(contracts),
        "session": str(today),
    }


ALERT_KEYS = ("id", "option_chain", "alert_rule", "type", "strike", "expiry", "total_premium",
              "total_ask_side_prem", "total_bid_side_prem", "total_size", "start_time", "end_time",
              "has_sweep", "has_floor", "has_multileg", "underlying_price", "price")
CONTRACT_KEYS = ("option_symbol", "option_type", "strike", "expiry", "volume", "open_interest",
                 "premium", "ask_side_volume", "bid_side_volume")


def slim_alerts(alerts, n=6):
    rows = sorted(alerts, key=lambda a: (-num(a.get("total_premium")), str(a.get("id"))))
    return [{k: a.get(k) for k in ALERT_KEYS if k in a} for a in rows[:n] if a.get("id")]


def slim_contracts(contracts, n=5):
    rows = sorted(contracts, key=lambda c: (-num(c.get("premium")), str(c.get("option_symbol"))))
    return [{k: c.get(k) for k in CONTRACT_KEYS if k in c} for c in rows[:n] if c.get("option_symbol")]


def exclusion_reason(card):
    """Why a card is not on the board, in words. Never let one just vanish.

    Swing Desk: a ticker that slipped out of the enrichment slice silently lost
    its points and dropped off the board, "an architecture artifact
    indistinguishable from a real fade". Anything the desk rejects says so.
    """
    if not card["direction_measured"]:
        return ("no direction: only $%s of premium was bought"
                % f"{card['ask_premium']:,.0f}")
    if card["modifier"] < RELIABILITY_FLOOR:
        kinds = ", ".join(n["kind"] for n in card["modifier_notes"]) or "unreliable"
        return "evidence not reliable (%s)" % kinds
    if card["ask_share"] is None:
        return "ask side never measured on any contract or alert"
    if not card["meets_ask_gate"]:
        return ("ask side %.0f%% is below the %.0f%% gate"
                % (card["ask_share"] * 100, ASK_GATE * 100))
    if card["ask_premium"] < MIN_TICKER_PREMIUM:
        return ("$%s bought is below the $%s floor"
                % (f"{card['ask_premium']:,.0f}",
                   f"{MIN_TICKER_PREMIUM:,.0f}"))
    return None


def qualifies(card):
    """Board entry. The brief: gate the board at ask side over 75%.

    A card also needs a defensible direction and evidence that survived the
    contamination checks: an undirected card is a pile of prints, not a read,
    and a card built on one cross is a spread leg wearing a conviction badge.
    """
    return exclusion_reason(card) is None and not card["is_spread"]


def lane(card):
    """Which lane a card belongs in. Every card lands in exactly one.

    board   -> the directional single-leg board the brief asked for
    spreads -> multi-leg orders: real flow, ambiguous direction
    near    -> 60-75% ask side, one step below the gate
    out     -> rejected, with exclusion_reason() saying why
    """
    if (card["is_spread"] and card["reliable"] and card["direction_measured"]
            and (card["meets_ask_gate"] or card["near_gate"])
            and card["ask_premium"] >= MIN_TICKER_PREMIUM):
        return "spreads"
    if qualifies(card):
        return "board"
    if near_miss(card) and card["reliable"]:
        return "near"
    return "out"


def near_miss(card):
    return (card["near_gate"] and card["ask_premium"] >= MIN_TICKER_PREMIUM)


def confluence_flow_leg(flow_score):
    """The flow leg of the rescaled Confluence composite, 0..W_FLOW_LEG."""
    return max(0.0, min(100.0, num(flow_score))) / 100.0 * W_FLOW_LEG
