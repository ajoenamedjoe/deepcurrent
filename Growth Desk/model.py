"""
Growth Leaders (O'Neil-style) -- the scoring model. Pure functions, no I/O.

Seven checks, inspired by William O'Neil's growth-stock method (this desk is not affiliated with or endorsed
by Investor's Business Daily; "CAN SLIM" is their trademark and the ratings here are our own calculations):

    C  current quarterly earnings    latest quarter's EPS vs the same quarter a year ago, and whether it is speeding up
    A  annual earnings               3-year growth of trailing-12-month EPS, with no down year
    N  new highs                     how close the price is to its 52-week high
    S  supply and demand             up-day vs down-day volume over 50 sessions; share count shrinking or flat
    L  leader                        our relative-strength rating (1-99) and the industry group's rank
    I  institutional sponsorship     the latest 13F quarter: more funds adding than cutting, net shares up
    M  market direction              SPY and QQQ: confirmed uptrend, under pressure, or in correction

C A N S L I make the 0-100 score (weights below, sum 100). M is the same for every stock, so it is not in the
score; it is the seventh check for the Leader badge and it gates Discord.

Rules carried over from the other desks (see the project notes):
  * an unmeasured part drops out and the rest re-scale ("N of 100 points measured"); below MIN_MEASURED
    points a stock gets no score at all rather than a flattering one
  * a missing number is never read as a bad one, and a loss is never a growth rate
  * every check says why, in numbers
"""

import math

MODEL_VERSION = "1.0"

WEIGHTS = {"C": 25, "A": 15, "N": 10, "S": 10, "L": 25, "I": 15}
MIN_MEASURED = 60          # points that must be measurable for a score to be shown

# thresholds (O'Neil's published rules of thumb, as pass marks)
C_PASS = 0.25              # latest quarter EPS up 25%+ year on year
A_PASS = 0.25              # 3-year EPS growth 25%+ a year
N_PASS = -0.15             # within 15% of the 52-week high
S_UD_PASS = 1.0            # up-volume at least equal to down-volume
S_SHARES_MAX = 0.05        # share count up no more than 5% in a year
L_PASS = 80                # relative strength rating 80+
I_BREADTH_PASS = 0.5       # at least as many funds adding as cutting
EPS_STALE_DAYS = 200       # a latest quarter older than this is not "current"
F13_STALE_DAYS = 200

# base / breakout
BASE_LOOKBACK = 65         # sessions searched for the pivot high (13 weeks)
BASE_MIN_AGE = 15          # sessions since the pivot high before a consolidation counts as a base
BASE_MIN_DEPTH = 0.03
BASE_MAX_DEPTH = 0.35
PRIOR_ADVANCE = 0.20       # the stock rose 20%+ into the base
BUY_RANGE = 0.05           # buy up to 5% above the pivot
BREAKOUT_VOL = 1.4         # breakout volume at least 40% above the 50-day average
STOP_LOSS = 0.08           # O'Neil: cut every loss at 7-8% below the buy point
TAKE_PROFIT = (0.20, 0.25)

# market direction
DD_DROP = 0.002            # a distribution day: index down 0.2%+ on higher volume than the day before
DD_WINDOW = 25
DD_EXPIRE_GAIN = 0.05      # a distribution day also expires once the index closes 5% above it
DD_PRESSURE = 5
CORRECTION_DRAWDOWN = 0.08
FTD_GAIN = 0.0125          # follow-through day: day 4+ of a rally attempt, up 1.25%+ on higher volume
FTD_GAIN_PRICE_ONLY = 0.017
FTD_MIN_DAY = 4
M_STATES = ("correction", "pressure", "uptrend")
M_LABEL = {"uptrend": "Confirmed uptrend", "pressure": "Uptrend under pressure", "correction": "Market in correction"}


