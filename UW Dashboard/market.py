"""
Market data the dashboard needs beyond Today's Events: a live quote, the next
earnings date, warnings on the positions you hold, sector ETFs, market tide and
daily closes. Every path below was looked up in the UW public API docs or is
already used live by a sibling desk (Valuation Desk uw.py) -- none invented:

  /api/stock/{t}/info                 quote + next_earnings_date (Valuation Desk)
  /api/stock/{t}/ohlc/{1m|1d}         candles; DAILY oldest first, INTRADAY newest first;
                                      1d interleaves extended hours -> keep market_time "r"
  /api/stock/{t}/earnings             newest row is the NEXT report (reported_eps null)
  /api/insider/transactions           ticker_symbol, transaction_codes[], start_date, limit
  /api/option-trades/flow-alerts      ticker_symbol, newer_than, limit<=200
  /api/market/sector-etfs             SPY + the 11 SPDR sectors, today
  /api/market/market-tide             net call / net put premium, 5-min

Landmines handled here (see the desk notes):
  * insider: the same grouped filing can arrive twice -> de-dupe on sorted ids;
    Form 144 rows are NOTICES of a proposed sale, not sales -> formtype 4/4A only;
    10b5-1 plan sales are scheduled, not a view -> counted separately, not warned on.
  * flow alerts: ask + bid premium need not reconcile to total_premium -> measure
    against the total and treat < 25% coverage as unmeasured; overlapping alerts
    for one contract share a start_time -> de-dupe on (option_chain, start_time).
  * next_earnings_date can be in the past or null -> only 0 <= days counts.
"""

import datetime as dt
import re
import threading
import time

from events import num, rows, text

PRICE_FIELDS = ("price", "last", "last_price", "close", "prev_close")

# Warning thresholds (named so the page can print them next to the warning).
EARNINGS_DAYS = 7              # warn when a holding reports within this many days
INSIDER_DAYS = 30
INSIDER_MIN_USD = 500_000      # non-plan open-market sales, summed, in the window
FLOW_DAYS = 3
FLOW_MIN_PUT_USD = 250_000     # put premium bought on the ask
FLOW_PUT_CALL_RATIO = 1.5      # ... and at least this much more than call premium bought


def pick_price(row):
    if not isinstance(row, dict):
        return None
    for k in PRICE_FIELDS:
        v = num(row.get(k))
        if v and v > 0:
            return v
    return None


def info_of(payload):
    """/info: may be wrapped {"data": {...}, "price": ...} or bare."""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    out = dict(inner)
    if "price" in payload and "price" not in out:
        out["price"] = payload["price"]
    return out


def days_until(day, today=None):
    try:
        d = dt.date.fromisoformat(str(day)[:10])
    except ValueError:
        return None
    return (d - (today or dt.date.today())).days


def next_earnings(info=None, earnings_rows=(), extra_rows=(), today=None):
    """First upcoming report date (0 <= days), with its session if known."""
    today = today or dt.date.today()
    cands = []
    if info:
        cands.append((info.get("next_earnings_date"), info.get("next_earnings_time") or info.get("er_time")))
    for r in earnings_rows or ():
        if num(r.get("reported_eps")) is None:
            cands.append((r.get("report_date"), r.get("report_time")))
    for r in extra_rows or ():
        cands.append((r.get("next_earnings_date"), r.get("er_time")))
    best = None
    for d, t in cands:
        n = days_until(d, today) if d else None
        if n is None or n < 0:
            continue
        if best is None or n < best[1]:
            best = (str(d)[:10], n, text(t) if text(t) not in ("unknown",) else None)
    return {"date": best[0], "days": best[1], "time": best[2]} if best else None


def insider_selling(raw, today=None, days=INSIDER_DAYS):
    """Open-market sales in the window, split plan / non-plan. Form 144 notices ignored."""
    today = today or dt.date.today()
    seen, plan, nonplan = set(), [], []
    for r in raw:
        key = tuple(sorted(r.get("ids") or [])) or (r.get("id"),)
        if key in seen:
            continue
        seen.add(key)
        if (r.get("transaction_code") or "").upper() != "S":
            continue
        if str(r.get("formtype") or "").upper() not in ("4", "4/A"):
            continue
        n = days_until(r.get("transaction_date"), today)
        if n is None or n > 0 or -n > days:
            continue
        shares = abs(num(r.get("amount")) or 0)
        px = num(r.get("price"))
        q = num(r.get("stock_price"))
        if not px or (q and not (0.2 < px / q < 5)):       # a 110x-wrong fill was seen live
            px = q
        usd = shares * (px or 0)
        role = ("10% owner" if r.get("is_ten_percent_owner") else
                r.get("officer_title") or ("Director" if r.get("is_director") else
                                           "Officer" if r.get("is_officer") else "Insider"))
        item = {"name": text(r.get("owner_name")) or "?", "role": role, "usd": usd,
                "date": str(r.get("transaction_date"))[:10]}
        (plan if r.get("is_10b5_1") else nonplan).append(item)
    agg = {}
    for x in nonplan:
        a = agg.setdefault(x["name"], dict(x, usd=0.0))
        a["usd"] += x["usd"]
        a["date"] = max(a["date"], x["date"])
    sellers = sorted(agg.values(), key=lambda a: -a["usd"])
    return {"nonplan_usd": sum(x["usd"] for x in nonplan), "plan_usd": sum(x["usd"] for x in plan),
            "sellers": sellers[:3], "days": days}


