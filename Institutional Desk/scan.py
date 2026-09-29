"""
The Institutional Desk scan pipeline.

Four stages, each resumable, each writing to SQLite as it goes:

  1. FUNDS     -- load every 13F filer (~20 calls), keep only small,
                  concentrated ones: AUM inside a band, hedge-fund or tagged.
  2. HOLDINGS  -- one call per qualifying fund. A full 500-row page means
                  "pages and pages of investments" -> the fund is culled and
                  its rows discarded. Marketcap for every ticker falls out of
                  this payload for free (shares_outstanding x close).
  3. SCREEN    -- shortlist the tickers that actually generated interesting
                  activity, then fetch 3 years of annual financials for just
                  those. Cached per ticker, so later runs are nearly free.
  4. SCORE     -- classify each position change, detect clusters, score.

Design rule inherited from Swing Desk: fewer wide polls beat many narrow
polls, and never pay for data another endpoint already gave you.
"""

import concurrent.futures as futures
import json
import threading
import time

import store
import uw
from uw import inum, num, opt_num

# The holdings endpoint returns up to 500 rows and takes ~2-3s per call, so a
# serial scan of ~1,000 funds is latency-bound, not rate-bound. A small worker
# pool fixes that; Client.get() still enforces one global minimum gap between
# requests, so the pool never exceeds the API's rate ceiling. All database
# writes stay on the calling thread -- workers only do network.
WORKERS = uw.env_int("IDESK_WORKERS", 5)

# ---------------------------------------------------------------- config

def config():
    """All thresholds, overridable from .env. Documented in README."""
    return {
        # --- fund universe (the "smaller firms" half of the ask)
        "fund_min_aum":       uw.env_num("IDESK_FUND_MIN_AUM", 50e6),
        "fund_max_aum":       uw.env_num("IDESK_FUND_MAX_AUM", 2.5e9),
        "fund_max_positions": uw.env_int("IDESK_FUND_MAX_POSITIONS", 75),
        "require_hedge_fund": uw.env_bool("IDESK_REQUIRE_HEDGE_FUND", True),

        # --- company screen (the "smaller balance sheets, profitable" half)
        "co_max_marketcap":   uw.env_num("IDESK_CO_MAX_MARKETCAP", 10e9),
        "co_max_assets":      uw.env_num("IDESK_CO_MAX_ASSETS", 10e9),
        "min_profit_years":   uw.env_int("IDESK_MIN_PROFIT_YEARS", 3),

        # --- what counts as an event
        "min_position_value": uw.env_num("IDESK_MIN_POSITION_VALUE", 1e6),
        "min_add_perc":       uw.env_num("IDESK_MIN_ADD_PERC", 0.25),
        "max_trim_perc":      uw.env_num("IDESK_MAX_TRIM_PERC", -0.50),
        "cluster_min_funds":  uw.env_int("IDESK_CLUSTER_MIN_FUNDS", 3),

        # --- budget
        # A full-quarter run saw ~1,500 tickers with bullish activity, under a
        # thousand of them under the marketcap cap. A budget of 1,200 covers that comfortably
        # without letting a pathological quarter run away.
        "max_fundamental_fetches": uw.env_int("IDESK_MAX_FUNDAMENTAL_FETCHES", 1200),
        "fundamental_max_age_days": uw.env_int("IDESK_FUNDAMENTAL_MAX_AGE_DAYS", 75),
    }


BULLISH = ("NEW", "ADD")
BEARISH = ("TRIM", "EXIT")

# Sum of every score component's ceiling: base 35 + weight 25 + concentration 12
# + trajectory 12 + cluster 20 + growth 10 + entry 8.
SCORE_MAX = 122.0


# ---------------------------------------------------------------- pure logic
# (kept free of I/O so tests/test_logic.py can hammer them)