# ---------------------------------------------------------------------------- helpers
def num(v):
    """UW numbers arrive as strings, numbers, null or the string "None". None means absent."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if v != v else float(v)
    s = str(v).strip().replace(",", "")
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return None if x != x or x in (float("inf"), float("-inf")) else x


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def half_up(x, nd=0):
    q = 10 ** nd
    return math.floor(x * q + 0.5) / q


def pct(x, nd=0):
    return ("%+." + str(nd) + "f%%") % (100 * x)


def _date(s):
    return str(s or "")[:10]


def days_between(a, b):
    import datetime as dt
    try:
        return (dt.date.fromisoformat(_date(b)) - dt.date.fromisoformat(_date(a))).days
    except ValueError:
        return None


# ---------------------------------------------------------------------------- L: relative strength
def rs_raw(row):
    """IBD weights the last quarter 40% and the three before it 20% each. With 3- and 12-month returns that
    collapses (in log terms) to ln(1+r3) + ln(1+r12). Without a year of history: 2*ln(1+r3) + ... from 6 months."""
    r3, r6, r12 = num(row.get("three_month_perc")), num(row.get("six_month_perc")), num(row.get("one_year_perc"))
    if r3 is None or r3 <= -1:
        return None, None
    if r12 is not None and r12 > -1:
        return math.log1p(r3) + math.log1p(r12), "12m"
    if r6 is not None and r6 > -1:
        return math.log1p(r3) + 2 * math.log1p(r6), "6m"
    return None, None


def rs_ratings(rows):
    """{ticker: {"rs": 1..99, "basis": "12m"|"6m"}} ranked across the whole universe."""
    scored = []
    for r in rows:
        v, basis = rs_raw(r)
        if v is not None and r.get("ticker"):
            scored.append((v, r["ticker"], basis))
    scored.sort()
    n = len(scored)
    out = {}
    for i, (_, t, basis) in enumerate(scored):
        out[t] = {"rs": 1 + int(98 * i / (n - 1)) if n > 1 else 50, "basis": basis}
    return out


def group_ranks(rows, rs):
    """Industry groups ranked by their members' median RS (groups with 3+ members). {industry: {...}}"""
    by = {}
    for r in rows:
        g = (r.get("industry_type") or "").strip()
        if g and r.get("ticker") in rs:
            by.setdefault(g, []).append(rs[r["ticker"]]["rs"])
    ranked = []
    for g, v in by.items():
        if len(v) >= 3:
            v = sorted(v)
            m = v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
            ranked.append((m, g, len(v)))
    ranked.sort()
    n = len(ranked)
    out = {}
    for i, (m, g, k) in enumerate(ranked):
        out[g] = {"median_rs": m, "members": k, "rank": n - i, "of": n,
                  "pct": half_up(100 * i / (n - 1)) if n > 1 else 50}
    return out


# ---------------------------------------------------------------------------- C and A: earnings
def eps_series(rows, today):
    """Reported quarters, newest first: [{"end", "eps", "est", "surprise", "reported"}]. The newest row of
    /earnings is usually the NEXT report (reported_eps null) -- dropped."""
    out, seen = [], set()
    for r in rows or []:
        if str(r.get("report_type") or "quarterly") != "quarterly":
            continue
        e = num(r.get("reported_eps"))
        end = _date(r.get("fiscal_date_ending"))
        if e is None or not end or end in seen:
            continue
        rep = _date(r.get("report_date"))
        if rep and today and rep > today:
            continue
        seen.add(end)
        out.append({"end": end, "eps": e, "est": num(r.get("estimated_eps")),
                    "surprise": num(r.get("surprise_percentage")), "reported": rep})
    out.sort(key=lambda q: q["end"], reverse=True)
    return out


def _yoy(q, i):
    """Quarter i vs the same quarter a year earlier (i+4). ("growth", g) | ("turned", None) | ("loss", None) | None."""
    if len(q) <= i + 4:
        return None
    cur, base = q[i]["eps"], q[i + 4]["eps"]
    if cur <= 0:
        return ("loss", None)
    if base <= 0:
        return ("turned", None)
    return ("growth", cur / base - 1)