def put_flow(raw, days=FLOW_DAYS, now_ms=None):
    """Premium bought on the ask, puts vs calls, over the last few sessions."""
    now_ms = now_ms or time.time() * 1000
    seen = set()
    out = {"put_ask": 0.0, "call_ask": 0.0, "alerts": 0, "unmeasured": 0, "days": days}
    for r in raw:
        start = num(r.get("start_time")) or 0
        if start and now_ms - start > days * 1.5 * 86400000:   # calendar slack for a weekend
            continue
        key = (r.get("option_chain"), r.get("start_time"))
        if key in seen:
            continue
        seen.add(key)
        total = num(r.get("total_premium")) or 0
        ask = num(r.get("total_ask_side_prem")) or 0
        bid = num(r.get("total_bid_side_prem")) or 0
        if total <= 0 or (ask + bid) < 0.25 * total:
            out["unmeasured"] += 1
            continue
        out["alerts"] += 1
        kind = (r.get("type") or "").lower()
        if kind == "put":
            out["put_ask"] += ask
        elif kind == "call":
            out["call_ask"] += ask
    return out


def warnings_for(ticker, info, earnings_rows, insider_rows, flow_rows, today=None, now_ms=None):
    """The three warnings for one holding. Each one says why, in numbers."""
    w = []
    er = next_earnings(info, earnings_rows, list(insider_rows or []) + list(flow_rows or []), today)
    if er and er["days"] <= EARNINGS_DAYS:
        when = "today" if er["days"] == 0 else "tomorrow" if er["days"] == 1 else "in %d days" % er["days"]
        w.append({"kind": "earnings", "level": "warn", "title": "Reports earnings %s" % when,
                  "detail": "%s%s" % (er["date"], " (%s)" % er["time"] if er["time"] else "")})
    ins = insider_selling(insider_rows or [], today)
    if ins["nonplan_usd"] >= INSIDER_MIN_USD:
        top = ", ".join("%s (%s) $%s" % (_name(s["name"]), s["role"], _short(s["usd"])) for s in ins["sellers"])
        w.append({"kind": "insider", "level": "serious",
                  "title": "Insiders sold $%s outside 10b5-1 plans in %d days" % (_short(ins["nonplan_usd"]), ins["days"]),
                  "detail": top})
    fl = put_flow(flow_rows or [], now_ms=now_ms)
    if fl["put_ask"] >= FLOW_MIN_PUT_USD and fl["put_ask"] >= FLOW_PUT_CALL_RATIO * fl["call_ask"]:
        w.append({"kind": "flow", "level": "serious",
                  "title": "Heavy put buying: $%s of puts bought on the ask in %d sessions" % (_short(fl["put_ask"]), fl["days"]),
                  "detail": "vs $%s of calls bought on the ask" % _short(fl["call_ask"])})
    return {"ticker": ticker, "warnings": w, "earnings": er, "insider": ins, "flow": fl}


def _name(n):
    """People's names arrive SHOUTED ("DOE JANE"); firms keep their capitals (L.P., LLC, SPV)."""
    if re.search(r"\b(L\.?P\.?|LLC|INC|CORP|FUND|TRUST|PARTNERS|SPV|HOLDINGS|GROUP|LTD|CAPITAL|MANAGEMENT)\b", n):
        return n
    return n.title()


