"""
The tape behind an alert: the individual option trades.

Two views, both read on demand (never during the scan) and cached for a minute:

  alert    GET /api/option-trades/flow-alerts/{id}
           "Returns the trades that made up the specific alert" (UW API docs).
           For RepeatedHits it is every transaction in the cluster; for a
           multi-leg alert it is every related leg.
  contract GET /api/option-contract/{OSI}/flow?limit=50[&date=YYYY-MM-DD]
           The contract's last 50 prints for the session, alert or not.

Row shape (verified through the MCP option-trades tool, 2026-09-25): executed_at,
price, size, premium, nbbo_bid, nbbo_ask, tags (ask_side / bid_side / mid_side /
no_side, bullish / bearish), report_flags (sweep / floor / cross), exchange,
upstream_condition_detail (mlet/mlat/... = multi-leg), underlying_price,
implied_volatility, delta, volume, open_interest, option_chain_id, canceled.
Numbers arrive as strings. The flow-alert detail envelope is NOT verified live
(no MCP tool reaches it), so rows are found by a breadth-first search for a list
of trade-shaped dicts rather than by a fixed key.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
OSI_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,5}\d{6}[CP]\d{8}\Z")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
MULTILEG_CODES = {"mlet", "mlat", "mlct", "mlft", "mesl", "masl", "mfsl", "tlet", "tlct", "tlft",
                  "tesl", "tasl", "tfsl", "tlat"}
TTL = 60.0
CONTRACT_LIMIT = 50


def num(x, default=None):
    if x is None or isinstance(x, bool):
        return default
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _is_trade(d):
    return isinstance(d, dict) and ("executed_at" in d or ("price" in d and "size" in d))


def find_trades(payload):
    """The first list of trade-shaped dicts anywhere in the payload (bounded BFS)."""
    queue, seen, n = [payload], set(), 0
    while queue and n < 400:
        node = queue.pop(0)
        n += 1
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, list):
            if node and all(isinstance(x, dict) for x in node[:5]) and any(_is_trade(x) for x in node[:5]):
                return [x for x in node if _is_trade(x)]
            queue.extend(x for x in node[:200] if isinstance(x, (dict, list)))
        elif isinstance(node, dict):
            queue.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return []


def parse_time(s):
    if not s:
        return None
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s / 1000.0 if s > 1e11 else s, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def et_offset(dt_utc):
    y = dt_utc.year

    def nth_sunday(month, n):
        d = datetime(y, month, 1, tzinfo=timezone.utc)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(days=7 * (n - 1))
    start = nth_sunday(3, 2).replace(hour=7)
    end = nth_sunday(11, 1).replace(hour=6)
    return -4 if start <= dt_utc < end else -5


def et_clock(dt_utc):
    """'13:16:05.760' in US Eastern -- fills inside one alert are milliseconds apart."""
    if dt_utc is None:
        return None
    et = dt_utc + timedelta(hours=et_offset(dt_utc))
    return et.strftime("%H:%M:%S.") + "%03d" % (et.microsecond // 1000)


def side_of(row):
    """ask / bid / mid / none. UW's own tag first; else price against the NBBO."""
    tags = [str(t).lower() for t in (row.get("tags") or [])]
    for side in ("ask", "bid", "mid"):
        if side + "_side" in tags:
            return side
    if "no_side" in tags:
        return "none"
    p, b, a = num(row.get("price")), num(row.get("nbbo_bid")), num(row.get("nbbo_ask"))
    if p is None or not a or not b:          # NBBO "0" happens on floor prints
        return "none"
    if p >= a:
        return "ask"
    if p <= b:
        return "bid"
    return "mid"


def flags_of(row):
    out = [str(f).lower() for f in (row.get("report_flags") or []) if f]
    code = str(row.get("upstream_condition_detail") or "").lower()
    if code in MULTILEG_CODES and "multileg" not in out:
        out.append("multileg")
    return out


