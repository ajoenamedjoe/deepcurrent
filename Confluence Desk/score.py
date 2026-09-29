"""
Confluence Desk -- the 0-100 score.

Three legs, weighted to a maximum of EXACTLY 100 so the composite can never
need clipping (Institutional Desk bug #1: clipping at 100 put six different
names at exactly 100 and destroyed the top of the board).

    INSIDER        45   management is buying on the open market, right now
    DARK POOL      30   size is being worked off-exchange, right now
    INSTITUTIONAL  25   funds were accumulating as of the last 13F

    -------------------------------------------------------------
    A name with a perfect insider + dark pool read but no 13F support
    caps at 75 BY CONSTRUCTION. That is deliberate: the
    13F leg is a backdrop rather than a gate, so it lifts a card into
    the top quartile but its absence never hides one.

Each leg is built from sub-components in 0..1, multiplied by the leg weight.
`test_weights_sum_to_100` guards the arithmetic.

-------------------------------------------------------------------------
NUMBERS THAT ARE FOR DISPLAY AND MUST NEVER BE SCORED
-------------------------------------------------------------------------
* "the stock is now N% below what the CEO paid" -- genuinely useful on a
  card, but scoring it ranks falling knives to the top. (Carried over from
  the Insider Desk, where this is written into the module docstring for
  exactly this reason.)
* the 13F filing date -- shown on every card so the quarterly staleness is
  never mistaken for live data, but it carries no points. The design is
  "backdrop, no decay"; adding decay later means touching ONLY
  `institutional_leg`.
"""

import math

# ---------------------------------------------------------------- weights

W_INSIDER = 45.0
W_DARKPOOL = 30.0
W_INSTITUTIONAL = 25.0

# Insider sub-weights. Same proportions as the standalone Insider Desk
# (32/26/20/22 of 100), rescaled to 45 so the model carries over intact.
WI_CONVICTION = 14.4
WI_CLUSTER = 11.7
WI_RANK = 9.0
WI_SIZE = 9.9

# Dark pool sub-weights.
WD_BLOCK = 12.0      # print size vs the ticker's own normal
WD_SUSTAIN = 8.0     # distinct sessions of elevated off-exchange activity
WD_PROXIMITY = 6.0   # did the blocks land around the insider's buy dates
# DELIBERATELY THE SMALLEST WEIGHT IN THE WHOLE MODEL, and it started at 7.
#
# Print location is the one sub-signal here whose SIGN cannot be defended on
# the evidence available. In replay, a small cap that was visibly working
# size, its price walking steadily higher over the window, leaned to the
# BID (about half of clean premium at the bid, under a third at the offer). Two readings fit
# equally well: the lean is noise on a dirty field, or an institution really
# was distributing off-exchange into lit strength. Two tickers cannot tell
# them apart.
#
# Print location was part of the brief, so it ships -- but at 4 points it cannot
# decide a card on its own, `pressure` is written to `observations` on every
# scan so a quarter of tape can settle it, and the card prints the ask and bid
# shares side by side rather than only the verdict.
#
# Sign conventions in this domain are the single richest source of silent
# bugs. The right response to one you cannot verify is a small weight and a
# recorded number, not a confident weight and a comment.
WD_LEAN = 4.0

# Institutional sub-weights.
WN_FUNDS = 13.0      # how many independent funds
WN_WEIGHT = 7.0      # how big the position is inside the fund's book
WN_TRAJECTORY = 5.0  # building / new conviction vs steady vs harvesting

ALL_WEIGHTS = (
    WI_CONVICTION, WI_CLUSTER, WI_RANK, WI_SIZE,
    WD_BLOCK, WD_LEAN, WD_SUSTAIN, WD_PROXIMITY,
    WN_FUNDS, WN_WEIGHT, WN_TRAJECTORY,
)

# A component that is genuinely unmeasurable (too few clean dark pool prints,
# no 13F coverage for the ticker) scores 0 but is flagged so the card can say
# "no data" instead of implying the signal was measured and came back weak.
UNMEASURED = None


def clamp01(x):
    if x is None or x != x:
        return 0.0
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


def lerp_curve(x, points):
    """
    Piecewise-linear interpolation through (input, output) anchors, which is
    how every component in this desk is shaped. Anchors must be sorted by
    input. Flat outside the ends -- never extrapolates, so every component is
    bounded by construction and `clamp01` is a belt not a brace.
    """
    if x is None or x != x:
        return 0.0
    if x <= points[0][0]:
        return float(points[0][1])
    if x >= points[-1][0]:
        return float(points[-1][1])
    for i in range(1, len(points)):
        x1, y1 = points[i - 1]
        x2, y2 = points[i]
        if x <= x2:
            if x2 == x1:
                return float(y2)
            t = (x - x1) / (x2 - x1)
            return float(y1 + t * (y2 - y1))
    return float(points[-1][1])


