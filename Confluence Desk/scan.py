"""
Confluence Desk -- the scan.

    ONE wide poll                     ->  candidates
    /api/insider/transactions            every company with material
    (4 calls for 90 days of the           open-market Form 4 buying
     entire market)

    ZERO calls                        ->  13F backdrop
    ../Institutional Desk/                read straight out of the sibling
    institutional.db                      desk's SQLite file

    ONE call per candidate            ->  dark pool
    /api/darkpool/{ticker}                block size, lean, sustain, proximity

Cost: roughly 4 + N calls, where N is the number of companies with material
insider buying in the window -- about 110 at 45 days, 225 at 90. At the 0.32s
throttle a full scan is 40-80 seconds and ~230 calls, and dark pool is cached
for CDESK_TTL_DARK seconds (default 30 min), so a desk left open all day costs
about 500 calls/hour. That is a third of what Swing Desk costs.

EVERY CANDIDATE IS ENRICHED -- there is deliberately no "top N only" slice.
Swing Desk enriches only its top 14, which means a ticker slipping out of that
slice silently loses its gamma and technical points and drops off the board:
an architecture artifact indistinguishable from a real fade. Here the dark
pool call is cheap enough that the whole candidate set gets one, so a card's
score only moves when the market moves.
"""

import datetime as dt
import threading
import time
import traceback

import darkpool
import idb
import insider
import score as scoring
import uw

CFG = {
    "window_days": uw.env_int("CDESK_WINDOW_DAYS", 45),
    "dark_window_sessions": uw.env_int("CDESK_DARK_SESSIONS", 10),
    "proximity_days": uw.env_int("CDESK_PROXIMITY_DAYS", 5),
    "min_print_premium": uw.env_num("CDESK_MIN_PRINT_PREMIUM", 50_000.0),
    "min_score": uw.env_num("CDESK_MIN_SCORE", 25.0),
    "max_candidates": uw.env_int("CDESK_MAX_CANDIDATES", 300),
    "ttl_dark": uw.env_num("CDESK_TTL_DARK", 1800.0),
    "ttl_insider": uw.env_num("CDESK_TTL_INSIDER", 600.0),
    "workers": uw.env_int("CDESK_WORKERS", 5),
    "require_institutional": uw.env_bool("CDESK_REQUIRE_INSTITUTIONAL", False),
}


