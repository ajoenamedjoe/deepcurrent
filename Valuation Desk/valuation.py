"""
Valuation Desk -- the model.

Stdlib only, and self-contained ON PURPOSE: `generate.analysis_script()` inlines
this whole file into every `{TICKER}_analysis.py`, so a script written today
reproduces today's numbers next year even after this file changes.

Pipeline
    extract()   raw UW payloads -> one clean `fin` dict (TTM where possible)
    classify()  which of six models fits the company
    value()     three-scenario DCF on a single unified engine
    score()     0-10 verdict (0 = strongly undervalued, 10 = avoid)
    analyse()   all of the above + the prose the report and card print

Every trap below was found on real payloads, not guessed (the test fixtures
reproduce each one with synthetic numbers):

  * numeric fields arrive as strings and as null.
  * `ebit` / `ebitda` can disagree with `operating_income` by more than the
    company's whole revenue in a quarter (warrant marks flowing through ebit
    but not operating income). EBITDA here is ALWAYS operating_income + D&A;
    the API's ebit/ebitda are never read.
  * net income can be dominated by non-cash marks (a small-revenue company
    swinging from a large profit one quarter to a larger loss the next). P/E is
    shown but never drives a model for a company whose operating income is
    negative.
  * `cash_and_short_term_investments` can EQUAL `cash_and_cash_equivalents`
    while `short_term_investments` is reported separately. Summing the
    collapsed field gives cash only and badly understates runway.
  * `capital_expenditures` is a POSITIVE number (an outflow). abs() it.
  * banks: operating cash flow can be hugely negative in a year of record
    earnings, and capex is reported as "0". Financials are valued on earnings,
    never on FCF.
  * buybacks can arrive as a NEGATIVE `proceeds_from_repurchase_of_equity`
    rather than in `payments_for_repurchase_of_common_stock`.
  * ADRs: statements in the home currency (e.g. TWD), quote in USD per ADR,
    `outstanding` in ADR units. Mixing them prints an absurdly low P/E. Non-USD
    reporters need an explicit FX rate or the run REFUSES -- never silently.
  * pre-listing rows are garbage (e.g. negative cash). Only the latest few
    periods are ever used.
"""

import re
import datetime
import json
import math

MODEL_VERSION = "3.2.0"   # 3.2 quality score, Buffett gates, expected returns; 1.1 SBC; 1.2 base margin; 2.0 scale inverted; 3.0 10-yr fade + funded growth; 3.1 fixed discount, trend margins, cycle check

RISK_FREE = 0.0425          # 10y UST, set by hand -- print it on every report
EQUITY_PREMIUM = 0.050
YEARS = 5            # years at the scenario growth rate
FADE_YEARS = 5       # then growth fades linearly to terminal: a 10-year projection (model 3.0)
FADE_START_CAP = 0.25  # years 6-10 fade from at most 25%: an 80%-a-year venture case
                       # compounding into year 10 turned a single-digit stock into a 40x value in testing
TAX_DEFAULT = 0.21

# Model 3.1 switches. All OFF = exactly model 3.0 (so a comparison run can flip
# them one at a time on the same code). The desk turns on what the user approves.
# 3.1 as shipped: fixed discount + trend rule change the value;
# the 10-yr cycle check is SHOWN (card, report, flag list) but moves nothing.
OPTIONS = {"fixed_discount": True, "trend_margin": True, "cycle_aware": False}
CYCLE_CLASSES = ("fcf", "mature", "utility")   # banks and loss-makers have their own logic
FIXED_DISCOUNT = {"mature": 0.07, "utility": 0.07, "financial": 0.09, "fcf": 0.09,
                  "growth_burn": 0.10, "turnaround": 0.10, "venture": 0.11}
S2C_MIN, S2C_MAX, S2C_DEFAULT = 0.8, 5.0, 2.0   # sales-to-capital bounds
ROE_CAP = 0.40

# Scenario growth bands straight from the brief ("DCF Assumptions Template").
# (lo, hi) per scenario. Where a company sits inside each band is set by ONE
# number -- its trailing revenue growth -- so the three scenarios move together
# and can never cross (bull >= base >= bear by construction, tested).
BANDS = {
    "growth":  {"bull": (0.20, 0.30), "base": (0.10, 0.15), "bear": (0.03, 0.08)},
    "mature":  {"bull": (0.08, 0.15), "base": (0.04, 0.08), "bear": (0.02, 0.04)},
    "utility": {"bull": (0.05, 0.06), "base": (0.03, 0.045), "bear": (0.02, 0.025)},
    "venture": {"bull": (0.50, 1.00), "base": (0.30, 0.80), "bear": (0.10, 0.50)},
}
TERMINAL = {"bull": 0.030, "base": 0.025, "bear": 0.020}

# Year-5 FCF margin the revenue-path models ramp toward. Only used where the
# company has no positive cash margin of its own to project.
TARGET_MARGIN = {
    "venture":     {"bull": 0.25, "base": 0.15, "bear": 0.05},
    "growth_burn": {"bull": 0.20, "base": 0.12, "bear": 0.05},
    "turnaround":  {"bull": 0.10, "base": 0.05, "bear": 0.02},
}

CLASSES = {
    "fcf":         {"label": "Profitable, positive FCF", "method": "FCF DCF",
                    "discount": (0.08, 0.12)},
    "mature":      {"label": "Mature / low growth", "method": "Conservative FCF DCF",
                    "discount": (0.06, 0.08)},
    "utility":     {"label": "Utility / regulated", "method": "Conservative FCF DCF",
                    "discount": (0.06, 0.08)},
    "financial":   {"label": "Bank / insurer / lender", "method": "Earnings DCF (FCF not meaningful)",
                    "discount": (0.08, 0.12)},
    "growth_burn": {"label": "High growth, negative FCF", "method": "Revenue scenario model",
                    "discount": (0.08, 0.12)},
    "venture":     {"label": "Pre-commercial / venture stage", "method": "Revenue scenario model",
                    "discount": (0.10, 0.12)},
    "turnaround":  {"label": "Negative FCF, low growth", "method": "Revenue scenario model",
                    "discount": (0.09, 0.12)},
}

# Static reference ranges -- NOT live data, and labelled as such everywhere
# they are printed. (P/E, P/S, EV/EBITDA) typical large-cap medians.
SECTOR_REF = {
    "Technology":             (28, 6.0, 20),
    "Communication Services": (20, 3.0, 12),
    "Consumer Cyclical":      (22, 1.5, 14),
    "Consumer Defensive":     (21, 1.3, 14),
    "Healthcare":             (22, 2.0, 15),
    "Financial Services":     (14, 3.0, None),
    "Industrials":            (22, 2.0, 15),
    "Energy":                 (12, 1.2, 6),
    "Utilities":              (18, 2.5, 11),
    "Real Estate":            (35, 6.0, 18),
    "Basic Materials":        (16, 1.5, 9),
}

# Red flags can only push the score DOWN, toward caution (never toward "buy"): the
# valuation-side analogue of "modifiers multiply down only". Capped in total so
# flags colour a verdict but cannot manufacture one on their own.
FLAG_POINTS_CAP = 2.0

# Scale 2.0: 10 = deeply undervalued, 0 = avoid. Higher is
# better, like every other desk's score. 1.x ran the other way; old runs are
# converted by upgrade_result().
SCALE_VERSION = 2
# An EXACT mirror of the 1.x bands, so inverting changed no stock's verdict,
# only its number. 1.x: 0-2 strong buy, 3-4 buy, 5-6 hold, 7-8 reduce, 9-10 avoid.
# 2.0 (whole-number badge): 0-1 avoid, 2-3 reduce, 4-5 hold, 6-7 buy, 8-10 strong buy.
# Thresholds are on the 1-dp score; a tie at .5 goes to the more cautious verdict.
VERDICTS = [  # (max score inclusive, label, colour key)
    (1.5, "AVOID", "bad"),
    (3.5, "REDUCE", "bad"),
    (5.5, "HOLD", "warn"),
    (7.5, "BUY", "good"),
    (10.0, "STRONG BUY", "good"),
]


# ======================================================================
# coercion
# ======================================================================

