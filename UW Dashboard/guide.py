"""
The "How it works" page: what each desk and dashboard page does, and how it scores.

Every number here has a WRITTEN value (below) that matches the code as shipped. When a
desk is running and publishes its own numbers, the live value replaces the written one,
so the page follows a desk whose .env or code changes. Each section says which it used.

Live sources (each desk's public HTTP API, never its database):
  Valuation     GET /api/method  -> quality weights, verdict bands, gates, margin of safety
  Flow          GET /api/flow    -> meta.weights, meta.gate, meta.near_gate, meta.min_premium
  Swing         GET /api/health  -> config (weights, gates, haircuts), discord (alert rules)
  Institutional GET /api/state   -> config (fund + company screen thresholds)
  Confluence    none published   -> written values only
Dashboard pages read the dashboard's own constants and Settings, so they are always live.
"""

import copy

import market as market_mod
import pnl as pnl_mod
import trackrec
import watchlist as watch_mod

ORDER = ("valuation", "flow", "confluence", "institutional", "swing", "growth")


def _usd(v):
    v = float(v)
    if abs(v) >= 1e9:
        return "$%sB" % ("%.1f" % (v / 1e9)).rstrip("0").rstrip(".")
    if abs(v) >= 1e6:
        return "$%sM" % ("%.1f" % (v / 1e6)).rstrip("0").rstrip(".")
    if abs(v) >= 1e3:
        return "$%sk" % ("%.0f" % (v / 1e3))
    return "$%.0f" % v


def _pct(v, dec=0):
    return ("%." + str(dec) + "f%%") % (float(v) * 100)


def _n(v):
    v = float(v)
    return ("%d" % v) if v == int(v) else ("%.2f" % v).rstrip("0").rstrip(".")


