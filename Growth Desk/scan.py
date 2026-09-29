"""
The Growth Leaders scan.

Daily, after the close (and at start-up when the last session was never scanned):
  1. universe  -- /api/screener/stocks, every common stock over $300M and $5, in pages of 500 (~6-8 calls)
  2. rank      -- relative strength (1-99) for every stock, industry groups by their members' median RS
  3. candidates-- RS 70+, price $10+, within 25% of the 52-week high. EVERY candidate is enriched (no top-N slice:
                  a name sliding out of a slice would lose points exactly like a real fade)
  4. enrich    -- per candidate: earnings (cached 3 days), 13F ownership (cached 7 days), 1 year of daily bars
  5. market    -- SPY and QQQ daily bars -> confirmed uptrend / under pressure / correction
  6. score     -- model.score_stock; each card is saved as it finishes, so a restart resumes the run

Intraday (09:50-16:00 ET, every 15 minutes): one screener call for the names near a pivot, a breakout check on the
live price and volume pace, an alert on the dashboard for each new breakout, and a Discord post when the breakout
is a Leader (all six stock checks pass) in a confirmed uptrend.
"""

import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import calendar_et as cal
import model as M

CANDIDATE_RS = 70
CANDIDATE_PRICE = 10.0
CANDIDATE_OFF_HIGH = -0.25
MAX_PAGES = 12
EARNINGS_TTL = 3 * 86400
OWNERSHIP_TTL = 7 * 86400
BARS_TTL = 20 * 3600
WATCH_STATES = ("near_pivot", "in_base", "weak_breakout", "breakout", "in_range")
WATCH_WITHIN = 0.08          # intraday watch: names within 8% of their pivot
WATCH_MIN_SCORE = 50


def bar_rows(raw):
    out = []
    for r in raw or []:
        d = str(r.get("date") or r.get("start_time") or "")[:10]
        c = M.num(r.get("close"))
        if not d or c is None:
            continue
        out.append({"date": d, "open": M.num(r.get("open")), "high": M.num(r.get("high")) or c,
                    "low": M.num(r.get("low")) or c, "close": c,
                    "volume": M.num(r.get("volume")) or M.num(r.get("total_volume"))})
    out.sort(key=lambda b: b["date"])
    dedup = {}
    for b in out:
        dedup[b["date"]] = b
    return list(dedup.values())