# ================================================================= INSIDER

# officer_title is free text. First match wins, so ORDER IS LOAD-BEARING:
# every vice-president pattern MUST sit above the bare PRESIDENT pattern, or
# "Executive Vice President" scores as President.
# (Insider Desk design lesson 7 -- about 1% of rows were mislabelled by
# exactly this bug. `test_vice_president_is_not_president` guards it.)
_ROLE_PATTERNS = [
    (r"\bCHAIR", 0.88),
    (r"\bCHIEF\s+EXECUTIVE", 0.95),
    (r"\bCEO\b", 0.95),
    (r"\bCHIEF\s+FINANCIAL", 0.80),
    (r"\bCFO\b", 0.80),
    (r"\bCHIEF\s+OPERATING", 0.76),
    (r"\bCOO\b", 0.76),
    (r"\bCHIEF\b", 0.70),                      # any other C-suite
    (r"\bEXEC\w*\s+VICE[\s\-]?PRES", 0.64),    # EVP -- above PRESIDENT
    (r"\bEVP\b", 0.64),
    (r"\bSENIOR\s+VICE[\s\-]?PRES", 0.58),
    (r"\bSVP\b", 0.58),
    (r"\bVICE[\s\-]?PRES", 0.55),              # plain VP -- still above PRESIDENT
    (r"\bVP\b", 0.55),
    (r"\bPRESIDENT\b", 0.82),                  # only reached if no VP matched
    (r"\bDIRECTOR\b", 0.45),
    (r"\bGENERAL\s+COUNSEL", 0.50),
]

RANK_TEN_PERCENT_OWNER = 0.72
RANK_DIRECTOR = 0.45
RANK_UNKNOWN = 0.30   # the floor -- see "no agreement count" note below


def seniority(officer_title, is_director=False, is_ten_percent_owner=False,
              is_officer=False):
    """0..1 seniority of one insider. Never returns below RANK_UNKNOWN."""
    import re
    title = (officer_title or "").upper()
    best = 0.0
    if title.strip():
        for pattern, value in _ROLE_PATTERNS:
            if re.search(pattern, title):
                best = value
                break
    if is_ten_percent_owner:
        best = max(best, RANK_TEN_PERCENT_OWNER)
    if is_director:
        best = max(best, RANK_DIRECTOR)
    if is_officer:
        best = max(best, 0.50)
    return clamp01(max(best, RANK_UNKNOWN))


# Distinct reporter_cik in the window. A lone buyer is a fact; three
# independent buyers in a month is a different kind of fact.
_CLUSTER_TABLE = {1: 0.0, 2: 0.50, 3: 0.75, 4: 0.90}


def cluster_component(distinct_buyers):
    n = int(distinct_buyers or 0)
    if n <= 0:
        return 0.0
    return _CLUSTER_TABLE.get(n, 1.0)


def conviction_component(shares, shares_owned_before):
    """
    Best single stake growth: sqrt(shares / shares_owned_before).

    shares_owned_before of 0, null OR NEGATIVE means there is no percentage to
    compute. A few percent of rows are a brand-new position; a NEGATIVE value
    also appears (a corporate filer can come back with a negative
    shares_owned_before). Both are credited as "new stake" rather than dividing by
    zero or, worse, silently producing a negative ratio.
    """
    shares = float(shares or 0.0)
    if shares <= 0:
        return 0.0
    before = shares_owned_before
    if before is None or float(before) <= 0:
        return 0.85           # a brand-new stake, but not automatically a 1.0
    ratio = shares / float(before)
    return clamp01(math.sqrt(min(ratio, 1.0)))


def size_component(notional, marketcap):
    """
    0.7 x (notional relative to market cap) + 0.3 x (absolute dollars).

    Relative dominates because $200k means something very different at a
    $70M company than at a $150B one, but the absolute term stops a
    micro-cap's $60k buy from outranking a CEO's $5M cheque.
    """
    notional = max(0.0, float(notional or 0.0))
    rel = 0.0
    if marketcap and float(marketcap) > 0:
        bps = 10000.0 * notional / float(marketcap)
        rel = lerp_curve(bps, [(1.0, 0.0), (10.0, 0.5), (100.0, 1.0)])
    absolute = lerp_curve(
        notional, [(50e3, 0.0), (250e3, 0.35), (1e6, 0.7), (10e6, 1.0)]
    )
    return clamp01(0.7 * rel + 0.3 * absolute)