def check_c(q, today):
    w = WEIGHTS["C"]
    if not q:
        return _unmeasured("C", "No reported quarters from Unusual Whales.")
    age = days_between(q[0]["end"], today)
    if age is not None and age > EPS_STALE_DAYS:
        return _unmeasured("C", "Latest reported quarter ended %s, %d days ago: not current." % (q[0]["end"], age))
    y0, y1 = _yoy(q, 0), _yoy(q, 1)
    if y0 is None:
        return _unmeasured("C", "Fewer than 5 reported quarters: no year-ago quarter to compare.")
    kind, g = y0
    e0, eb = q[0]["eps"], q[4]["eps"]
    if kind == "loss":
        return _result("C", 0, w, False, "Latest quarter EPS %.2f: a loss (year ago %.2f)." % (e0, eb), g0=None)
    if kind == "turned":
        return _result("C", half_up(w * 0.36, 1), w, False,
                       "Turned profitable: EPS %.2f vs %.2f a year ago (no growth rate from a loss)." % (e0, eb), g0=None)
    pts = 18 * clamp(g / 0.75)
    accel, note = None, ""
    if y1 and y1[0] == "growth":
        accel = g - y1[1]
        if accel > 0.05:
            pts += 7
            note = ", speeding up from %s" % pct(y1[1])
        elif accel >= -0.05 and g >= C_PASS:
            pts += 3
            note = ", steady (%s the quarter before)" % pct(y1[1])
        else:
            note = ", slowing from %s" % pct(y1[1])
    sur = q[0].get("surprise")
    if sur is not None:
        note += "; %s estimates by %.0f%%" % ("beat" if sur >= 0 else "missed", abs(sur))
    return _result("C", half_up(pts, 1), w, g >= C_PASS,
                   "Latest quarter EPS %.2f vs %.2f a year ago: %s%s." % (e0, eb, pct(g), note), g0=g, accel=accel)


def ttm(q, k):
    """Trailing-12-month EPS ending k years before the latest quarter (needs 4 consecutive quarters)."""
    s = q[4 * k:4 * k + 4]
    return sum(x["eps"] for x in s) if len(s) == 4 else None


def check_a(q):
    w = WEIGHTS["A"]
    t = [ttm(q, k) for k in range(4)]
    years = max([k for k in range(1, 4) if t[k] is not None], default=0)
    if years < 2:
        return _unmeasured("A", "Fewer than 3 years of quarterly EPS: no annual trend.")
    t0, tb = t[0], t[years]
    growth = []
    for k in range(years):
        if t[k + 1] is not None and t[k + 1] > 0 and t[k] is not None:
            growth.append(t[k] / t[k + 1] - 1)
    no_down = all(x > 0 for x in growth) and len(growth) == years
    if t0 <= 0:
        return _result("A", 0, w, False, "Trailing-12-month EPS %.2f: a loss." % t0, cagr=None)
    if tb <= 0:
        return _result("A", 5, w, False, "Trailing EPS %.2f now vs a loss (%.2f) %d years ago: turned profitable, "
                       "no growth rate." % (t0, tb, years), cagr=None)
    cagr = (t0 / tb) ** (1.0 / years) - 1
    pts = 12 * clamp(cagr / 0.5) + (3 if no_down else 0)
    yrs = ", ".join(pct(x) for x in reversed(growth))
    return _result("A", half_up(pts, 1), w, cagr >= A_PASS and no_down,
                   "Trailing EPS %.2f vs %.2f %d years ago: %s a year (%s)%s." % (
                       t0, tb, years, pct(cagr), yrs, "" if no_down else "; at least one down year"), cagr=cagr)