def num(val, default=None):
    """UW numbers are strings, ints, floats or null. None means ABSENT."""
    if val is None:
        return default
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return default if val != val else float(val)
    try:
        text = str(val).strip().replace(",", "")
        if text == "" or text.lower() in ("none", "null", "nan"):
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def rows_of(payload):
    """Envelopes differ (data / result / bare list / single object). Dig."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "result", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
            if isinstance(val, dict):
                inner = rows_of(val)
                if inner:
                    return inner
        if "fiscal_date_ending" in payload:
            return [payload]
    return []


def info_of(payload):
    """/info: MCP wraps in {"data":{...},"price":...}; REST may be bare."""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    out = dict(inner)
    if "price" in payload and "price" not in out:
        out["price"] = payload["price"]
    return out


def _by_date(rows, kind=None):
    out = [r for r in rows if r.get("fiscal_date_ending")]
    if kind:
        out = [r for r in out if (r.get("report_type") or kind) == kind]
    out.sort(key=lambda r: r["fiscal_date_ending"], reverse=True)
    return out


def _days(a, b):
    """Days between two YYYY-MM-DD strings (a - b)."""
    from datetime import date
    ya, ma, da = (int(x) for x in a[:10].split("-"))
    yb, mb, db = (int(x) for x in b[:10].split("-"))
    return (date(ya, ma, da) - date(yb, mb, db)).days


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


# ======================================================================
# extract
# ======================================================================

JUNK_CCY = {"", "NONE", "NULL", "NAN", "N/A", "NA", "-", "UNKNOWN"}


def norm_ccy(val):
    """A currency code, or None. UW sends the literal string "None" sometimes
    for some large caps -- which the first draft read as a currency and
    refused to value the company in 'NONE'."""
    if val is None:
        return None
    t = str(val).strip().upper()
    if t in JUNK_CCY or not t.isalpha() or len(t) != 3:
        return None
    return t


def statement_currency(window, all_rows):
    """
    -> (currency, note). The income-statement rows actually used decide; a
    junk label falls back to the most common real label on the ticker's
    other income-statement rows (newest 8), then to USD -- with a note either way.
    """
    seen = [norm_ccy(r.get("reported_currency")) for r in window]
    known = [c for c in seen if c]
    if known:
        top = max(set(known), key=known.count)
        if len(set(known)) > 1:
            return top, ("Income-statement rows in this period carry mixed currency labels "
                         "%s -- treated as %s." % ("/".join(sorted(set(known))), top))
        if len(known) < len(seen):
            return top, None
        return top, None
    others = [norm_ccy(r.get("reported_currency"))
              for r in sorted(all_rows, key=lambda r: r.get("fiscal_date_ending") or "", reverse=True)[:8]]
    others = [c for c in others if c]
    if others:
        top = max(set(others), key=others.count)
        return top, "No currency label on the latest statements -- using %s from earlier filings." % top
    return "USD", "No currency label on any statement -- assumed USD."


def _sum4(rows, field):
    vals = [num(r.get(field)) for r in rows]
    if any(v is None for v in vals):
        return None
    return sum(vals)


def _contiguous(rows):
    """Four quarters spanning ~one year, newest first."""
    if len(rows) < 4:
        return False
    span = _days(rows[0]["fiscal_date_ending"], rows[3]["fiscal_date_ending"])
    return 250 <= span <= 300


def _period(is_a, is_q, cf_a, cf_q):
    """
    Pick the most current full-year view.

    TTM from the last four quarters when (a) they are contiguous, (b) revenue
    is present in all four, and (c) the newest quarter is NEWER than the
    newest annual. Otherwise the latest annual. The chosen basis is printed
    on every deliverable.
    """
    qa = _by_date(is_q, "quarterly")
    ca = _by_date(cf_q, "quarterly")
    aa = _by_date(is_a, "annual")
    caa = _by_date(cf_a, "annual")
    newest_annual = aa[0]["fiscal_date_ending"] if aa else "0000-00-00"

    if (_contiguous(qa) and qa[0]["fiscal_date_ending"] > newest_annual
            and _sum4(qa[:4], "total_revenue") is not None):
        cq = {r["fiscal_date_ending"]: r for r in ca}
        cf4 = [cq.get(r["fiscal_date_ending"]) for r in qa[:4]]
        cur = {"basis": "TTM to %s" % qa[0]["fiscal_date_ending"],
               "end": qa[0]["fiscal_date_ending"], "is": qa[:4],
               "cf": cf4 if all(cf4) else None}
        prior = None
        if len(qa) >= 8 and _contiguous(qa[4:8]) and _sum4(qa[4:8], "total_revenue") is not None:
            prior = {"is": qa[4:8]}
        elif aa:
            # prior-year proxy: the annual whose end is ~1y before the TTM end
            for r in aa:
                if 330 <= _days(qa[0]["fiscal_date_ending"], r["fiscal_date_ending"]) <= 400:
                    prior = {"is": [r]}
                    break
        # CF fallback when quarterly cash flow is missing: newest annual
        if cur["cf"] is None and caa:
            cur["cf"] = [caa[0]]
            cur["cf_note"] = "cash flow from FY %s (quarterly CF incomplete)" % caa[0]["fiscal_date_ending"][:4]
        return cur, prior, qa

    if aa:
        cur = {"basis": "FY%s" % aa[0]["fiscal_date_ending"][:4],
               "end": aa[0]["fiscal_date_ending"], "is": [aa[0]], "cf": None}
        cmap = {r["fiscal_date_ending"]: r for r in caa}
        if aa[0]["fiscal_date_ending"] in cmap:
            cur["cf"] = [cmap[aa[0]["fiscal_date_ending"]]]
        elif caa:
            cur["cf"] = [caa[0]]
        prior = {"is": [aa[1]]} if len(aa) > 1 else None
        return cur, prior, qa
    return None, None, qa


def _total(rows, field):
    if not rows:
        return None
    vals = [num(r.get(field)) for r in rows]
    if all(v is None for v in vals):
        return None
    return sum(v for v in vals if v is not None)


def extract(info, is_a=None, is_q=None, bs_a=None, bs_q=None, cf_a=None, cf_q=None,
            fx=None):
    """
    -> fin dict. `fx` maps currency code -> units of that currency per 1 USD.
    Raises ValueError with a human sentence when the run cannot be honest.
    """
    inf = info_of(info)
    notes, quality = [], []
    is_a, is_q = rows_of(is_a), rows_of(is_q)
    bs_a, bs_q = rows_of(bs_a), rows_of(bs_q)
    cf_a, cf_q = rows_of(cf_a), rows_of(cf_q)

    ticker = (inf.get("symbol") or inf.get("ticker") or "").upper()
    price = num(inf.get("price"))
    mcap = num(inf.get("marketcap"))
    out_sh = num(inf.get("outstanding"))
    if not ticker:
        raise ValueError("No such ticker (the info endpoint returned nothing).")
    if price is None or price <= 0:
        raise ValueError("No current price for %s." % ticker)
    if not is_a and not is_q:
        raise ValueError("%s has no income statements on Unusual Whales "
                         "(ETF, fund, or a very recent listing?)." % ticker)

    # Share count: /info `outstanding` first. It is ADR units for an ADR (price x
    # count matches marketcap closely), which is the unit the quote is in.
    # `marketcap` can be STALE against `price` -- marketcap / price, `outstanding`
    # and the latest balance-sheet count can all disagree, with the cap priced
    # ~10% away from the quote after a big move. So market cap is
    # always recomputed as price x shares, and a disagreement is reported.
    shares = out_sh if (out_sh and out_sh > 0) else None
    if shares is None and mcap and price:
        shares = mcap / price
        quality.append("No share count on /info -- derived from marketcap / price.")
    if shares and mcap:
        drift = (shares * price) / mcap - 1
        if abs(drift) > 0.05:
            quality.append("UW marketcap (%s) is %+.0f%% off price x shares -- stale; "
                           "recomputed." % (money(mcap), -100 * drift))
    if shares:
        mcap = shares * price

    cur, prior, quarters = _period(is_a, is_q, cf_a, cf_q)
    if cur is None:
        raise ValueError("%s: no usable income statement periods." % ticker)

    currency, cur_note = statement_currency(cur["is"], is_a + is_q)
    if cur_note:
        quality.append(cur_note)
    rate = 1.0
    if currency != "USD":
        rate = (fx or {}).get(currency)
        if not rate:
            raise ValueError(
                "%s's financial statements are in %s but its share price is in US dollars. "
                "Add the line VDESK_FX_%s=<%s per 1 US dollar> to the .env file in the "
                "Valuation Desk folder, then restart the desk -- it will not mix "
                "currencies." % (ticker, currency, currency, currency))
        notes.append("Statements converted from %s at %s %s/USD (set in .env)."
                     % (currency, rate, currency))
    k = 1.0 / rate

    def conv(v):
        return None if v is None else v * k

    isr = cur["is"]
    revenue = conv(_total(isr, "total_revenue"))
    op_inc = conv(_total(isr, "operating_income"))
    gross = conv(_total(isr, "gross_profit"))
    da = conv(_total(isr, "depreciation_and_amortization"))
    net_inc = conv(_total(isr, "net_income"))
    int_exp = conv(_total(isr, "interest_expense"))
    pretax = _total(isr, "income_before_tax")
    taxx = _total(isr, "income_tax_expense")
    tax_rate = TAX_DEFAULT
    if pretax and taxx is not None and pretax > 0 and 0.05 <= taxx / pretax <= 0.35:
        tax_rate = taxx / pretax
    ebitda = (op_inc + (da or 0)) if op_inc is not None else None

    cfr = cur.get("cf") or []
    odd = sorted({norm_ccy(r.get("reported_currency")) for r in cfr} - {None, currency})
    if odd:
        # Seen on a foreign-domiciled listing: cash-flow rows labelled in a different currency while the income
        # statement for the SAME quarters says USD, and the magnitudes are
        # plainly dollars. The income-statement label is taken as authoritative.
        quality.append("Cash-flow rows labelled %s while the income statement for the same "
                       "periods says %s -- treated as %s (label conflict in the source data)."
                       % ("/".join(odd), currency, currency))
    ocf = conv(_total(cfr, "operating_cashflow"))
    capex_raw = _total(cfr, "capital_expenditures")
    capex = conv(abs(capex_raw)) if capex_raw is not None else None
    cf_da = conv(_total(cfr, "depreciation_depletion_and_amortization"))
    sbc = conv(_total(cfr, "stock_based_compensation"))
    divs = conv(_total(cfr, "dividend_payout"))
    buy = _total(cfr, "payments_for_repurchase_of_common_stock")
    if buy is None:
        pr = _total(cfr, "proceeds_from_repurchase_of_equity")
        buy = -pr if (pr is not None and pr < 0) else None
    buybacks = conv(abs(buy)) if buy is not None else None
    if cur.get("cf_note"):
        notes.append(cur["cf_note"])
    if not cfr:
        quality.append("No cash flow statement for the period -- FCF unmeasured; "
                       "after-tax operating income used as the cash proxy where positive.")

    fcf = (ocf - (capex or 0)) if ocf is not None else None
    cash_proxy = False
    if ocf is None and op_inc is not None and op_inc > 0:
        # No cash flow statement: after-tax operating income stands in, and
        # the report says so. Missing data must never read as cash BURN.
        cash_proxy = True
    maint = min(capex, cf_da if cf_da is not None else (da or capex)) if capex is not None else None
    owner = (ocf - maint) if (ocf is not None and maint is not None) else fcf

    # Up to three fiscal years of cash margins, for normalisation. One year of
    # FCF is noise for anything cyclical: a power producer's reported FCF margin
    # can fall from the mid-20s to under 1% in three years.
    hist = []
    cfmap = {r["fiscal_date_ending"]: r for r in _by_date(cf_a, "annual")}
    for r in _by_date(is_a, "annual")[:10]:
        c = cfmap.get(r["fiscal_date_ending"])
        rv = num(r.get("total_revenue"))
        if not c or not rv or rv <= 0:
            continue
        o = num(c.get("operating_cashflow"))
        cx = num(c.get("capital_expenditures"))
        d = num(c.get("depreciation_depletion_and_amortization"))
        if d is None:
            d = num(r.get("depreciation_and_amortization"))
        if o is None:
            continue
        cx = abs(cx) if cx is not None else 0.0
        mnt = min(cx, d) if d is not None else cx
        sb = num(c.get("stock_based_compensation"))
        opi = num(r.get("operating_income"))
        hist.append({"fy": r["fiscal_date_ending"][:4], "rev": rv * k, "rep": (o - cx) / rv, "own": (o - mnt) / rv,
                     "sbc": (abs(sb) / rv) if sb is not None else None,
                     "op": (opi / rv) if opi is not None else None})

    # Model 3.2: one row per fiscal year (newest first, up to 10) with everything
    # the QUALITY score reads -- returns on capital, share count, goodwill,
    # leverage, earnings quality. Money in USD (k), share counts as reported.
    annual = []
    bsmap = {r["fiscal_date_ending"]: r for r in _by_date(bs_a, "annual")}
    for r in _by_date(is_a, "annual")[:10]:
        fd = r["fiscal_date_ending"]
        c = cfmap.get(fd) or {}
        b_ = bsmap.get(fd) or {}
        g = lambda row, f: num(row.get(f))
        rv = g(r, "total_revenue")
        if rv is None or rv <= 0:
            continue
        d_ = g(b_, "short_long_term_debt_total")
        if d_ is None:
            parts = [g(b_, f) for f in ("short_term_debt", "long_term_debt", "capital_lease_obligations")]
            d_ = sum(x for x in parts if x is not None) if any(x is not None for x in parts) else (0.0 if b_ else None)
        cs, st = g(b_, "cash_and_cash_equivalents"), g(b_, "short_term_investments")
        csti = g(b_, "cash_and_short_term_investments")
        liq = (cs + st) if (csti is not None and cs is not None and st and abs(csti - cs) < 1) else \
            (csti if csti is not None else ((cs or 0) + (st or 0) if b_ else None))
        cx = g(c, "capital_expenditures")
        bb = g(c, "payments_for_repurchase_of_common_stock")
        if bb is None:
            pr_ = g(c, "proceeds_from_repurchase_of_equity")
            bb = -pr_ if (pr_ is not None and pr_ < 0) else None
        kk = lambda v: None if v is None else v * k
        annual.append({
            "fy": fd[:4], "end": fd, "rev": kk(rv), "op": kk(g(r, "operating_income")),
            "ni": kk(g(r, "net_income")), "pretax": kk(g(r, "income_before_tax")),
            "tax": kk(g(r, "income_tax_expense")),
            "da": kk(g(c, "depreciation_depletion_and_amortization") if g(c, "depreciation_depletion_and_amortization") is not None
                     else g(r, "depreciation_and_amortization")),
            "ocf": kk(g(c, "operating_cashflow")), "capex": kk(abs(cx)) if cx is not None else None,
            "sbc": kk(abs(g(c, "stock_based_compensation"))) if g(c, "stock_based_compensation") is not None else None,
            "buyback": kk(abs(bb)) if bb is not None else None,
            "dividends": kk(abs(g(c, "dividend_payout"))) if g(c, "dividend_payout") is not None else None,
            "shares": g(b_, "common_stock_shares_outstanding"),
            "goodwill": kk(g(b_, "goodwill")) if g(b_, "goodwill") is not None else (0.0 if "goodwill" in b_ else None),
            "intangibles": kk(g(b_, "intangible_assets")),
            "equity": kk(g(b_, "total_shareholder_equity")), "assets": kk(g(b_, "total_assets")),
            "debt": kk(d_), "liquid": kk(liq), "has_bs": bool(b_), "has_cf": bool(c)})

    # hist holds up to 10 fiscal years (newest first). The 3-year view is what
    # 1.x-3.0 always used; the long view feeds the cycle check (model 3.1).
    hist_long = [dict(h) for h in hist]
    for h in hist_long:
        h["adj"] = h["own"] - (h["sbc"] if h["sbc"] is not None else (abs(sbc) / revenue if (sbc and revenue) else 0.0))
    hist = hist[:3]

    # growth
    prev_rev = conv(_total(prior["is"], "total_revenue")) if prior else None
    prev_op = conv(_total(prior["is"], "operating_income")) if prior else None
    rev_growth = (revenue / prev_rev - 1) if (revenue and prev_rev and prev_rev > 0) else None
    q_growth = None
    qq = [r for r in _by_date(is_q, "quarterly")][:2]
    if len(qq) == 2:
        a, b = num(qq[0].get("total_revenue")), num(qq[1].get("total_revenue"))
        if a is not None and b and b > 0:
            q_growth = a / b - 1
    op_margin = safe_div(op_inc, revenue)
    prev_op_margin = safe_div(prev_op, prev_rev)

    # balance sheet: the newest statement of either kind
    bs_all = _by_date(bs_q) + _by_date(bs_a)
    bs_all.sort(key=lambda r: r["fiscal_date_ending"], reverse=True)
    bs = bs_all[0] if bs_all else None
    bs_prev = None
    if bs:
        for r in bs_all[1:]:
            if 330 <= _days(bs["fiscal_date_ending"], r["fiscal_date_ending"]) <= 400:
                bs_prev = r
                break
    cash = liquid = debt = equity = assets = liabilities = goodwill = None
    cur_assets = cur_liab = None
    bs_shares = bs_shares_prev = None
    if bs:
        cash = conv(num(bs.get("cash_and_cash_equivalents")))
        csti = conv(num(bs.get("cash_and_short_term_investments")))
        sti = conv(num(bs.get("short_term_investments")))
        if csti is not None and cash is not None and abs(csti - cash) < 1 and sti:
            liquid = cash + sti          # the collapsed-field trap
        else:
            liquid = csti if csti is not None else ((cash or 0) + (sti or 0))
        debt = conv(num(bs.get("short_long_term_debt_total")))
        if debt is None:
            parts = [num(bs.get(f)) for f in ("short_term_debt", "long_term_debt",
                                              "capital_lease_obligations")]
            if any(p is not None for p in parts):
                debt = conv(sum(p for p in parts if p is not None))
            elif num(bs.get("total_liabilities")) is not None or num(bs.get("total_assets")) is not None:
                # A real balance sheet with EVERY debt field null means NO debt.
                # Seen on a debt-free software name: debt None -> net cash None -> its whole
                # cash pile silently ignored and sales-to-capital fell back to the
                # "no balance sheet" default -- base value roughly halved.
                debt = 0.0
                notes.append("No debt fields on the balance sheet -- treated as debt-free.")
            else:
                debt = None
        equity = conv(num(bs.get("total_shareholder_equity")))
        assets = conv(num(bs.get("total_assets")))
        liabilities = conv(num(bs.get("total_liabilities")))
        goodwill = conv((num(bs.get("goodwill")) or 0) + (num(bs.get("intangible_assets")) or 0))
        cur_assets = conv(num(bs.get("total_current_assets")))
        cur_liab = conv(num(bs.get("total_current_liabilities")))
        bs_shares = num(bs.get("common_stock_shares_outstanding"))
        if bs_prev:
            bs_shares_prev = num(bs_prev.get("common_stock_shares_outstanding"))
    else:
        quality.append("No balance sheet -- cash, debt and equity are UNMEASURED, "
                       "not zero. Equity value = enterprise value.")

    dilution = None
    if bs_shares and bs_shares_prev and bs_shares_prev > 0:
        dilution = bs_shares / bs_shares_prev - 1

    if bs_shares and shares and currency == "USD":
        ratio = shares / bs_shares
        if ratio > 1.5 or ratio < 0.67:
            quality.append("Quote share count (%.0fM) and balance-sheet count (%.0fM) differ "
                           "%.1fx -- multi-class shares or an ADR ratio. Per-share values use "
                           "the quote's count." % (shares / 1e6, bs_shares / 1e6, ratio))

    beta = num(inf.get("beta"))
    if beta is None:
        quality.append("No beta -- discount rate set at the band midpoint.")
    if revenue is None:
        quality.append("Revenue missing for the period.")

    return {
        "ticker": ticker,
        "name": (inf.get("full_name") or inf.get("short_name") or ticker).strip(),
        "sector": inf.get("sector") or "Unknown",
        "issue_type": inf.get("issue_type") or "",
        "description": (inf.get("short_description") or "").strip(),
        "next_earnings": inf.get("next_earnings_date"),
        "price": price, "shares": shares, "marketcap": mcap, "beta": beta,
        "currency": currency, "fx_rate": rate if currency != "USD" else None,
        "basis": cur["basis"], "period_end": cur["end"],
        "revenue": revenue, "prev_revenue": prev_rev, "rev_growth": rev_growth,
        "q_growth": q_growth, "gross_profit": gross, "operating_income": op_inc,
        "op_margin": op_margin, "prev_op_margin": prev_op_margin,
        "net_income": net_inc, "net_margin": safe_div(net_inc, revenue),
        "ebitda": ebitda, "da": cf_da if cf_da is not None else da,
        "interest_expense": int_exp,
        "ocf": ocf, "capex": capex, "fcf": fcf, "owner_fcf": owner,
        "fcf_margin": safe_div(fcf, revenue), "owner_margin": safe_div(owner, revenue),
        "margin_hist": hist, "margin_hist_long": hist_long, "annual": annual, "cash_proxy": cash_proxy, "tax_rate": tax_rate,
        "capex_intensity": safe_div(capex, revenue), "sbc": sbc,
        "sbc_pct": safe_div(sbc, revenue), "dividends": divs, "buybacks": buybacks,
        "bs_date": bs["fiscal_date_ending"] if bs else None,
        "cash": cash, "liquid": liquid, "debt": debt,
        "net_cash": (liquid - debt) if (liquid is not None and debt is not None) else None,
        "equity": equity, "assets": assets, "liabilities": liabilities,
        "goodwill_intang": goodwill, "current_assets": cur_assets,
        "current_liabilities": cur_liab, "dilution": dilution,
        "notes": notes, "quality": quality,
    }


# ======================================================================
# classify
# ======================================================================

FINANCIAL_WORDS = ("financial", "bank", "insurance")


def cycle_profile(fin):
    """
    Is this business cyclical, judged from up to 10 years of its OWN history?

        cyclical = (revenue fell in >= 2 years AND the SBC-adjusted cash margin
                    dropped > 5 pts in >= 2 years)
                or (revenue fell in >= 1 year AND margin dropped > 5 pts in >= 3)

    Margin VOLATILITY alone was tried first and is wrong: young companies moving
    from losses to profits have the widest swings of all and
    are improving, not cycling. A cycle is repeated DECLINES. On a 45-ticker
    test set this flags the classic cyclicals (asset managers, crypto, solar,
    semiconductors, power) and nothing that is simply maturing. Needs >= 5 fiscal years, else "unknown".
    """
    L = list(reversed(fin.get("margin_hist_long") or []))       # oldest first
    if len(L) < 5:
        return {"known": False, "cyclical": False, "years": len(L)}
    revs = [h["rev"] for h in L]
    adj = [h["adj"] for h in L]
    rdown = sum(1 for i in range(1, len(revs)) if revs[i] < revs[i - 1] * 0.98)
    mdrops = sum(1 for i in range(1, len(adj)) if adj[i] < adj[i - 1] - 0.05)
    cyc = (rdown >= 2 and mdrops >= 2) or (rdown >= 1 and mdrops >= 3)
    srt = sorted(adj)
    p25 = srt[max(0, int(round(0.25 * (len(srt) - 1))))]
    n = len(srt)
    med = srt[n // 2] if n % 2 else 0.5 * (srt[n // 2 - 1] + srt[n // 2])
    mean_adj = sum(adj) / len(adj)
    sd = (sum((a - mean_adj) ** 2 for a in adj) / len(adj)) ** 0.5
    own = [h["own"] for h in L]
    yrs = len(revs) - 1
    cagr = (revs[-1] / revs[0]) ** (1.0 / yrs) - 1 if (revs[0] and revs[0] > 0 and revs[-1] > 0) else None
    return {"known": True, "cyclical": cyc, "years": len(L), "rev_down_years": rdown,
            "margin_drops": mdrops, "mean_adj": mean_adj, "median_adj": med, "p25_adj": p25, "sd_adj": sd,
            "mean_own": sum(own) / len(own), "rev_cagr": cagr,
            "adj_series": adj, "rev_series": revs}


def capital_efficiency(fin, cls):
    """
    How much capital growth costs -> ("s2c" | "roe", value, how).

    Non-financials: SALES-TO-CAPITAL = revenue / invested capital (equity +
    debt - cash & ST investments), clamped 0.8..5.0. Each extra $1 of revenue
    needs $1/s2c of new investment. Asset-light software (several dollars of revenue
    per dollar of capital) pays little for growth; capital-heavy businesses pay a lot.
    Invested capital <= 0 (net cash above book equity + debt) is asset-light by
    definition -> the cap. No balance sheet -> 2.0, stated.

    Financials: ROE (net income / equity), clamped to 0.40; unmeasurable -> 12%.

    Why not ROIC on operating income (tried first, model 3.0 draft): operating
    income carries non-cash marks (hedge marks at a power producer, crypto
    marks at an exchange), and a lender's "invested capital" is its loan book
    (a single-digit ROIC). It priced healthy companies at or near $0.
    """
    if cls == "financial":
        ni, eq = fin["net_income"], fin["equity"]
        if ni and eq and eq > 0 and ni > 0:
            roe = ni / eq
            return "roe", min(roe, ROE_CAP), "ROE %.1f%% (net income / equity)%s" % (
                100 * roe, " -- capped at 40%" if roe > ROE_CAP else "")
        return "roe", 0.12, "ROE not measurable -- using 12%"
    rev, eq, debt, liq = fin["revenue"], fin["equity"], fin["debt"], fin["liquid"]
    gw = fin.get("goodwill_intang") or 0.0
    if not rev or rev <= 0:
        return "s2c", S2C_DEFAULT, "sales-to-capital: no revenue -- using %.1f" % S2C_DEFAULT
    if eq is None or debt is None:
        return "s2c", S2C_DEFAULT, "sales-to-capital: no balance sheet -- using %.1f" % S2C_DEFAULT
    # Goodwill and acquired intangibles are EXCLUDED: they are the price of past
    # acquisitions, not capital new growth needs. With them in, several acquisitive
    # large caps all clamped to the 0.8 floor and looked like heavy industry.
    ic = eq + debt - (liq or 0) - gw
    if ic <= 0:
        return "s2c", S2C_MAX, ("sales-to-capital: operating capital %s <= 0 (asset-light) -- using %.1f"
                                % (money(ic), S2C_MAX))
    raw = rev / ic
    v = clamp(raw, S2C_MIN, S2C_MAX)
    return "s2c", v, "sales-to-capital %.2f (revenue %s / operating capital %s, ex goodwill)%s" % (
        raw, money(rev), money(ic), "" if v == raw else " -- clamped to %.1f" % v)


def classify(fin):
    """
    -> (class_key, reason sentence). First match wins; order matters and is
    tested (a bank with negative OCF must never fall through to 'turnaround').
    """
    sector = (fin["sector"] or "").lower()
    rev, g = fin["revenue"], fin["rev_growth"]
    fcf, owner, ni, ocf = fin["fcf"], fin["owner_fcf"], fin["net_income"], fin["ocf"]
    capint = fin["capex_intensity"]
    if fin.get("cash_proxy"):
        owner = fcf = (fin["operating_income"] or 0) * (1 - 0.21)

    if any(w in sector for w in FINANCIAL_WORDS):
        # Payment networks sit in Financial Services with normal cash flows;
        # banks/insurers do not. The tell is OCF that disagrees with earnings
        # in sign, or zero reported capex.
        weird = ((ocf is None and not fin.get("cash_proxy")) or ni is None or (ocf is not None and ocf < 0 < ni) or
                 (ocf is not None and ni > 0 and abs(ocf) > 2.5 * abs(ni)) or
                 (capint is not None and capint < 0.002))
        if weird:
            return "financial", ("Financial-sector balance sheet: operating cash flow "
                                 "does not track earnings, so FCF is not meaningful.")
    if rev is None or rev < 100e6 or (fcf is not None and fcf < 0 and abs(fcf) > rev):
        return "venture", ("Revenue of %s with cash burn larger than revenue -- "
                           "valued on a path-to-profitability scenario." % money(rev))
    if (owner is None or owner <= 0) and (g or 0) >= 0.15:
        return "growth_burn", ("Growing %s but not yet generating cash -- revenue "
                               "scenario model with a margin ramp." % pct(g))
    if owner is None or owner <= 0:
        return "turnaround", ("Negative cash generation without growth to carry it -- "
                              "revenue scenario model with modest margins.")
    if "utilit" in sector:
        return "utility", "Utility sector -- conservative growth, 6-8% discount rate."
    if g is not None and g < 0.05:
        return "mature", "Revenue growing under 5% -- conservative FCF DCF."
    if sector in ("consumer defensive", "real estate") and (g is None or g < 0.08):
        return "mature", "Defensive sector with single-digit growth -- conservative FCF DCF."
    return "fcf", "Profitable and cash-generative -- FCF-based DCF."


# ======================================================================
# value
# ======================================================================

def discount_rate(fin, cls):
    if OPTIONS.get("fixed_discount"):
        r = FIXED_DISCOUNT[cls]
        return r, "fixed %.0f%% for %s (long-horizon rate, not volatility-based)" % (
            100 * r, CLASSES[cls]["label"].lower())
    lo, hi = CLASSES[cls]["discount"]
    beta = fin["beta"]
    if beta is None:
        return (lo + hi) / 2, "band midpoint (no beta)"
    capm = RISK_FREE + beta * EQUITY_PREMIUM
    r = clamp(capm, lo, hi)
    how = "CAPM %.1f%% (rf %.2f%% + beta %.2f x %.1f%%)" % (
        100 * capm, 100 * RISK_FREE, beta, 100 * EQUITY_PREMIUM)
    if r != capm:
        how += ", clamped to the %s band %.0f-%.0f%%" % (
            CLASSES[cls]["label"].lower(), 100 * lo, 100 * hi)
    return r, how


def band_key(fin, cls):
    if cls == "venture":
        return "venture"
    if cls == "utility":
        return "utility"
    if cls == "mature":
        return "mature"
    g = fin["rev_growth"]
    if cls in ("fcf", "financial"):
        return "growth" if (g is not None and g >= 0.10) else "mature"
    if cls == "turnaround":
        return "mature"
    return "growth"


def scenario_growth(fin, cls):
    """
    One number places the company inside every band: t = where trailing growth
    sits inside the BASE band (0..1). All three scenarios use the same t, so
    bull >= base >= bear always, and a company growing at the top of its base
    band gets the top of every band.
    """
    bk = band_key(fin, cls)
    bands = BANDS[bk]
    g = fin["rev_growth"]

    blo, bhi = bands["base"]
    t = 0.5 if g is None else clamp((g - blo) / (bhi - blo), 0.0, 1.0)
    out = {}
    for s in ("bull", "base", "bear"):
        lo, hi = bands[s]
        out[s] = lo + t * (hi - lo)
    return out, bk, t


def start_margins(fin, cls):
    """
    Starting cash margin for each scenario and the year-5 margin it ramps to.

    FCF classes (model 3.0), 3 fiscal years of history for normalisation, all on
    OWNER earnings (OCF - maintenance capex, maintenance ~ D&A, never more than
    actual capex) -- growth capex is no longer hidden in a margin, it is charged
    explicitly in dcf() through sales-to-capital:
        bear = the WORSE of current and average owner margin, minus stock comp
        base = their midpoint, each minus its stock comp (the 1.2 lesson)
        bull = the BETTER of the two (bull assumes dilution is in the count)
    Stock comp is added BACK in operating cash flow, so it is charged here (the
    1.1 EVR lesson).

    Loss-making classes keep their reported-cash-margin ramp to TARGET_MARGIN.
    A negative margin is NEVER compounded -- it ramps toward a floor.
    """
    rev = fin["revenue"] or 0
    if cls == "financial":
        m = safe_div(fin["net_income"], rev) or 0.0
        return {s: (m, m) for s in ("bull", "base", "bear")}
    if cls in TARGET_MARGIN:
        m0 = fin["fcf_margin"]
        if m0 is None:
            m0 = -0.5
        m0 = max(m0, -3.0)     # a venture name can burn ~2x revenue; cap the ramp's start
        return {s: (m0, TARGET_MARGIN[cls][s]) for s in ("bull", "base", "bear")}
    rep_now = fin["fcf_margin"] if fin["fcf_margin"] is not None else fin["owner_margin"]
    own_now = fin["owner_margin"] if fin["owner_margin"] is not None else rep_now
    if fin.get("cash_proxy"):
        rep_now = own_now = safe_div((fin["operating_income"] or 0) * (1 - fin.get("tax_rate", TAX_DEFAULT)), rev) or 0.0
    hist = fin.get("margin_hist") or []
    own_avg = (sum(h["own"] for h in hist) / len(hist)) if len(hist) >= 2 else own_now
    sbc_now = fin.get("sbc_pct") or 0.0
    hs = [h["sbc"] for h in hist if h.get("sbc") is not None]
    sbc_avg = (sum(hs) / len(hs)) if len(hs) >= 2 else sbc_now
    a, b = own_now - sbc_now, own_avg - sbc_avg
    bear, base, bull = min(a, b), 0.5 * (a + b), max(own_now, own_avg)
    fin["_margin_rule"] = "midpoint"
    cyc = fin.get("_cycle") or {}
    if OPTIONS.get("cycle_aware") and cyc.get("cyclical"):
        # Mid-cycle margins: base = the 10-yr MEDIAN (not mean -- one-off
        # disasters are not the cycle: a post-bankruptcy year, a freak storm, a
        # break-up year, each ~-10%, dragged the mean to a $0
        # base value), bear = a bad year (25th percentile) or today if worse,
        # bull = the better of today and the through-cycle owner margin.
        # Blended half-and-half with the 3-yr base: a pure 10-yr median read
        # a structural margin step-up as a peak and halved it.
        base = 0.5 * (cyc["median_adj"] + base)
        bear = min(a, cyc["p25_adj"])
        bull = max(own_now, cyc["mean_own"])
        fin["_margin_rule"] = "cycle"
    elif OPTIONS.get("trend_margin") and _steady_improver(fin, cyc, a):
        # Three straight years of rising SBC-adjusted margins AND rising revenue,
        # not cyclical, not a lender: the base case uses TODAY's margin rather
        # than a midpoint anchored to a weaker past (e.g. 3% -> 6% -> 10%).
        base = min(a, bull)
        fin["_margin_rule"] = "trend"
    out = {}
    for s, m in (("bear", bear), ("base", base), ("bull", bull)):
        out[s] = (m, max(0.0, 0.5 * max(own_now, own_avg))) if m <= 0 else (m, m)
    # A negative margin ramps UP to a floor -- and that floor must never carry a
    # worse case past a better one. In a dry run a bear case starting below zero
    # ramped past the flat base margin, and bear came out worth more than base.
    for lo_s, hi_s in (("base", "bull"), ("bear", "base")):
        lo, hi = out[lo_s], out[hi_s]
        out[lo_s] = (min(lo[0], hi[0]), min(lo[1], hi[1]))
    return out


_LENDER = re.compile(r"\b(lend(s|ing|er|ers)?|loans?|buy now,? pay later|credit cards?|financial technology|fintech)\b", re.I)


def is_lender(fin):
    """Financial sector, or a lender filed elsewhere (AFRM is 'Technology' in /info;
    its short_description says 'financial technology ... buy now, pay later')."""
    return "financ" in (fin.get("sector") or "").lower() or bool(_LENDER.search(fin.get("description") or ""))


def _steady_improver(fin, cyc, a_now):
    if is_lender(fin):
        return False                     # lenders: AFRM's "margin" is its loan book
    if cyc.get("cyclical"):
        return False
    L = list(reversed(fin.get("margin_hist_long") or []))[-3:]
    if len(L) < 3:
        return False
    adj = [h["adj"] for h in L]
    revs = [h["rev"] for h in L]
    return (all(adj[i] < adj[i + 1] for i in range(2)) and all(revs[i] < revs[i + 1] for i in range(2))
            and a_now > 0 and a_now >= adj[-2])


def fade_path(g, tg, years=YEARS, fade=FADE_YEARS, cap=FADE_START_CAP):
    """Growth for years 1..years+fade: g flat, then a straight line from
    min(g, cap) down to tg. A shrinking company fades UP toward tg the same way."""
    path = [g] * years
    g0 = min(g, cap) if cap is not None else g
    for k in range(1, fade + 1):
        path.append(g0 + (tg - g0) * k / (fade + 1))
    return path


def dcf(revenue, growth, m0, m5, r, tg, years=YEARS, fade=FADE_YEARS, s2c=None, roe=None,
        fade_cap=FADE_START_CAP, charge_from=1):
    """
    The single engine every class uses (model 3.0: 10 years + funded growth).

        g_t       = growth for years 1..5, then a linear fade to tg over years 6..10
        revenue_t = revenue_{t-1} (1 + g_t)
        margin_t  = m0 + (m5 - m0) t / 5 for t <= 5, then held at m5
        earn_t    = revenue_t x margin_t
        reinvest  = (revenue_{t+1} - revenue_t) / s2c         (sales-to-capital), or
                  = earn_t x g_{t+1} / roe                     (financials)
                    charged only while earn_t > 0, and with s2c floored at r / margin
                    (roe floored at r), so growth can be worth zero but never
                    NEGATIVE -- which keeps bull >= base >= bear.
        CF_t      = earn_t - reinvest_t
        TV        = earn_N (1+tg) - reinvest_N+1, / (r - tg)   (0 if earn_N <= 0)

    Reinvestment this year funds NEXT year's growth. s2c=roe=None means no
    reinvestment charge. charge_from=6 is used for loss-making classes: in
    years 1-5 their REPORTED cash margin already includes capex, but from year 6
    growth is funded like anyone else's. Without that, the most
    capital-hungry growth name on the board scored 8.0 STRONG BUY in testing on
    free years-6-10 growth.
    """
    path = fade_path(growth, tg, years, fade, fade_cap)
    n = len(path)
    rows = []
    pv_sum = 0.0
    rev = revenue

    def charge(earn, rev_t, g_next, m):
        if earn <= 0:
            return 0.0
        if roe is not None:
            return earn * g_next / max(roe, r)
        if s2c is not None:
            eff = max(s2c, r / m) if m > 0 else s2c
            return rev_t * g_next / eff
        return 0.0

    for t in range(1, n + 1):
        g = path[t - 1]
        g_next = path[t] if t < n else tg
        rev = rev * (1 + g)
        m = m0 + (m5 - m0) * min(t, years) / years
        earn = rev * m
        reinvest = charge(earn, rev, g_next, m) if t >= charge_from else 0.0
        cf = earn - reinvest
        pv = cf / (1 + r) ** t
        pv_sum += pv
        rows.append({"year": t, "growth": g, "revenue": rev, "margin": m, "earn": earn,
                     "reinvest": reinvest, "cf": cf, "pv": pv})
    last = rows[-1]
    if last["earn"] > 0:
        e11 = last["earn"] * (1 + tg)
        tv = (e11 - charge(e11, last["revenue"] * (1 + tg), tg, last["margin"])) / (r - tg)
    else:
        tv = 0.0
    pv_tv = tv / (1 + r) ** n
    return {"rows": rows, "pv_cf": pv_sum, "tv": tv, "pv_tv": pv_tv, "ev": pv_sum + pv_tv,
            "cum_cf": sum(x["cf"] for x in rows),
            "min_cum": min(sum(x["cf"] for x in rows[:i + 1]) for i in range(len(rows)))}


def value(fin, cls, overrides=None):
    ov = overrides or {}
    r, r_how = discount_rate(fin, cls)
    if ov.get("discount") is not None:
        r = float(ov["discount"])
        r_how = "OVERRIDE %.1f%% (model would use %s)" % (100 * r, r_how)
    # Asset managers / banks are excluded: fund consolidation swings their cash
    # flow (swinging from deeply negative to strongly positive) without an economic cycle behind it.
    _fin_sector = "financ" in (fin.get("sector") or "").lower()
    fin["_cycle"] = (cycle_profile(fin) if cls in CYCLE_CLASSES and not _fin_sector
                     else {"known": False, "cyclical": False})
    gfin = fin
    if ov.get("growth") is not None:
        gfin = dict(fin, rev_growth=float(ov["growth"]))
    elif OPTIONS.get("cycle_aware") and fin["_cycle"].get("cyclical") and fin["_cycle"].get("rev_cagr") is not None:
        # A cyclical's trailing growth at a peak (e.g. a memory maker) would pick the growth BAND
        # and its position for five years. Use the through-cycle revenue CAGR
        # for both -- a single acquisition year otherwise put it in "growth".
        gfin = dict(fin, rev_growth=fin["_cycle"]["rev_cagr"])
    growth, bk, t = scenario_growth(gfin, cls)
    margins = start_margins(fin, cls)
    kind, eff, eff_how = capital_efficiency(fin, cls)
    funded = True
    kw = {"roe": eff} if kind == "roe" else {"s2c": eff}
    if cls in TARGET_MARGIN:          # years 1-5: capex already in the reported cash margin
        kw["charge_from"] = YEARS + 1
    rev = fin["revenue"] or 0.0
    net_cash = 0.0 if cls == "financial" else (fin["net_cash"] or 0.0)
    scen = {}
    for s in ("bull", "base", "bear"):
        m0, m5 = margins[s]
        tg = TERMINAL[s]
        model = dcf(rev, growth[s], m0, m5, r, tg, **kw)
        equity = model["ev"] + net_cash
        per_share = equity / fin["shares"] if fin["shares"] else None
        floored = per_share is not None and per_share < 0
        if floored:
            per_share = 0.0
        scen[s] = dict(model, growth=growth[s], m0=m0, m5=m5, terminal=tg,
                       equity=equity, per_share=per_share, floored=floored,
                       upside=(per_share / fin["price"] - 1) if per_share is not None else None,
                       tv_share=safe_div(model["pv_tv"], model["ev"]) if model["ev"] > 0 else None)
    return {"discount": r, "discount_how": r_how, "band": bk, "band_pos": t,
            "net_cash_used": net_cash, "scenarios": scen, "years": YEARS + FADE_YEARS,
            "capital": {"kind": kind, "value": eff, "how": eff_how, "charged": funded,
                        "from_year": kw.get("charge_from", 1)},
            "cycle": {k: v for k, v in fin["_cycle"].items() if not k.endswith("_series")},
            "options": dict(OPTIONS),
            # what the base case was built from -- expected_returns() re-runs it
            "engine": {"revenue": rev, "growth": growth["base"], "m0": margins["base"][0],
                       "m5": margins["base"][1], "r": r, "tg": TERMINAL["base"], "kw": kw,
                       "net_cash": net_cash, "shares": fin["shares"]}}


def _solve(f, lo, hi, target, iters=80):
    """Bisection for f(x) = target on [lo, hi], f monotonic. None if not bracketed."""
    flo, fhi = f(lo) - target, f(hi) - target
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if (flo > 0) == (fhi > 0):
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        fm = f(mid) - target
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def expected_returns(fin, cls, val):
    """
    Model 3.2 -- what a 5-10 year holder earns, from the BASE case.

      irr_N    : buy at today's price, collect the base case's cash flow per
                 share for N years, and sell at the base-case intrinsic value in
                 year N. The annual return that makes that work. When price ==
                 value this is exactly the discount rate (net cash is carried
                 forward at r so that identity holds). N = 5 and 10.
      implied_return : the return you earn at today's price if the price NEVER
                 converges -- the discount rate at which the DCF equals the price.
      implied_growth : the growth rate for years 1-5 (same margins, fade,
                 funding) that the price already assumes -- a reverse DCF.
    Negative or zero base values have no meaningful IRR; those return None
    with a reason.
    """
    e = val.get("engine") or {}
    b = val["scenarios"]["base"]
    price, sh = fin["price"], e.get("shares")
    out = {"irr": {}, "implied_return": None, "implied_growth": None, "notes": []}
    if not sh or not price or b["per_share"] is None or b["floored"] or b["per_share"] <= 0:
        out["notes"].append("No positive base value -- expected return not meaningful.")
        return out
    r, tg, nc = e["r"], e["tg"], e["net_cash"]
    rows = b["rows"]
    n = len(rows)
    for N in (5, 10):
        # Net debt is carried FLAT and its interest (at r) is paid out of each
        # year's cash flow; net cash earns r the same way. The first version let
        # debt compound at r while the holder took the unlevered cash flow --
        # a heavily levered name (net debt ~8x EBITDA) then had a NEGATIVE year-10 equity value and
        # no IRR ("n/a"). Both versions satisfy price == value => IRR == r.
        rest = sum(x["cf"] / (1 + r) ** (x["year"] - N) for x in rows[N:])
        vN = (rest + b["tv"] / (1 + r) ** (n - N) + nc) / sh
        flows = [(x["cf"] + r * nc) / sh for x in rows[:N]]
        f = lambda x, flows=flows, vN=vN, N=N: (sum(c / (1 + x) ** (t + 1) for t, c in enumerate(flows))
                                                  + vN / (1 + x) ** N)
        out["irr"][N] = _solve(f, -0.95, 3.0, price)
    kw = dict(e["kw"])

    def ev_at(rate):
        m = dcf(e["revenue"], e["growth"], e["m0"], e["m5"], rate, tg, **kw)
        return (m["ev"] + nc) / sh
    out["implied_return"] = _solve(ev_at, tg + 0.002, 1.0, price)

    def ev_g(g):
        m = dcf(e["revenue"], g, e["m0"], e["m5"], r, tg, **kw)
        return (m["ev"] + nc) / sh
    g = _solve(ev_g, -0.30, 1.50, price)
    out["implied_growth"] = g
    if g is None:
        out["notes"].append("No growth rate between -30%% and +150%%/yr makes the base case reach %s."
                            % px(price) if ev_g(1.5) < price else
                            "Even -30%%/yr growth is worth more than %s." % px(price))
    return out


# ======================================================================
# quality + management (model 3.2)
# ======================================================================
#
# Buffett asks two questions: is this a wonderful business, and is the price
# below what it is worth? The DCF answers the second. This answers the first
# from the FOOTPRINTS management leaves in the numbers -- nobody can measure
# integrity or a moat from a payload, and the report says so.
#
# Eight components, each 0..1, weighted to exactly 100. A component with no
# data is UNMEASURED: it drops out and the rest are re-scaled, and the report
# shows how many of the 100 points were measured. Missing data never scores
# as poor quality.

QUALITY_WEIGHTS = {
    "returns": 25,       # return on invested capital (ROE for lenders)
    "consistency": 10,   # share of years with positive free cash flow
    "shares": 15,        # share count trend: buybacks vs dilution
    "acquisitions": 10,  # goodwill piled up relative to revenue
    "leverage": 10,      # net debt / EBITDA
    "earnings": 10,      # operating cash flow / net income
    "insiders": 10,      # open-market buying, ownership, discretionary selling
    "predictability": 10,  # earnings beats vs misses, last 12 quarters
}
QUALITY_LABELS = {
    "returns": "Return on capital", "consistency": "Cash-flow consistency",
    "shares": "Share count (buybacks vs dilution)", "acquisitions": "Acquisition discipline",
    "leverage": "Debt discipline", "earnings": "Earnings quality (cash vs reported)",
    "insiders": "Insider behaviour", "predictability": "Earnings predictability",
}
QUALITY_MIN_MEASURED = 50     # below this many of 100 points measured -> quality "unknown", no gate
QUALITY_GATES = ((4.0, 5.5, "HOLD"), (6.0, 7.5, "BUY"))   # quality below q -> score capped at cap
MARGIN_OF_SAFETY = 0.25       # BUY or better needs price <= 75% of base value


def _lin(x, lo, hi):
    """0 at lo, 1 at hi (either direction), clamped."""
    if x is None:
        return None
    if hi == lo:
        return 1.0 if x >= hi else 0.0
    return clamp((x - lo) / (hi - lo), 0.0, 1.0)


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def share_trend(annual):
    """
    Share-count CAGR over up to 5 years from the balance sheet. UW's counts are
    split-adjusted (forward splits do not show), but pre-IPO rows are not: a
    pre-IPO year can show a quarter of the count on either side of it, or the
    count can double at a direct listing. The series is cut at the first year-on-year jump outside 0.6-1.6x.
    """
    ser = []
    for y in annual:
        s = y.get("shares")
        if not s or s <= 0:
            continue
        if ser and not (0.6 < ser[-1][1] / s < 1.6):
            break
        ser.append((y["fy"], s))
        if len(ser) == 6:
            break
    if len(ser) < 3:
        return None, None
    yrs = len(ser) - 1
    return (ser[0][1] / ser[-1][1]) ** (1.0 / yrs) - 1, yrs


def insider_summary(rows, price, mcap, shares, today=None):
    """
    /api/insider/transactions rows (P and S, non-derivative) -> summary.
    For some large caps the same grouped filing comes back TWICE, once with
    reporter_cik null -- de-dupe on the sorted `ids`. Nearly every sale by a
    large-cap executive is a 10b5-1 plan (scheduled, not a view on the price),
    so only NON-plan sales count against management. Fill prices are
    cross-checked against the quote (a 110x-wrong `price` was seen live).
    """
    if rows is None:
        return None
    seen, buys, sells, own = set(), [], [], {}
    today = today or datetime.date.today().isoformat()
    for r in rows:
        key = tuple(sorted(r.get("ids") or [])) or (r.get("id"),)
        if key in seen:
            continue
        seen.add(key)
        code = (r.get("transaction_code") or "").upper()
        if (r.get("security_ad_code") or "") not in ("NA", "ND", ""):
            continue
        amt = abs(num(r.get("amount")) or 0)
        fill = num(r.get("price"))
        q = num(r.get("stock_price")) or price
        if not fill or (q and not (0.2 < fill / q < 5)):
            fill = q
        usd = amt * (fill or 0)
        d = r.get("transaction_date") or ""
        who = r.get("reporter_cik") or r.get("owner_name")
        if code == "P" and not r.get("is_10b5_1"):
            buys.append({"date": d, "usd": usd, "who": who, "name": r.get("owner_name"),
                         "title": r.get("officer_title") or ("Director" if r.get("is_director") else "")})
        elif code == "S" and not r.get("is_10b5_1") and _days(today, d) is not None and _days(today, d) <= 365:
            sells.append({"date": d, "usd": usd, "who": who})
        after = num(r.get("shares_owned_after"))
        if who and after is not None and after >= 0 and not r.get("is_ten_percent_owner"):
            if who not in own or d > own[who][0]:
                own[who] = (d, after)
    held = sum(v[1] for v in own.values())
    return {
        "rows": len(seen), "buy_usd": sum(b["usd"] for b in buys),
        "buyers": len({b["who"] for b in buys}), "buys": sorted(buys, key=lambda b: -b["usd"])[:3],
        "sell_usd_12m": sum(s["usd"] for s in sells),
        "owned_pct": (held / shares) if shares else None, "holders": len(own),
        "mcap": mcap,
    }


def earnings_summary(rows, today=None):
    """/api/stock/{t}/earnings -> beat/miss record over the last 12 REPORTED
    quarters. The newest row is usually the NEXT report (reported_eps null)."""
    if rows is None:
        return None
    q = [r for r in rows if (r.get("report_type") or "quarterly") == "quarterly"
         and num(r.get("reported_eps")) is not None and num(r.get("estimated_eps")) is not None]
    q.sort(key=lambda r: r.get("fiscal_date_ending") or "", reverse=True)
    q = q[:12]
    if len(q) < 4:
        return {"quarters": len(q)}
    sp = []
    for r in q:
        p = num(r.get("surprise_percentage"))
        if p is None:
            est, rep = num(r.get("estimated_eps")), num(r.get("reported_eps"))
            p = 100 * (rep - est) / abs(est) if est else None
        sp.append(p)
    beats = sum(1 for p in sp if p is not None and p > 0)
    big = sum(1 for p in sp if p is not None and p < -10)
    return {"quarters": len(q), "beats": beats, "big_misses": big,
            "beat_rate": beats / len(q)}


def quality(fin, cls, insiders=None, earnings=None):
    """
    -> {score 0..10 or None, measured (points of 100), components[], strengths[],
        weaknesses[]}. See QUALITY_WEIGHTS. Every mapping is linear and stated.
    """
    A = fin.get("annual") or []
    lender = cls == "financial" or is_lender(fin)
    tax = fin.get("tax_rate") or TAX_DEFAULT
    comp = {}

    def put(key, val, text, raw=None):
        comp[key] = {"key": key, "label": QUALITY_LABELS[key], "weight": QUALITY_WEIGHTS[key],
                     "value": None if val is None else round(val, 3), "text": text, "raw": raw}

    # 1. returns on capital ------------------------------------------------
    if lender:
        roe = [y["ni"] / y["equity"] for y in A[:5] if y.get("ni") is not None and (y.get("equity") or 0) > 0]
        med = _median(roe)
        put("returns", _lin(med, 0.05, 0.18),
            "Median ROE %s over %d yrs (5%% scores 0, 18%%+ full marks)" % (pct(med), len(roe))
            if med is not None else "ROE unmeasured", med)
    else:
        ro = []
        for y in A[:5]:
            if not y.get("has_bs"):
                continue
            # floor invested capital at 10% of revenue: cash-rich companies
            # otherwise divide by ~0 (ROIC of -1000%+ or +300%+ in the dry run)
            ic = max((y.get("equity") or 0) + (y.get("debt") or 0) - (y.get("liquid") or 0), 0.1 * y["rev"])
            parts = []
            if y.get("op") is not None:
                parts.append(clamp(y["op"] * (1 - tax) / ic, -0.5, 0.6))
            if y.get("ocf") is not None:
                mnt = min(y.get("capex") or 0, y["da"] if y.get("da") is not None else (y.get("capex") or 0))
                parts.append(clamp((y["ocf"] - mnt - (y.get("sbc") or 0)) / ic, -0.5, 0.6))
            if parts:
                ro.append(sum(parts) / len(parts))
        if ro:
            med = _median(ro)
            blend = 0.5 * (ro[0] + med)      # latest year counts half: improvers are not anchored
            put("returns", _lin(blend, 0.0, 0.25),
                "Return on invested capital %s (latest %s, %d-yr median %s; 0%% scores 0, 25%%+ full)"
                % (pct(blend), pct(ro[0]), len(ro), pct(med)), blend)
        else:
            put("returns", None, "Return on capital unmeasured (no balance sheet history)")

    # 2. consistency --------------------------------------------------------
    cf = [y for y in A if y.get("has_cf") and y.get("ocf") is not None]
    if lender:
        put("consistency", None, "Not meaningful for a lender (operating cash flow is loan-book noise)")
    elif len(cf) >= 3:
        pos = sum(1 for y in cf if y["ocf"] - (y.get("capex") or 0) > 0)
        put("consistency", _lin(pos / len(cf), 0.5, 1.0),
            "Positive free cash flow in %d of %d years" % (pos, len(cf)), pos / len(cf))
    else:
        put("consistency", None, "Fewer than 3 years of cash flows")

    # 3. share count --------------------------------------------------------
    cg, yrs = share_trend(A)
    if cg is None:
        put("shares", None, "Share-count history too short")
    else:
        # -2%/yr (steady buybacks) scores 1, flat 0.7, +2%/yr 0.35, +5%/yr 0
        v = clamp(0.7 - 0.15 * (100 * cg) if cg >= 0 else 0.7 + 0.15 * (100 * -cg), 0.0, 1.0)
        put("shares", v, "Shares %s %.1f%%/yr over %d yrs (%s)" % (
            "shrinking" if cg < -0.0025 else "growing" if cg > 0.0025 else "flat", abs(100 * cg), yrs,
            "buybacks" if cg < -0.0025 else "dilution" if cg > 0.0025 else "neutral"), cg)

    # 4. acquisitions -------------------------------------------------------
    gw = [y for y in A if y.get("has_bs") and y.get("goodwill") is not None]
    if len(gw) >= 3 and A[0].get("rev"):
        j = min(5, len(gw) - 1)
        added = (gw[0]["goodwill"] - gw[j]["goodwill"]) / A[0]["rev"]
        drops = sum(1 for i in range(j) if gw[i + 1]["goodwill"] > 0
                    and gw[i]["goodwill"] < 0.85 * gw[i + 1]["goodwill"])
        v = _lin(added, 1.0, 0.1)
        if v is not None and drops:
            v = max(0.0, v - 0.25 * drops)
        put("acquisitions", v, "Goodwill added over %d yrs = %s of revenue%s" % (
            j, pct(added), ("; %d write-down year(s)" % drops) if drops else ""), added)
    else:
        # absent FIELD is unmeasured; a present-but-null goodwill line is zero
        put("acquisitions", None, "Goodwill history unavailable")

    # 5. leverage -----------------------------------------------------------
    y0 = A[0] if A else None
    if lender:
        put("leverage", None, "Not meaningful for a lender (debt is its raw material)")
    elif y0 and y0.get("has_bs"):
        nd = (y0.get("debt") or 0) - (y0.get("liquid") or 0)
        ebitda = (y0.get("op") or 0) + (y0.get("da") or 0)
        if nd <= 0:
            put("leverage", 1.0 if ebitda > 0 else 0.6,
                "Net cash %s%s" % (money(-nd), "" if ebitda > 0 else " (but EBITDA negative)"), nd)
        elif ebitda <= 0:
            put("leverage", 0.0, "Net debt %s with negative EBITDA" % money(nd), nd)
        else:
            x = nd / ebitda
            put("leverage", _lin(x, 4.0, 1.0), "Net debt %.1fx EBITDA (1x scores full, 4x+ zero)" % x, x)
    else:
        put("leverage", None, "No balance sheet")

    # 6. earnings quality ---------------------------------------------------
    last3 = A[:3]
    ni = sum(y.get("ni") or 0 for y in last3)
    oc = sum(y.get("ocf") or 0 for y in last3 if y.get("ocf") is not None)
    if lender:
        put("earnings", None, "Not meaningful for a lender")
    elif ni > 0 and len(last3) >= 2 and all(y.get("ocf") is not None for y in last3):
        x = oc / ni
        put("earnings", _lin(x, 0.5, 1.0), "Operating cash flow %.2fx net income over %d yrs "
            "(below 0.5x scores 0)" % (x, len(last3)), x)
    else:
        put("earnings", None, "Not meaningful -- %s" % ("net losses" if ni <= 0 else "cash flows missing"))

    # 7. insiders -----------------------------------------------------------
    ins = insiders
    if not ins:
        put("insiders", None, "Insider filings not fetched")
    else:
        mc = fin.get("marketcap") or ins.get("mcap") or 0
        buy = 1.0 if (ins["buyers"] >= 2 or (mc and ins["buy_usd"] >= 1e-4 * mc)) else (0.6 if ins["buy_usd"] > 0 else 0.0)
        own = _lin(ins.get("owned_pct"), 0.0, 0.05) or 0.0
        sell = _lin(ins["sell_usd_12m"] / mc if mc else 0.0, 0.0, 0.005) or 0.0
        # no activity at all is NEUTRAL (0.45) -- most large caps have no open-market
        # buying; buying lifts it, skin in the game lifts it, non-plan selling cuts it
        v = clamp(0.45 + 0.35 * buy + 0.2 * own - 0.6 * sell, 0.0, 1.0)
        bits = []
        if not ins["rows"]:
            bits.append("no open-market insider trades filed in 2 yrs (neutral)")
        else:
            bits.append("%d open-market buyer(s), %s in 2 yrs" % (ins["buyers"], money(ins["buy_usd"]))
                        if ins["buyers"] else "no open-market buying in 2 yrs")
        if ins.get("owned_pct"):
            bits.append("filers own at least %s of shares" % pct(ins["owned_pct"]))
        if ins["sell_usd_12m"]:
            bits.append("%s non-plan selling in 12 mo" % money(ins["sell_usd_12m"]))
        put("insiders", v, "; ".join(bits), {"buy": buy, "own": own, "sell": sell})

    # 8. predictability -----------------------------------------------------
    er = earnings
    if not er or er.get("quarters", 0) < 4:
        put("predictability", None, "Earnings history not fetched" if not er else "Fewer than 4 reported quarters")
    else:
        v = clamp(_lin(er["beat_rate"], 0.5, 0.9) - 0.15 * er["big_misses"], 0.0, 1.0)
        put("predictability", v, "Beat estimates in %d of %d quarters; %d miss(es) worse than 10%%"
            % (er["beats"], er["quarters"], er["big_misses"]), er["beat_rate"])

    measured = sum(c["weight"] for c in comp.values() if c["value"] is not None)
    pts = sum(c["weight"] * c["value"] for c in comp.values() if c["value"] is not None)
    sc = round(10.0 * pts / measured, 1) if measured >= QUALITY_MIN_MEASURED else None
    ranked = sorted([c for c in comp.values() if c["value"] is not None],
                    key=lambda c: c["weight"] * c["value"] - 0.5 * c["weight"])
    weak = [c for c in ranked if c["value"] < 0.4][:3]
    strong = [c for c in reversed(ranked) if c["value"] >= 0.7][:3]
    return {"score": sc, "measured": measured, "points": round(pts, 1),
            "components": [comp[k] for k in QUALITY_WEIGHTS],
            "weaknesses": [c["label"] + ": " + c["text"] for c in weak],
            "strengths": [c["label"] + ": " + c["text"] for c in strong],
            "tier": (None if sc is None else "high" if sc >= 6 else "average" if sc >= 4 else "low")}


# ======================================================================
# score
# ======================================================================

def red_flags(fin, cls, val):
    """
    -> list of {key, text, points, bad}. points > 0 only for real red flags.
    Every flag the brief lists is checked; a check that could not be measured
    says so rather than passing silently.
    """
    out = []

    def add(key, bad, text, points=0.0):
        out.append({"key": key, "bad": bad, "text": text, "points": points if bad else 0.0})

    fcf, liquid = fin["fcf"], fin["liquid"]
    if cls == "financial":
        add("fcf", False, "Bank-style cash flows: FCF and runway not meaningful")
    elif fcf is not None and fcf < 0:
        burn = -fcf
        if liquid is None:
            add("runway", True, "Burning %s/yr and cash is UNMEASURED" % money(burn), 1.0)
        else:
            runway = liquid / burn if burn else None
            if runway < 1:
                add("runway", True, "Cash runway %.1f yrs at %s/yr burn" % (runway, money(burn)), 1.5)
            elif runway < 2:
                add("runway", True, "Cash runway %.1f yrs at %s/yr burn" % (runway, money(burn)), 1.0)
            else:
                add("runway", False, "Runway %.1f yrs covers the burn" % runway)
            base = val["scenarios"]["base"]
            if base["min_cum"] < 0 and -base["min_cum"] > liquid:
                add("funding", True, "Base case needs %s more than cash on hand -- dilution likely"
                    % money(-base["min_cum"] - liquid), 0.5)
    elif fcf is not None:
        add("fcf", False, "Positive free cash flow %s" % money(fcf))
    else:
        add("fcf", False, "Free cash flow UNMEASURED (no cash flow statement)")

    if cls != "financial" and fin["debt"] is not None and fin["ebitda"]:
        nd = fin["debt"] - (fin["liquid"] or 0)
        lev = nd / fin["ebitda"] if fin["ebitda"] > 0 else None
        if lev is None and nd > 0:
            add("leverage", True, "Net debt %s with negative EBITDA" % money(nd), 0.5)
        elif lev is not None and lev > 4:
            add("leverage", True, "Net debt %.1fx EBITDA" % lev, 0.5)
        elif lev is not None and lev > 0:
            add("leverage", False, "Net debt %.1fx EBITDA" % lev)
        else:
            add("leverage", False, "Net cash balance sheet")
    if (cls != "financial" and fin["equity"] is not None and fin["debt"] is not None
            and fin["equity"] > 0 and fin["debt"] / fin["equity"] > 3):
        add("debt_equity", True, "Debt %.1fx book equity" % (fin["debt"] / fin["equity"]), 0.25)

    om, pom = fin["op_margin"], fin["prev_op_margin"]
    if om is not None and pom is not None:
        if om < pom - 0.05:
            add("margins", True, "Operating margin fell %.0f pts to %s" % (100 * (pom - om), pct(om)), 0.5)
        else:
            add("margins", False, "Operating margin %s (was %s)" % (pct(om), pct(pom)))

    g = fin["rev_growth"]
    if g is not None:
        if g < 0:
            add("growth", True, "Revenue shrinking %s YoY" % pct(g), 0.5)
        else:
            add("growth", False, "Revenue growing %s YoY" % pct(g))
    else:
        add("growth", False, "Revenue growth unmeasured (no prior period)")

    if fin["sbc_pct"] is not None and fin["sbc_pct"] > 0.15:
        add("sbc", True, "Stock comp %s of revenue" % pct(fin["sbc_pct"]), 0.5)
    if fin["dilution"] is not None and fin["dilution"] > 0.05:
        add("dilution", True, "Share count up %s YoY" % pct(fin["dilution"]), 0.5)

    base = val["scenarios"]["base"]
    if base.get("tv_share") is not None and base["tv_share"] > 0.85:
        add("terminal", True, "%s of base value is terminal -- multiple-expansion dependent"
            % pct(base["tv_share"]), 0.25)
    if cls in ("venture", "growth_burn", "turnaround"):
        add("execution", True, "Value depends on a margin ramp that has not happened yet", 0.25)
    cyc = fin.get("_cycle") or {}
    if cyc.get("cyclical") and cls in CYCLE_CLASSES:
        a_now = (fin["owner_margin"] or 0) - (fin.get("sbc_pct") or 0)
        if a_now > cyc["median_adj"] + cyc["sd_adj"]:
            # Costs points only when the cycle rule is ON; shipped 3.1 shows it as a warning.
            add("cycle_peak", True, "Cyclical: cash margin %s vs %d-yr median %s -- possible cycle peak%s"
                % (pct(a_now), cyc["years"], pct(cyc["median_adj"]),
                   "" if OPTIONS.get("cycle_aware") else " (warning only, not scored)"),
                0.5 if OPTIONS.get("cycle_aware") else 0.0)
        else:
            add("cycle_peak", False, "Cyclical: margin %s near its %d-yr median %s"
                % (pct(a_now), cyc["years"], pct(cyc["median_adj"])))
    # Customer concentration and competitive threat are not in the payload.
    # They are listed on the report as UNMEASURED so nobody reads silence as a pass.
    return out


def half_up(x):
    """Python's round() is banker's rounding: round(6.5) = 6 but round(7.5) = 8,
    so a stock at 6.5 read HOLD while a 7.5 read REDUCE. Always round .5 up."""
    return int(math.floor(x + 0.5))


def badge_for(score):
    """Whole-number badge. .5 rounds DOWN on the 2.0 scale -- toward caution,
    the mirror of 1.x's half-up (Python's round() is banker's rounding)."""
    return int(math.ceil(round(score, 1) - 0.5))