def insider_leg(agg):
    """
    agg: the per-company insider aggregate (see insider.py).
      distinct_buyers, best_stake_growth (shares, before), top_rank,
      notional, marketcap, frac_10b5_1, frac_corporate
    Returns (points, parts) where points <= W_INSIDER.
    """
    parts = {}
    parts["conviction"] = clamp01(agg.get("conviction", 0.0))
    parts["cluster"] = cluster_component(agg.get("distinct_buyers", 0))
    parts["rank"] = clamp01(agg.get("top_rank", 0.0))
    parts["size"] = size_component(agg.get("notional"), agg.get("marketcap"))

    raw = (
        WI_CONVICTION * parts["conviction"]
        + WI_CLUSTER * parts["cluster"]
        + WI_RANK * parts["rank"]
        + WI_SIZE * parts["size"]
    )

    # Modifiers only ever multiply DOWN, and only in proportion to how much of
    # the cluster they affect -- one planned sale among four discretionary
    # buyers should not haircut the whole company.
    mod = 1.0
    f_plan = clamp01(agg.get("frac_10b5_1", 0.0))
    f_corp = clamp01(agg.get("frac_corporate", 0.0))
    mod *= (1.0 - f_plan) + f_plan * 0.75
    mod *= (1.0 - f_corp) + f_corp * 0.70
    parts["modifier"] = mod
    return raw * mod, parts


# =============================================================== DARK POOL

def block_component(max_size_vs_avg30, window_notional, marketcap):
    """
    'Someone is working a size order.'

    Two halves, because either alone lies:
      * the single biggest print as a fraction of 30-day average volume --
        catches one genuine block,
      * the whole window's off-exchange notional against market cap --
        catches a big order sliced into ordinary-looking pieces, which is
        what a patient institution actually does.

    Anchors come from real prints: a small cap working size printed blocks of
    roughly 1.5-2.5% of its 30-day average volume, while a megacap's routine
    prints sit in the thousandths of a percent. 1% of avg30 in ONE print is
    already unusual for a small cap; 5% is a real block.
    """
    single = lerp_curve(
        clamp01(max_size_vs_avg30),
        [(0.002, 0.0), (0.01, 0.35), (0.05, 0.8), (0.12, 1.0)],
    )
    rel = 0.0
    if marketcap and float(marketcap) > 0:
        bps = 10000.0 * max(0.0, float(window_notional or 0.0)) / float(marketcap)
        rel = lerp_curve(bps, [(5.0, 0.0), (50.0, 0.5), (300.0, 1.0)])
    return clamp01(max(single, 0.55 * single + 0.65 * rel))


def spread_position(price, bid, ask):
    """
    Where a print landed in the NBBO: 0.0 = at the bid, 1.0 = at the ask.

    Returns None when the quote cannot support the inference. This is not
    defensiveness -- it is the single dirtiest field on the endpoint:

      * `prior_reference_price` prints reference an EARLIER quote, so the
        attached NBBO is unrelated to the fill (a print can sit well inside or
        outside a quote that was current seconds or minutes earlier).
      * extended-hours prints carry stale, absurdly wide quotes -- a ~10%
        spread on a stock that traded all day inside a few cents.
      * prints land OUTSIDE the NBBO routinely (a print below its own bid).

    Scoring those as "at the bid, distribution" would be inventing a signal
    out of quote noise, so they are dropped instead. `clean_prints` in
    darkpool.py applies the session/condition filters; this function is the
    last line and only rejects geometry.
    """
    try:
        price = float(price)
        bid = float(bid)
        ask = float(ask)
    except (TypeError, ValueError):
        return None
    if not (price > 0 and bid > 0 and ask > 0) or ask <= bid:
        return None
    spread = ask - bid
    mid = 0.5 * (ask + bid)
    if spread / mid > 0.02:          # wider than 2% -- a stale or gapped quote
        return None
    if price < bid or price > ask:   # outside the book: nothing to infer
        return None
    return (price - bid) / spread


# A print in the top third of the spread is lifting the offer; one in the
# bottom third is hitting the bid. The middle third is genuinely ambiguous
# and is deliberately counted for NEITHER side.
ASK_SIDE = 0.65
BID_SIDE = 0.35