# ---------------------------------------------------------------------------- N
def check_n(row, bars=None):
    w = WEIGHTS["N"]
    p, hi = num(row.get("close")), num(row.get("week_52_high"))
    if not p or not hi:
        return _unmeasured("N", "No price or 52-week high.")
    d = p / hi - 1
    pts = w * clamp(1 + d / 0.25)
    extra = ""
    if bars and len(bars) >= 6:
        recent = max((b["high"] for b in bars[-5:] if b.get("high")), default=None)
        prior = max((b["high"] for b in bars[-252:-5] if b.get("high")), default=None)
        if recent and prior and recent >= prior:
            extra = "; made a new 52-week high this week"
    return _result("N", half_up(pts, 1), w, d >= N_PASS,
                   ("At the 52-week high of %s%s." % (_usd(hi), extra)) if d >= -0.005 else
                   "%.0f%% below the 52-week high of %s%s." % (-100 * d, _usd(hi), extra), off_high=d)


# ---------------------------------------------------------------------------- S
def up_down_volume(bars, n=50):
    """Up-day volume / down-day volume over the last n sessions. None when volume is missing."""
    seg = bars[-(n + 1):]
    up = dn = 0.0
    counted = 0
    for a, b in zip(seg, seg[1:]):
        v = b.get("volume")
        if not v or a.get("close") is None or b.get("close") is None:
            continue
        counted += 1
        if b["close"] > a["close"]:
            up += v
        elif b["close"] < a["close"]:
            dn += v
    if counted < n * 0.8 or dn <= 0:
        return None
    return up / dn


def check_s(row, bars):
    w = WEIGHTS["S"]
    ud = up_down_volume(bars or [])
    sg = num(row.get("shares_outstanding_growth_4q"))
    if sg is not None and abs(sg) > 5:            # 999849x: a data glitch, not dilution
        sg = None
    earned = possible = 0.0
    bits = []
    if ud is not None:
        possible += 6
        earned += 6 * clamp((ud - 0.8) / 0.7)
        bits.append("up-day volume %.2fx down-day volume over 50 sessions" % ud)
    if sg is not None:
        possible += 4
        earned += 4 if sg <= 0 else 2 if sg <= 0.03 else 0
        bits.append("share count %s in a year%s" % (pct(sg, 1), " (buybacks)" if sg < -0.005 else ""))
    if not possible:
        return _unmeasured("S", "No daily volume or share-count history.")
    ok = (ud is None or ud >= S_UD_PASS) and (sg is None or sg <= S_SHARES_MAX)
    return _result("S", half_up(earned * w / 10.0, 1), possible * w / 10.0, ok, "; ".join(bits).capitalize() + ".",
                   ud=ud, shares=sg)


# ---------------------------------------------------------------------------- L
def check_l(t, rs, groups, row):
    w = WEIGHTS["L"]
    r = rs.get(t)
    if not r:
        return _unmeasured("L", "No 3-month return: no relative-strength rating.")
    earned, possible = 20 * clamp((r["rs"] - 50) / 45.0), 20.0
    g = groups.get((row.get("industry_type") or "").strip())
    gtxt = ""
    if g:
        possible += 5
        earned += 5 * clamp((g["pct"] - 40) / 50.0)
        gtxt = "; industry group %s ranks %d of %d" % (row.get("industry_type"), g["rank"], g["of"])
    return _result("L", half_up(earned, 1), possible, r["rs"] >= L_PASS,
                   "Relative strength %d of 99%s%s." % (r["rs"], "" if r["basis"] == "12m" else " (6-month basis: under a year listed)", gtxt),
                   rs=r["rs"], group_pct=g and g["pct"], group_rank=g and g["rank"], group_of=g and g["of"])


