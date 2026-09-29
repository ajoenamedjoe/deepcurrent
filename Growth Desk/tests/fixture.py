"""Synthetic Unusual Whales payloads for the Growth Leaders tests. Invented tickers and numbers, same shapes as
the live endpoints (MCP-verified 2026-09-28): screener rows with string decimals, /earnings rows newest first with
the next report's eps null, 13F ownership rows with historical_units, daily bars oldest first."""

import datetime as dt
import math
import random

TODAY = "2026-09-25"


def trading_days(n, end=TODAY):
    d = dt.date.fromisoformat(end)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def s(x):
    return None if x is None else ("%.10f" % x)


def screener_row(t, i, rnd, industry, r3, r6, r12, close, hi, shares_g=0.0, mcap=5e9):
    return {"ticker": t, "full_name": "%s Corp" % t, "issue_type": "Common Stock", "is_index": False,
            "close": s(close), "open": s(close), "high": s(close * 1.01), "low": s(close * 0.99),
            "marketcap": s(mcap), "sector": "Technology", "industry_type": industry,
            "week_52_high": s(hi), "week_52_low": s(close * 0.5),
            "three_month_perc": s(r3), "six_month_perc": s(r6), "one_year_perc": s(r12),
            "shares_outstanding_growth_4q": s(shares_g), "relative_volume": s(1.0),
            "avg30_volume": s(1e6), "stock_volume": 1000000, "sma_50": close * 0.95, "sma_200": close * 0.85,
            "next_earnings_date": "2026-11-01", "bullish_premium": s(rnd.uniform(0, 2e6)),
            "bearish_premium": s(rnd.uniform(0, 2e6))}


def universe(n=400, seed=7):
    rnd = random.Random(seed)
    inds = ["Semis", "Software", "Banks", "Retail", "Biotech", "Utilities", "Autos", "Media"]
    rows = []
    for i in range(n):
        t = "Z%03d" % i
        ind = inds[i % len(inds)]
        drift = {"Semis": 0.25, "Software": 0.15, "Banks": 0.0, "Retail": -0.05, "Biotech": 0.05,
                 "Utilities": 0.02, "Autos": -0.1, "Media": -0.02}[ind]
        r12 = drift + rnd.gauss(0.1, 0.35)
        r6 = r12 * 0.6 + rnd.gauss(0, 0.1)
        r3 = r6 * 0.5 + rnd.gauss(0, 0.08)
        close = rnd.uniform(8, 300)
        hi = close / (1 - min(0.6, abs(rnd.gauss(0.12, 0.12))))
        rows.append(screener_row(t, i, rnd, ind, max(-0.9, r3), max(-0.9, r6), max(-0.9, r12), close, hi,
                                 shares_g=rnd.gauss(0.01, 0.03)))
    return rows