def classify_trajectory(hist):
    """
    Read the 8-quarter historical_units array. Index 0 is the current
    quarter, index 7 the oldest. Returns a behaviour label.
    """
    hist = [inum(h) for h in (hist or [])]
    if not hist:
        return "unknown"
    if hist[0] == 0:
        return "closing"
    active = [h for h in hist if h > 0]
    quarters_held = len(active)
    if quarters_held <= 2:
        return "new_conviction"

    # walk oldest -> newest over the span the position has existed
    span = hist[:quarters_held]
    diffs = [span[i] - span[i + 1] for i in range(len(span) - 1)]
    if not diffs:
        return "steady"
    ups = sum(1 for d in diffs if d > 0)
    downs = sum(1 for d in diffs if d < 0)
    if ups >= len(diffs) * 0.7:
        return "building"
    if downs >= len(diffs) * 0.7:
        return "harvesting"
    if max(active) > 2 * min(active):
        return "volatile"
    return "steady"


def classify_event(row, report_date, cfg):
    """
    Map one holdings row to an event kind, or None if it is not
    alert-worthy. `row` uses the raw API field names.

    Ordering matters: a closed position has units == 0 AND a negative
    units_change, so EXIT must be tested before TRIM.
    """
    sec = (row.get("security_type") or "").strip()
    if sec != "Share":
        return None                      # skip funds/ETFs, options, debt, warrants
    if row.get("put_call"):
        return None

    units = inum(row.get("units"))
    change = inum(row.get("units_change"))
    value = num(row.get("value"))
    close = num(row.get("close"))
    change_perc = opt_num(row.get("change_perc"))

    if units <= 0:
        # position closed this quarter -- size it by what was sold
        prior_value = abs(change) * close
        if prior_value < cfg["min_position_value"]:
            return None
        return "EXIT"

    if value < cfg["min_position_value"]:
        return None

    if row.get("first_buy") and row.get("first_buy") == report_date:
        return "NEW"

    if change > 0:
        # change_perc is null on a brand-new position; here it should exist
        if change_perc is None or change_perc >= cfg["min_add_perc"]:
            return "ADD"
        return None

    if change < 0:
        if change_perc is not None and change_perc <= cfg["max_trim_perc"]:
            return "TRIM"
        return None

    return None                          # unchanged -- held, not an event


def score_event(kind, weight, position_count, trajectory, cluster_funds,
                ni_growth, discount_to_cost):
    """
    Conviction score, 0-100, with an auditable breakdown.

    These weights are REASONED, NOT FITTED -- same honest caveat as the
    Swing Desk model. The events table stores the parts so that once a few
    quarters of history accumulate they can actually be tested.
    """
    parts = {}
    parts["base"] = 35.0 if kind == "NEW" else 20.0

    # how big a bet is this inside the fund's own equity book
    w = max(0.0, num(weight))
    parts["weight"] = min(25.0, (w / 0.08) * 25.0)

    # a 12-name fund saying something is louder than a 400-name fund
    pc = position_count or 999
    parts["concentration"] = 12.0 if pc <= 15 else 8.0 if pc <= 30 else 4.0 if pc <= 50 else 0.0

    parts["trajectory"] = {
        "building": 12.0, "new_conviction": 10.0, "volatile": 4.0,
        "steady": 2.0, "harvesting": 0.0, "closing": 0.0, "unknown": 0.0,
    }.get(trajectory, 0.0)

    n = max(1, int(cluster_funds or 1))
    parts["cluster"] = min(20.0, 6.0 * (n - 1))

    g = ni_growth
    parts["earnings_growth"] = 0.0 if g is None else (10.0 if g > 0.15 else 6.0 if g > 0 else 0.0)

    # still buyable near where the fund got in
    d = discount_to_cost
    if d is None:
        parts["entry"] = 0.0
    elif d <= 0:
        parts["entry"] = 8.0            # trading below the fund's average price
    elif d <= 0.15:
        parts["entry"] = 4.0
    else:
        parts["entry"] = 0.0

    # Normalise rather than clip. Clipping at 100 crushed the top of the board
    # into a wall of identical 100s on the live September run; scaling by the
    # theoretical maximum keeps the ordering and the spread.
    total = min(100.0, 100.0 * sum(parts.values()) / SCORE_MAX)
    return round(total, 1), parts