# ---------------------------------------------------------------------------- I
def ownership(rows, today=None):
    """Latest 13F quarter from /api/institution/{t}/ownership rows."""
    rows = [r for r in rows or [] if isinstance(r, dict)]
    if not rows:
        return None
    latest = max(_date(r.get("report_date")) for r in rows)
    rows = [r for r in rows if _date(r.get("report_date")) == latest]
    adds = cuts = exits = new = 0
    now_u = prev_u = 0.0
    for r in rows:
        u = num(r.get("units")) or 0.0
        ch = num(r.get("units_changed")) or 0.0
        now_u += u
        prev_u += u - ch
        hist = r.get("historical_units") or []
        if ch > 0:
            adds += 1
            if len(hist) > 1 and (num(hist[1]) or 0) == 0:
                new += 1
        elif ch < 0:
            cuts += 1
            if u <= 0:
                exits += 1
    return {"report_date": latest, "holders": len(rows), "adds": adds, "cuts": cuts, "exits": exits, "new": new,
            "net": (now_u / prev_u - 1) if prev_u > 0 else None}


def check_i(own, today):
    w = WEIGHTS["I"]
    if not own or own.get("holders", 0) < 10:
        return _unmeasured("I", "Fewer than 10 13F holders reported.")
    age = days_between(own["report_date"], today)
    if age is not None and age > F13_STALE_DAYS:
        return _unmeasured("I", "Latest 13F quarter %s is %d days old." % (own["report_date"], age))
    moved = own["adds"] + own["cuts"]
    if own.get("net") is None or not moved:
        return _unmeasured("I", "The latest 13F quarter shows no changes.")
    b = own["adds"] / float(moved)
    earned = 8 * clamp((own["net"] + 0.02) / 0.10) + 7 * clamp((b - 0.4) / 0.3)
    return _result("I", half_up(earned, 1), w, own["net"] > 0 and b >= I_BREADTH_PASS,
                   "13F quarter to %s: %d funds added (%d new), %d cut (%d sold out); institutional shares %s." % (
                       own["report_date"], own["adds"], own["new"], own["cuts"], own["exits"], pct(own["net"], 1)),
                   breadth=b, net=own["net"])


# ---------------------------------------------------------------------------- base and breakout
def sma(vals, n):
    v = [x for x in vals[-n:] if x is not None]
    return sum(v) / len(v) if len(v) == n else None


def find_base(bars):
    """A consolidation below a pivot high: the high is 15+ sessions old, the pullback 3-35% deep, and the stock
    rose 20%+ into it. Returns None when there is no base (e.g. the stock is making new highs every week)."""
    if len(bars) < BASE_LOOKBACK + 2:
        return None
    win = bars[-BASE_LOOKBACK - 1:-1]
    hi_i = max(range(len(win)), key=lambda i: (win[i]["high"] or 0, i))
    pivot = win[hi_i]["high"]
    age = len(win) - 1 - hi_i
    after = [b["low"] for b in win[hi_i + 1:] if b.get("low")]
    if not pivot or age < BASE_MIN_AGE or not after:
        return {"ok": False, "why": ("No base yet: the high was %d session%s ago." % (age, "" if age == 1 else "s")) if pivot else "No data.",
                "pivot": pivot, "age": age}
    depth = 1 - min(after) / pivot
    before = [b["close"] for b in bars[max(0, len(bars) - 1 - BASE_LOOKBACK - 60):len(bars) - 1 - BASE_LOOKBACK + hi_i]
              if b.get("close")]
    advance = (pivot / min(before) - 1) if before else None
    ok, why = True, ""
    if depth < BASE_MIN_DEPTH:
        ok, why = False, "too tight to call a base (%.0f%% deep)" % (100 * depth)
    elif depth > BASE_MAX_DEPTH:
        ok, why = False, "too deep for a base (%.0f%% below the high)" % (100 * depth)
    elif advance is not None and advance < PRIOR_ADVANCE:
        ok, why = False, "no prior advance into the base (+%.0f%%)" % (100 * advance)
    return {"ok": ok, "why": why, "pivot": pivot, "age": age, "weeks": half_up(age / 5.0, 1), "depth": depth,
            "advance": advance, "pivot_date": win[hi_i].get("date")}


