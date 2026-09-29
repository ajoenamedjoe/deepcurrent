#!/usr/bin/env python3
"""
UW Swing Dashboard - local server.

Holds your Unusual Whales API token server-side (never sent to the browser),
polls the UW REST API, runs the swing-confluence scoring model, and serves
the dashboard at http://127.0.0.1:8787

Usage:
    export UW_API_TOKEN="your-token"        # or put it in a .env file beside this script
    python3 server.py                       # then open http://127.0.0.1:8787

Stdlib only. No pip install required.
"""

import json
import math
import os
import re
import statistics
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from notify import NOTIFY, Discord, et_now, session_state
from store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
API_BASE = "https://api.unusualwhales.com/api"
PORT = int(os.environ.get("UW_DASH_PORT", "8787"))
HOST = os.environ.get("UW_DASH_HOST", "127.0.0.1")

# ----------------------------------------------------------------------------
# Tunables. Everything the model keys off lives here.
# ----------------------------------------------------------------------------
CFG = {
    # Universe (stage 1)
    "min_option_volume": 2000,      # options contracts traded today
    "min_price": 5.0,
    "min_marketcap": 1_000_000_000,
    "universe_size": 120,           # how many screener rows to consider
    "enrich_top": 14,               # how many get the per-ticker GEX + OHLC calls

    # Scanning runs on its own thread during the session, page open or not.
    "scan_start": "09:35",          # ET
    "scan_end": "16:00",            # ET

    # Alert gates. Recalibrated 2026-09-25 with the component gains below: on a
    # full-session sample, |score| >= 40 was about the top 5% of names.
    "min_score": 40.0,              # |score| needed to surface as an alert
    "min_agreeing": 3,              # signals of real size pointing the same way
    "agree_threshold": 0.15,        # a signal must contribute this much (of 1.0)
                                    # before it counts as "agreeing" - at 0.05 a
                                    # signal doing almost nothing inflated the count
    "min_rr": 1.3,                  # reject setups with worse reward:risk

    # Component weights (sum 100)
    "w_flow": 30.0,
    "w_oi": 20.0,
    "w_dark": 15.0,
    "w_gamma": 15.0,
    "w_tech": 20.0,

    # Component calibration (2026-09-25). Measured on a full-session screener
    # sample of 150 names: flow's 90th percentile was only 0.52 of full strength,
    # OI change 0.30, gamma 0.25-0.34, while technicals sat above 0.5 for 69% of
    # names. The composite therefore topped out near 46 and the 45/60 gates
    # almost never opened. Gains stretch each signal so a genuinely strong
    # reading (about its 90th percentile) is worth ~0.8 of its weight.
    "flow_gain": 1.5,               # applied to the finished flow value
    "oi_gain": 28.0,                # was 8: tanh((call_chg - put_chg) * gain)
    "gamma_flip_gain": 60.0,        # was 25: distance from flip, as a fraction of spot
    "gamma_room_gain": 30.0,        # was 12: room-to-call-wall minus room-to-put-wall

    # Penalties
    "earnings_haircut": 0.65,       # applied if earnings land inside the horizon
    "high_iv_rank": 85.0,
    "high_iv_haircut": 0.85,
    "thin_rvol": 0.40,
    "thin_haircut": 0.70,

    # Cache TTLs (seconds)
    "ttl_scan": 45,
    "ttl_tape": 15,
    "ttl_tide": 60,
    "ttl_gex": 60,
    "ttl_ohlc": 600,                # daily candles barely move intraday
}

# ----------------------------------------------------------------------------
# Token
# ----------------------------------------------------------------------------


def load_token():
    tok = os.environ.get("UW_API_TOKEN", "").strip()
    if tok:
        return tok
    envfile = os.path.join(HERE, ".env")
    if os.path.exists(envfile):
        with open(envfile) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() in ("UW_API_TOKEN", "UNUSUAL_WHALES_API_TOKEN"):
                    return v.strip().strip('"').strip("'")
    return ""


TOKEN = load_token()

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def num(v, default=0.0):
    """UW returns plenty of numbers as strings; some as null."""
    if v is None:
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v) if not (isinstance(v, float) and math.isnan(v)) else default
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def tanh(x):
    try:
        return math.tanh(x)
    except (OverflowError, ValueError):
        return 1.0 if x > 0 else -1.0


def safe_div(a, b, default=0.0):
    return a / b if b else default


class TTLCache:
    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def get(self, key, ttl):
        with self._lock:
            hit = self._d.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.time() - ts > ttl:
            return None
        return val

    def put(self, key, val):
        with self._lock:
            self._d[key] = (time.time(), val)

    def stale(self, key):
        """Last known value regardless of age - used when a poll fails."""
        with self._lock:
            hit = self._d.get(key)
        return hit[1] if hit else None


CACHE = TTLCache()
FEED_STATUS = {}
FEED_LOCK = threading.Lock()

STORE = Store()
DISCORD = Discord(STORE)