def evaluate_fundamentals(income_rows, bs_rows, cfg, marketcap=None):
    """
    Apply the profitability + balance-sheet-size gate to one ticker.

    Only annual, USD-reported periods count. Foreign-currency filers are the
    trap here: a filer reporting in a low-value currency can show a net income
    in the trillions, which sails through any dollar threshold.

    Worse, `reported_currency` is not reliable. It comes back as the literal
    string "None" both for plain US companies and for filers that are
    clearly reporting in something else (revenue in the tens of trillions). So when the currency is missing we fall back on a dimensional
    check: revenue more than 100x the company's USD market cap cannot be
    dollars. Real US companies sit around 0.1-5x.
    """
    annual = [
        r for r in (income_rows or [])
        if (r.get("report_type") == "annual") and r.get("fiscal_date_ending")
    ]
    annual.sort(key=lambda r: r["fiscal_date_ending"], reverse=True)

    out = {
        "currency": None, "annual_years": [], "profitable_years": 0,
        "years_available": len(annual), "net_income_latest": None,
        "revenue_latest": None, "ni_growth": None, "total_assets": None,
        "total_liabilities": None, "shareholder_equity": None, "fy_end": None,
        "passes": 0, "reject_reason": None,
    }

    if not annual:
        out["reject_reason"] = "no_annual_statements"
        return out

    currency = (annual[0].get("reported_currency") or "").upper()
    out["currency"] = currency
    if currency not in ("USD", "", "NONE"):
        out["reject_reason"] = "reports_in_" + currency
        return out
    if currency != "USD":
        rev = opt_num(annual[0].get("total_revenue"))
        if marketcap and marketcap > 0 and rev and rev > 100 * marketcap:
            out["reject_reason"] = "currency_unknown_and_scale_implausible"
            return out

    years = []
    for r in annual[:4]:
        years.append({
            "year": r["fiscal_date_ending"][:4],
            "fy_end": r["fiscal_date_ending"],
            "net_income": opt_num(r.get("net_income")),
            "operating_income": opt_num(r.get("operating_income")),
            "revenue": opt_num(r.get("total_revenue")),
        })
    out["annual_years"] = years
    out["fy_end"] = years[0]["fy_end"]
    out["net_income_latest"] = years[0]["net_income"]
    out["revenue_latest"] = years[0]["revenue"]

    # consecutive profitable years, counting back from the most recent
    streak = 0
    for y in years:
        ni = y["net_income"]
        if ni is not None and ni > 0:
            streak += 1
        else:
            break
    out["profitable_years"] = streak

    if len(years) >= 2 and years[1]["net_income"] not in (None, 0):
        prior = years[1]["net_income"]
        out["ni_growth"] = (years[0]["net_income"] - prior) / abs(prior)

    b_annual = [
        r for r in (bs_rows or [])
        if r.get("report_type") == "annual" and r.get("fiscal_date_ending")
    ]
    b_annual.sort(key=lambda r: r["fiscal_date_ending"], reverse=True)
    if b_annual:
        out["total_assets"] = opt_num(b_annual[0].get("total_assets"))
        out["total_liabilities"] = opt_num(b_annual[0].get("total_liabilities"))
        out["shareholder_equity"] = opt_num(b_annual[0].get("total_shareholder_equity"))

    need = cfg["min_profit_years"]
    if len(years) < need:
        out["reject_reason"] = "only_%d_annual_years" % len(years)
        return out
    if streak < need:
        out["reject_reason"] = "profitable_%d_of_last_%d_years" % (streak, need)
        return out
    if out["total_assets"] is not None and out["total_assets"] > cfg["co_max_assets"]:
        out["reject_reason"] = "total_assets_above_cap"
        return out

    out["passes"] = 1
    return out