# ---------------------------------------------------------------- written values
WRITTEN = {
    "valuation": {
        "name": "Valuation Desk",
        "question": "What is this company worth, and is it cheap?",
        "how": [
            "Sorts the company into a type (profitable, mature, utility, bank, cash-burning growth, "
            "turnaround or pre-revenue) and values it with the method that fits that type: a cash-flow "
            "DCF for most, an earnings DCF for banks, a revenue scenario model for companies without profits.",
            "Runs a bear, base and bull case. The score is driven by how far the base-case value sits above "
            "or below today's price.",
            "Red flags (dilution, debt, weak cash conversion and similar) can only pull the score down.",
            "Two Buffett-style gates then cap the verdict: a margin of safety for BUY, and a business-quality "
            "score for STRONG BUY.",
        ],
        "score": {
            "title": "Verdict score (0 to 10, higher is better)",
            "formula": "5 + 5 x base-case upside, clamped to 0-10 (fair value = 5, +100% upside = 10), "
                       "minus red-flag points (at most 2)",
            "bands": [["0 - 1.5", "AVOID"], ["1.6 - 3.5", "REDUCE"], ["3.6 - 5.5", "HOLD"],
                      ["5.6 - 7.5", "BUY"], ["7.6 - 10", "STRONG BUY"]],
        },
        "parts_title": "Business quality (0 to 10, eight parts weighted to 100)",
        "parts": [["Return on capital", 25], ["Share count (buybacks vs dilution)", 15],
                  ["Cash-flow consistency", 10], ["Acquisition discipline", 10], ["Debt discipline", 10],
                  ["Earnings quality (cash vs reported)", 10], ["Insider behaviour", 10],
                  ["Earnings predictability", 10]],
        "rules": [
            ["Margin of safety", "BUY or better needs the price at or below 75% of base value (a 25% margin); otherwise capped at 5.5 (HOLD)"],
            ["Quality gate", "Quality below 4 caps the score at 5.5 (HOLD); below 6 caps it at 7.5 (BUY)"],
            ["Unknown quality", "If fewer than 50 of the 100 quality points can be measured, quality is 'unknown' and no cap applies"],
            ["Structural", "If even the bull case is below the price the score is at most 3; if even the bear case clears it, at least 6 before flags"],
            ["Track Record", "A BUY or STRONG BUY run in the last 2 days is recorded as a long pick"],
        ],
    },
    "flow": {
        "name": "Unusual Options Flow Desk",
        "question": "Who is lifting the offer on single-leg options right now?",
        "how": [
            "Pulls UW flow alerts and unusual contracts, groups them by ticker, and scores each ticker 0 to 100 "
            "from six parts.",
            "Only premium that was actually bought (at or near the ask) counts toward direction. Puts bought "
            "is bearish, calls bought is bullish.",
            "Each card goes into a lane: the main board, spreads (mostly multi-leg), near misses, or out.",
            "Show tape opens the actual prints behind an alert, plus the contract's last 50 trades.",
        ],
        "score": {
            "title": "Flow score (0 to 100)",
            "formula": "Sum of six parts, then multiplied down when the data is unreliable (crosses, "
                       "side volumes that don't add up)",
            "bands": [],
        },
        "parts_title": "The six parts",
        "parts": [["Aggression: how hard the buyer lifts the offer", 24], ["Opening: is this a new position", 20],
                  ["Sweep: sweeps and floor urgency", 18], ["Relative: unusual for this ticker", 18],
                  ["Urgency: short expiry, repeat hits", 12], ["Conviction: absolute dollars", 8]],
        "rules": [
            ["Board gate", "At least 75% of premium on the ask, with at least $100k bought"],
            ["Near-miss lane", "60% to 75% on the ask"],
            ["Spreads lane", "More than 50% of volume is multi-leg"],
            ["Direction", "Needs at least $25k bought premium, otherwise 'undirected'"],
            ["Unreliable", "Evidence multiplied below 0.6 goes to 'out' and says why"],
            ["Track Record", "Top 5 directional board cards; bearish picks win when the stock falls"],
        ],
    },
    "confluence": {
        "name": "Confluence Desk",
        "question": "Where are insiders, institutions and dark-pool size all buying the same company?",
        "how": [
            "Three independent legs, weighted to exactly 100. Each leg is scored from its own sub-parts.",
            "The 13F leg is a backdrop, not a gate: a company with no 13F coverage can still score up to 75.",
            "Parts that can't be measured are shown as 'no data', never as zero evidence.",
        ],
        "score": {
            "title": "Confluence score (0 to 100)",
            "formula": "Insider leg + Dark pool leg + Institutional leg",
            "bands": [["75+", "high"], ["55 - 74", "elevated"], ["35 - 54", "watch"], ["below 35", "low"]],
        },
        "parts_title": "Legs and sub-parts",
        "parts": [["Insider buying (Form 4, open-market)", 45], ["  conviction: how much each buyer grew their stake", 14.4],
                  ["  cluster: how many different insiders bought", 11.7], ["  rank: seniority of the top buyer", 9.0],
                  ["  size: dollars vs market cap", 9.9],
                  ["Dark pool (off-exchange prints)", 30], ["  block: biggest print vs normal volume", 12],
                  ["  sustain: sessions with heavy off-exchange blocks", 8],
                  ["  proximity: blocks near the insider buy dates", 6], ["  lean: ask share minus bid share", 4],
                  ["Institutional (13F, small concentrated funds)", 25], ["  funds: how many are buying", 13],
                  ["  weight: how big a bet it is for them", 7], ["  trajectory: building over quarters", 5]],
        "rules": [
            ["Board", "Cards below 25 are left off"],
            ["Discord", "Alerts at 70+ with all three legs contributing"],
            ["Insider modifiers", "10b5-1 plan buys x0.75, corporate filers x0.70 (only ever down)"],
            ["Insider floor", "At least $50k bought per company in the window"],
            ["Track Record", "Top 5 cards by score, as long picks"],
        ],
    },
    "institutional": {
        "name": "Institutional Desk",
        "question": "Which small, concentrated funds are adding to small, profitable companies?",
        "how": [
            "Scans 13F filings from small hedge funds, keeps new positions and big adds in small, profitable "
            "companies, and scores each event.",
            "A ticker's headline score is its best single event, plus up to 8 points when several funds buy it.",
            "13F data is quarterly and about 45 days late, so this desk moves slowly by design.",
        ],
        "score": {
            "title": "Event score (0 to 100)",
            "formula": "Sum of seven parts (max 122), rescaled to 100 so the top keeps its spread",
            "bands": [],
        },
        "parts_title": "The seven parts (points, before rescaling)",
        "parts": [["Base: new position 35, add 20", 35], ["Weight in the fund (full at 8% of its book)", 25],
                  ["Cluster: 6 per extra fund buying", 20], ["Concentration: fund with 15 or fewer names", 12],
                  ["Trajectory: building over quarters", 12], ["Earnings growth above 15%", 10],
                  ["Entry: price at or below the fund's cost", 8]],
        "rules": [
            ["Funds", "Hedge funds with $50M to $2.5B and at most 75 positions"],
            ["Companies", "Market cap and total assets under $10B, profitable 3 years running"],
            ["Events", "New position, or an add of 25%+, worth at least $1M"],
            ["Cluster", "3 or more funds buying the same name"],
            ["Track Record", "Top 5 clusters as long picks, recorded once per quarter"],
        ],
    },
    "swing": {
        "name": "Swing Desk",
        "question": "Where do at least 3 of 5 independent signals agree on a swing setup?",
        "how": [
            "Screens the most active optionable stocks, then scores the top names on five signals. Each signal "
            "is -1 to +1 (bearish to bullish) times its weight, so the score runs -100 to +100.",
            "A signal only 'agrees' if it contributes at least 15% of its full strength in the same direction.",
            "Haircuts shrink the score for earnings inside the trade window, very high IV and thin volume.",
            "Scans on its own about every 45 seconds during market hours, whether or not the page is open.",
        ],
        "score": {
            "title": "Swing score (-100 to +100; the sign is the direction)",
            "formula": "Sum of five weighted signals, then haircuts",
            "bands": [],
        },
        "parts_title": "The five signals",
        "parts": [["Options flow", 30], ["Open-interest change", 20], ["Chart (trend, RSI, moving averages)", 20],
                  ["Dark pool", 15], ["Dealer gamma (flip and walls)", 15]],
        "rules": [
            ["Alert", "Score 40+ with 3 signals agreeing and reward:risk of at least 1.3"],
            ["Discord", "Score 50+ with 4 signals agreeing, held for 3 scans in a row; 6-hour cooldown"],
            ["Haircuts", "Earnings in window x0.65, IV rank 85+ x0.85, thin volume x0.70"],
            ["Scan window", "09:35 to 16:00 ET on trading days"],
            ["Track Record", "Top 5 alerts; bearish setups are short picks"],
        ],
    },
    "growth": {
        "name": "Growth Leaders (O'Neil-style)",
        "question": "Which growth stocks look like leaders the way William O'Neil described them, and are any breaking out?",
        "how": [
            "After every close it ranks ~3,000 stocks by relative strength (1-99, our own calculation from 3- and "
            "12-month returns) and ranks industry groups by their members' median strength.",
            "Every stock with strength 70+, a price of $10+ and within 25% of its 52-week high is then checked in full: "
            "quarterly and annual earnings, a year of daily prices and volume, and the latest 13F quarter.",
            "It finds each stock's latest base and pivot (buy point). In market hours it re-checks the names near a pivot "
            "every 15 minutes for a breakout on volume.",
            "Inspired by O'Neil's published method; not affiliated with Investor's Business Daily.",
        ],
        "score": {
            "title": "Growth score (0-100)",
            "formula": "C + A + N + S + L + I points; parts with no data drop out and the rest re-scale "
                       "(no score below 60 measurable points)",
            "bands": [],
        },
        "parts_title": "The six scored checks (M is the seventh, for the Leader badge)",
        "parts": [["C: current quarterly earnings", 25], ["L: relative strength and group rank", 25],
                  ["A: 3-year annual earnings growth", 15], ["I: institutional sponsorship (13F)", 15],
                  ["N: near the 52-week high", 10], ["S: up/down volume and share count", 10]],
        "rules": [
            ["Pass marks", "C: EPS +25% on a year ago. A: +25% a year, no down year. N: within 15% of the high. "
                           "S: up-volume at least down-volume, shares up 5% or less. L: strength 80+. I: more funds adding than cutting, net shares up"],
            ["Market (M)", "SPY and QQQ: correction at 8% off the high (or below the 50-day with 5+ distribution days); "
                           "uptrend again only on a follow-through day (day 4+, up 1.25%+ on higher volume)"],
            ["Leader", "All seven checks pass"],
            ["Breakout", "Price above the pivot on volume 40%+ above average; buy range to 5% above the pivot"],
            ["Sell rules", "Stop 8% below the buy point; take profits at 20-25%"],
            ["Discord", "Only a Leader breaking out in a confirmed uptrend"],
            ["Track Record", "Leaders first, then scores 70+: top 5 as long picks"],
        ],
    },
}


