"""
Ticker Lookup: today's SINGLE-LEG, likely-OPENING options flow for one ticker.

Source: /api/option-trades/flow-alerts (ticker_symbol, newer_than, older_than, limit<=200), the call the
dashboard already makes live for holding warnings. Paged backwards with older_than until the session start.
Everything is filtered HERE, not with the endpoint's boolean filters (they are include-flags that default to
true; see the desk notes).

Single leg  : has_singleleg and not has_multileg, one expiry, no multi-leg volume in the prints.
Opening     : Unusual Whales does not label a print "opening" until tomorrow's open interest confirms it.
              An alert counts as LIKELY opening when any of these holds, and the row says which:
                * size > OI    the alert alone is bigger than yesterday's open interest
                * vol > OI     the contract has traded more than its open interest today
                * UW opening   all_opening_trades (rare: UW sets it only when every print looks opening)
              Anything else could be someone closing, so it is left out (and counted, never hidden silently).
Side        : ask-side premium >= 60% of the total = bought; bid-side >= 60% = sold; else mixed.
              Ask + bid premium need not add up to the total (the gap can be 94%): under 25% coverage the side
              is "unknown" rather than guessed.
Lean        : bought calls / sold puts = bullish; bought puts / sold calls = bearish; mixed/unknown = neutral.
"""

import datetime as dt
import time

from events import num, rows, text

MAX_PAGES = 5            # 5 x 200 alerts; a mega-cap can print ~1,000 alerts in a session
PAGE = 200
SIDE_MIN = 0.60
COVER_MIN = 0.25


def _et_offset(utc):
    y = utc.year
    d = dt.date(y, 3, 1)
    start = dt.datetime.combine(d + dt.timedelta(days=(6 - d.weekday()) % 7 + 7), dt.time(7))
    d = dt.date(y, 11, 1)
    end = dt.datetime.combine(d + dt.timedelta(days=(6 - d.weekday()) % 7), dt.time(6))
    return 4 if start <= utc < end else 5


def session_start(now_utc=None):
    """(session date, UTC start of that session's 04:00 ET) -- today, or the last weekday before it."""
    now_utc = now_utc or dt.datetime.utcnow()
    et = now_utc - dt.timedelta(hours=_et_offset(now_utc))
    day = et.date()
    if et.hour < 4:
        day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    local = dt.datetime.combine(day, dt.time(4))
    return day, local + dt.timedelta(hours=_et_offset(local + dt.timedelta(hours=5)))


def is_single_leg(a):
    if a.get("has_multileg"):
        return False
    if a.get("has_singleleg") is False:
        return False
    if (num(a.get("expiry_count")) or 1) > 1:
        return False
    if (num(a.get("multi_vol")) or 0) > 0:
        return False
    return True


def opening_reasons(a):
    oi = num(a.get("open_interest"))
    size, vol = num(a.get("total_size")) or 0, num(a.get("volume")) or 0
    out = []
    if oi is not None and size > oi:
        out.append("size > OI")
    if oi is not None and vol > oi:
        out.append("vol > OI")
    if a.get("all_opening_trades"):
        out.append("UW opening")
    return out


def side_of(a):
    total = num(a.get("total_premium")) or 0
    ask, bid = num(a.get("total_ask_side_prem")) or 0, num(a.get("total_bid_side_prem")) or 0
    if total <= 0 or ask + bid < COVER_MIN * total:
        return "unknown", None
    share = ask / total
    if share >= SIDE_MIN:
        return "bought", share
    if bid / total >= SIDE_MIN:
        return "sold", share
    return "mixed", share


def lean_of(kind, side):
    if side == "bought":
        return "bullish" if kind == "call" else "bearish"
    if side == "sold":
        return "bearish" if kind == "call" else "bullish"
    return "neutral"