def verdict_for(score):
    s = round(score, 1)
    for hi, label, colour in VERDICTS:
        if s <= hi:
            return label, colour
    return VERDICTS[-1][1], VERDICTS[-1][2]


def score(fin, cls, val, flags, qual=None):
    """
    0 (avoid) .. 10 (deeply undervalued). Higher is better.

        valuation = 5 + 5 x base_upside       clamped 0..10
                    (+100% upside -> 10, fair -> 5, -100% -> 0)
                    The slope was 10 in the first draft and SATURATED: -50%
                    already hit the end of the scale, so names at -56%, -73%
                    and -100% were indistinguishable. Downside is bounded
                    at -100%, so slope 5 is exactly the one that uses the whole
                    0..5 half of the scale.
        flags     = sum of red-flag points, capped at FLAG_POINTS_CAP, and they
                    only ever SUBTRACT (toward caution)
        structural: no scenario reaches the price -> at most 3;
                    even the bear case clears the price -> at least 6 before flags.
    """
    s = val["scenarios"]
    up = s["base"]["upside"]
    if up is None:
        raise ValueError("No per-share value (share count missing).")
    v = clamp(5 + 5 * up, 0.0, 10.0)
    notes = []
    if s["bull"]["upside"] is not None and s["bull"]["upside"] < 0:
        if v > 3:
            notes.append("even the bull case is below the price -> cap 3")
        v = min(v, 3.0)
    if s["bear"]["upside"] is not None and s["bear"]["upside"] > 0:
        if v < 6:
            notes.append("even the bear case clears the price -> floor 6")
        v = max(v, 6.0)
    pts = min(FLAG_POINTS_CAP, sum(f["points"] for f in flags))
    # Round to the displayed precision FIRST: a stock scored 6.47, printed "6.5", and
    # read one verdict next to a 6.5 that read another. Verdict follows what is shown.
    total = round(clamp(v - pts, 0.0, 10.0), 1)
    before = total
    gates = gate_checks(fin, val, qual, total)
    for g in gates:
        if g["cap"] is not None and total > g["cap"]:
            total = g["cap"]
            g["applied"] = True
    label, colour = verdict_for(total)
    carried = [{"what": "Valuation (base %s)" % signed_pct(up), "points": round(v, 2)}]
    for f in sorted([f for f in flags if f["points"] > 0], key=lambda f: -f["points"]):
        carried.append({"what": f["text"], "points": -f["points"]})
    return {"score": total, "badge": badge_for(total), "verdict": label, "scale": SCALE_VERSION,
            "colour": colour, "valuation_points": round(v, 2), "flag_points": round(pts, 2),
            "flag_points_raw": round(sum(f["points"] for f in flags), 2),
            "structural": notes, "carried_by": carried,
            "before_gates": before, "gates": gates}