class Scanner:
    def __init__(self, store, log=print, client=None):
        self.store = store
        self.log = log
        self.client = client or uw.Client()
        self.inst = idb.Institutional()
        self._dark_cache = {}
        self._insider_cache = None
        self._insider_at = 0.0
        self._lock = threading.Lock()
        self.last_result = None
        self.last_error = None
        self.stopping = False

    def stop(self):
        self.stopping = True

    # ------------------------------------------------------------- stage 1

    def candidates(self, force=False):
        """Market-wide Form 4 purchases, aggregated per company."""
        now = time.time()
        if (not force and self._insider_cache is not None
                and now - self._insider_at < CFG["ttl_insider"]):
            return self._insider_cache

        start = (dt.date.today() - dt.timedelta(days=CFG["window_days"])).isoformat()
        rows = insider.fetch_purchases(self.client, start)
        self.log("  insider: %d raw filings since %s" % (len(rows), start))
        aggregated = insider.aggregate(rows)
        self.log("  insider: %d companies clear the $%.0fk floor"
                 % (len(aggregated), insider.MIN_COMPANY_NOTIONAL / 1000.0))

        # Register filing ids so the notifier can tell a genuinely new filing
        # from one that has simply been in the window for three weeks.
        ids, owner = [], {}
        for ticker, agg in aggregated.items():
            for person in agg["people"]:
                key = "%s|%s|%s" % (ticker, person["cik"], person["last_date"])
                ids.append(key)
                owner[key] = ticker
        self._pending_filings = (ids, owner)

        self._insider_cache = aggregated
        self._insider_at = now
        return aggregated

    # ------------------------------------------------------------- stage 2

    def dark_for(self, ticker, insider_dates, marketcap, force=False):
        now = time.time()
        cached = self._dark_cache.get(ticker)
        if not force and cached and now - cached[0] < CFG["ttl_dark"]:
            rows = cached[1]
        else:
            try:
                rows = self.client.darkpool(
                    ticker, limit=200, min_premium=CFG["min_print_premium"])
            except RuntimeError as exc:
                self.log("  darkpool %s failed: %s" % (ticker, exc))
                rows = cached[1] if cached else []
            else:
                self._dark_cache[ticker] = (now, rows)
        return darkpool.aggregate(
            rows,
            marketcap=marketcap,
            insider_dates=insider_dates,
            window_days=CFG["dark_window_sessions"],
            proximity_days=CFG["proximity_days"],
        )

    # --------------------------------------------------------------- scan

    def run(self, force=False, deadline=None):
        baseline = self.store.is_first_scan()
        scan_id = self.store.start_scan(baseline=baseline)
        started = time.time()
        calls_before = self.client.calls
        error = None
        cards = []

        try:
            aggregated = self.candidates(force=force)
            ranked = sorted(aggregated.values(), key=lambda a: -a["notional"])
            if CFG["require_institutional"]:
                ranked = [a for a in ranked if self.inst.get(a["ticker"])]
            ranked = ranked[: CFG["max_candidates"]]

            results = [None] * len(ranked)
            index = {"i": 0}
            lock = threading.Lock()

            def worker():
                while not self.stopping:
                    with lock:
                        i = index["i"]
                        if i >= len(ranked):
                            return
                        index["i"] = i + 1
                    if deadline and time.time() > deadline:
                        return
                    agg = ranked[i]
                    try:
                        results[i] = self.dark_for(
                            agg["ticker"], agg["dates"], agg["marketcap"], force=force)
                    except Exception as exc:  # one bad ticker must not kill the scan
                        self.log("  darkpool %s: %s" % (agg["ticker"], exc))
                        results[i] = None

            threads = [threading.Thread(target=worker, daemon=True)
                       for _ in range(max(1, CFG["workers"]))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            for agg, dark in zip(ranked, results):
                inst = self.inst.get(agg["ticker"])
                composed = scoring.composite(agg, dark or {}, inst)
                if composed["score"] < CFG["min_score"]:
                    continue
                cards.append(_build_card(agg, dark, inst, composed))

            cards.sort(key=lambda c: -c["score"])
            self.store.record(scan_id, cards)
            self.store.bump(scan_id, cards)

            ids, owner = getattr(self, "_pending_filings", ([], {}))
            fresh = self.store.register_filings(ids, owner, baseline=baseline)
            fresh_tickers = {owner[f] for f in fresh if f in owner}
            for card in cards:
                card["has_new_filing"] = card["ticker"] in fresh_tickers

        except Exception as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
            self.last_error = error
            self.log("SCAN FAILED: %s\n%s" % (error, traceback.format_exc()))
        else:
            self.last_error = None

        api_calls = self.client.calls - calls_before
        self.store.finish_scan(scan_id, api_calls, len(cards), len(cards), error)

        result = {
            "scan_id": scan_id,
            "baseline": baseline,
            "cards": cards,
            "api_calls": api_calls,
            "seconds": round(time.time() - started, 1),
            "error": error,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "institutional": self.inst.coverage(),
            "config": dict(CFG),
            "distinct_days": self.store.distinct_days(),
            "best_ever": self.store.best_ever(),
        }
        # A failed scan must never blank the board -- the last good result
        # stays visible with the error banner on top of it.
        if error and self.last_result:
            self.last_result["error"] = error
            self.last_result["stale"] = True
            return self.last_result
        self.last_result = result
        return result


def _build_card(agg, dark, inst, composed):
    dark = dark or {}
    card = {
        "ticker": agg["ticker"],
        "sector": agg.get("sector"),
        "marketcap": agg.get("marketcap"),
        "quote": agg.get("quote"),
        "next_earnings_date": agg.get("next_earnings_date"),
        "score": composed["score"],
        "band": scoring.score_band(composed["score"]),
        "legs": composed["legs"],
        "carried_by": composed["carried_by"],
        "all_three": composed["all_three"],

        "distinct_buyers": agg["distinct_buyers"],
        "filings": agg["filings"],
        "notional": agg["notional"],
        "shares": agg["shares"],
        "avg_price": agg["avg_price"],
        "first_date": agg["first_date"],
        "last_date": agg["last_date"],
        "top_buyer": {
            "name": agg["top_buyer"]["name"],
            "title": agg["top_buyer"]["title"],
            # The parsed label and the RAW title both go on the card. Putting a
            # derived value next to its source is a free correctness check: the
            # Insider Desk's vice-president/president bug was caught exactly
            # this way, by a card reading "Top buyer: President" beside a raw
            # title of "Vice-President".
            "rank": round(agg["top_buyer"]["rank"], 2),
            "notional": agg["top_buyer"]["notional"],
            "new_stake": agg["top_buyer"]["new_stake"],
            "stake_pct": (None if agg["top_buyer"]["stake_pct"] is None
                          else round(agg["top_buyer"]["stake_pct"], 1)),
        },
        "people": [
            {
                "name": p["name"], "title": p["title"],
                "rank": round(p["rank"], 2), "shares": p["shares"],
                "notional": p["notional"], "filings": p["filings"],
                "stake_growth": round(p["stake_growth"], 3),
                # The true, unclamped growth for display. See insider.py.
                "stake_pct": (None if p["stake_pct"] is None
                              else round(p["stake_pct"], 1)),
                "new_stake": p["new_stake"],
                "first_date": p["first_date"].isoformat() if p["first_date"] else None,
                "last_date": p["last_date"].isoformat() if p["last_date"] else None,
            }
            for p in agg["people"]
        ],
        # DISPLAY ONLY. Never scored -- see the warning in score.py.
        "vs_fills_pct": agg.get("vs_fills_pct"),
        "price_flags": agg.get("price_flags") or [],
        "frac_10b5_1": agg["frac_10b5_1"],
        "frac_corporate": agg["frac_corporate"],

        "dark": {
            "print_count": dark.get("print_count", 0),
            "clean_count": dark.get("clean_count", 0),
            "window_notional": dark.get("window_notional", 0.0),
            "max_size_vs_avg30": dark.get("max_size_vs_avg30", 0.0),
            "max_size": dark.get("max_size", 0),
            "avg30_volume": dark.get("avg30_volume", 0.0),
            # pressure / ask_share / bid_share are what the card's lean row
            # actually reads. They were missing from this dict once and every
            # card silently rendered "not enough clean prints"; the smoke test
            # asserts they are here.
            "weighted_position": dark.get("weighted_position"),
            "pressure": dark.get("pressure"),
            "ask_share": dark.get("ask_share"),
            "bid_share": dark.get("bid_share"),
            "active_sessions": dark.get("active_sessions", 0),
            "window_sessions": dark.get("window_sessions", 0),
            "overlap_days": dark.get("overlap_days", 0),
            "testable_days": dark.get("testable_days", []),
            "sessions": dark.get("sessions", []),
            "top_prints": dark.get("top_prints", []),
            "latest_session": dark.get("latest_session"),
        },
        "inst": inst,
        "has_new_filing": False,
    }
    return card