def breakout(bars, price=None, vol_ratio=None):
    """Where the price stands against the latest base. Looks back up to 10 sessions for a base that was
    broken out of, so a stock three days past its buy point still shows its pivot and buy range."""
    if not bars or len(bars) < BASE_LOOKBACK + 2:
        return {"state": "unknown", "text": "Not enough daily history for a base."}
    closes = [b["close"] for b in bars]
    vols = [b.get("volume") for b in bars]
    avg50 = sma([v for v in vols[:-1] if v], 50) if sum(1 for v in vols[:-1] if v) >= 50 else None
    p = price or closes[-1]
    if vol_ratio is None and avg50 and vols[-1]:
        vol_ratio = vols[-1] / avg50
    base, since = None, 0
    for e in range(0, 11):
        cut = bars[:len(bars) - e] if e else bars
        b = find_base(cut)
        if b and b["ok"]:
            later = closes[len(bars) - e:] if e else []
            above = [k for k, c in enumerate(later) if c > b["pivot"]]
            if e == 0 or above:
                base, since = b, (e - above[0] - 1 if above else 0)
                break
    if not base:
        b0 = find_base(bars)
        return {"state": "none", "text": (b0 or {}).get("why") or "No base.", "base": b0}
    pivot = base["pivot"]
    lo, hi = pivot, pivot * (1 + BUY_RANGE)
    out = {"base": base, "pivot": pivot, "buy_from": lo, "buy_to": hi, "stop": pivot * (1 - STOP_LOSS),
           "profit_from": pivot * (1 + TAKE_PROFIT[0]), "profit_to": pivot * (1 + TAKE_PROFIT[1]),
           "vol_ratio": vol_ratio, "vs_pivot": p / pivot - 1}
    desc = "%.1f-week base, %.0f%% deep, pivot %s" % (base["weeks"], 100 * base["depth"], _usd(pivot))
    if p > hi:
        out.update(state="extended", text="Extended %.0f%% past the pivot, above the %s buy range: don't chase (%s)." % (
            100 * (p / pivot - 1), _usd(hi), desc))
    elif p > lo:
        loud = vol_ratio is not None and vol_ratio >= BREAKOUT_VOL
        if since:
            out.update(state="in_range", text="Broke out %d session%s ago; still in the buy range %s-%s (%s)." % (
                since, "" if since == 1 else "s", _usd(lo), _usd(hi), desc))
        elif loud:
            out.update(state="breakout", text="Breaking out on %.1fx average volume: buy range %s-%s (%s)." % (
                vol_ratio, _usd(lo), _usd(hi), desc))
        else:
            out.update(state="weak_breakout", text="Above the pivot but on %s volume: wait for volume (%s)." % (
                "%.1fx average" % vol_ratio if vol_ratio is not None else "unknown", desc))
    elif p >= pivot * (1 - BUY_RANGE):
        out.update(state="near_pivot", text="%.1f%% below the pivot: on watch (%s)." % (100 * (1 - p / pivot), desc))
    else:
        out.update(state="in_base", text="In the base, %.0f%% below the pivot (%s)." % (100 * (1 - p / pivot), desc))
    return out