def gate_checks(fin, val, qual, total):
    """
    Model 3.2 -- the Buffett gates. Every gate is reported, passed or not, so
    the verdict always comes with its reasons:

      1. Margin of safety: BUY or better needs price <= 75% of the base value
         (25% margin). Otherwise capped at 5.5 (HOLD) -- "cheap, but not cheap
         enough to be wrong".
      2. Quality: below 4/10 caps at 5.5 (HOLD) -- a cheap bad business is a
         value trap; 4-6 caps at 7.5 (BUY) -- STRONG BUY is reserved for a good
         business at a great price. Quality UNKNOWN (<50 of 100 points
         measured) applies no cap and says so: missing data is not poor quality.
    """
    b = val["scenarios"]["base"]
    price = fin["price"]
    out = []
    bv = b.get("per_share")
    mos = (1 - price / bv) if (bv and bv > 0) else None
    need = MARGIN_OF_SAFETY
    ok = mos is not None and mos >= need
    out.append({"key": "margin", "rule": "BUY needs a %d%% margin of safety (price at most %s)"
                % (round(100 * need), px(bv * (1 - need)) if bv else "n/a"),
                "value": ("%s below base value" % pct(mos)) if (mos is not None and mos >= 0)
                else ("%s ABOVE base value" % pct(-mos) if mos is not None else "no base value"),
                "passed": ok, "cap": None if ok else 5.5, "applied": False, "mos": mos})
    q = (qual or {}).get("score")
    if q is None:
        out.append({"key": "quality", "rule": "Quality >= 4 for BUY, >= 6 for STRONG BUY",
                    "value": "unknown (%d of 100 points measured)" % (qual or {}).get("measured", 0)
                    if qual else "not measured", "passed": True, "cap": None, "applied": False})
    else:
        cap = None
        for qmin, c, _lbl in QUALITY_GATES:
            if q < qmin:
                cap = c
                break
        out.append({"key": "quality", "rule": "Quality >= 4 for BUY, >= 6 for STRONG BUY",
                    "value": "%.1f/10 (%s)" % (q, (qual or {}).get("tier")), "passed": cap is None,
                    "cap": cap, "applied": False, "quality": q})
    return out