def earnings(eps_newest_first, start_end="2026-06-30"):
    """Quarterly rows newest first, plus the next (unreported) quarter on top, like /api/stock/{t}/earnings."""
    end = dt.date.fromisoformat(start_end)
    rows = [{"ticker": "X", "fiscal_date_ending": (end + dt.timedelta(days=92)).isoformat(), "report_type": "quarterly",
             "report_date": "2026-10-28", "reported_eps": None, "estimated_eps": "1.00", "surprise_percentage": None}]
    y, m = end.year, end.month
    for k, e in enumerate(eps_newest_first):
        mm = m - 3 * k
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        last = (dt.date(yy + (mm // 12), mm % 12 + 1, 1) - dt.timedelta(days=1))
        rows.append({"ticker": "X", "fiscal_date_ending": last.isoformat(), "report_type": "quarterly",
                     "report_date": (last + dt.timedelta(days=28)).isoformat(), "reported_eps": s(e),
                     "estimated_eps": s(e * 0.95), "surprise_percentage": "5.2"})
    return rows


def growth_eps(q0=1.0, yoy=0.40, n=16):
    """A grower: each quarter `yoy` above the same quarter a year before (newest first)."""
    out = []
    for k in range(n):
        out.append(q0 / ((1 + yoy) ** (k // 4)) * (1 - 0.02 * (k % 4)))
    return out


def ownership(adds=60, cuts=30, new=10, exits=5, units=1e6, report_date="2026-06-30"):
    rows = []
    for i in range(adds):
        hist = [units, 0 if i < new else units * 0.8]
        rows.append({"report_date": report_date, "units": s(units), "units_changed": s(units * 0.2 if i >= new else units),
                     "historical_units": hist})
    for i in range(cuts):
        u = 0 if i < exits else units * 0.9
        rows.append({"report_date": report_date, "units": s(u), "units_changed": s(-units * 0.1 if i >= exits else -units),
                     "historical_units": [u, units]})
    rows.append({"report_date": "2026-03-31", "units": s(units), "units_changed": "0", "historical_units": [units]})
    return rows


def bars_series(closes, vols=None, end=TODAY):
    days = trading_days(len(closes), end)
    out = []
    for i, (d, c) in enumerate(zip(days, closes)):
        v = vols[i] if vols else 1e6
        out.append({"date": d, "open": s(c), "high": s(c * 1.005), "low": s(c * 0.995), "close": s(c),
                    "volume": v, "market_time": "r"})
    return out


def base_then(kind="breakout", n=250):
    """Advance 60%, pull back 15% over ~8 weeks, then: breakout on volume / near pivot / extended / in base."""
    closes = []
    p = 50.0
    for i in range(n - 60):
        p *= 1.0025
        closes.append(p)
    top = p * 1.02
    closes.append(top)                                 # the pivot high
    for i in range(40):
        closes.append(top * (1 - 0.15 * math.sin(math.pi * i / 40)))
    tail = {"breakout": [top * 0.97] * 18 + [top * 1.02],
            "near": [top * 0.97] * 19,
            "extended": [top * 0.97] * 15 + [top * 1.03, top * 1.06, top * 1.09, top * 1.12],
            "in_base": [top * 0.88] * 19}[kind]
    closes += tail
    vols = [1e6] * (len(closes) - 1) + [3e6 if kind == "breakout" else 1e6]
    return bars_series(closes[-n:], vols[-n:])


def index_series(kind, n=260):
    """SPY-like: 'uptrend' steady, 'correction' -12% slide, 'ftd' slide then a follow-through day,
    'pressure' uptrend with 6 recent distribution days."""
    closes, vols = [], []
    p, v = 400.0, 1e8
    for i in range(n):
        if kind == "uptrend":
            p *= 1.001
            vv = v
        elif kind == "correction":
            p *= 1.001 if i < n - 40 else 0.9965
            vv = v
        elif kind == "ftd":
            if i < n - 40:
                p *= 1.001
            elif i < n - 12:
                p *= 0.995
            elif i < n - 6:
                p *= 1.002
            elif i == n - 6:
                p *= 1.02                      # day 7 of the rally attempt: +2% on higher volume
                vv = v * 1.3
                closes.append(p); vols.append(vv)
                continue
            else:
                p *= 1.001
            vv = v
        elif kind == "pressure":
            if i >= n - 20 and i % 3 == 0:
                p *= 0.996                     # down 0.4% on higher volume: distribution
                vv = v * 1.2
            else:
                p *= 1.0015
                vv = v
        closes.append(p)
        vols.append(vv)
    return bars_series(closes, vols)


class FakeClient:
    """Serves the synthetic payloads through the same methods as uw.Client."""

    def __init__(self, n=400, seed=7):
        self.rows = universe(n, seed)
        self.calls = 0
        self.errors = 0
        self.fail = set()
        rnd = random.Random(seed + 1)
        self.eps, self.own, self.bars = {}, {}, {}
        for i, r in enumerate(self.rows):
            t = r["ticker"]
            yoy = rnd.choice([-0.3, 0.05, 0.15, 0.3, 0.5, 0.9])
            self.eps[t] = earnings(growth_eps(rnd.uniform(0.3, 3), yoy))
            self.own[t] = ownership(adds=rnd.randint(10, 80), cuts=rnd.randint(10, 80), new=5, exits=3)
            self.bars[t] = base_then(rnd.choice(["breakout", "near", "extended", "in_base"]))
        self.bars["SPY"] = index_series("uptrend")
        self.bars["QQQ"] = index_series("uptrend")

    def _tick(self, t=None):
        self.calls += 1
        if t in self.fail:
            raise RuntimeError("HTTP 500 for %s" % t)

    def screener_page(self, offset, limit=500, **kw):
        self._tick()
        return self.rows[offset * limit:(offset + 1) * limit]

    def screener_tickers(self, tickers):
        self._tick()
        return [r for r in self.rows if r["ticker"] in set(tickers)]

    def earnings(self, t):
        self._tick(t)
        return self.eps.get(t, [])

    def ownership(self, t):
        self._tick(t)
        return self.own.get(t, [])

    def ohlc_daily(self, t, timeframe="1Y"):
        self._tick(t)
        return self.bars.get(t, [])