# ---------------------------------------------------------------- live overrides
def _live_valuation(sec, m):
    q = m.get("quality")
    if q:
        sec["parts"] = [[str(a), float(b)] for a, b in q]
    v = m.get("verdicts")
    if v:
        bands, lo = [], 0.0
        for hi, label in v:
            bands.append(["%s - %s" % (_n(lo) if lo == 0 else _n(lo + 0.1), _n(hi)), label])
            lo = float(hi)
        sec["score"]["bands"] = bands
    mos = m.get("margin_of_safety")
    if mos is not None:
        sec["rules"][0][1] = ("BUY or better needs the price at or below %s of base value (a %s margin); "
                              "otherwise capped at 5.5 (HOLD)" % (_pct(1 - mos), _pct(mos)))
    gates = m.get("quality_gates")
    if gates:
        sec["rules"][1][1] = "; ".join("quality below %s caps the score at %s (%s)" % (_n(q), _n(c), lbl)
                                       for q, c, lbl in gates).capitalize()
    if m.get("quality_min_measured") is not None:
        sec["rules"][2][1] = ("If fewer than %s of the 100 quality points can be measured, quality is "
                              "'unknown' and no cap applies" % _n(m["quality_min_measured"]))
    if m.get("flag_cap") is not None:
        sec["score"]["formula"] = sec["score"]["formula"].replace("(at most 2)", "(at most %s)" % _n(m["flag_cap"]))
    if m.get("model_version"):
        sec["version"] = "model %s" % m["model_version"]