def why_verdict(fin, val, sc, qual):
    """Plain sentences, in order, that explain the verdict -- the card and the
    report print these word for word. The reasoning has to be clear."""
    b = val["scenarios"]["base"]
    L = []
    L.append("Value: base-case intrinsic value %s vs price %s (%s)." % (
        px(b.get("per_share")), px(fin["price"]), signed_pct(b.get("upside"))))
    if sc["flag_points"]:
        L.append("Red flags take off %.2f points." % sc["flag_points"])
    for g in sc.get("gates") or []:
        if g["key"] == "margin":
            if g.get("applied"):
                L.append("Margin of safety FAILS and caps this at HOLD: the price is %s; %s." % (
                    g["value"], g["rule"]))
            elif g["passed"]:
                L.append("Margin of safety passes: the price is %s (BUY needs 25%%)." % g["value"])
            else:
                L.append("Margin of safety fails (the price is %s), but the score is already HOLD "
                         "or lower, so it changes nothing." % g["value"])
        else:
            if g.get("quality") is None:
                L.append("Quality %s -- no quality cap applied (missing data is not poor quality)."
                         % g["value"])
            elif g.get("applied"):
                L.append("Quality %s caps this at %s: %s." % (
                    g["value"], verdict_for(g["cap"])[0],
                    "a cheap weak business is a value trap" if g["cap"] <= 5.5
                    else "STRONG BUY is kept for high-quality businesses (6+)"))
            elif g["passed"]:
                L.append("Quality passes: %s." % g["value"])
            else:
                L.append("Quality %s would cap this at %s, but the score is already below that."
                         % (g["value"], verdict_for(g["cap"])[0]))
    if qual and qual.get("score") is not None:
        if qual.get("strengths"):
            L.append("Strongest: " + "; ".join(qual["strengths"][:2]) + ".")
        if qual.get("weaknesses"):
            L.append("Weakest: " + "; ".join(qual["weaknesses"][:2]) + ".")
    if sc["before_gates"] != sc["score"]:
        L.append("Score %.1f before the gates, %.1f after." % (sc["before_gates"], sc["score"]))
    return L


