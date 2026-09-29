"""Unusual-flow scanner: two wide REST polls per scan, joined by ticker.

Drops into the Confluence Desk folder beside server.py / store.py / insider.py.
Standard library only, per the no-install promise the five sibling desks make.

    from flowscan import FlowScan
    scan = FlowScan(token)
    board = scan.run()          # -> {"meta": {...}, "cards": [...]}

------------------------------------------------------------------ CALL BUDGET

Two calls per scan, both market-wide. Swing Desk's rule -- "fewer wide polls
beat many narrow polls" -- applies exactly here, and the arithmetic is what
makes a 5-minute cadence affordable:

    /api/option-activity/unusual        1-2 calls   contract-level snapshot
    /api/option-trades/flow-alerts      1 call      rule-level alerts

At 5 minutes that is ~3 calls/scan x 78 scans = **~234 calls per session**,
against the ~10,000/day the Swing Desk spends with its dashboard open. Nothing
here is per-ticker: both endpoints already carry every field the model reads.

EVERY candidate is scored -- there is no "top N" enrichment slice. Swing Desk
enriched only its top 14, so a ticker sliding out of the slice silently lost
points and dropped off the board, which is an architecture artifact that looks
exactly like a real fade. Confluence Desk fixed this by enriching everything;
this desk cannot regress because the wide polls already contain everything.

--------------------------------------------------------------- ENDPOINT NOTES

`/api/option-activity/unusual` is the Hottest Chains screener
(`/api/screener/option-contracts`) with the `unusual` preset pre-applied:
volume > OI, OTM, DTE <= 60, ask-side >= 50%, premium >= $10k, issue types
ADR/Common Stock/ETF. Contracts under 200 volume are never returned. Passing
any Hottest Chains filter overrides that piece of the preset.

`/api/option-trades/flow-alerts` has its own `unusual=true` preset, matching the
live-options-flow defaults. **It is REST-only** -- the MCP tool's schema has no
`unusual` argument at all, so a model built against the MCP surface will not
know the preset exists.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import flow

BASE = "https://api.unusualwhales.com"
UA = "uw-flow-desk/1.0 (+stdlib)"


# --------------------------------------------------------------------- CLIENT

def dig(payload, *keys):
    """Breadth-first search for the object or list that carries `keys`.

    GEX ES Desk shipping bug #2: every fetcher there was written against the
    shapes the MCP tools return, and the raw REST shapes differ -- the MCP
    passes envelopes through on some endpoints and unwraps them on others, so
    there is no single rule. Both flow endpoints answered `{"result": [...]}`
    live on 2026-09-11, but that is an observation, not a guarantee.
    """
    seen, queue = set(), [payload]
    scanned = 0
    while queue and scanned < 500:
        node = queue.pop(0)
        scanned += 1
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, list):
            if node and isinstance(node[0], dict) and any(k in node[0] for k in keys):
                return node
            queue.extend(node[:500])
        elif isinstance(node, dict):
            if any(k in node for k in keys):
                return node
            for v in node.values():
                if isinstance(v, (dict, list)):
                    queue.append(v)
    return None


class Client:
    def __init__(self, token, timeout=30, min_interval=0.25):
        self.token = token
        self.timeout = timeout
        self.min_interval = min_interval
        self._last = 0.0
        self._ctx = ssl.create_default_context()

    def get(self, path, params=None):
        # List params use repeated bracket notation: issue_types[]=Common Stock
        pairs = []
        for k, v in (params or {}).items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    pairs.append((k if k.endswith("[]") else k + "[]", item))
            elif isinstance(v, bool):
                pairs.append((k, "true" if v else "false"))
            elif v is not None:
                pairs.append((k, v))
        url = BASE + path + ("?" + urllib.parse.urlencode(pairs) if pairs else "")
        gap = self.min_interval - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json",
            "User-Agent": UA,
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=self._ctx) as r:
                body = r.read().decode("utf-8")
        finally:
            self._last = time.time()
        return json.loads(body)


# ------------------------------------------------------------------ THE SCAN

# Universe choice: whole market, equities only. Index and ETF flow is
# mostly hedging and would dominate every board.
ISSUE_TYPES = ["Common Stock"]

CONTRACT_PARAMS = {
    "unusual": True,          # volume>OI, OTM, DTE<=60, ask>=50%, premium>=$10k
    "issue_types": ISSUE_TYPES,
    "limit": 250,             # REST max is 250. The MCP tool caps at 50
                              # whatever you ask for -- they are not the same
                              # surface, and 50 is not the API's limit.
    "order": "premium",
    "order_direction": "desc",
}

ALERT_PARAMS = {
    "unusual": True,          # REST-only preset; absent from the MCP schema
    "issue_types": ISSUE_TYPES,
    "limit": 200,             # documented max for this endpoint
}


class FlowScan:
    def __init__(self, token, client=None, pages=2, min_premium=None):
        self.client = client or Client(token)
        self.pages = pages
        self.min_premium = min_premium
        self.warnings = []

    # -- fetch ------------------------------------------------------------
    def fetch_contracts(self):
        rows, self.warnings = [], []
        for page in range(self.pages):
            p = dict(CONTRACT_PARAMS)
            p["page"] = page          # page numbering starts at 0
            if self.min_premium:
                p["min_premium"] = int(self.min_premium)
            payload = self.client.get("/api/option-activity/unusual", p)
            got = dig(payload, "option_symbol", "ticker_symbol")
            if got is None:
                self.warnings.append({
                    "endpoint": "/api/option-activity/unusual",
                    "shape": _shape(payload),
                    "detail": "no rows carrying option_symbol / ticker_symbol",
                })
                break
            got = got if isinstance(got, list) else [got]
            rows.extend(got)
            if len(got) < p["limit"]:
                break
        return rows

    def fetch_alerts(self):
        payload = self.client.get("/api/option-trades/flow-alerts", ALERT_PARAMS)
        got = dig(payload, "option_chain", "alert_rule")
        if got is None:
            self.warnings.append({
                "endpoint": "/api/option-trades/flow-alerts",
                "shape": _shape(payload),
                "detail": "no rows carrying option_chain / alert_rule",
            })
            return []
        return got if isinstance(got, list) else [got]

    # -- score ------------------------------------------------------------
    def run(self, today=None, contracts=None, alerts=None):
        today = today or session_today()
        contracts = self.fetch_contracts() if contracts is None else contracts
        alerts = self.fetch_alerts() if alerts is None else alerts

        # De-dupe BEFORE grouping. One contract can carry a RepeatedHits alert
        # and a SweepsFollowedByFloor alert that contains it; summing both
        # books roughly double the real premium.
        alerts = flow.dedupe_alerts(alerts)

        by_ticker = {}
        for a in alerts:
            t = a.get("ticker")
            if t:
                by_ticker.setdefault(t, {"a": [], "c": []})["a"].append(a)
        for c in contracts:
            t = c.get("ticker_symbol")
            if t:
                by_ticker.setdefault(t, {"a": [], "c": []})["c"].append(c)

        cards = []
        for t, v in by_ticker.items():
            card = flow.build_card(t, v["a"], v["c"], today)
            card["lane"] = flow.lane(card)
            card["excluded"] = flow.exclusion_reason(card)
            card["sector"] = (v["c"][0].get("sector") if v["c"]
                              else (v["a"][0].get("sector") if v["a"] else None))
            cards.append(card)
        cards.sort(key=lambda c: -c["score"])

        counts = {ln: sum(1 for c in cards if c["lane"] == ln)
                  for ln in ("board", "spreads", "near", "out")}
        return {
            "meta": {
                "session": str(today),
                "scanned_at": datetime.now(timezone.utc).isoformat(),
                "tickers": len(cards),
                "alerts": len(alerts),
                "contracts": len(contracts),
                "gate": flow.ASK_GATE,
                "near_gate": flow.ASK_NEAR_GATE,
                "min_premium": flow.MIN_TICKER_PREMIUM,
                "weights": dict(flow.WEIGHTS),
                "counts": counts,
                "observed_max": max([c["score"] for c in cards] or [0.0]),
                "warnings": self.warnings,
            },
            "cards": cards,
        }


def _shape(payload):
    """Describe an unexpected payload without leaking its contents."""
    if isinstance(payload, dict):
        return "object keys: " + ", ".join(sorted(payload.keys())[:12])
    if isinstance(payload, list):
        return "list of %d" % len(payload)
    return type(payload).__name__


# ------------------------------------------------------------------ CALENDAR
# No tzdata on Windows, so the ET offset is implemented directly -- the same
# rule the Swing Desk and Confluence Desk use, verified across DST boundaries.

def et_offset(dt_utc):
    """-4 during US Eastern DST, -5 otherwise."""
    y = dt_utc.year
    mar = datetime(y, 3, 8, 7, 0, tzinfo=timezone.utc)
    while mar.weekday() != 6:
        mar += timedelta(days=1)
    nov = datetime(y, 11, 1, 6, 0, tzinfo=timezone.utc)
    while nov.weekday() != 6:
        nov += timedelta(days=1)
    return -4 if mar <= dt_utc < nov else -5


def et_now(now=None):
    now = now or datetime.now(timezone.utc)
    return now + timedelta(hours=et_offset(now))


def session_today(now=None):
    return et_now(now).date()


def market_open(now=None):
    """Regular US session, Mon-Fri 09:30-16:00 ET.

    The desk warms up for 20 minutes: ask-side percentages early in the session
    are computed over a handful of prints and swing wildly. Same reason the
    Swing Desk is not trusted before 10:00 and the GEX desk ships a 30-minute
    WARMUP.
    """
    t = et_now(now)
    if t.weekday() >= 5:
        return False
    mins = t.hour * 60 + t.minute
    return 9 * 60 + 50 <= mins <= 16 * 60


if __name__ == "__main__":
    import os
    import sys
    tok = os.environ.get("UW_TOKEN") or os.environ.get("UNUSUAL_WHALES_TOKEN")
    if not tok:
        print("set UW_TOKEN"); sys.exit(2)
    board = FlowScan(tok).run()
    m = board["meta"]
    print("session %s  %d tickers  %d alerts  %d contracts  %s"
          % (m["session"], m["tickers"], m["alerts"], m["contracts"], m["counts"]))
    for w in m["warnings"]:
        print("WARNING", w)
    for c in board["cards"][:12]:
        print("%-6s %5.1f  %-10s ask %s  bought $%s  %s"
              % (c["ticker"], c["score"], c["direction"] or "undirected",
                 "n/a" if c["ask_share"] is None else "%.0f%%" % (c["ask_share"] * 100),
                 format(int(c["ask_premium"]), ","), c["lane"]))