def lean_component(pressure, clean_count, min_clean=6):
    """
    Premium share printing UP at the offer minus the share printing DOWN at
    the bid, in -1..+1, mapped to 0..1. UNMEASURED when there are too few
    clean prints to mean anything.

    THIS STARTED OUT AS A PREMIUM-WEIGHTED MEAN SPREAD POSITION AND THAT
    VERSION WAS WORTHLESS. Replayed on real prints, ZENO (visibly working
    size, price walking steadily higher) and QRTX (flat, prints sitting on the
    bid) BOTH came back at the same weighted mean, around 0.41 -- identical to three
    decimals, both scoring exactly 0 against a curve anchored at the 0.50
    midpoint. Seven points of the model that could never pay out.

    The mean was hiding the shape. The two distributions are nothing alike:

        ZENO  ~20 prints, about a quarter at the offer, and a
                     separate cluster at 0.00 -- bimodal, which is what a
                     buyer lifting the offer against ordinary two-way flow
                     actually looks like
        QRTX  ~10 prints, nothing near the offer at all

    Averaging a bimodal distribution returns its dead centre and destroys
    exactly the signal. Counting the two tails separately keeps it.

    The lesson generalises past this component: when a statistic scores two
    obviously different names identically, the statistic is wrong, and the
    only way to find that out is to replay it on real data and look.
    """
    if clean_count is None or clean_count < min_clean or pressure is None:
        return UNMEASURED
    return clamp01(
        lerp_curve(pressure, [(-0.20, 0.0), (0.0, 0.20), (0.25, 0.55),
                              (0.50, 0.85), (0.75, 1.0)])
    )


def sustain_component(active_sessions, window_sessions):
    """
    Distinct SESSIONS with elevated off-exchange activity -- not print count.

    A sample count is not an evidence count (GEX ES Desk called a hit rate
    meaningful at n=117 when all 117 came from one afternoon). Forty blocks
    in one frantic hour is one observation; four sessions in a row is four.
    """
    n = int(active_sessions or 0)
    total = max(1, int(window_sessions or 1))
    if n <= 0:
        return 0.0
    count_score = lerp_curve(n, [(1, 0.20), (2, 0.45), (3, 0.65), (5, 0.85), (8, 1.0)])
    density = lerp_curve(n / float(total), [(0.1, 0.0), (0.35, 0.5), (0.7, 1.0)])
    return clamp01(0.7 * count_score + 0.3 * density)


def proximity_component(overlap_days, testable_days):
    """
    Did the off-exchange size show up around the days the insider was buying?

    Measured against TESTABLE insider dates only -- the ones that fall inside
    the dark pool lookback. The insider window is 45 days and one
    /api/darkpool/{ticker} call reaches back about 8 sessions, so most insider
    dates on a card have no off-exchange data anywhere near them. Scoring
    those as misses says "the blocks did not line up" when the truth is
    "there was nothing to line them up against" -- the same no-data-is-not-a-
    zero distinction the 13F leg and the lean already make.

    Returns UNMEASURED when nothing is testable, which is the common case and
    is now visible on the card instead of being a silent zero.
    """
    if not testable_days:
        return UNMEASURED
    frac = float(overlap_days or 0) / float(len(testable_days))
    return clamp01(lerp_curve(frac, [(0.0, 0.0), (0.34, 0.55), (0.67, 0.85), (1.0, 1.0)]))


def darkpool_leg(dp):
    """
    dp: the per-company dark pool aggregate (see darkpool.py).
    Returns (points, parts, measured) where measured says which sub-signals
    had enough data to mean anything.
    """
    parts = {}
    measured = {}

    parts["block"] = block_component(
        dp.get("max_size_vs_avg30", 0.0),
        dp.get("window_notional", 0.0),
        dp.get("marketcap"),
    )
    measured["block"] = bool(dp.get("print_count", 0) > 0)

    lean = lean_component(dp.get("pressure"), dp.get("clean_count", 0))
    measured["lean"] = lean is not UNMEASURED
    parts["lean"] = 0.0 if lean is UNMEASURED else lean

    parts["sustain"] = sustain_component(
        dp.get("active_sessions", 0), dp.get("window_sessions", 1)
    )
    measured["sustain"] = bool(dp.get("print_count", 0) > 0)

    prox = proximity_component(
        dp.get("overlap_days", 0), dp.get("testable_days") or []
    )
    measured["proximity"] = prox is not UNMEASURED
    parts["proximity"] = 0.0 if prox is UNMEASURED else prox

    points = (
        WD_BLOCK * parts["block"]
        + WD_LEAN * parts["lean"]
        + WD_SUSTAIN * parts["sustain"]
        + WD_PROXIMITY * parts["proximity"]
    )
    return points, parts, measured


# =========================================================== INSTITUTIONAL