FLOW_LABELS = {"aggression": "Aggression: how hard the buyer lifts the offer",
               "opening": "Opening: is this a new position", "sweep": "Sweep: sweeps and floor urgency",
               "relative": "Relative: unusual for this ticker", "urgency": "Urgency: short expiry, repeat hits",
               "conviction": "Conviction: absolute dollars"}


def _live_flow(sec, f):
    meta = (f or {}).get("meta") or {}
    w = meta.get("weights")
    if not w:
        raise ValueError("no board yet")
    sec["parts"] = sorted([[FLOW_LABELS.get(k, k), float(v)] for k, v in w.items()], key=lambda p: -p[1])
    if meta.get("gate") is not None and meta.get("min_premium") is not None:
        sec["rules"][0][1] = "At least %s of premium on the ask, with at least %s bought" % (
            _pct(meta["gate"]), _usd(meta["min_premium"]))
    if meta.get("near_gate") is not None and meta.get("gate") is not None:
        sec["rules"][1][1] = "%s to %s on the ask" % (_pct(meta["near_gate"]), _pct(meta["gate"]))
    if meta.get("observed_max") is not None:
        sec["note_live"] = "Top score on today's board: %s" % _n(round(float(meta["observed_max"]), 1))


SWING_PARTS = (("w_flow", "Options flow"), ("w_oi", "Open-interest change"),
               ("w_tech", "Chart (trend, RSI, moving averages)"), ("w_dark", "Dark pool"),
               ("w_gamma", "Dealer gamma (flip and walls)"))