# ---------------------------------------------------------------------------- M: market direction
def index_state(bars):
    """Walk an index's daily bars and return O'Neil's market state for its last day.
    uptrend -> pressure (5+ distribution days or below the 50-day) -> correction (8% off the high since the
    uptrend began, or below the 50-day with 5+ distribution days) -> uptrend again only on a follow-through day."""
    if len(bars) < 60:
        return None
    closes = [b["close"] for b in bars]
    vol_known = sum(1 for b in bars[-60:] if b.get("volume")) >= 50
    s50 = lambda i: sum(closes[i - 49:i + 1]) / 50.0 if i >= 49 else None
    state = "uptrend" if closes[49] >= s50(49) else "correction"
    ref_high = closes[49]
    low, rally = closes[49], 0
    dds, ftd, ftd_i, since = [], None, -999, bars[49].get("date")
    for i in range(50, len(bars)):
        c, pc = closes[i], closes[i - 1]
        v, pv = bars[i].get("volume"), bars[i - 1].get("volume")
        if vol_known and v and pv and c < pc * (1 - DD_DROP) and v > pv:
            dds.append((i, c))
        dds = [(j, x) for j, x in dds if i - j < DD_WINDOW and max(closes[j:i + 1]) < x * (1 + DD_EXPIRE_GAIN)]
        m50 = s50(i)
        if state in ("uptrend", "pressure"):
            ref_high = max(ref_high, c)
            if c <= ref_high * (1 - CORRECTION_DRAWDOWN) or (c < m50 * 0.98 and len(dds) >= DD_PRESSURE):
                state, low, rally, since = "correction", c, 0, bars[i].get("date")
            else:
                # a fresh follow-through day confirms the uptrend even below a still-falling 50-day line
                below = c < m50 and i - ftd_i > DD_WINDOW
                new = "pressure" if (len(dds) >= DD_PRESSURE or below) else "uptrend"
                if new != state:
                    since = bars[i].get("date")
                state = new
        else:
            if c < low:
                low, rally = c, 0
            elif rally == 0 and c > pc:
                rally = 1
            elif rally >= 1:
                rally += 1
                need = FTD_GAIN if vol_known else FTD_GAIN_PRICE_ONLY
                up_vol = (v and pv and v > pv) if vol_known else True
                if rally >= FTD_MIN_DAY and c >= pc * (1 + need) and up_vol:
                    state, ref_high, ftd, ftd_i, since = "uptrend", c, bars[i].get("date"), i, bars[i].get("date")
                    dds = []
    i = len(bars) - 1
    m50 = s50(i)
    m200 = sum(closes[-200:]) / 200.0 if len(closes) >= 200 else None
    return {"state": state, "since": since, "distribution_days": len(dds) if vol_known else None,
            "follow_through": ftd, "close": closes[-1], "vs_50": closes[-1] / m50 - 1,
            "vs_200": (closes[-1] / m200 - 1) if m200 else None,
            "off_high": closes[-1] / max(closes[-252:]) - 1, "volume_known": vol_known}


def market_state(spy_bars, qqq_bars):
    parts = {}
    for name, b in (("SPY", spy_bars), ("QQQ", qqq_bars)):
        s = index_state(b or [])
        if s:
            parts[name] = s
    if not parts:
        return {"state": None, "label": "Market direction unknown", "ok": False, "indexes": {},
                "text": "No SPY or QQQ history."}
    worst = min(parts.values(), key=lambda s: M_STATES.index(s["state"]))["state"]
    bits = []
    for name, s in parts.items():
        dd = "" if s["distribution_days"] is None else ", %d distribution day%s" % (
            s["distribution_days"], "" if s["distribution_days"] == 1 else "s")
        bits.append("%s %s (%.1f%% %s its 50-day line%s)" % (
            name, M_LABEL[s["state"]].lower().replace("market in ", "in "), abs(100 * s["vs_50"]),
            "above" if s["vs_50"] >= 0 else "below", dd))
    price_only = not all(s["volume_known"] for s in parts.values())
    return {"state": worst, "label": M_LABEL[worst], "ok": worst == "uptrend", "indexes": parts,
            "price_only": price_only,
            "text": "; ".join(bits) + (". Volume unavailable: distribution days not counted." if price_only else ".")}