def stance_for(badge):
    return ("undervalued" if badge >= 6 else "fairly valued" if badge >= 4 else "overvalued")


def upgrade_result(res):
    """
    Convert a stored 1.x result (0 = buy, 10 = avoid) to the 2.0 scale in place,
    so history never mixes the two. Only the SCORE is mirrored (10 - x); the
    valuation itself is left exactly as it was computed at the time.
    """
    sc = res.get("score") or {}
    if sc.get("scale", 1) >= SCALE_VERSION:
        return res
    old_badge, old_verdict = sc.get("badge"), sc.get("verdict")
    total = round(10.0 - float(sc["score"]), 1)
    label, colour = verdict_for(total)
    sc.update(score=total, badge=badge_for(total), verdict=label, colour=colour, scale=SCALE_VERSION,
              valuation_points=round(10.0 - float(sc.get("valuation_points", 5)), 2))
    for c in sc.get("carried_by") or []:
        if c["what"].startswith("Valuation"):
            c["points"] = round(10.0 - c["points"], 2)
        else:
            c["points"] = -abs(c["points"])
    sc["structural"] = [n.replace("floor 7", "cap 3").replace("cap 4", "floor 6")
                        for n in sc.get("structural") or []]
    res["stance"] = stance_for(sc["badge"])
    th = res.get("thesis", "")
    res["verdict_line"] = "%s \u2014 %s" % (label, th)
    if old_badge is not None and old_verdict:
        s_ = res.get("summary", "")
        s_ = s_.replace("(%d/10 %s)" % (old_badge, old_verdict), "(%d/10 %s)" % (sc["badge"], label))
        for w in ("undervalued", "fairly valued", "overvalued"):
            s_ = s_.replace(" is %s at " % w, " is %s at " % res["stance"], 1)
        res["summary"] = s_
    res["scale_converted_from"] = res.get("model_version")
    return res