def _live_swing(sec, h):
    c = (h or {}).get("config") or {}
    if not c.get("w_flow"):
        raise ValueError("no config")
    sec["parts"] = sorted([[lbl, float(c[k])] for k, lbl in SWING_PARTS if k in c], key=lambda p: -p[1])
    sec["rules"][0][1] = "Score %s+ with %s signals agreeing and reward:risk of at least %s" % (
        _n(c.get("min_score", 40)), _n(c.get("min_agreeing", 3)), _n(c.get("min_rr", 1.3)))
    d = h.get("discord") or {}
    if d.get("min_score") is not None:
        sec["rules"][1][1] = "Score %s+ with %s signals agreeing, held for %s scans in a row; %s-hour cooldown%s" % (
            _n(d["min_score"]), _n(d.get("min_agreeing", 4)), _n(d.get("min_streak", 3)),
            _n(d.get("cooldown_hours", 6)), "" if d.get("enabled") else " (Discord not set up)")
    sec["rules"][2][1] = "Earnings in window x%s, IV rank %s+ x%s, thin volume x%s" % (
        _n(c.get("earnings_haircut", 0.65)), _n(c.get("high_iv_rank", 85)), _n(c.get("high_iv_haircut", 0.85)),
        _n(c.get("thin_haircut", 0.70)))
    if c.get("scan_start") and c.get("scan_end"):
        sec["rules"][3][1] = "%s to %s ET on trading days" % (c["scan_start"], c["scan_end"])
    if c.get("ttl_scan"):
        sec["how"][3] = ("Scans on its own about every %s seconds during market hours, whether or not the page "
                         "is open." % _n(c["ttl_scan"]))
    if c.get("agree_threshold") is not None:
        sec["how"][1] = ("A signal only 'agrees' if it contributes at least %s of its full strength in the same "
                         "direction." % _pct(c["agree_threshold"]))


def _live_institutional(sec, s):
    c = (s or {}).get("config") or {}
    if not c.get("fund_max_aum"):
        raise ValueError("no config")
    sec["rules"][0][1] = "%s with %s to %s and at most %s positions" % (
        "Hedge funds" if c.get("require_hedge_fund", True) else "Funds",
        _usd(c.get("fund_min_aum", 0)), _usd(c["fund_max_aum"]), _n(c.get("fund_max_positions", 75)))
    sec["rules"][1][1] = "Market cap under %s and total assets under %s, profitable %s years running" % (
        _usd(c.get("co_max_marketcap", 10e9)), _usd(c.get("co_max_assets", 10e9)), _n(c.get("min_profit_years", 3)))
    sec["rules"][2][1] = "New position, or an add of %s+, worth at least %s" % (
        _pct(c.get("min_add_perc", 0.25)), _usd(c.get("min_position_value", 1e6)))
    sec["rules"][3][1] = "%s or more funds buying the same name" % _n(c.get("cluster_min_funds", 3))
    if s.get("report_date"):
        sec["note_live"] = "Latest 13F quarter scanned: %s" % s["report_date"]


def _live_growth(sec, m):
    w = (m or {}).get("weights") or {}
    if sum(w.values()) != 100:
        raise ValueError("no weights")
    labels = {p[0][0]: p[0] for p in sec["parts"]}
    sec["parts"] = sorted([[labels.get(k, k), float(v)] for k, v in w.items()], key=lambda p: -p[1])
    b = m.get("breakout") or {}
    if b.get("volume"):
        sec["rules"][3][1] = "Price above the pivot on volume %s+ above average; buy range to %s above the pivot" % (
            _pct(b["volume"] - 1), _pct(b.get("buy_range", 0.05)))
    if b.get("stop"):
        sec["rules"][4][1] = "Stop %s below the buy point; take profits at %s-%s" % (
            _pct(b["stop"]), _pct((b.get("profit") or [0.2, 0.25])[0]), _pct((b.get("profit") or [0.2, 0.25])[1]))


LIVE = {"valuation": ("/api/method", _live_valuation), "flow": ("/api/flow", _live_flow),
        "growth": ("/api/method", _live_growth),
        "swing": ("/api/health", _live_swing), "institutional": ("/api/state", _live_institutional)}