# ---------------------------------------------------------------- the scan

class Scan:
    """Runs the pipeline. One instance per run; progress is readable live."""

    def __init__(self, client=None, cfg=None, log=None):
        self.cli = client or uw.Client()
        self.cfg = cfg or config()
        self.log = log or (lambda msg: print(msg, flush=True))
        self.state = {
            "stage": "idle", "detail": "", "done": 0, "total": 0,
            "started_at": None, "finished_at": None, "error": None,
            "api_calls": 0, "report_date": None,
        }
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _tick(self, stage=None, detail=None, done=None, total=None):
        if stage is not None:
            self.state["stage"] = stage
        if detail is not None:
            self.state["detail"] = detail
        if done is not None:
            self.state["done"] = done
        if total is not None:
            self.state["total"] = total
        self.state["api_calls"] = self.cli.calls

    # -- stage 1 ---------------------------------------------------

    def load_funds(self):
        self._tick("funds", "loading 13F filer list", 0, 0)
        rows = []
        for rec in self.cli.institutions():
            rows.append({
                "cik": rec.get("cik"),
                "name": rec.get("name"),
                "short_name": rec.get("short_name"),
                "is_hedge_fund": 1 if rec.get("is_hedge_fund") else 0,
                "tags": store.jdump(rec.get("tags") or []),
                "people": store.jdump(rec.get("people") or []),
                "report_date": rec.get("date"),
                "filing_date": rec.get("filing_date"),
                "total_value": num(rec.get("total_value")),
                "share_value": num(rec.get("share_value")),
                "buy_value": num(rec.get("buy_value")),
                "sell_value": num(rec.get("sell_value")),
                "refreshed_at": store.now(),
            })
            if len(rows) % 500 == 0:
                self._tick(detail="%d filers" % len(rows), done=len(rows))
        rows = [r for r in rows if r["cik"]]
        store.upsert_institutions(rows)

        # the quarter everyone just filed for
        dates = {}
        for r in rows:
            if r["report_date"]:
                dates[r["report_date"]] = dates.get(r["report_date"], 0) + 1
        report_date = max(dates, key=dates.get) if dates else None
        self.state["report_date"] = report_date

        cfg = self.cfg
        kept = []
        for r in rows:
            if r["report_date"] != report_date:
                continue
            if not (cfg["fund_min_aum"] <= r["total_value"] <= cfg["fund_max_aum"]):
                continue
            if cfg["require_hedge_fund"]:
                if not (r["is_hedge_fund"] or store.jload(r["tags"])):
                    continue
            kept.append(r)
        kept.sort(key=lambda r: r["total_value"])
        self.log("funds: %d filers, %d qualify for %s" % (len(rows), len(kept), report_date))
        return report_date, kept

    # -- stage 2 ---------------------------------------------------

    def load_holdings(self, report_date, funds, resume=True):
        cfg = self.cfg
        already = store.scanned_ciks(report_date) if resume else set()
        todo = [f for f in funds if f["cik"] not in already]
        self._tick("holdings", "%d funds to fetch" % len(todo), 0, len(todo))
        self.log("holdings: %d funds to fetch (%d cached)" % (len(todo), len(funds) - len(todo)))

        def fetch(fund):
            try:
                return fund, self.cli.holdings(fund["cik"], date=report_date, page_size=500), None
            except RuntimeError as exc:
                return fund, None, str(exc)[:200]

        ticker_facts = {}
        pool = futures.ThreadPoolExecutor(max_workers=max(1, WORKERS))
        pending = [pool.submit(fetch, f) for f in todo]
        i = 0
        for fut in futures.as_completed(pending):
            i += 1
            if self._stop.is_set():
                for p in pending:
                    p.cancel()
                break
            fund, rows, err = fut.result()
            cik = fund["cik"]
            if err is not None:
                store.record_fund_scan(cik, report_date, None, False, None, err)
                self._tick(done=i, detail="error on %s" % (fund["name"] or "")[:40])
                continue

            oversized = len(rows) >= 500
            if oversized or len(rows) > cfg["fund_max_positions"]:
                # "pages and pages of investments" -- not what we want
                store.record_fund_scan(cik, report_date, len(rows), oversized, None)
                self._tick(done=i, detail="culled %s (%d positions)" % (fund["name"][:34], len(rows)))
                continue

            batch = []
            shares = 0
            for h in rows:
                tkr = (h.get("ticker") or "").strip().upper()
                if not tkr:
                    continue
                sec = (h.get("security_type") or "").strip()
                if sec == "Share":
                    shares += 1
                batch.append({
                    "cik": cik,
                    "ticker": tkr,
                    "report_date": report_date,
                    "security_type": sec,
                    "put_call": h.get("put_call") or "",
                    "full_name": h.get("full_name"),
                    "sector": h.get("sector"),
                    "units": inum(h.get("units")),
                    "units_change": inum(h.get("units_change")),
                    "change_perc": opt_num(h.get("change_perc")),
                    "value": num(h.get("value")),
                    "avg_price": opt_num(h.get("avg_price")),
                    "close": num(h.get("close")),
                    "shares_outstanding": num(h.get("shares_outstanding")),
                    "perc_of_share_value": num(h.get("perc_of_share_value")),
                    "first_buy": h.get("first_buy"),
                    "historical_units": store.jdump(h.get("historical_units") or []),
                    "price_change_since_first_buy_perc":
                        opt_num(h.get("price_change_since_first_buy_perc")),
                })
                # marketcap for free -- no screener call needed
                if sec == "Share":
                    close = num(h.get("close"))
                    so = num(h.get("shares_outstanding"))
                    if close > 0 and so > 0:
                        prev = ticker_facts.get(tkr)
                        fact = {
                            "ticker": tkr,
                            "full_name": h.get("full_name"),
                            "sector": h.get("sector"),
                            "close": close,
                            "shares_out": so,
                            "marketcap": close * so,
                            "updated_at": store.now(),
                        }
                        if prev is None or fact["marketcap"] > prev["marketcap"]:
                            ticker_facts[tkr] = fact

            store.upsert_holdings(batch)
            store.record_fund_scan(cik, report_date, len(rows), False, shares)
            if i % 10 == 0 or i == len(todo):
                store.upsert_tickers(list(ticker_facts.values()))
                ticker_facts = {}
                self._tick(done=i, detail="%d/%d funds" % (i, len(todo)))

        pool.shutdown(wait=False, cancel_futures=True)
        store.upsert_tickers(list(ticker_facts.values()))
        conn = store.connect()
        kept = conn.execute(
            "SELECT COUNT(*) c FROM fund_scan WHERE report_date=? AND oversized=0 "
            "AND error IS NULL AND position_count<=?",
            (report_date, cfg["fund_max_positions"]),
        ).fetchone()["c"]
        self.log("holdings: %d funds kept" % kept)
        return kept

    # -- stage 3 ---------------------------------------------------

    def shortlist_tickers(self, report_date):
        """
        Which tickers deserve a fundamentals lookup? Only ones that both
        (a) look small by marketcap and (b) actually generated bullish
        activity. This is the cull that keeps the run affordable.
        """
        cfg = self.cfg
        conn = store.connect()
        rows = conn.execute(
            """SELECT h.*, f.position_count
               FROM holdings h
               JOIN fund_scan f ON f.cik=h.cik AND f.report_date=h.report_date
               WHERE h.report_date=? AND h.security_type='Share'
                 AND f.oversized=0 AND f.error IS NULL
                 AND f.position_count<=?""",
            (report_date, cfg["fund_max_positions"]),
        ).fetchall()

        interest = {}
        for r in rows:
            kind = classify_event(dict(r), report_date, cfg)
            if kind not in BULLISH:
                continue
            t = r["ticker"]
            slot = interest.setdefault(t, {"funds": 0, "weight": 0.0, "value": 0.0})
            slot["funds"] += 1
            slot["weight"] = max(slot["weight"], num(r["perc_of_share_value"]))
            slot["value"] += num(r["value"])

        caps = {
            row["ticker"]: num(row["marketcap"])
            for row in conn.execute("SELECT ticker, marketcap FROM tickers").fetchall()
        }
        cand = []
        for t, slot in interest.items():
            mc = caps.get(t, 0.0)
            if mc <= 0 or mc > cfg["co_max_marketcap"]:
                continue
            # rank: cluster size first, then how big a bet it is
            rank = slot["funds"] * 1000 + min(999, slot["weight"] * 5000)
            cand.append((rank, t))
        cand.sort(reverse=True)
        shortlist = [t for _, t in cand[: cfg["max_fundamental_fetches"]]]
        self.log("screen: %d bullish tickers, %d small enough, %d shortlisted"
                 % (len(interest), len(cand), len(shortlist)))
        return shortlist

    def load_fundamentals(self, tickers):
        cfg = self.cfg
        fresh = store.fresh_fundamentals(cfg["fundamental_max_age_days"])
        todo = [t for t in tickers if t not in fresh]
        caps = {
            r["ticker"]: num(r["marketcap"])
            for r in store.connect().execute(
                "SELECT ticker, marketcap FROM tickers").fetchall()
        }
        self._tick("screen", "%d tickers need financials" % len(todo), 0, len(todo))
        self.log("screen: %d financial lookups (%d cached)" % (len(todo), len(tickers) - len(todo)))

        def fetch(tkr):
            try:
                return tkr, self.cli.income_statements(tkr), self.cli.balance_sheets(tkr), None
            except RuntimeError as exc:
                return tkr, None, None, str(exc)[:80]

        pool = futures.ThreadPoolExecutor(max_workers=max(1, WORKERS))
        pending = [pool.submit(fetch, t) for t in todo]
        i = 0
        for fut in futures.as_completed(pending):
            i += 1
            if self._stop.is_set():
                for p in pending:
                    p.cancel()
                break
            tkr, inc, bal, err = fut.result()
            if err is not None:
                store.upsert_fundamentals({
                    "ticker": tkr, "fetched_at": store.now(), "currency": None,
                    "annual_years": "[]", "profitable_years": 0, "years_available": 0,
                    "net_income_latest": None, "revenue_latest": None, "ni_growth": None,
                    "total_assets": None, "total_liabilities": None,
                    "shareholder_equity": None, "fy_end": None, "passes": 0,
                    "reject_reason": "fetch_error: %s" % err,
                })
                self._tick(done=i)
                continue

            ev = evaluate_fundamentals(inc, bal, cfg, marketcap=caps.get(tkr))
            ev["ticker"] = tkr
            ev["fetched_at"] = store.now()
            ev["annual_years"] = store.jdump(ev["annual_years"])
            store.upsert_fundamentals(ev)
            if i % 5 == 0 or i == len(todo):
                self._tick(done=i, detail="%d/%d financials" % (i, len(todo)))
        pool.shutdown(wait=False, cancel_futures=True)

    # -- stage 4 ---------------------------------------------------

    def build_events(self, report_date):
        cfg = self.cfg
        conn = store.connect()
        rows = conn.execute(
            """SELECT h.*, f.position_count
               FROM holdings h
               JOIN fund_scan f ON f.cik=h.cik AND f.report_date=h.report_date
               WHERE h.report_date=? AND h.security_type='Share'
                 AND f.oversized=0 AND f.error IS NULL
                 AND f.position_count<=?""",
            (report_date, cfg["fund_max_positions"]),
        ).fetchall()

        passing = {
            r["ticker"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM fundamentals WHERE passes=1"
            ).fetchall()
        }
        caps = {
            r["ticker"]: num(r["marketcap"])
            for r in conn.execute("SELECT ticker, marketcap FROM tickers").fetchall()
        }

        # pass 1: classify, keeping only tickers that clear the company screen
        raw = []
        for r in rows:
            d = dict(r)
            tkr = d["ticker"]
            if tkr not in passing:
                continue
            if caps.get(tkr, 0.0) > cfg["co_max_marketcap"]:
                continue
            kind = classify_event(d, report_date, cfg)
            if not kind:
                continue
            raw.append((kind, d))

        # pass 2: cluster counts over bullish events only
        cluster = {}
        for kind, d in raw:
            if kind in BULLISH:
                cluster[d["ticker"]] = cluster.get(d["ticker"], 0) + 1

        # pass 3: score
        out = []
        for kind, d in raw:
            tkr = d["ticker"]
            fund = passing[tkr]
            traj = classify_trajectory(store.jload(d["historical_units"]))
            avg_price = d["avg_price"]
            close = num(d["close"])
            discount = None
            if avg_price and avg_price > 0 and close > 0:
                discount = close / avg_price - 1.0
            if kind in BULLISH:
                score, parts = score_event(
                    kind, num(d["perc_of_share_value"]), d["position_count"],
                    traj, cluster.get(tkr, 1), fund.get("ni_growth"), discount,
                )
            else:
                # bearish rows are context, not a ranked buy signal
                base = 30.0 if kind == "EXIT" else 15.0
                parts = {"base": base,
                         "size": min(20.0, num(d["perc_of_share_value"]) / 0.08 * 20.0)}
                score = round(min(100.0, sum(parts.values())), 1)
            out.append({
                "report_date": report_date,
                "cik": d["cik"],
                "ticker": tkr,
                "kind": kind,
                "units": d["units"],
                "units_change": d["units_change"],
                "change_perc": d["change_perc"],
                "value": d["value"],
                "weight": d["perc_of_share_value"],
                "trajectory": traj,
                "score": score,
                "score_parts": store.jdump(parts),
            })

        store.replace_events(report_date, out)
        self.log("events: %d (%d bullish) across %d tickers"
                 % (len(out), sum(1 for e in out if e["kind"] in BULLISH),
                    len({e["ticker"] for e in out})))
        return out

    # -- orchestration ---------------------------------------------

    def run(self, resume=True):
        self.state["started_at"] = store.now()
        self.state["error"] = None
        run_id = None
        try:
            report_date, funds = self.load_funds()
            if not report_date:
                raise RuntimeError("no report date found in institutions payload")
            run_id = store.start_run(report_date)
            store.update_run(run_id, stage="holdings")

            kept = self.load_holdings(report_date, funds, resume=resume)
            store.update_run(run_id, stage="screen", funds_kept=kept)

            shortlist = self.shortlist_tickers(report_date)
            self.load_fundamentals(shortlist)
            store.update_run(run_id, stage="score", tickers_kept=len(shortlist))

            events = self.build_events(report_date)
            self._tick("done", "complete", 1, 1)
            self.state["finished_at"] = store.now()
            store.update_run(
                run_id, stage="done", status="ok", finished_at=store.now(),
                api_calls=self.cli.calls, events_made=len(events),
            )
            store.set_meta("last_report_date", report_date)
            store.set_meta("last_scan_finished", store.now())
            return events
        except Exception as exc:                     # noqa: BLE001 - surfaced in UI
            self.state["error"] = str(exc)
            self.state["stage"] = "error"
            self.state["finished_at"] = store.now()
            if run_id:
                store.update_run(run_id, status="error", finished_at=store.now(),
                                 note=str(exc)[:400], api_calls=self.cli.calls)
            raise


if __name__ == "__main__":
    t0 = time.time()
    scan = Scan()
    scan.run()
    print("done in %.1f min, %d API calls" % ((time.time() - t0) / 60, scan.cli.calls))