# ---------------------------------------------------------------------------- the card
def score_stock(t, row, rs, groups, eps_rows=None, bars=None, own_rows=None, market=None, today=None,
                price=None, vol_ratio=None):
    """Everything the desk says about one stock."""
    q = eps_series(eps_rows, today)
    own = ownership(own_rows, today) if own_rows is not None else None
    checks = [check_c(q, today), check_a(q), check_n(row, bars), check_s(row, bars), check_l(t, rs, groups, row),
              check_i(own, today)]
    earned = sum(c["points"] for c in checks if c["measured"])
    possible = sum(c["possible"] for c in checks if c["measured"])
    score = int(half_up(100.0 * earned / possible)) if possible >= MIN_MEASURED else None
    m_ok = bool(market and market.get("ok"))
    checks.append({"key": "M", "measured": bool(market and market.get("state")), "passed": m_ok,
                   "points": 0, "possible": 0,
                   "text": (market or {}).get("label", "Market direction unknown") + ((": " + market["text"]) if market and market.get("text") else "")})
    passed = [c["key"] for c in checks if c["passed"]]
    six = all(c["passed"] for c in checks[:6])
    bo = breakout(bars or [], price=price, vol_ratio=vol_ratio) if bars else {"state": "unknown", "text": "No daily history."}
    carried = sorted((c for c in checks[:6] if c["measured"] and c["points"] > 0), key=lambda c: -c["points"])
    return {
        "ticker": t, "name": row.get("full_name"), "sector": row.get("sector"), "industry": row.get("industry_type"),
        "price": price or num(row.get("close")), "marketcap": num(row.get("marketcap")),
        "score": score, "measured": half_up(possible, 1), "earned": half_up(earned, 1),
        "checks": checks, "passed": passed, "six_pass": six, "leader": six and m_ok,
        "rs": (rs.get(t) or {}).get("rs"), "base": bo,
        "carried_by": [c["key"] for c in carried[:3]],
        "next_earnings": row.get("next_earnings_date"),
        "flow": flow_note(row),
        "model": MODEL_VERSION,
    }


def flow_note(row):
    """Display only, never scored: what options traders did in the name today (the Unusual Whales twist)."""
    bull, bear = num(row.get("bullish_premium")), num(row.get("bearish_premium"))
    if not bull and not bear:
        return None
    b, s = bull or 0.0, bear or 0.0
    if b + s < 250000:
        return None
    lean = b / (b + s)
    return {"bullish": b, "bearish": s, "lean": lean,
            "text": "Options today: %s bullish vs %s bearish premium" % (_usd(b, True), _usd(s, True)),
            "tone": "good" if lean >= 0.6 else "bad" if lean <= 0.4 else "neutral"}


def _usd(x, short=False):
    if x is None:
        return "?"
    if short:
        for d, s in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(x) >= d:
                return "$%.1f%s" % (x / d, s)
    return "$%.2f" % x


def _unmeasured(key, text):
    return {"key": key, "measured": False, "passed": False, "points": 0, "possible": 0, "text": text}


def _result(key, points, possible, passed, text, **extra):
    d = {"key": key, "measured": True, "passed": bool(passed), "points": points, "possible": possible, "text": text}
    d.update(extra)
    return d


def method():
    """What the dashboard's How it works page shows."""
    return {"model_version": MODEL_VERSION, "weights": dict(WEIGHTS), "min_measured": MIN_MEASURED,
            "pass": {"C": C_PASS, "A": A_PASS, "N": N_PASS, "S_updown": S_UD_PASS, "S_shares": S_SHARES_MAX,
                     "L": L_PASS, "I_breadth": I_BREADTH_PASS},
            "breakout": {"buy_range": BUY_RANGE, "volume": BREAKOUT_VOL, "stop": STOP_LOSS, "profit": list(TAKE_PROFIT),
                         "base_min_sessions": BASE_MIN_AGE, "depth": [BASE_MIN_DEPTH, BASE_MAX_DEPTH]},
            "market": {"distribution_days_pressure": DD_PRESSURE, "correction_drawdown": CORRECTION_DRAWDOWN,
                       "follow_through_gain": FTD_GAIN, "follow_through_day": FTD_MIN_DAY}}