def mark_feed(name, ok, detail=""):
    with FEED_LOCK:
        FEED_STATUS[name] = {
            "ok": ok,
            "detail": detail,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


# ----------------------------------------------------------------------------
# UW API
# ----------------------------------------------------------------------------


class UWError(Exception):
    pass


def uw_get(path, params=None, timeout=25):
    if not TOKEN:
        raise UWError("No API token. Set UW_API_TOKEN or create a .env file.")
    qs = ""
    if params:
        flat = []
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, (list, tuple)):
                for item in v:
                    flat.append((k, str(item)))
            elif isinstance(v, bool):
                flat.append((k, "true" if v else "false"))
            else:
                flat.append((k, str(v)))
        qs = "?" + urllib.parse.urlencode(flat)
    url = f"{API_BASE}{path}{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
            "User-Agent": "uw-swing-dashboard/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise UWError(f"HTTP {e.code} on {path} :: {body}") from e
    except Exception as e:
        raise UWError(f"{type(e).__name__} on {path}: {e}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise UWError(f"Bad JSON from {path}: {e}") from e


def rows(payload):
    """UW wraps lists in {'data': [...]} or {'result': [...]} depending on endpoint."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    for key in ("data", "result", "results", "chains"):
        v = payload.get(key)
        if isinstance(v, list):
            return v
    return []


# ----------------------------------------------------------------------------
# Technicals, computed here from daily candles (no extra API surface needed)
# ----------------------------------------------------------------------------


def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains) / n, sum(losses) / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def atr(highs, lows, closes, n=14):
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def fetch_technicals(ticker):
    key = f"ohlc:{ticker}"
    hit = CACHE.get(key, CFG["ttl_ohlc"])
    if hit is not None:
        return hit
    try:
        payload = uw_get(f"/stock/{urllib.parse.quote(ticker)}/ohlc/1d",
                         {"timeframe": "6M"})
    except UWError as e:
        mark_feed("ohlc", False, str(e)[:160])
        return CACHE.stale(key)
    bars = rows(payload)
    # Daily candles come oldest-first, but sort defensively.
    bars = [b for b in bars if b.get("close") is not None]
    bars.sort(key=lambda b: str(b.get("date") or b.get("start_time") or ""))
    closes = [num(b.get("close")) for b in bars]
    highs = [num(b.get("high")) for b in bars]
    lows = [num(b.get("low")) for b in bars]
    if len(closes) < 55:
        return None
    out = {
        "close": closes[-1],
        "sma20": sma(closes, 20),
        "sma50": sma(closes, 50),
        "rsi14": rsi(closes, 14),
        "atr14": atr(highs, lows, closes, 14),
        "high20": max(highs[-20:]) if len(highs) >= 20 else None,
        "low20": min(lows[-20:]) if len(lows) >= 20 else None,
        "spark": [round(c, 4) for c in closes[-40:]],
    }
    mark_feed("ohlc", True)
    CACHE.put(key, out)
    return out


def fetch_gex(ticker):
    key = f"gex:{ticker}"
    hit = CACHE.get(key, CFG["ttl_gex"])
    if hit is not None:
        return hit
    try:
        payload = uw_get(f"/stock/{urllib.parse.quote(ticker)}/gex-levels")
    except UWError as e:
        mark_feed("gex", False, str(e)[:160])
        return CACHE.stale(key)
    if not isinstance(payload, dict):
        return None
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    out = {
        "call_wall": num(body.get("call_wall"), None) if body.get("call_wall") else None,
        "put_wall": num(body.get("put_wall"), None) if body.get("put_wall") else None,
        "gamma_flip": num(body.get("gamma_flip"), None) if body.get("gamma_flip") else None,
        "gamma_magnet": num(body.get("gamma_magnet"), None) if body.get("gamma_magnet") else None,
    }
    mark_feed("gex", True)
    CACHE.put(key, out)
    return out


# ----------------------------------------------------------------------------
# Market-wide tapes (one wide poll each, aggregated per ticker)
# ----------------------------------------------------------------------------


def fetch_flow_alerts():
    key = "flowalerts"
    hit = CACHE.get(key, CFG["ttl_tape"])
    if hit is not None:
        return hit
    try:
        payload = uw_get("/option-trades/flow-alerts", {
            "limit": 200,
            "min_premium": 50000,
            "min_dte": 3,
        })
    except UWError as e:
        mark_feed("flow_alerts", False, str(e)[:160])
        return CACHE.stale(key) or []
    out = []
    today = date.today()
    for r in rows(payload):
        tkr = (r.get("ticker") or "").upper()
        if not tkr:
            continue
        dte = None
        exp = r.get("expiry")
        if exp:
            try:
                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            except ValueError:
                dte = None
        out.append({
            "ticker": tkr,
            "type": (r.get("type") or "").lower(),
            "strike": num(r.get("strike")),
            "expiry": exp,
            "dte": dte,
            "premium": num(r.get("total_premium")),
            "volume": num(r.get("volume")),
            "oi": num(r.get("open_interest")),
            "vol_oi": num(r.get("volume_oi_ratio")),
            "rule": r.get("alert_rule") or "",
            "sweep": bool(r.get("has_sweep")),
            "floor": bool(r.get("has_floor")),
            "opening": bool(r.get("all_opening_trades")),
            "multileg": bool(r.get("has_multileg")),
            "underlying": num(r.get("underlying_price")),
            "created_at": r.get("created_at"),
        })
    mark_feed("flow_alerts", True, f"{len(out)} alerts")
    CACHE.put(key, out)
    return out


def fetch_darkpool():
    key = "darkpool"
    hit = CACHE.get(key, CFG["ttl_tape"])
    if hit is not None:
        return hit
    try:
        payload = uw_get("/darkpool/recent", {
            "limit": 200,
            "min_premium": 2_000_000,
            "order_by": "premium",
            "order": "desc",
        })
    except UWError as e:
        mark_feed("darkpool", False, str(e)[:160])
        return CACHE.stale(key) or []
    out = []
    for r in rows(payload):
        tkr = (r.get("ticker") or "").upper()
        if not tkr:
            continue
        price = num(r.get("price"))
        bid, ask = num(r.get("nbbo_bid")), num(r.get("nbbo_ask"))
        mid = (bid + ask) / 2 if (bid and ask) else 0.0
        # Above the mid reads as buyer-initiated; below as seller-initiated.
        lean = 0.0
        if mid and price:
            lean = clamp((price - mid) / mid * 400.0)
        out.append({
            "ticker": tkr,
            "price": price,
            "size": num(r.get("size")),
            "premium": num(r.get("premium")),
            "volume": num(r.get("volume")),
            "lean": lean,
            "sector": r.get("sector"),
            "executed_at": r.get("executed_at"),
            "cond": r.get("sale_cond_codes") or "",
        })
    mark_feed("darkpool", True, f"{len(out)} prints")
    CACHE.put(key, out)
    return out


def fetch_tide():
    key = "tide"
    hit = CACHE.get(key, CFG["ttl_tide"])
    if hit is not None:
        return hit
    try:
        payload = uw_get("/market/market-tide", {"interval_5m": "true"})
    except UWError as e:
        mark_feed("tide", False, str(e)[:160])
        return CACHE.stale(key) or {"series": [], "net": 0.0}
    series = []
    for r in rows(payload):
        series.append({
            "t": r.get("timestamp") or r.get("date"),
            "call": num(r.get("net_call_premium")),
            "put": num(r.get("net_put_premium")),
        })
    net = 0.0
    if series:
        last = series[-1]
        net = last["call"] - last["put"]
    out = {"series": series[-80:], "net": net}
    mark_feed("tide", True, f"{len(series)} points")
    CACHE.put(key, out)
    return out


def fetch_universe():
    key = "universe"
    hit = CACHE.get(key, CFG["ttl_scan"])
    if hit is not None:
        return hit
    try:
        payload = uw_get("/screener/stocks", {
            "min_volume": CFG["min_option_volume"],
            "min_underlying_price": CFG["min_price"],
            "issue_types[]": ["Common Stock", "ETF", "ADR"],
            "order": "premium",
            "order_direction": "desc",
        })
    except UWError as e:
        mark_feed("screener", False, str(e)[:160])
        return CACHE.stale(key) or []
    out = rows(payload)[: CFG["universe_size"]]
    mark_feed("screener", True, f"{len(out)} tickers")
    CACHE.put(key, out)
    return out


# ----------------------------------------------------------------------------
# The scoring model
# ----------------------------------------------------------------------------


def score_flow(r):
    """Directional option premium: calls bought + puts sold = bullish."""
    ncp = num(r.get("net_call_premium"))
    npp = num(r.get("net_put_premium"))
    directional = ncp - npp
    gross = num(r.get("call_premium")) + num(r.get("put_premium"))
    if gross <= 0 or abs(directional) < 1_000_000:
        return 0.0, {"directional": directional, "gross": gross}
    ratio = directional / gross
    base = tanh(ratio * 6.0)

    # Ask-side conviction: are the calls being lifted or sold into?
    cv, pv = num(r.get("call_volume")), num(r.get("put_volume"))
    call_ask = safe_div(num(r.get("call_volume_ask_side")), cv)
    call_bid = safe_div(num(r.get("call_volume_bid_side")), cv)
    put_ask = safe_div(num(r.get("put_volume_ask_side")), pv)
    put_bid = safe_div(num(r.get("put_volume_bid_side")), pv)
    skew = (call_ask - call_bid) - (put_ask - put_bid)

    # Unusual relative option volume amplifies, it doesn't create direction.
    rel = safe_div(cv + pv,
                   num(r.get("avg_30_day_call_volume")) + num(r.get("avg_30_day_put_volume")),
                   1.0)
    amp = clamp(0.75 + 0.25 * math.log(max(rel, 0.05)) / math.log(3.0), 0.6, 1.35)

    val = clamp((0.7 * base + 0.3 * clamp(skew * 1.6)) * amp * CFG["flow_gain"])
    return val, {
        "directional": directional,
        "gross": gross,
        "ratio": ratio,
        "skew": skew,
        "rel_vol": rel,
    }


def score_oi(r):
    """Open interest actually grew = someone opened a position, not scalped one."""
    pc, cc = num(r.get("prev_call_oi")), num(r.get("call_open_interest"))
    pp, cp = num(r.get("prev_put_oi")), num(r.get("put_open_interest"))
    call_chg = safe_div(cc - pc, pc)
    put_chg = safe_div(cp - pp, pp)
    val = tanh((call_chg - put_chg) * CFG["oi_gain"])
    return clamp(val), {
        "call_oi_chg": call_chg,
        "put_oi_chg": put_chg,
        "call_oi": cc,
        "put_oi": cp,
    }


def score_dark(r, dp_agg):
    """Institutional accumulation off-exchange, sized against the day's notional."""
    tkr = (r.get("ticker") or "").upper()
    agg = dp_agg.get(tkr)
    if not agg:
        return 0.0, {}
    notional = num(r.get("stock_volume")) * num(r.get("close"))
    if notional <= 0:
        return 0.0, {}
    intensity = clamp(agg["premium"] / (notional * 0.15), 0.0, 1.0)
    lean = clamp(agg["lean"])
    return clamp(intensity * lean * 2.0), {
        "dp_premium": agg["premium"],
        "dp_prints": agg["count"],
        "dp_intensity": intensity,
        "dp_lean": lean,
    }


def score_gamma(r, gex):
    """Dealer positioning: which way is hedging pushing, and how much room is there."""
    spot = num(r.get("close"))
    if not gex or not spot:
        # Fall back to the screener's own gamma tilt.
        return clamp(tanh(num(r.get("gex_perc_change")) * 0.5) * 0.4), {}
    flip = gex.get("gamma_flip")
    cw, pw = gex.get("call_wall"), gex.get("put_wall")
    parts = []
    detail = {"call_wall": cw, "put_wall": pw, "gamma_flip": flip}
    if flip:
        # Above the flip = positive gamma = dealers dampen; trend holds better.
        parts.append(0.6 * clamp((spot - flip) / spot * CFG["gamma_flip_gain"]))
    if cw and pw and cw > spot > pw:
        room_up = (cw - spot) / spot
        room_dn = (spot - pw) / spot
        parts.append(0.4 * clamp((room_up - room_dn) * CFG["gamma_room_gain"]))
        detail["room_up"] = room_up
        detail["room_dn"] = room_dn
    if not parts:
        return 0.0, detail
    return clamp(sum(parts)), detail


def score_tech(r, t):
    """Trend + momentum, with a bias toward pullbacks inside an uptrend."""
    if not t:
        return 0.0, {}
    close = t["close"] or num(r.get("close"))
    s20, s50, rs = t["sma20"], t["sma50"], t["rsi14"]
    if not (close and s20 and s50 and rs is not None):
        return 0.0, {}

    trend = 0.0
    trend += 0.4 if close > s20 else -0.4
    trend += 0.3 if s20 > s50 else -0.3
    trend += 0.3 if close > s50 else -0.3

    # Swing entries want momentum with room left, not an exhausted move.
    if rs >= 75:
        mom = -0.5
    elif rs <= 25:
        mom = -0.3
    else:
        mom = clamp(1.0 - abs(rs - 57.0) / 25.0)

    val = 0.62 * trend + 0.38 * mom

    # A shallow pullback in an established uptrend is the best swing entry there is.
    pullback = 0.0
    if t["high20"] and trend > 0.5:
        off_high = (t["high20"] - close) / t["high20"]
        if 0.02 <= off_high <= 0.08:
            pullback = 0.30
    if t["low20"] and trend < -0.5:
        off_low = (close - t["low20"]) / t["low20"]
        if 0.02 <= off_low <= 0.08:
            pullback = -0.30
    val += pullback

    return clamp(val), {
        "rsi14": rs,
        "sma20": s20,
        "sma50": s50,
        "atr14": t["atr14"],
        "high20": t["high20"],
        "low20": t["low20"],
        "pullback": pullback != 0.0,
        "spark": t["spark"],
    }


# SHORT/SWING/POSITION stay as the internal keys - the filter buttons and the
# history database are keyed on them. What the user sees is the time range, so
# "SHORT" can never be misread as "short the stock".
HORIZON_DISPLAY = {
    "SHORT":    {"short": "2-10d", "detail": "2-10 days"},
    "SWING":    {"short": "2-6w",  "detail": "2-6 weeks"},
    "POSITION": {"short": "2mo+",  "detail": "2+ months"},
}


def _hz(label, days, src):
    return {"label": label, "days": days, "src": src, **HORIZON_DISPLAY[label]}


def horizon_for(ticker, alerts_by_ticker, spot, target, atr14):
    """Tag every alert with the holding period its own evidence implies."""
    dtes = [a["dte"] for a in alerts_by_ticker.get(ticker, []) if a.get("dte") is not None]
    src = "flow"
    if dtes:
        med = statistics.median(dtes)
    elif atr14 and spot and target:
        # No option-flow evidence: infer from how many ATRs away the target sits.
        med = abs(target - spot) / atr14 * 1.6
        src = "atr"
    else:
        return _hz("SWING", None, "default")

    if med <= 14:
        return _hz("SHORT", round(med), src)
    if med <= 45:
        return _hz("SWING", round(med), src)
    return _hz("POSITION", round(med), src)


def days_to(datestr):
    if not datestr:
        return None
    try:
        return (datetime.strptime(str(datestr)[:10], "%Y-%m-%d").date() - date.today()).days
    except ValueError:
        return None


def build_scan(record=True):
    """record=False: a scan made outside the session only to fill an empty page;
    it is not written to the history and never notifies."""
    universe = fetch_universe()
    if not universe:
        return {"alerts": [], "watch": [], "generated_at": now_iso(), "universe": 0}

    dark = fetch_darkpool()
    alerts_tape = fetch_flow_alerts()

    dp_agg = {}
    for p in dark:
        a = dp_agg.setdefault(p["ticker"], {"premium": 0.0, "count": 0, "wsum": 0.0})
        a["premium"] += p["premium"]
        a["count"] += 1
        a["wsum"] += p["lean"] * p["premium"]
    for a in dp_agg.values():
        a["lean"] = safe_div(a["wsum"], a["premium"])

    alerts_by_ticker = {}
    for a in alerts_tape:
        alerts_by_ticker.setdefault(a["ticker"], []).append(a)

    # --- Stage 1: cheap components across the whole universe -----------------
    prelim = []
    for r in universe:
        tkr = (r.get("ticker") or "").upper()
        if not tkr:
            continue
        close = num(r.get("close"))
        mcap = num(r.get("marketcap"))
        issue = r.get("issue_type") or ""
        if close < CFG["min_price"]:
            continue
        if issue != "ETF" and mcap < CFG["min_marketcap"]:
            continue

        f, fd = score_flow(r)
        o, od = score_oi(r)
        d, dd = score_dark(r, dp_agg)
        rough = CFG["w_flow"] * f + CFG["w_oi"] * o + CFG["w_dark"] * d
        prelim.append({
            "row": r, "ticker": tkr, "rough": rough,
            "f": f, "fd": fd, "o": o, "od": od, "d": d, "dd": dd,
        })

    prelim.sort(key=lambda x: abs(x["rough"]), reverse=True)
    head = prelim[: CFG["enrich_top"]]

    # --- Stage 2: per-ticker GEX + technicals, in parallel -------------------
    def enrich(item):
        tkr = item["ticker"]
        try:
            item["gex"] = fetch_gex(tkr)
        except Exception:
            item["gex"] = None
        try:
            item["tech"] = fetch_technicals(tkr)
        except Exception:
            item["tech"] = None
        return item

    with ThreadPoolExecutor(max_workers=6) as pool:
        head = list(pool.map(enrich, head))

    out = []
    for item in head:
        r, tkr = item["row"], item["ticker"]
        g, gd = score_gamma(r, item.get("gex"))
        t, td = score_tech(r, item.get("tech"))

        comps = {
            "flow": {"value": item["f"], "weight": CFG["w_flow"], "detail": item["fd"]},
            "oi": {"value": item["o"], "weight": CFG["w_oi"], "detail": item["od"]},
            "dark": {"value": item["d"], "weight": CFG["w_dark"], "detail": item["dd"]},
            "gamma": {"value": g, "weight": CFG["w_gamma"], "detail": gd},
            "tech": {"value": t, "weight": CFG["w_tech"], "detail": td},
        }
        raw = sum(c["value"] * c["weight"] for c in comps.values())

        # --- Penalties -------------------------------------------------------
        penalties = []
        mult = 1.0
        er_days = days_to(r.get("next_earnings_date"))
        iv_rank = num(r.get("iv_rank"))
        rvol = num(r.get("relative_volume"), 1.0)

        if iv_rank > CFG["high_iv_rank"]:
            mult *= CFG["high_iv_haircut"]
            penalties.append(f"IV rank {iv_rank:.0f} - premium is rich")
        if rvol < CFG["thin_rvol"]:
            mult *= CFG["thin_haircut"]
            penalties.append(f"Relative volume {rvol:.2f} - thin tape")

        spot = num(r.get("close"))
        a14 = (item.get("tech") or {}).get("atr14") or (spot * 0.02)
        direction = 1 if raw >= 0 else -1

        gex = item.get("gex") or {}
        cw, pw = gex.get("call_wall"), gex.get("put_wall")

        # Gamma walls make good targets/stops, but only when they sit at a
        # plausible swing distance. A wall parked on top of spot yields a
        # near-zero risk leg and a fake-attractive reward:risk; a wall far
        # up the chain is not a swing target at all. Bound both, then floor
        # the risk leg at half an ATR so the ratio can't be gamed by geometry.
        # Leg geometry. A swing stop inside 1 ATR is just noise; a target inside
        # 1.5 ATR isn't worth the risk; a target past 6 ATR (or 25% of spot)
        # isn't reachable inside a swing horizon.
        MIN_STOP = 1.0 * a14
        MIN_TGT = 1.5 * a14
        MAX_LEG = min(0.25 * spot, 6.0 * a14)

        # If one ATR is wider than the whole tradeable band, the stock's daily
        # noise exceeds the trade. That is not a swing candidate at any score.
        too_volatile = MIN_STOP > 0.25 * spot
        if too_volatile:
            penalties.append(
                f"ATR ${a14:.2f} is {a14 / spot * 100:.0f}% of price - "
                f"daily range exceeds the trade band")

        def usable(level, lo, hi):
            return level is not None and lo <= level <= hi

        def bound(level, lo, hi):
            return max(min(level, hi), lo) if lo <= hi else lo

        if direction > 0:
            target = cw if usable(cw, spot + MIN_TGT, spot + MAX_LEG) \
                else spot + 2.5 * a14
            stop = pw if usable(pw, spot - MAX_LEG, spot - MIN_STOP) \
                else spot - 1.5 * a14
            # Bound both legs regardless of source: the ATR fallback overshoots
            # on low-priced high-vol names, and a wall can sit right on spot.
            target = bound(target, spot + MIN_TGT, spot + MAX_LEG)
            stop = bound(stop, spot - MAX_LEG, spot - MIN_STOP)
        else:
            target = pw if usable(pw, spot - MAX_LEG, spot - MIN_TGT) \
                else spot - 2.5 * a14
            stop = cw if usable(cw, spot + MIN_STOP, spot + MAX_LEG) \
                else spot + 1.5 * a14
            target = bound(target, spot - MAX_LEG, spot - MIN_TGT)
            stop = bound(stop, spot + MIN_STOP, spot + MAX_LEG)

        reward = abs(target - spot)
        risk = max(abs(spot - stop), MIN_STOP)
        rr = min(safe_div(reward, risk), 6.0)

        hz = horizon_for(tkr, alerts_by_ticker, spot, target, a14)
        if er_days is not None and 0 <= er_days <= (hz["days"] or 30):
            mult *= CFG["earnings_haircut"]
            penalties.append(f"Earnings in {er_days}d - binary event inside the horizon")

        score = raw * mult
        # Which signals carried it: real size (agree_threshold) AND the score's sign,
        # biggest contribution first. The count is only an internal gate; the page
        # and Discord name the signals instead of showing "N of 5 agree".
        drivers = [k for k, c in sorted(comps.items(),
                                        key=lambda kv: -abs(kv[1]["value"] * kv[1]["weight"]))
                   if abs(c["value"]) > CFG["agree_threshold"] and (c["value"] > 0) == (score > 0)]
        agreeing = len(drivers)

        out.append({
            "ticker": tkr,
            "name": r.get("full_name") or tkr,
            "sector": r.get("sector"),
            "issue_type": r.get("issue_type"),
            "score": round(score, 1),
            "raw_score": round(raw, 1),
            "direction": "BULL" if direction > 0 else "BEAR",
            "agreeing": agreeing,
            "drivers": drivers,
            "components": {k: {"value": round(v["value"], 3),
                               "weight": v["weight"],
                               "contribution": round(v["value"] * v["weight"], 1),
                               "detail": v["detail"]}
                           for k, v in comps.items()},
            "penalties": penalties,
            "too_volatile": too_volatile,
            "horizon": hz,
            "price": spot,
            "change_pct": safe_div(spot - num(r.get("prev_close")), num(r.get("prev_close"))) * 100,
            "levels": {
                "entry_low": round(spot - 0.25 * a14, 2),
                "entry_high": round(spot + 0.25 * a14, 2),
                "stop": round(stop, 2),
                "target": round(target, 2),
                "atr14": round(a14, 2),
                "rr": round(rr, 2),
                "call_wall": cw,
                "put_wall": pw,
                "gamma_flip": gex.get("gamma_flip"),
            },
            "context": {
                "iv_rank": round(iv_rank, 1),
                "iv30d": num(r.get("iv30d")),
                "realized_vol": num(r.get("realized_volatility")),
                "relative_volume": round(rvol, 2),
                "marketcap": num(r.get("marketcap")),
                "earnings_date": r.get("next_earnings_date"),
                "earnings_in": er_days,
                "put_call_ratio": num(r.get("put_call_ratio")),
                "week_52_high": num(r.get("week_52_high")),
                "week_52_low": num(r.get("week_52_low")),
            },
            "flow_alerts": sorted(alerts_by_ticker.get(tkr, []),
                                  key=lambda a: a["premium"], reverse=True)[:6],
            "dark_prints": sorted([p for p in dark if p["ticker"] == tkr],
                                  key=lambda p: p["premium"], reverse=True)[:6],
            "spark": (item.get("tech") or {}).get("spark") or [],
        })

    out.sort(key=lambda x: abs(x["score"]), reverse=True)

    def qualifies(a):
        return (abs(a["score"]) >= CFG["min_score"]
                and a["agreeing"] >= CFG["min_agreeing"]
                and a["levels"]["rr"] >= CFG["min_rr"]
                and not a["too_volatile"])

    alerts = [a for a in out if qualifies(a)]
    watch = [a for a in out if not qualifies(a)]

    # Record this scan so alerts gain a history, then attach it for the UI.
    persistence = {}
    if record:
        try:
            persistence = STORE.record_scan(alerts, watch, len(prelim))
            for a in alerts + watch:
                a["persistence"] = persistence.get(a["ticker"])
        except Exception as e:
            print(f"  [store] {type(e).__name__}: {e}")

        # Notify on high-conviction setups that have actually held.
        try:
            DISCORD.process(alerts, persistence)
            DISCORD.maybe_daily_wrap()
        except Exception as e:
            print(f"  [discord] {type(e).__name__}: {e}")

    return {
        "alerts": alerts,
        "watch": watch,
        "generated_at": now_iso(),
        "universe": len(prelim),
        "enriched": len(head),
        "recorded": record,
        "config": CFG,
        "notify": {"enabled": DISCORD.enabled, **NOTIFY},
    }


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCAN_LOCK = threading.Lock()
SCAN_STATE = {"session": None, "reason": "", "loop": False}


def run_scan(record=True):
    """One scan at a time, whoever asks (the loop or a page with nothing cached)."""
    with SCAN_LOCK:
        val = build_scan(record=record)
        CACHE.put("scan", val)
        return val


def scan_loop(sleep=time.sleep, now=None, once=False):
    """Scans on its own during the session, so history, Discord and the daily wrap
    no longer depend on a browser tab. Before 2026-09-25 scans were pull-driven:
    the desk recorded 3 trading days in three weeks, and scanned at 2am when a
    page happened to be open."""
    SCAN_STATE["loop"] = True
    while True:
        st, why = session_state(now() if now else None, CFG["scan_start"], CFG["scan_end"])
        SCAN_STATE.update(session=st, reason=why)
        if st == "open":
            try:
                run_scan(record=True)
            except Exception:
                traceback.print_exc()
            if once:
                return
            sleep(CFG["ttl_scan"])
        else:
            if once:
                return
            sleep(60)


def cached_scan():
    """The page reads what the loop produced. It only triggers a scan itself when
    nothing is cached yet (first start); outside the session that scan is shown
    but not recorded."""
    st, why = session_state(None, CFG["scan_start"], CFG["scan_end"])
    hit = CACHE.stale("scan")
    if hit is None:
        try:
            hit = run_scan(record=(st == "open"))
        except Exception as e:
            traceback.print_exc()
            return {"alerts": [], "watch": [], "error": str(e), "generated_at": now_iso(),
                    "market": {"open": st == "open", "reason": why}}
    return dict(hit, market={"open": st == "open", "reason": why,
                             "scan_start": CFG["scan_start"], "scan_end": CFG["scan_end"]})


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---- security guard (2026-09-26 review) -----------------------------------------
    # Runs before every request. Only this PC may talk to the desk (Host check: no DNS rebinding);
    # another website may open the page but never drive the API (Sec-Fetch-Site), and anything
    # that changes state must be same-origin JSON (a cross-site form can't send that).
    def parse_request(self):
        if not super().parse_request():
            return False
        why = self._uw_guard()
        if why:
            self.send_error(403, why)
            return False
        return True

    def _uw_guard(self):
        from urllib.parse import urlsplit
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in ("", "127.0.0.1", "localhost", "127.0.0.1:%d" % port, "localhost:%d" % port):
            return "local only"
        path = urlsplit(self.path).path
        if self.command in ("GET", "HEAD"):
            site = self.headers.get("Sec-Fetch-Site")
            if path.startswith("/api/") and site not in (None, "same-origin", "none"):
                return "same-site only"
            return None
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return "JSON only"
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
            return "same-origin only"
        return None

    def end_headers(self):
        # the dashboard (any local port) may frame this desk; no other site may (clickjacking)
        self.send_header("Content-Security-Policy", "frame-ancestors 'self' http://127.0.0.1:* http://localhost:*")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                fn = os.path.join(HERE, "index.html")
                with open(fn, "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")

            if path == "/api/health":
                with FEED_LOCK:
                    feeds = dict(FEED_STATUS)
                return self._send(200, {
                    "token": bool(TOKEN),
                    "feeds": feeds,
                    "config": CFG,
                    "discord": {"enabled": DISCORD.enabled, **NOTIFY},
                    "history": STORE.stats(),
                    "session": dict(SCAN_STATE),
                    "server_time": now_iso(),
                })

            if path == "/api/history":
                return self._send(200, STORE.stats())

            if path == "/api/notify/test":
                if not DISCORD.enabled:
                    return self._send(200, {
                        "ok": False,
                        "error": "No DISCORD_WEBHOOK_URL set. Add it to your .env file "
                                 "and restart, then try again."})
                return self._send(200, {"ok": DISCORD.test()})

            if path == "/api/scan":
                return self._send(200, cached_scan())

            if path == "/api/tape":
                return self._send(200, {
                    "flow_alerts": fetch_flow_alerts()[:60],
                    "dark_pool": fetch_darkpool()[:60],
                    "generated_at": now_iso(),
                })

            if path == "/api/tide":
                return self._send(200, fetch_tide())

            m = re.match(r"^/api/ticker/([A-Za-z][A-Za-z0-9.\-:]{0,11})$", path)
            if m:
                tkr = m.group(1).upper()
                return self._send(200, {
                    "ticker": tkr,
                    "gex": fetch_gex(tkr),
                    "technicals": fetch_technicals(tkr),
                    "flow_alerts": [a for a in fetch_flow_alerts() if a["ticker"] == tkr][:25],
                    "dark_pool": [p for p in fetch_darkpool() if p["ticker"] == tkr][:25],
                })

            return self._send(404, {"error": "not found", "path": path})
        except FileNotFoundError:
            return self._send(500, {"error": "index.html is missing next to server.py"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})


def preflight():
    """Verify the token against the live API before serving, so a bad setup is
    obvious here rather than showing up as an empty dashboard."""
    if not TOKEN:
        print("\n  [X] No Unusual Whales API token found.\n")
        print("      Either:  export UW_API_TOKEN='your-token'")
        print(f"      Or put:  UW_API_TOKEN=your-token   in {os.path.join(HERE, '.env')}")
        print("\n      Your token is under Settings -> API on unusualwhales.com.")
        print("      (API access is a paid add-on, separate from the site subscription.)")
        print("\n      Starting anyway so you can see the UI, but every panel will be empty.\n")
        return
    print(f"\n  Checking token (...{TOKEN[-4:]}) against the API ", end="", flush=True)
    try:
        payload = uw_get("/screener/stocks", {"min_volume": 50000, "order": "premium"}, timeout=20)
        n = len(rows(payload))
        print(f"-> OK, {n} tickers returned.")
    except UWError as e:
        msg = str(e)
        print("-> FAILED.\n")
        if "HTTP 401" in msg or "HTTP 403" in msg:
            print("  [X] The API rejected this token (401/403).")
            print("      It's mistyped, expired, or the account lacks API access.")
            print("      Check Settings -> API on unusualwhales.com.")
        elif "HTTP 429" in msg:
            print("  [!] Rate limited (429). The token works; you're over quota right now.")
        else:
            print(f"  [X] Could not reach the API: {msg[:200]}")
            print("      Check your network connection.")
        print("\n      Starting anyway; the dashboard will show this same diagnosis.\n")


def wrap_watcher():
    """The daily wrap shouldn't depend on a scan happening right after 4pm ET."""
    while True:
        time.sleep(60)
        try:
            DISCORD.maybe_daily_wrap()
        except Exception:
            pass


def main():
    preflight()

    if DISCORD.enabled:
        print(f"  Discord: connected. Posting setups scoring {NOTIFY['min_score']:.0f}+ "
              f"with {NOTIFY['min_agreeing']} signals pointing the same way,")
        print(f"           held {NOTIFY['min_streak']} scans; daily wrap at "
              f"{NOTIFY['wrap_hour_et']}:00 ET.")
    else:
        print("  Discord: not configured (add DISCORD_WEBHOOK_URL to .env to enable).")
    st = STORE.stats()
    print(f"  History: {st['scans']} scans recorded, {st['notifications']} alerts posted.")

    threading.Thread(target=wrap_watcher, daemon=True).start()
    threading.Thread(target=scan_loop, daemon=True, name="scan").start()

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"\n  UW Swing Dashboard  ->  http://{HOST}:{PORT}")
    print("  Open that URL in a browser. Do NOT open index.html directly -")
    print("  the page and the data are served by this process.\n")
    print(f"  Universe {CFG['universe_size']} | enrich top {CFG['enrich_top']} "
          f"| alert gate |score| >= {CFG['min_score']} with >= {CFG['min_agreeing']} signals pointing the same way")
    print(f"  Scans on its own {CFG['scan_start']}-{CFG['scan_end']} ET on trading days, page open or not.")
    print("  Ctrl-C to stop.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
        srv.shutdown()


if __name__ == "__main__":
    main()