def desks(view, enabled=ORDER):
    out = []
    for did in ORDER:
        if did not in enabled:
            continue
        sec = copy.deepcopy(WRITTEN[did])
        sec.update({"id": did, "kind": "desk", "source": "written", "source_note": None})
        if did in LIVE:
            path, apply = LIVE[did]
            data, err = view._get(did, path, ttl=60) if view else (None, "not running")
            if err:
                sec["source_note"] = "Desk %s, so these are the written values." % err
            else:
                try:
                    apply(sec, data or {})
                    sec["source"] = "live"
                except Exception as exc:  # noqa: BLE001 -- a desk answering oddly must not break the page
                    sec = copy.deepcopy(WRITTEN[did])
                    sec.update({"id": did, "kind": "desk", "source": "written",
                                "source_note": "Desk answered without its numbers (%s); written values shown." % exc})
        else:
            sec["source_note"] = "This desk doesn't publish its weights, so these are the written values."
        out.append(sec)
    return out


# ---------------------------------------------------------------- dashboard pages
def pages(settings):
    s = settings or {}
    H = list(trackrec.HORIZONS)
    brokers = [v for k, v in pnl_mod.BROKER_NAMES.items() if k not in ("generic", "ibkr_flex")]
    return [
        {"id": "watchlist", "kind": "page", "name": "Watchlist & theses", "source": "live",
         "question": "Is anything I like cheap now, and does my reason for owning each name still hold?",
         "how": ["Your holdings (from the portfolio sheet) plus any names you add. Each gets a buy price, a written "
                 "thesis and sell rules, and three checks that flag when the thesis breaks.",
                 "The day after a name reports earnings, the Valuation desk re-values it by itself and the change is "
                 "logged; if the new financials aren't filed yet it tries again every %d days, for up to %d days."
                 % (watch_mod.RETRY_DAYS, watch_mod.GIVE_UP_DAYS),
                 "Smart money in your names: insider buying and selling, the latest 13F quarter, and what the "
                 "Confluence, Flow, Institutional and Swing desks say about each one."],
         "rules": [["Buy price", "Intrinsic value less the Valuation desk's margin of safety (25%), or your own price"],
                   ["Buy zone", "Price at or below the buy price; 'near' within %d%% above it" % round(watch_mod.NEAR_ZONE * 100)],
                   ["Price vs value", "Price above intrinsic value, above your sell target, or below your stop"],
                   ["Business got worse", "Verdict fell, or quality fell %s+ points, since you wrote the thesis" % watch_mod.QUALITY_DROP],
                   ["Smart money leaving", "Insiders sold %s+ outside 10b5-1 plans in %d days, or %d+ funds exited and institutions cut %d%%+ in the latest 13F"
                    % (_usd(market_mod.INSIDER_MIN_USD), market_mod.INSIDER_DAYS, watch_mod.FUND_EXITS, round(-watch_mod.FUND_NET * 100))],
                   ["Insider buying light", "%s+ bought, or %d+ different insiders, in %d days"
                    % (_usd(watch_mod.INSIDER_BUY_USD), watch_mod.INSIDER_BUY_PEOPLE, watch_mod.INSIDER_BUY_DAYS)]]},
        {"id": "morning", "kind": "page", "name": "Today", "source": "live",
         "question": "What do I need to know today, and what did I plan?",
         "how": ["One page for the day: market direction, the economic calendar, warnings on your holdings, your "
                 "watchlist names, market tide and sectors, the top names from each running desk, earnings (your "
                 "names first) and the day's journal note, which you write right on the page.",
                 "The date strip (‹ › and the date box) shows the calendar, earnings and journal for any other day. "
                 "It opens by itself on weekday mornings.",
                 "Warnings on your own holdings (from your portfolio sheet) are checked against the rules below. "
                 "A red triangle is serious; an amber dot is a heads-up."],
         "rules": [["Earnings", "A holding reports within %d days" % market_mod.EARNINGS_DAYS],
                   ["Insider selling", "At least %s of open-market sales outside 10b5-1 plans in %d days"
                    % (_usd(market_mod.INSIDER_MIN_USD), market_mod.INSIDER_DAYS)],
                   ["Heavy put buying", "At least %s of puts bought on the ask in %d days, and %sx the call premium bought"
                    % (_usd(market_mod.FLOW_MIN_PUT_USD), market_mod.FLOW_DAYS, _n(market_mod.FLOW_PUT_CALL_RATIO))]]},
        {"id": "trackrecord", "kind": "page", "name": "Track Record", "source": "live",
         "question": "Did each desk's picks go the right way?",
         "how": ["While the market is open (9:45 to 16:30 ET), about every 20 minutes the dashboard records each "
                 "running desk's top 5 names.",
                 "Each pick is graded close to close: entry is the close on the day it was flagged, exit the close "
                 "%s sessions later." % ", ".join(str(n) for n in H),
                 "A bearish pick wins when the stock falls. Results not yet due show as pending, never zero."],
         "rules": [["Hit rate", "Share of graded picks that moved the right way"],
                   ["Avg", "Average return in the pick's direction"],
                   ["vs SPY", "The same return minus SPY's over the same sessions"],
                   ["No repeats", "A name is recorded once per desk per %d days (%d for the 13F desk)"
                    % (trackrec.DEFAULT_WINDOW, trackrec.WINDOW_DAYS.get("institutional", 120))]]},
        {"id": "sizer", "kind": "page", "name": "Position sizer (Ticker Lookup)", "source": "live",
         "question": "How many shares can I buy without breaking my risk rules?",
         "how": ["Takes the smaller of two limits: the shares whose stop-out loses your risk budget, and the "
                 "shares that fit inside your max position size. It never rounds up.",
                 "Shows 1R, 2R and 3R targets, and warns if the position is bigger than your free cash."],
         "rules": [["Risk per trade", "%s%% of capital (Settings)" % _n(s.get("sizer_risk_pct", 1.0))],
                   ["Max position", "%s%% of capital" % _n(s.get("sizer_max_pos_pct", 10.0))],
                   ["Default stop", "%s%% from entry" % _n(s.get("sizer_stop_pct", 5.0))],
                   ["Capital", "%s (Settings, Portfolio)" % _usd(s.get("portfolio_capital", 100000))]],
         "formula": "shares = min( capital x risk% / (entry x stop%),  capital x max% / entry )"},
        {"id": "pnl", "kind": "page", "name": "Interactive P&L", "source": "live",
         "question": "What did I actually make, day by day?",
         "how": ["Upload a broker CSV; the calendar shows realised P&L per day in green and red, with weekly totals.",
                 "Buys and sells are matched first-in, first-out (FIFO). Re-uploading overlapping statements never "
                 "double-counts, because every fill has a stable id."],
         "rules": [["Brokers", ", ".join(brokers) + ", or any CSV with a column mapping"]]},
        {"id": "discord", "kind": "page", "name": "Discord alerts", "source": "written",
         "source_note": "Written in guide.py",
         "question": "Where do the desks' Discord alerts go, and who may see them?",
         "how": ["Confluence, Swing and Growth Leaders can post alerts to Discord. Nothing is posted until you paste "
                 "your own webhook into that desk's .env file; the suite ships with none.",
                 "Discord alerts are meant for your own private channel. Posting Unusual Whales data to other "
                 "people, in a public or paid server, may need a redistribution licence from Unusual Whales. "
                 "Check their terms first.",
                 "A webhook URL lets anyone post to that channel: keep it in .env (never committed) and don't share it. "
                 "Growth Leaders uses the Swing or Confluence webhook if it has none of its own."],
         "rules": [["Confluence", "DISCORD_WEBHOOK_URL in Confluence Desk/.env"],
                   ["Swing", "DISCORD_WEBHOOK_URL in Swing Desk/.env"],
                   ["Growth Leaders", "GDESK_DISCORD_WEBHOOK_URL in Growth Desk/.env, or a sibling desk's"]]},
    ]


def build(view, settings=None, enabled=ORDER):
    return {"desks": desks(view, enabled), "pages": pages(settings)}