# ======================================================================
# multiples
# ======================================================================

def multiples(fin):
    p, mc = fin["price"], fin["marketcap"]
    ev = None
    if mc is not None and fin["debt"] is not None:
        ev = mc + fin["debt"] - (fin["liquid"] or 0)
    ni, rev = fin["net_income"], fin["revenue"]
    ref = SECTOR_REF.get(fin["sector"])
    return {
        "pe": (mc / ni) if (mc and ni and ni > 0) else None,
        "ps": (mc / rev) if (mc and rev and rev > 0) else None,
        "pfcf": (mc / fin["fcf"]) if (mc and fin["fcf"] and fin["fcf"] > 0) else None,
        "fcf_yield": (fin["fcf"] / mc) if (mc and fin["fcf"] is not None) else None,
        "ev": ev,
        "ev_ebitda": (ev / fin["ebitda"]) if (ev and fin["ebitda"] and fin["ebitda"] > 0) else None,
        "pb": (mc / fin["equity"]) if (mc and fin["equity"] and fin["equity"] > 0) else None,
        "sector_ref": {"pe": ref[0], "ps": ref[1], "ev_ebitda": ref[2]} if ref else None,
        "pe_meaningful": (fin["operating_income"] or -1) > 0 and (ni or -1) > 0,
    }


# ======================================================================
# formatting (shared by console, docx, card)
# ======================================================================

def money(v, dp=1):
    if v is None:
        return "n/a"
    a = abs(v)
    sign = "-" if v < 0 else ""
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return "%s$%.*f%s" % (sign, dp, a / div, suf)
    return "%s$%.0f" % (sign, a)


def pct(v, dp=1):
    if v is None:
        return "n/a"
    return "%.*f%%" % (dp, 100 * v)


def signed_pct(v, dp=1):
    if v is None:
        return "n/a"
    return "%+.*f%%" % (dp, 100 * v)


def px(v):
    if v is None:
        return "n/a"
    return "$%.2f" % v if v < 1000 else "$%s" % format(round(v), ",")


def mult(v):
    return "n/m" if v is None else "%.1fx" % v


# ======================================================================
# narrative
# ======================================================================

def _thesis(fin, cls, val, sc):
    b = val["scenarios"]["base"]
    if cls == "venture":
        return "%s-REVENUE BET BURNING %s/YEAR" % (money(fin["revenue"], 0).upper(),
                                                   money(-(fin["fcf"] or 0), 0).upper())
    if cls == "growth_burn":
        return "GROWING %s, CASH-NEGATIVE, PRICED FOR THE RAMP" % pct(fin["rev_growth"], 0)
    if cls == "financial":
        return "EARNINGS-BASED: %s AT %s P/E" % (money(fin["net_income"], 0).upper(),
                                                mult(multiples(fin)["pe"]))
    if cls == "turnaround":
        return "NO CASH GENERATION AND NO GROWTH TO FIX IT"
    if b["upside"] is not None and b["upside"] > 0.15:
        return "CASH GENERATION THE PRICE DOES NOT REFLECT"
    if b["upside"] is not None and b["upside"] < -0.25:
        return "PRICE ASSUMES FAR MORE GROWTH THAN THE CASH FLOWS SHOW"
    return "PRICED ROUGHLY FOR WHAT IT EARNS"


def key_driver(fin, cls, val):
    """One plain sentence -- the thing the verdict hinges on."""
    b = val["scenarios"]["base"]
    if cls == "venture":
        return ("%s of revenue against %s a year of cash burn; the value is a year-5 margin "
                "that has not happened yet" % (money(fin["revenue"]), money(-(fin["fcf"] or 0))))
    if cls == "growth_burn":
        return "%s revenue growth has to turn into cash before the base case pays" % pct(fin["rev_growth"], 0)
    if cls == "financial":
        return "earnings power of %s a year, valued without FCF" % money(fin["net_income"])
    if cls == "turnaround":
        return "cash generation is negative and revenue is not growing fast enough to fix it"
    rep, own = fin["fcf_margin"], fin["owner_margin"]
    if rep is not None and own is not None and own - rep > 0.03:
        return ("capex of %s of revenue takes reported FCF margin to %s against %s on "
                "owner earnings" % (pct(fin["capex_intensity"], 0), pct(rep), pct(own)))
    return "a %s cash margin growing %s a year in the base case" % (pct(own), pct(b["growth"]))


def _suitable(sc, cls):
    v = sc["verdict"]
    if v in ("AVOID", "REDUCE"):
        return "Speculative capital only, if at all; not a core holding at this price."
    if cls in ("venture", "growth_burn"):
        return "High-risk-tolerance growth investors sizing for a possible total loss."
    if v == "HOLD":
        return "Existing holders; new money waits for a better entry or a catalyst."
    return "Long-term investors comfortable with the stated growth assumptions."


def _sizing(sc, cls):
    v = sc["verdict"]
    if v == "AVOID":
        return "0% -- no position."
    if v == "REDUCE":
        return "Trim to at most 1% of a portfolio."
    if v == "HOLD":
        return "Hold an existing 1-3%; no additions."
    if cls in ("venture", "growth_burn"):
        return "Starter position of at most 1-2%."
    return "Build toward 2-4%, scaling in over quarters."


def watch_metrics(fin, cls, val=None):
    base_g = val["scenarios"]["base"]["growth"] if val else None
    out = ["Revenue growth vs the base case's %s a year (trailing %s)" % (pct(base_g, 0), signed_pct(fin["rev_growth"])),
           "Operating margin trend (now %s)" % pct(fin["op_margin"])]
    if fin["fcf"] is not None and fin["fcf"] < 0:
        out += ["Quarterly cash burn and cash + short-term investments",
                "Share count / dilution each quarter"]
    else:
        out += ["Free cash flow vs capex (capex now %s of revenue)" % pct(fin["capex_intensity"])]
    if fin["debt"]:
        out.append("Net debt / EBITDA and refinancing dates")
    if fin["sbc_pct"] is not None and fin["sbc_pct"] > 0.05:
        out.append("Stock comp as %% of revenue (now %s)" % pct(fin["sbc_pct"]))
    if fin["next_earnings"]:
        out.append("Next earnings: %s" % fin["next_earnings"])
    return out


def analyse(fin, overrides=None):
    ov = {k: v for k, v in (overrides or {}).items() if v is not None}
    for key, lo, hi in (("discount", 0.03, 0.30), ("growth", -0.5, 5.0)):
        if key in ov and not (lo <= float(ov[key]) <= hi):
            raise ValueError("Override %s=%s is outside %s..%s." % (key, ov[key], lo, hi))
    if "discount" in ov and float(ov["discount"]) <= TERMINAL["bull"]:
        raise ValueError("Discount rate must exceed the 3% bull terminal growth.")
    cls, why = classify(fin)
    val = value(fin, cls, ov)
    val["returns"] = expected_returns(fin, cls, val)
    flags = red_flags(fin, cls, val)
    qual = quality(fin, cls, fin.get("_insiders"), fin.get("_earnings"))
    sc = score(fin, cls, val, flags, qual)
    mul = multiples(fin)
    b = val["scenarios"]["base"]
    thesis = _thesis(fin, cls, val, sc)
    runway = None
    if fin["fcf"] is not None and fin["fcf"] < 0 and fin["liquid"] is not None:
        runway = fin["liquid"] / -fin["fcf"]
    # the four card bullets: worst flags first, then the best passes
    bad = [f for f in flags if f["bad"]]
    good = [f for f in flags if not f["bad"]]
    bad.sort(key=lambda f: -f["points"])
    bullets = (bad + good)[:4]
    cyc_f = [f for f in flags if f["key"] == "cycle_peak"]
    if cyc_f and cyc_f[0] not in bullets:
        # the 10-yr cycle check is the only place a peak warning surfaces on the card
        bullets = bullets[:3] + cyc_f
    stance = stance_for(sc["badge"])
    summary = ("%s is %s at %s (%d/10 %s). Base case DCF suggests intrinsic value of %s, "
               "implying %s %s. Key driver: %s. Suitable for: %s"
               % (fin["ticker"], stance, px(fin["price"]), sc["badge"], sc["verdict"],
                  px(b["per_share"]),
                  pct(abs(b["upside"]) if b["upside"] is not None else None),
                  "upside" if (b["upside"] or 0) >= 0 else "downside",
                  key_driver(fin, cls, val), _suitable(sc, cls)))
    out = {
        "model_version": MODEL_VERSION, "overrides": ov, "fin": fin, "class": cls, "class_label": CLASSES[cls]["label"],
        "class_reason": why, "method": CLASSES[cls]["method"], "valuation": val,
        "flags": flags, "score": sc, "quality": qual, "why": why_verdict(fin, val, sc, qual),
        "multiples": mul, "thesis": thesis,
        "verdict_line": "%s — %s" % (sc["verdict"], thesis),
        "summary": summary, "stance": stance, "key_driver": key_driver(fin, cls, val), "suitable": _suitable(sc, cls), "sizing": _sizing(sc, cls),
        "watch": watch_metrics(fin, cls, val), "runway_years": runway, "card_bullets": bullets,
        "unmeasured": ["Customer concentration (not in the UW payload)",
                       "Competitive position / market share (not in the UW payload)"],
    }
    out["synopsis"] = synopsis(out)
    return out


SYNOPSIS_MAX = 280     # one tweet