class Scanner:
    def __init__(self, client, store, log=print, notifier=None, workers=3):
        self.client, self.store, self.log, self.notifier = client, store, log, notifier
        self.workers = workers
        self.lock = threading.Lock()
        self.running = False
        self.progress = None
        self.last_error = None
        self.market = None
        self.ctx = None              # rs, groups, rows of the latest run (for /api/ticker)
        self.watch = {}              # ticker -> latest intraday reading

    def load_last(self):
        _, ctx, market = self.store.last_context()
        if ctx:
            self.ctx, self.market = ctx, market

    # ------------------------------------------------------------------ data with cache
    def _cached(self, key, ttl, fetch):
        hit = self.store.cache_get(key, ttl)
        if hit is not None:
            return hit
        val = fetch()
        self.store.cache_put(key, val)
        return val

    def earnings(self, t):
        return self._cached("earn:" + t, EARNINGS_TTL, lambda: [
            {k: r.get(k) for k in ("fiscal_date_ending", "report_type", "report_date", "reported_eps",
                                   "estimated_eps", "surprise_percentage")}
            for r in self.client.earnings(t) if str(r.get("report_type") or "quarterly") == "quarterly"][:24])

    def own(self, t):
        return self._cached("own:" + t, OWNERSHIP_TTL, lambda: [
            {k: r.get(k) for k in ("report_date", "units", "units_changed", "historical_units")}
            for r in self.client.ownership(t)])

    def bars(self, t, session):
        return self._cached("bars:%s:%s" % (t, session), BARS_TTL, lambda: bar_rows(self.client.ohlc_daily(t, "1Y")))

    # ------------------------------------------------------------------ universe + market
    def universe(self):
        rows, seen = [], set()
        for page in range(MAX_PAGES):
            got = self.client.screener_page(page)
            for r in got:
                t = r.get("ticker")
                if t and t not in seen and not r.get("is_index"):
                    seen.add(t)
                    rows.append(r)
            if len(got) < 500:
                break
        return rows

    def market_now(self, session):
        spy = self.bars("SPY", session)
        qqq = self.bars("QQQ", session)
        return M.market_state(spy, qqq)

    # ------------------------------------------------------------------ daily run
    def run(self, force=False):
        if not self.lock.acquire(blocking=False):
            return {"error": "a scan is already running"}
        self.running = True
        t0 = time.time()
        calls0 = self.client.calls
        try:
            et = cal.et_now()
            session = cal.last_session(et).isoformat()
            if cal.market_open(et, cal.OPEN_MIN):
                session = et.date().isoformat()          # intraday data: label it with today
            prev = None if force else self.store.interrupted(session)
            if prev:
                import json
                rid = prev["id"]
                ctx = json.loads(prev["ctx"])
                market = json.loads(prev["market"]) if prev.get("market") else self.market_now(session)
                self.store.resume_run(rid)
                self.log("resuming run %d for %s: %d of %d cards already done" % (
                    rid, session, len(self.store.done_tickers(rid)), len(ctx["cands"])))
            else:
                self.progress = {"step": "universe", "done": 0, "total": 0}
                rows = self.universe()
                if len(rows) < 50:
                    raise RuntimeError("the screener returned only %d stocks" % len(rows))
                rs = M.rs_ratings(rows)
                groups = M.group_ranks(rows, rs)
                cands = []
                for r in rows:
                    t, p, hi = r.get("ticker"), M.num(r.get("close")), M.num(r.get("week_52_high"))
                    if (rs.get(t, {}).get("rs") or 0) >= CANDIDATE_RS and p and p >= CANDIDATE_PRICE and hi \
                            and p / hi - 1 >= CANDIDATE_OFF_HIGH:
                        cands.append(t)
                keep = set(cands)
                slim = {r["ticker"]: {k: r.get(k) for k in SLIM} for r in rows if r.get("ticker") in keep}
                raws = sorted(x for x in (M.rs_raw(r)[0] for r in rows) if x is not None)
                ctx = {"rs": rs, "groups": groups, "rows": slim, "cands": cands, "universe": len(rows),
                       "raws": [round(x, 5) for x in raws]}
                self.progress = {"step": "market", "done": 0, "total": len(cands)}
                market = self.market_now(session)
                rid = self.store.start_run(session, len(rows), len(cands), market, ctx)
                self.log("run %d for %s: %d stocks ranked, %d candidates (RS %d+, $%.0f+, within %d%% of the high); "
                         "market: %s" % (rid, session, len(rows), len(cands), CANDIDATE_RS, CANDIDATE_PRICE,
                                          -100 * CANDIDATE_OFF_HIGH, market.get("label")))
            self.ctx, self.market = ctx, market
            todo = [t for t in ctx["cands"] if t not in self.store.done_tickers(rid)]
            done = len(ctx["cands"]) - len(todo)
            self.progress = {"step": "enrich", "done": done, "total": len(ctx["cands"]), "run": rid}
            errors = []

            def one(t):
                try:
                    card = self.card(t, ctx, market, session)
                    self.store.save_card(rid, card)
                except Exception as exc:          # noqa: BLE001 -- one bad ticker never stops the run
                    errors.append("%s: %s" % (t, exc))
                with self.lock_progress:
                    self.progress["done"] += 1

            self.lock_progress = threading.Lock()
            with ThreadPoolExecutor(self.workers) as ex:
                list(ex.map(one, todo))
            cards = self.store.cards(rid)
            for c in cards:
                self.maybe_alert(c, session, market, source="close")
            note = "%d errors (first: %s)" % (len(errors), errors[0]) if errors else None
            self.store.finish_run(rid, "done", self.client.calls - calls0, note)
            self.store.cache_prune()
            self.last_error = None
            self.log("run %d done: %d cards, %d leaders, %d API calls, %.0fs%s" % (
                rid, len(cards), sum(1 for c in cards if c.get("leader")), self.client.calls - calls0,
                time.time() - t0, ("; " + note) if note else ""))
            return {"run": rid, "cards": len(cards)}
        except Exception as exc:                  # noqa: BLE001
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self.log("scan failed: %s" % self.last_error)
            return {"error": self.last_error}
        finally:
            self.running = False
            self.progress = None
            self.lock.release()

    def card(self, t, ctx, market, session, price=None, vol_ratio=None, bars=None):
        row = ctx["rows"].get(t) or {}
        bars = bars if bars is not None else self.bars(t, session)
        return M.score_stock(t, row, ctx["rs"], ctx["groups"], eps_rows=self.earnings(t), bars=bars,
                             own_rows=self.own(t), market=market, today=session, price=price, vol_ratio=vol_ratio)

    # ------------------------------------------------------------------ any ticker (dashboard drawer)
    def ticker(self, t):
        """A card for any ticker, scored against the latest run's universe ranks."""
        ctx = self.ctx or {}
        if not ctx.get("rs"):
            return {"error": "no scan yet"}
        session = cal.last_session().isoformat()
        rows = self.client.screener_tickers([t])
        row = next((r for r in rows if r.get("ticker") == t), None)
        if not row:
            return {"error": "Unusual Whales has no screener row for %s" % t}
        rs = ctx["rs"]
        if t not in rs:
            # rank it into the universe it was not part of (e.g. under $300M): same formula, same distribution
            v, basis = M.rs_raw(row)
            if v is not None:
                raws = ctx.get("raws") or []
                rs = dict(rs)
                rs[t] = {"rs": max(1, min(99, int(round(99 * _rank(raws, v))))) if raws else 50, "basis": basis,
                         "approx": True}
        c = dict(ctx, rows={t: {k: row.get(k) for k in SLIM}}, rs=rs)
        card = self.card(t, c, self.market, session)
        card["in_universe"] = t in ctx["rs"]
        return card

    # ------------------------------------------------------------------ intraday watch
    def watch_once(self):
        last = self.store.last_run()
        if not last or not self.ctx:
            return 0
        cards = self.store.cards(last["id"])
        names = [c["ticker"] for c in cards if (c.get("score") or 0) >= WATCH_MIN_SCORE
                 and (c.get("base") or {}).get("pivot")
                 and (c["base"].get("state") in WATCH_STATES)
                 and (c["base"].get("vs_pivot") or -1) >= -WATCH_WITHIN]
        if not names:
            return 0
        et = cal.et_now()
        frac = max(cal.session_fraction(et), 0.05)
        today = et.date().isoformat()
        by = {c["ticker"]: c for c in cards}
        n = 0
        for i in range(0, len(names), 200):
            for r in self.client.screener_tickers(names[i:i + 200]):
                t = r.get("ticker")
                if t not in by:
                    continue
                p, vol, avg = M.num(r.get("close")), M.num(r.get("stock_volume")), M.num(r.get("avg30_volume"))
                if not p:
                    continue
                pace = (vol / avg / frac) if vol and avg else None
                bars = list(self.bars(t, last["session"]))
                if bars and bars[-1]["date"] < today:
                    bars.append({"date": today, "open": M.num(r.get("open")), "high": M.num(r.get("high")) or p,
                                 "low": M.num(r.get("low")) or p, "close": p, "volume": vol})
                card = self.card(t, self.ctx, self.market, last["session"], price=p, vol_ratio=pace, bars=bars)
                self.watch[t] = {"at": time.time(), "price": p, "pace": pace, "state": card["base"].get("state"),
                                 "text": card["base"].get("text")}
                n += 1
                self.maybe_alert(card, today, self.market, source="intraday")
        return n

    def maybe_alert(self, card, session, market, source):
        b = card.get("base") or {}
        if b.get("state") != "breakout" or (card.get("score") or 0) < WATCH_MIN_SCORE:
            return None
        text = "%s: %s Score %s%s." % (card["ticker"], b.get("text"), card.get("score"),
                                       ", Leader" if card.get("leader") else "")
        aid = self.store.add_alert(session, card["ticker"], "breakout", b.get("pivot"), card.get("score"),
                                   card.get("leader"), (market or {}).get("state"), text)
        if aid is None:
            return None
        self.log("breakout (%s): %s" % (source, text))
        if self.notifier and card.get("leader") and (market or {}).get("ok"):
            if self.notifier.breakout(card, market):
                self.store.mark_posted(aid)
        return aid


SLIM = ("ticker", "full_name", "close", "open", "high", "low", "marketcap", "sector", "industry_type", "week_52_high",
        "week_52_low", "three_month_perc", "six_month_perc", "one_year_perc", "shares_outstanding_growth_4q",
        "relative_volume", "avg30_volume", "stock_volume", "sma_50", "sma_200", "next_earnings_date",
        "bullish_premium", "bearish_premium")


def _rank(sorted_vals, v):
    import bisect
    return bisect.bisect_left(sorted_vals, v) / float(max(1, len(sorted_vals)))