_FUNDS_TABLE = {1: 0.35, 2: 0.60, 3: 0.80, 4: 0.90}

# From the Institutional Desk classifier. 'harvesting' is a fund that has been
# trimming into the position's strength -- it still held and still bought,
# but it is not the same statement as a fund building.
_TRAJECTORY_SCORE = {
    "new_conviction": 1.00,
    "building": 0.90,
    "steady": 0.55,
    "volatile": 0.40,
    "harvesting": 0.20,
}


def funds_component(distinct_funds):
    n = int(distinct_funds or 0)
    if n <= 0:
        return 0.0
    return _FUNDS_TABLE.get(n, 1.0)


def weight_component(best_position_weight):
    """
    perc_of_share_value: the position as a fraction of the fund's equity book.
    A concentrated fund putting 5% of its book into one small cap is the
    signal this desk was built to find.
    """
    pct = max(0.0, float(best_position_weight or 0.0))
    if pct > 1.0:                     # some rows arrive as 0-100, some as 0-1
        pct = pct / 100.0
    return clamp01(lerp_curve(pct, [(0.005, 0.0), (0.02, 0.45), (0.05, 0.8), (0.10, 1.0)]))


def trajectory_component(trajectories):
    if not trajectories:
        return 0.0
    return clamp01(max(_TRAJECTORY_SCORE.get(t, 0.4) for t in trajectories))


def institutional_leg(inst):
    """
    inst: the per-company 13F aggregate (see idb.py), or None when the ticker
    is outside the small-fund universe. None scores 0 and is flagged as
    unmeasured, which is the difference between "funds are not accumulating"
    and "this desk does not cover this company's funds".
    """
    if not inst or not inst.get("distinct_funds"):
        return 0.0, {"funds": 0.0, "weight": 0.0, "trajectory": 0.0}, False

    parts = {
        "funds": funds_component(inst.get("distinct_funds")),
        "weight": weight_component(inst.get("best_position_weight")),
        "trajectory": trajectory_component(inst.get("trajectories")),
    }
    points = (
        WN_FUNDS * parts["funds"]
        + WN_WEIGHT * parts["weight"]
        + WN_TRAJECTORY * parts["trajectory"]
    )
    return points, parts, True


# ================================================================ COMPOSITE

LEG_LABELS = {
    "insider": "Insider buying",
    "darkpool": "Dark pool",
    "institutional": "Institutional 13F",
}


def composite(agg_insider, dp, inst):
    """
    The whole model. Returns a dict that is the card.

    There is deliberately NO "N of 3 signals agree" readout.

    The Swing Desk shipped one, gave it a materiality floor after a ticker
    showed "5/5 agree" on a score in the low 30s, and the Insider Desk STILL had to delete its
    version because every card in the top 25 read 4 of 4 -- the seniority
    component cannot fall below 0.30 and the size component is gated at $50k,
    so the count discriminated nowhere near where it mattered. The same trap
    is waiting here: the insider leg is a precondition for a card existing, so
    it always "agrees".

    What goes on the card instead is the honest version -- each leg's points
    out of its maximum, and the name of whichever leg carried the score.
    """
    i_pts, i_parts = insider_leg(agg_insider)
    d_pts, d_parts, d_measured = darkpool_leg(dp or {})
    n_pts, n_parts, n_measured = institutional_leg(inst)

    total = i_pts + d_pts + n_pts
    legs = {
        "insider": {"points": round(i_pts, 2), "max": W_INSIDER,
                    "parts": i_parts, "measured": True},
        "darkpool": {"points": round(d_pts, 2), "max": W_DARKPOOL,
                     "parts": d_parts, "measured": d_measured},
        "institutional": {"points": round(n_pts, 2), "max": W_INSTITUTIONAL,
                          "parts": n_parts, "measured": n_measured},
    }

    # "Carried by" = the legs contributing the most POINTS, which is what a
    # reader actually wants to know and what the contribution bar shows.
    ranked = sorted(legs.items(), key=lambda kv: kv[1]["points"], reverse=True)
    carried = [name for name, leg in ranked if leg["points"] >= 0.15 * total] or [ranked[0][0]]

    return {
        "score": round(total, 1),
        "legs": legs,
        "carried_by": carried,
        "all_three": bool(
            i_pts > 0
            and d_pts >= 0.15 * W_DARKPOOL
            and n_pts >= 0.15 * W_INSTITUTIONAL
        ),
    }


def score_band(score):
    if score >= 75:
        return "high"
    if score >= 55:
        return "elevated"
    if score >= 35:
        return "watch"
    return "low"