def synopsis(res):
    """
    A tweet-length reading of the whole result, in plain words (the brief asked for
    "making sense of the data and scores in something that fits in a tweet").
    Built from the numbers, clause by clause; optional clauses are dropped,
    last first, until it fits in SYNOPSIS_MAX characters. Never invents a fact
    the result does not hold.
    """
    f, s, v = res["fin"], res["score"], res["valuation"]
    b = v["scenarios"]["base"]
    m = res.get("multiples") or {}
    q = (res.get("quality") or {}).get("score")
    t = "$" + f["ticker"]
    g = f.get("rev_growth")
    grow = (None if g is None else "shrinking" if g < -0.005 else "slow-growing" if g < 0.05
            else "growing" if g < 0.15 else "fast-growing")
    qual = (None if q is None else "high-quality" if q >= 7 else "decent-quality" if q >= 6
            else "average-quality" if q >= 4 else "weak")
    if qual == "weak":
        lead = "%s: a %sbusiness with weak quality scores" % (t, (grow + " ") if grow else "")
    elif qual or grow:
        words = " ".join(x for x in (qual, grow) if x)
        lead = "%s: %s %s business" % (t, "an" if words[0] in "aeiou" else "a", words)
    else:
        lead = t + ":"

    # what the market is paying for it
    cheap_earn = (m.get("pe_meaningful") and m.get("pe") and m["pe"] < 15) or \
                 (m.get("fcf_yield") is not None and m["fcf_yield"] > 0.08)
    nd = (f.get("debt") or 0) - (f.get("liquid") or 0) if f.get("debt") is not None else None
    ebitda = f.get("ebitda")
    lev = nd / ebitda if (nd is not None and ebitda and ebitda > 0) else None
    heavy = res["class"] != "financial" and lev is not None and lev > 4
    clauses = []
    if cheap_earn and heavy:
        clauses.append("cheap on earnings, but debt of %.1fx EBITDA leaves little for shareholders" % lev)
    elif heavy:
        clauses.append("carrying heavy debt (%.1fx EBITDA)" % lev)
    elif cheap_earn:
        clauses.append("cheap on earnings (%s)" % ("P/E %.0fx" % m["pe"] if m.get("pe_meaningful") and m.get("pe")
                                                  else "FCF yield %s" % pct(m["fcf_yield"], 0)))
    first = lead + ((", " + clauses[0]) if clauses else "") + "."

    up = b.get("upside")
    if b.get("per_share") is None or up is None:
        price = "No per-share value could be computed."
    elif b.get("floored"):
        price = "Debt exceeds the value of the business in the base case; at %s it has no margin of safety." % px(f["price"])
    else:
        ratio = f["price"] / b["per_share"]
        if ratio >= 2:
            price = "At %s it trades at %.1fx its ~%s intrinsic value." % (px(f["price"]), ratio, px(b["per_share"]))
        else:
            price = "At %s it trades %.0f%% %s its ~%s intrinsic value." % (
                px(f["price"]), 100 * abs(ratio - 1), "above" if ratio > 1 else "below", px(b["per_share"]))

    applied = [g_ for g_ in s.get("gates") or [] if g_.get("applied")]
    if applied:
        why = "; ".join("no 25% margin of safety" if g_["key"] == "margin" else "quality too low for more"
                        for g_ in applied)
        verdict = "%s (%s)." % (s["verdict"], why)
    else:
        verdict = "%s." % s["verdict"]
    er = (v.get("returns") or {}).get("irr") or {}
    irr10 = er.get(10, er.get("10"))
    ret = ("About %+.0f%%/yr if the price gets there by year 10." % (100 * irr10)) if (irr10 is not None and up and up > 0) else None
    bull, bear = v["scenarios"]["bull"]["per_share"], v["scenarios"]["bear"]["per_share"]
    rng = ("Wide range: %s-%s." % (px(bear), px(bull))) if (bull and bear is not None and bear > 0 and bull / bear > 4) else None
    growth_note = "Little growth to close the gap." if (g is not None and g < 0.03 and up is not None and up < 0) else None

    parts = [first, price, verdict]
    optional = [x for x in (ret, growth_note, rng) if x]
    out = " ".join(parts + optional)
    while len(out) > SYNOPSIS_MAX and optional:
        optional.pop()
        out = " ".join(parts + optional)
    if len(out) > SYNOPSIS_MAX:                       # drop the debt/earnings clause last
        out = " ".join([lead + ".", price, verdict])
    return out[:SYNOPSIS_MAX]


def method_info():
    """
    The numbers the "How this desk works" panel prints, read from the model
    itself so the explanation can never drift from the code that runs.
    """
    return {
        "model_version": MODEL_VERSION, "years": YEARS, "fade_years": FADE_YEARS,
        "fade_cap": FADE_START_CAP, "terminal": dict(TERMINAL),
        "fixed_discount": OPTIONS.get("fixed_discount", False),
        "discounts": [[CLASSES[k]["label"], FIXED_DISCOUNT[k]] for k in CLASSES],
        "flag_cap": FLAG_POINTS_CAP, "margin_of_safety": MARGIN_OF_SAFETY,
        "quality_gates": [[q, cap, verdict_for(cap)[0]] for q, cap, _l in QUALITY_GATES],
        "quality_min_measured": QUALITY_MIN_MEASURED,
        "quality": [[QUALITY_LABELS[k], w] for k, w in QUALITY_WEIGHTS.items()],
        "verdicts": [[hi, label] for hi, label, _c in VERDICTS],
        "cycle_scored": OPTIONS.get("cycle_aware", False),
        "trend_margin": OPTIONS.get("trend_margin", False),
    }


def run(payloads, fx=None, overrides=None):
    """payloads: dict with keys info, is_a, is_q, bs_a, bs_q, cf_a, cf_q."""
    fin = extract(payloads.get("info"), payloads.get("is_a"), payloads.get("is_q"),
                  payloads.get("bs_a"), payloads.get("bs_q"), payloads.get("cf_a"),
                  payloads.get("cf_q"), fx=fx)
    # Model 3.2: management footprints outside the statements. Both optional --
    # a run saved before 3.2, or an endpoint that failed, leaves them
    # UNMEASURED (the quality score re-scales over what was measured).
    if payloads.get("insiders") is not None:
        fin["_insiders"] = insider_summary(rows_of(payloads["insiders"]), fin["price"], fin["marketcap"],
                                           fin["shares"], today=payloads.get("as_of"))
    if payloads.get("earnings") is not None:
        fin["_earnings"] = earnings_summary(rows_of(payloads["earnings"]))
    return analyse(fin, overrides)


# ======================================================================
# console report (Step 3 of the brief)
# ======================================================================

def console_report(res):
    f, v, s, m = res["fin"], res["valuation"], res["score"], res["multiples"]
    L = []
    w = L.append
    bar = "=" * 72
    w(bar)
    w("%s  %s  |  %s" % (f["ticker"], f["name"], f["sector"]))
    w("Valuation Desk model %s  |  data basis %s  |  balance sheet %s"
      % (res["model_version"], f["basis"], f["bs_date"] or "n/a"))
    w(bar)
    w("")
    w("CURRENT METRICS")
    w("  Price            %s" % px(f["price"]))
    w("  Market cap       %s" % money(f["marketcap"]))
    w("  Shares           %.1fM" % ((f["shares"] or 0) / 1e6))
    w("  Beta             %s" % ("n/a" if f["beta"] is None else "%.2f" % f["beta"]))
    w("")
    w("REVENUE & MARGINS")
    w("  Revenue          %s   YoY %s   QoQ %s" % (money(f["revenue"]), signed_pct(f["rev_growth"]),
                                                  signed_pct(f["q_growth"])))
    w("  Gross profit     %s" % money(f["gross_profit"]))
    w("  Operating inc.   %s   margin %s (prior %s)" % (money(f["operating_income"]), pct(f["op_margin"]),
                                                       pct(f["prev_op_margin"])))
    w("  Net income       %s   margin %s" % (money(f["net_income"]), pct(f["net_margin"])))
    w("  EBITDA (op+D&A)  %s" % money(f["ebitda"]))
    w("")
    w("FREE CASH FLOW")
    w("  Operating CF     %s" % money(f["ocf"]))
    w("  CapEx            %s   (%s of revenue)" % (money(f["capex"]), pct(f["capex_intensity"])))
    w("  FCF (OCF-CapEx)  %s   margin %s" % (money(f["fcf"]), pct(f["fcf_margin"])))
    w("  Owner FCF        %s   (OCF - maintenance capex ~ D&A)" % money(f["owner_fcf"]))
    w("  Stock comp       %s   (%s of revenue)" % (money(f["sbc"]), pct(f["sbc_pct"])))
    if res["runway_years"] is not None:
        w("  Cash runway      %.1f years" % res["runway_years"])
    w("")
    w("BALANCE SHEET")
    w("  Cash + ST inv.   %s" % money(f["liquid"]))
    w("  Total debt       %s   (incl. leases)" % money(f["debt"]))
    w("  Net cash         %s" % money(f["net_cash"]))
    w("  Equity           %s" % money(f["equity"]))
    w("")
    w("VALUATION MULTIPLES")
    ref = m["sector_ref"] or {}
    w("  P/E       %-8s sector ref %s%s" % (mult(m["pe"]), ref.get("pe", "n/a"),
                                            "" if m["pe_meaningful"] else "  (not meaningful: op. loss)"))
    w("  P/S       %-8s sector ref %s" % (mult(m["ps"]), ref.get("ps", "n/a")))
    w("  P/FCF     %-8s FCF yield %s" % (mult(m["pfcf"]), pct(m["fcf_yield"])))
    w("  EV/EBITDA %-8s sector ref %s" % (mult(m["ev_ebitda"]), ref.get("ev_ebitda", "n/a")))
    w("  (sector refs are static typical medians, not live data)")
    w("")
    w("MODEL: %s -- %s" % (res["class_label"], res["method"]))
    w("  %s" % res["class_reason"])
    w("  Discount rate %.1f%%: %s" % (100 * v["discount"], v["discount_how"]))
    w("  Growth band: %s, position %.0f%% (from trailing revenue growth)" % (v["band"], 100 * v["band_pos"]))
    if res.get("overrides"):
        w("  OVERRIDES: %s" % ", ".join("%s=%s" % kv for kv in sorted(res["overrides"].items())))
    w("")
    w("  %-6s %8s %8s %8s %8s %12s %10s %9s" % ("", "growth", "m0", "m5", "term.", "equity", "per sh.", "vs price"))
    for k in ("bull", "base", "bear"):
        sc = v["scenarios"][k]
        w("  %-6s %8s %8s %8s %8s %12s %10s %9s" % (
            k.upper(), pct(sc["growth"]), pct(sc["m0"]), pct(sc["m5"]), pct(sc["terminal"]),
            money(sc["equity"]), px(sc["per_share"]), signed_pct(sc["upside"])))
    b = v["scenarios"]["base"]
    w("")
    cap = v["capital"]
    w("  Projection: %d years (%d at the scenario rate, then fading to terminal)" % (v["years"], YEARS))
    w("  Growth funding: %s%s" % (cap["how"], "" if cap["charged"] else " -- not charged (capex already in cash margin)"))
    w("  Base case, year by year:")
    for r in b["rows"]:
        w("    Y%-2d growth %6s  revenue %10s  margin %7s  reinvest %10s  cash flow %10s  PV %10s" % (
            r["year"], pct(r["growth"]), money(r["revenue"]), pct(r["margin"]), money(r["reinvest"]),
            money(r["cf"]), money(r["pv"])))
    w("    Terminal value %s (PV %s, %s of EV)" % (money(b["tv"]), money(b["pv_tv"]), pct(b["tv_share"])))
    w("    + net cash used %s" % money(v["net_cash_used"]))
    w("")
    w("RED FLAGS & CHECKS")
    for fl in res["flags"]:
        w("  %s %s%s" % ("x" if fl["bad"] else "+", fl["text"],
                         ("   (-%.2f)" % fl["points"]) if fl["points"] else ""))
    for u in res["unmeasured"]:
        w("  ? %s" % u)
    w("")
    q = res.get("quality")
    if q:
        w("QUALITY & MANAGEMENT: %s  (%d of 100 points measured)" % (
            "%.1f/10 %s" % (q["score"], q["tier"]) if q["score"] is not None else "unknown", q["measured"]))
        for c in q["components"]:
            w("  %-38s %3d  %s  %s" % (c["label"], c["weight"],
                                       " -- " if c["value"] is None else "%3.0f%%" % (100 * c["value"]), c["text"]))
        w("")
    er = v.get("returns")
    if er:
        irr = er.get("irr") or {}
        g = lambda k: irr.get(k, irr.get(str(k)))
        f_ = lambda x: "n/a" if x is None else "%+.1f%%/yr" % (100 * x)
        w("EXPECTED RETURN (base case): reaches value by yr 5 %s, by yr 10 %s, never re-rates %s; "
          "price implies %s growth" % (f_(g(5)), f_(g(10)), f_(er.get("implied_return")),
                                       "n/a" if er.get("implied_growth") is None else pct(er["implied_growth"])))
        w("")
    for g_ in s.get("gates") or []:
        w("GATE %s: %s -- %s%s" % (g_["key"], "pass" if g_["passed"] else "FAIL", g_["value"],
                                    " -> capped at %.1f" % g_["cap"] if g_.get("applied") else ""))
    w("VERDICT: %s/10 %s" % (s["badge"], s["verdict"]))
    if res.get("synopsis"):
        w("  IN A TWEET: %s" % res["synopsis"])
    w("  valuation %.2f - red flags %.2f%s = %.1f   (10 = deeply undervalued, 0 = avoid)" % (s["valuation_points"], s["flag_points"],
        " (capped from %.2f)" % s["flag_points_raw"] if s["flag_points_raw"] > s["flag_points"] else "",
        s["score"]))
    for n in s["structural"]:
        w("  structural: %s" % n)
    w("  %s" % res["verdict_line"])
    w("")
    w(res["summary"])
    for q in f["quality"] + f["notes"]:
        w("  note: %s" % q)
    w("")
    w("Educational use only. Not financial advice. Data: Unusual Whales API.")
    return "\n".join(L)


def to_json(res):
    return json.dumps(res, default=str, indent=1)