def normalize(row):
    t = parse_time(row.get("executed_at"))
    size = num(row.get("size"), 0.0)
    price = num(row.get("price"))
    prem = num(row.get("premium"))
    if prem is None and price is not None:
        prem = price * size * 100
    return {
        "executed_at": t.isoformat() if t else None,
        "time_et": et_clock(t),
        "option": row.get("option_chain_id") or row.get("option_chain") or row.get("option_symbol"),
        "contracts": int(size),
        "price": price,
        "bid": num(row.get("nbbo_bid")),
        "ask": num(row.get("nbbo_ask")),
        "side": side_of(row),
        "premium": prem,
        "exchange": row.get("exchange"),
        "flags": flags_of(row),
        "underlying": num(row.get("underlying_price")),
        "iv": num(row.get("implied_volatility")),
        "delta": num(row.get("delta")),
        "volume": num(row.get("volume")),
        "oi": num(row.get("open_interest")),
        "canceled": bool(row.get("canceled")),
    }


def summarize(trades):
    live = [t for t in trades if not t["canceled"]]
    prem = sum(t["premium"] or 0 for t in live)
    ctr = sum(t["contracts"] for t in live)
    by_side = {}
    for t in live:
        by_side[t["side"]] = by_side.get(t["side"], 0.0) + (t["premium"] or 0)
    times = sorted(t["executed_at"] for t in live if t["executed_at"])
    vwap = (sum((t["price"] or 0) * t["contracts"] for t in live) / ctr) if ctr else None
    return {
        "trades": len(live),
        "canceled": len(trades) - len(live),
        "contracts": ctr,
        "premium": round(prem, 2),
        "ask_share": round(by_side.get("ask", 0.0) / prem, 4) if prem else None,
        "premium_by_side": {k: round(v, 2) for k, v in by_side.items()},
        "sweeps": sum(1 for t in live if "sweep" in t["flags"]),
        "vwap": round(vwap, 4) if vwap is not None else None,
        "first": times[0] if times else None,
        "last": times[-1] if times else None,
        "options": sorted({t["option"] for t in live if t["option"]}),
    }


def uw_link(ticker):
    """The ticker's live flow on unusualwhales.com (the page the MCP option-trades
    tool maps to, with its ticker_symbol filter)."""
    t = re.sub(r"[^A-Z0-9.]", "", str(ticker or "").upper())
    return "https://unusualwhales.com/live-options-flow?ticker_symbol=" + t if t else None


class Tape:
    def __init__(self, client, ttl=TTL, clock=time.time):
        self.client, self.ttl, self.clock = client, ttl, clock
        self.cache = {}
        self.lock = threading.Lock()

    def _get(self, key, path, params):
        now = self.clock()
        with self.lock:
            hit = self.cache.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        payload = self.client.get(path, params)
        with self.lock:
            self.cache[key] = (now, payload)
            if len(self.cache) > 200:
                for k in sorted(self.cache, key=lambda k: self.cache[k][0])[:50]:
                    del self.cache[k]
        return payload

    def lookup(self, alert=None, contract=None, date=None):
        """Returns (http_code, body). Input is validated before anything is fetched."""
        if alert:
            if not UUID_RE.match(alert):
                return 400, {"error": "alert must be a flow-alert id (a UUID)"}
            kind, key = "alert", alert
            path, params = "/api/option-trades/flow-alerts/%s" % alert, None
        elif contract:
            contract = contract.upper()
            if not OSI_RE.match(contract):
                return 400, {"error": "contract must be an OSI symbol like ACME261016C00200000"}
            if date and not DATE_RE.match(date):
                return 400, {"error": "date must be YYYY-MM-DD"}
            kind, key = "contract", contract
            path = "/api/option-contract/%s/flow" % contract
            params = {"limit": CONTRACT_LIMIT, "date": date or None}
        else:
            return 400, {"error": "send ?alert=<id> or ?contract=<OSI symbol>"}
        try:
            payload = self._get((kind, key, date), path, params)
        except Exception as exc:          # noqa: BLE001 -- reported, never fatal
            code = getattr(exc, "code", None)
            msg = ("UW answered HTTP %s" % code) if code else ("%s" % exc.__class__.__name__)
            return 502, {"kind": kind, "key": key, "error": msg + " on " + path, "trades": [],
                         "summary": summarize([])}
        rows = [normalize(r) for r in find_trades(payload)]
        rows.sort(key=lambda t: t["executed_at"] or "", reverse=(kind == "contract"))
        return 200, {"kind": kind, "key": key, "source": path, "trades": rows,
                     "summary": summarize(rows),
                     "note": (None if rows else
                              "UW returned no trades for this %s%s." % (kind, " on that date" if date else ""))}