def _short(v):
    v = float(v or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return ("%.1f" % (v / div)).rstrip("0").rstrip(".") + suf
    return "%d" % round(v)


def sector_rows(raw):
    out = []
    for r in raw:
        last, prev = num(r.get("last")), num(r.get("prev_close"))
        bull, bear = num(r.get("bullish_premium")), num(r.get("bearish_premium"))
        out.append({"ticker": text(r.get("ticker")), "name": text(r.get("full_name")),
                    "last": last, "chg": (last / prev - 1) if last and prev else None,
                    "bull": bull, "bear": bear,
                    "lean": ((bull - bear) / (bull + bear)) if bull is not None and bear is not None and (bull + bear) > 0 else None})
    return out


def tide_rows(raw):
    out = []
    for r in raw:
        out.append({"t": text(r.get("timestamp")), "call": num(r.get("net_call_premium")),
                    "put": num(r.get("net_put_premium")), "vol": num(r.get("net_volume"))})
    return [r for r in out if r["t"]]


class Market:
    """Cached UW reads. Failures keep the last good copy and say so."""

    def __init__(self, client):
        self.client = client
        self.cache = {}
        self.lock = threading.Lock()

    def _cached(self, key, ttl, fn):
        now = time.time()
        with self.lock:
            hit = self.cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        try:
            val = fn()
        except Exception as exc:          # noqa: BLE001
            if hit:
                v = dict(hit[1]) if isinstance(hit[1], dict) else hit[1]
                if isinstance(v, dict):
                    v["stale"] = str(exc)
                return v
            raise
        with self.lock:
            self.cache[key] = (now, val)
        return val

    # ---- per ticker
    def info(self, t):
        return self._cached(("info", t), 300, lambda: info_of(self.client.get("/api/stock/%s/info" % t)[1]))

    def quote(self, t):
        """(price, source). /info first, then the newest 1-minute candle, then the last daily bar."""
        def fetch():
            inf = self.info(t)
            p = pick_price(inf)
            if p:
                return {"price": p, "source": "UW quote", "name": inf.get("full_name") or inf.get("name"),
                        "next_earnings_date": inf.get("next_earnings_date")}
            bars = rows(self.client.get("/api/stock/%s/ohlc/1m" % t, timeframe="1D", limit=5)[1])
            if bars:
                bars.sort(key=lambda b: str(b.get("start_time") or ""))
                p = pick_price(bars[-1])
                if p:
                    return {"price": p, "source": "last 1-min candle", "name": inf.get("full_name")}
            daily = self.daily(t, "1M")
            if daily:
                return {"price": daily[-1]["close"], "source": "last daily close (%s)" % daily[-1]["date"],
                        "name": inf.get("full_name")}
            raise RuntimeError("No price for %s from UW." % t)
        return self._cached(("quote", t), 20, fetch)

    def daily(self, t, timeframe="6M"):
        """Regular-session daily closes, oldest first: [{"date","close"}]."""
        def fetch():
            raw = rows(self.client.get("/api/stock/%s/ohlc/1d" % t, timeframe=timeframe)[1])
            out = []
            for r in raw:
                if (r.get("market_time") or "r") != "r":
                    continue
                d = text(r.get("date")) or text(r.get("start_time"))
                c = num(r.get("close"))
                if d and c:
                    out.append({"date": d[:10], "close": c})
            out.sort(key=lambda b: b["date"])
            return out
        return self._cached(("daily", t, timeframe), 3600, fetch)

    def holding(self, t, today=None):
        def fetch():
            inf = {}
            try:
                inf = self.info(t)
            except Exception:         # noqa: BLE001 -- optional
                pass
            try:
                er = rows(self.client.get("/api/stock/%s/earnings" % t)[1])
            except Exception:         # noqa: BLE001
                er = []
            start = ((today or dt.date.today()) - dt.timedelta(days=INSIDER_DAYS + 5)).isoformat()
            ins = rows(self.client.get("/api/insider/transactions", {
                "ticker_symbol": t, "transaction_codes[]": ["S"], "start_date": start, "limit": 200})[1])
            newer = ((today or dt.date.today()) - dt.timedelta(days=FLOW_DAYS + 3)).isoformat()
            fl = rows(self.client.get("/api/option-trades/flow-alerts", {
                "ticker_symbol": t, "newer_than": newer, "limit": 200})[1])
            return warnings_for(t, inf, er, ins, fl, today)
        return self._cached(("holding", t), 1800, fetch)

    def opening_flow(self, t):
        """Today's single-leg, likely-opening flow (openflow.py). Cached a minute."""
        import openflow
        return self._cached(("openflow", t), 60, lambda: openflow.fetch(self.client, t))

    # ---- market wide
    def sectors(self):
        return self._cached(("sectors",), 120, lambda: sector_rows(rows(self.client.get("/api/market/sector-etfs")[1])))

    def tide(self):
        return self._cached(("tide",), 120, lambda: tide_rows(rows(self.client.get("/api/market/market-tide", interval_5m="true")[1])))


def diag(client, ticker="AAPL"):
    """Every endpoint this module calls: status, envelope, row count, first row's keys."""
    today = dt.date.today()
    checks = (
        ("/api/stock/%s/info" % ticker, {}),
        ("/api/stock/%s/ohlc/1d" % ticker, {"timeframe": "1M"}),
        ("/api/stock/%s/ohlc/1m" % ticker, {"timeframe": "1D", "limit": 5}),
        ("/api/stock/%s/earnings" % ticker, {}),
        ("/api/insider/transactions", {"ticker_symbol": ticker, "transaction_codes[]": ["S"],
                                       "start_date": (today - dt.timedelta(days=35)).isoformat(), "limit": 5}),
        ("/api/option-trades/flow-alerts", {"ticker_symbol": ticker, "limit": 5,
                                            "newer_than": (today - dt.timedelta(days=6)).isoformat()}),
        ("/api/market/sector-etfs", {}),
        ("/api/market/market-tide", {"interval_5m": "true"}),
    )
    out = []
    for path, params in checks:
        try:
            status, payload = client.get(path, params)
            rs = rows(payload)
            keys = sorted(rs[0].keys()) if rs else sorted(info_of(payload).keys()) if isinstance(payload, dict) else []
            out.append({"path": path, "ok": True, "status": status, "rows": len(rs), "first_keys": keys[:40]})
        except Exception as exc:      # noqa: BLE001
            out.append({"path": path, "ok": False, "error": str(exc)})
    return out