def summarize(alerts, day, since_ms, now_ms=None):
    """Group single-leg, likely-opening alerts by contract. Pure: tested on fixtures."""
    now_ms = now_ms or time.time() * 1000
    seen, dropped = set(), {"multi_leg": 0, "not_opening": 0, "older": 0, "duplicate": 0}
    by = {}
    for a in alerts:
        start = num(a.get("start_time")) or num(a.get("executed_at")) or 0
        if start and start < since_ms:
            dropped["older"] += 1
            continue
        key = (a.get("option_chain"), a.get("start_time"))
        if key in seen:
            dropped["duplicate"] += 1                 # one contract's alerts can overlap (same start_time)
            continue
        seen.add(key)
        if not is_single_leg(a):
            dropped["multi_leg"] += 1
            continue
        why = opening_reasons(a)
        if not why:
            dropped["not_opening"] += 1
            continue
        kind = (text(a.get("type")) or "").lower()
        side, share = side_of(a)
        c = by.setdefault(a.get("option_chain"), {
            "contract": a.get("option_chain"), "type": kind, "strike": num(a.get("strike")),
            "expiry": text(a.get("expiry")), "oi": num(a.get("open_interest")), "volume": 0,
            "premium": 0.0, "size": 0, "ask_prem": 0.0, "bid_prem": 0.0, "alerts": 0,
            "sweep": False, "floor": False, "reasons": set(), "first": start, "last": start,
            "underlying": num(a.get("underlying_price")), "price": num(a.get("price"))})
        c["premium"] += num(a.get("total_premium")) or 0
        c["size"] += int(num(a.get("total_size")) or 0)
        c["ask_prem"] += num(a.get("total_ask_side_prem")) or 0
        c["bid_prem"] += num(a.get("total_bid_side_prem")) or 0
        c["volume"] = max(c["volume"], int(num(a.get("volume")) or 0))
        c["alerts"] += 1
        c["sweep"] = c["sweep"] or bool(a.get("has_sweep"))
        c["floor"] = c["floor"] or bool(a.get("has_floor"))
        c["reasons"].update(why)
        if start and start < c["first"]:
            c["first"] = start
        if start and start >= c["last"]:
            c["last"], c["price"], c["underlying"] = start, num(a.get("price")), num(a.get("underlying_price"))
    out = []
    for c in by.values():
        side, share = side_of({"total_premium": c["premium"], "total_ask_side_prem": c["ask_prem"],
                               "total_bid_side_prem": c["bid_prem"]})
        c["side"], c["ask_share"] = side, share
        c["lean"] = lean_of(c["type"], side)
        c["reasons"] = sorted(c["reasons"], key=["size > OI", "vol > OI", "UW opening"].index)
        try:
            c["dte"] = (dt.date.fromisoformat(c["expiry"]) - day).days if c["expiry"] else None
        except ValueError:
            c["dte"] = None
        out.append(c)
    out.sort(key=lambda c: -c["premium"])
    tot = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}
    for c in out:
        tot[c["lean"]] += c["premium"]
    directed = tot["bullish"] + tot["bearish"]
    return {"session": day.isoformat(), "contracts": out, "totals": tot,
            "lean": None if not directed else (tot["bullish"] - tot["bearish"]) / directed,
            "premium": sum(c["premium"] for c in out), "dropped": dropped}


def fetch(client, ticker, now_utc=None):
    """Page today's flow alerts for one ticker, newest first, back to the session start."""
    day, start = session_start(now_utc)
    since_ms = (start - dt.datetime(1970, 1, 1)).total_seconds() * 1000
    params = {"ticker_symbol": ticker, "newer_than": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": PAGE}
    got, pages, truncated = [], 0, False
    while pages < MAX_PAGES:
        batch = rows(client.get("/api/option-trades/flow-alerts", params)[1])
        pages += 1
        got.extend(batch)
        if len(batch) < PAGE:
            break
        oldest = min((num(r.get("start_time")) or since_ms) for r in batch)
        if oldest <= since_ms:
            break
        params = dict(params, older_than=dt.datetime.utcfromtimestamp(oldest / 1000.0).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        if pages == MAX_PAGES:
            truncated = True
    out = summarize(got, day, since_ms)
    out.update(ticker=ticker, alerts_read=len(got), pages=pages, truncated=truncated,
               fetched_at=time.time())
    return out
