"""
The investing layer: a watchlist with buy zones, a written thesis and sell rules for every name,
checks that say when a thesis has broken, what smart money is doing in YOUR names, and a fresh
valuation after each earnings report.

Names
  Your holdings (open rows of the portfolio sheet) are always included. Anything else you add
  is a watchlist name. A holding's thesis lives in the same table (on_list = 0 until you add it).

Buy zone
  buy price = your override, else the Valuation desk's margin-of-safety price
              (base intrinsic value x (1 - margin of safety), 25% by default, read from the desk).
  in zone   = price <= buy price;  near = within NEAR_ZONE above it.

Thesis checks (you chose these three; each says why, in numbers)
  price vs value   price above intrinsic value, above your sell target, or below your stop
  business worse   verdict fell (e.g. BUY -> HOLD) or quality fell >= QUALITY_DROP since you
                   wrote the thesis (the "baseline" run, saved with the thesis)
  smart money out  insiders sold >= $500k outside 10b5-1 plans in 30 days, or the latest 13F
                   quarter shows >= FUND_EXITS full exits and net institutional selling >= 5%

After earnings
  Each name's next report date is recorded while it is upcoming. The day after it passes, the
  Valuation desk re-runs the name (starting the desk if needed) and the change is logged. If the
  new financials aren't filed yet (same period end), it tries again every RETRY_DAYS, for up to
  GIVE_UP_DAYS.

Every threshold here is reasoned, not fitted -- same caveat as the desks.
"""

import datetime as dt
import json
import re
import threading
import time
import urllib.parse

import market as market_mod
import trackrec
from events import num, rows, text

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
VERDICT_RANK = {"AVOID": 0, "REDUCE": 1, "HOLD": 2, "BUY": 3, "STRONG BUY": 4}
NEAR_ZONE = 0.10
QUALITY_DROP = 1.0
FUND_EXITS = 3
FUND_NET = -0.05
INSIDER_BUY_DAYS = 90
INSIDER_BUY_USD = 100_000
INSIDER_BUY_PEOPLE = 2
RETRY_DAYS = 3
GIVE_UP_DAYS = 45
MAX_RERUNS = 4                 # per refresher pass, so a big earnings day can't flood the API
DEFAULT_MOS = 0.25

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch (
    ticker       TEXT PRIMARY KEY,
    on_list      INTEGER NOT NULL DEFAULT 1,
    added_at     REAL,
    thesis       TEXT,
    sell_rules   TEXT,
    buy_override REAL,
    sell_target  REAL,
    stop         REAL,
    base_run     INTEGER,
    base_verdict TEXT,
    base_score   REAL,
    base_quality REAL,
    base_value   REAL,
    thesis_at    REAL,
    next_er      TEXT,
    er_checked   TEXT,
    rerun_on     TEXT,
    rerun_tries  INTEGER DEFAULT 0,
    updated_at   REAL
);
CREATE TABLE IF NOT EXISTS watch_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker  TEXT NOT NULL,
    at      REAL NOT NULL,
    kind    TEXT NOT NULL,
    text    TEXT NOT NULL,
    data    TEXT
);
CREATE INDEX IF NOT EXISTS watch_events_at ON watch_events(at);
"""
FIELDS = ("thesis", "sell_rules", "buy_override", "sell_target", "stop")


def clean_ticker(t):
    t = str(t or "").strip().upper()
    return t if TICKER_RE.match(t) else None


def _money(v):
    return "$%s" % ("{:,.2f}".format(v) if v < 1000 else "{:,.0f}".format(v))


def _short(v):
    return market_mod._short(v)


def _pos(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


class Watchlist:
    def __init__(self, store, market, view, holdings=None, supervisor=None, log=print, today=None):
        self.store, self.market, self.view = store, market, view
        self.holdings = holdings or (lambda: [])
        self.sup = supervisor
        self.log = log
        self.today = today or (lambda: trackrec.et_now().date())
        self.run_cache = {}
        self.lock = threading.Lock()
        with store.lock:
            store.conn.executescript(SCHEMA)
            store.conn.commit()

    # ------------------------------------------------------------ storage
    def _rows(self):
        with self.store.lock:
            return {r["ticker"]: dict(r) for r in self.store.conn.execute("SELECT * FROM watch")}

    def _row(self, t):
        with self.store.lock:
            r = self.store.conn.execute("SELECT * FROM watch WHERE ticker=?", (t,)).fetchone()
        return dict(r) if r else None

    def _upsert(self, t, **f):
        f["updated_at"] = time.time()
        with self.store.lock:
            if not self.store.conn.execute("SELECT 1 FROM watch WHERE ticker=?", (t,)).fetchone():
                self.store.conn.execute("INSERT INTO watch(ticker, on_list, added_at) VALUES(?,?,?)",
                                        (t, f.pop("on_list", 1), time.time()))
            if f:
                self.store.conn.execute("UPDATE watch SET %s WHERE ticker=?" % ",".join("%s=?" % k for k in f),
                                        list(f.values()) + [t])
            self.store.conn.commit()

    def event(self, t, kind, msg, data=None):
        with self.store.lock:
            self.store.conn.execute("INSERT INTO watch_events(ticker,at,kind,text,data) VALUES(?,?,?,?,?)",
                                    (t, time.time(), kind, msg, json.dumps(data) if data is not None else None))
            self.store.conn.commit()

    def events(self, days=7, ticker=None):
        q, a = "SELECT * FROM watch_events WHERE at>=?", [time.time() - days * 86400]
        if ticker:
            q += " AND ticker=?"
            a.append(ticker)
        with self.store.lock:
            out = [dict(r) for r in self.store.conn.execute(q + " ORDER BY at DESC LIMIT 100", a)]
        for e in out:
            e["data"] = json.loads(e["data"]) if e["data"] else None
        return out

    # ------------------------------------------------------------ editing
    def add(self, t):
        t = clean_ticker(t)
        if not t:
            return 400, {"error": "That isn't a ticker."}
        self._upsert(t, on_list=1)
        r = self._row(t)
        if not r["on_list"]:
            self._upsert(t, on_list=1)
        return 200, {"ok": True, "ticker": t}

    def remove(self, t):
        t = clean_ticker(t)
        r = self._row(t) if t else None
        if not r:
            return 404, {"error": "Not on the watchlist."}
        if (r.get("thesis") or "").strip() or (r.get("sell_rules") or "").strip():
            self._upsert(t, on_list=0)          # keep the thesis you wrote; it still applies if you hold it
        else:
            with self.store.lock:
                self.store.conn.execute("DELETE FROM watch WHERE ticker=?", (t,))
                self.store.conn.commit()
        return 200, {"ok": True}

    def save_thesis(self, t, body):
        """Save the thesis fields. The first time (or when asked) the current valuation becomes the
        baseline the 'business got worse' check compares against."""
        t = clean_ticker(t)
        if not t:
            return 400, {"error": "That isn't a ticker."}
        f = {}
        for k in ("thesis", "sell_rules"):
            if k in body:
                f[k] = str(body.get(k) or "")[:5000]
        for k in ("buy_override", "sell_target", "stop"):
            if k in body:
                f[k] = _pos(body.get(k))
        cur = self._row(t)
        if cur is None:
            self._upsert(t, on_list=1 if body.get("add") else 0)
            cur = self._row(t)
        if body.get("rebaseline") or not cur.get("thesis_at"):
            v = self.valuation(t)
            f.update(thesis_at=time.time(), base_run=v and v["run_id"], base_verdict=v and v["verdict"],
                     base_score=v and v["score"], base_quality=v and v["quality"], base_value=v and v["base_value"])
        self._upsert(t, **f)
        self.event(t, "thesis", "Thesis %s" % ("re-based on the latest valuation" if body.get("rebaseline") else "saved"))
        return 200, {"ok": True, "item": self.item(t)}

    # ------------------------------------------------------------ reads
    def mos(self):
        m, _ = self.view._get("valuation", "/api/method", ttl=3600)
        return float((m or {}).get("margin_of_safety") or DEFAULT_MOS)

    def valuation(self, t):
        """Latest Valuation run for the ticker, with quality (from the run's card). None if never run
        or the desk is off."""
        h, err = self.view._get("valuation", "/api/history?ticker=%s" % urllib.parse.quote(t, safe=""), ttl=60)
        runs = (h or {}).get("runs") or []
        if not runs:
            return None
        r = runs[0]
        rid = r.get("id")
        card = self.run_cache.get(rid)
        if card is None:
            d, err = self.view._get("valuation", "/api/run/%d" % int(rid), ttl=10 ** 9)
            card = (d or {}).get("card") or {}
            fin = ((d or {}).get("result") or {}).get("fin") or {}
            card = {"quality": (card.get("quality") or {}).get("score"), "tier": (card.get("quality") or {}).get("tier"),
                    "synopsis": card.get("synopsis"), "period_end": fin.get("period_end"), "basis": fin.get("basis")}
            if d:
                self.run_cache[rid] = card
        return {"run_id": rid, "run_at": r.get("run_at"), "price_at_run": num(r.get("price")),
                "base_value": num(r.get("base_value")), "bull": num(r.get("bull_value")), "bear": num(r.get("bear_value")),
                "verdict": r.get("verdict"), "score": num(r.get("score")), "quality": card.get("quality"),
                "tier": card.get("tier"), "synopsis": card.get("synopsis"), "period_end": card.get("period_end"),
                "basis": card.get("basis")}

    def names(self):
        """[(ticker, is_holding, is_on_list, db_row, holding_row)]: holdings first, then the watchlist."""
        db = self._rows()
        out, seen = [], set()
        try:
            hold = {h["ticker"]: h for h in self.holdings() if clean_ticker(h.get("ticker"))}
        except Exception:                  # noqa: BLE001 -- the sheet being down must not hide the watchlist
            hold = {}
        for t, h in hold.items():
            r = db.get(t) or {}
            out.append((t, True, bool(r.get("on_list")), r, h))
            seen.add(t)
        for t, r in sorted(db.items()):
            if t not in seen and r.get("on_list"):
                out.append((t, False, True, r, None))
        return out

    def item(self, t, mos=None, hold="auto", db=None):
        t = clean_ticker(t)
        db = db if db is not None else (self._row(t) or {})
        if hold == "auto":                    # asked for one name: is it in the portfolio sheet?
            try:
                hold = next((h for h in self.holdings() if str(h.get("ticker") or "").upper() == t), None)
            except Exception:                  # noqa: BLE001
                hold = None
        mos = self.mos() if mos is None else mos
        v = self.valuation(t)
        price = None
        try:
            price = num(self.market.quote(t).get("price"))
        except Exception:                  # noqa: BLE001
            pass
        auto_buy = v["base_value"] * (1 - mos) if v and v.get("base_value") and v["base_value"] > 0 else None
        buy = db.get("buy_override") or auto_buy
        zone = None
        if buy and price:
            zone = "in" if price <= buy else "near" if price <= buy * (1 + NEAR_ZONE) else "above"
        it = {"ticker": t, "holding": hold is not None, "on_list": bool(db.get("on_list")), "price": price,
              "valuation": v, "buy": buy, "buy_auto": auto_buy, "buy_is_override": bool(db.get("buy_override")),
              "zone": zone, "to_buy": (price / buy - 1) if buy and price else None, "mos": mos,
              "thesis": db.get("thesis") or "", "sell_rules": db.get("sell_rules") or "",
              "sell_target": db.get("sell_target"), "stop": db.get("stop"), "thesis_at": db.get("thesis_at"),
              "baseline": {"run_id": db.get("base_run"), "verdict": db.get("base_verdict"), "score": db.get("base_score"),
                           "quality": db.get("base_quality"), "value": db.get("base_value")} if db.get("thesis_at") else None,
              "next_er": db.get("next_er"), "position": hold and {k: hold.get(k) for k in ("amt", "entry", "mark", "weight")}}
        it["checks"] = self.fast_checks(it)
        return it

    def fast_checks(self, it):
        """The checks that need no extra API calls: price vs value and business worse."""
        v, p, out = it["valuation"], it["price"], []
        parts = []
        if p and v and v.get("base_value"):
            if p > v["base_value"]:
                parts.append("price %s is above intrinsic value %s" % (_money(p), _money(v["base_value"])))
        if p and it.get("sell_target") and p >= it["sell_target"]:
            parts.append("price reached your sell target %s" % _money(it["sell_target"]))
        if p and it.get("stop") and p <= it["stop"]:
            parts.append("price fell to your stop %s" % _money(it["stop"]))
        measured = bool(p and (v or it.get("sell_target") or it.get("stop")))
        out.append({"key": "price", "label": "Price vs value", "status": "break" if parts else "ok" if measured else "n/a",
                    "detail": "; ".join(parts) if parts else (
                        "price %s vs intrinsic value %s" % (_money(p), _money(v["base_value"])) if measured and v and v.get("base_value")
                        else "price is inside your rules" if measured else "needs a price and a Valuation run")})
        b = it.get("baseline")
        if not b or not v:
            out.append({"key": "business", "label": "Business got worse", "status": "n/a",
                        "detail": "needs a Valuation run" if not v else "write a thesis to set the baseline"})
        else:
            parts = []
            r0, r1 = VERDICT_RANK.get(str(b.get("verdict") or "").upper()), VERDICT_RANK.get(str(v.get("verdict") or "").upper())
            if r0 is not None and r1 is not None and r1 < r0:
                parts.append("verdict fell from %s to %s" % (b["verdict"], v["verdict"]))
            if b.get("quality") is not None and v.get("quality") is not None and v["quality"] <= b["quality"] - QUALITY_DROP:
                parts.append("quality fell from %.1f to %.1f" % (b["quality"], v["quality"]))
            out.append({"key": "business", "label": "Business got worse", "status": "break" if parts else "ok",
                        "detail": "; ".join(parts) if parts else "%s, quality %s (baseline %s, %s)" % (
                            v.get("verdict"), "%.1f" % v["quality"] if v.get("quality") is not None else "unknown",
                            b.get("verdict"), "%.1f" % b["quality"] if b.get("quality") is not None else "unknown")})
        return out

    def signals(self, t):
        """What smart money is doing in this one name, plus the 'smart money out' check.
        Several UW calls, cached (30 min insiders, 12 h 13F)."""
        t = clean_ticker(t)
        out = {"ticker": t, "lights": [], "errors": []}
        # insiders: selling (the Morning warning rule) and open-market buying
        try:
            h = self.market.holding(t)
            out["earnings"] = h.get("earnings")
            out["insider_sell"] = h.get("insider")
        except Exception as exc:           # noqa: BLE001
            out["errors"].append("insiders: %s" % exc)
        try:
            out["insider_buy"] = self.market._cached(("ibuy", t), 1800, lambda: insider_buying(self.market.client, t, self.today()))
        except Exception as exc:           # noqa: BLE001
            out["errors"].append("insider buying: %s" % exc)
        try:
            out["funds"] = self.market._cached(("own", t), 43200, lambda: fund_changes(self.market.client, t))
        except Exception as exc:           # noqa: BLE001
            out["errors"].append("13F: %s" % exc)
        # the desks, when running
        try:
            d = self.view.for_ticker(t)
        except Exception:                  # noqa: BLE001
            d = {}
        c = ((d.get("confluence") or {}).get("card")) or None
        fc = ((d.get("flow") or {}).get("card")) or None
        cl = ((d.get("institutional") or {}).get("cluster")) or None
        s, _ = self.view._get("swing", "/api/scan", ttl=60)
        sw = next((a for a in (s or {}).get("alerts") or [] if a.get("ticker") == t), None)
        out["desks"] = {"confluence": c, "flow": fc, "institutional": cl,
                        "swing": sw and {"score": sw.get("score"), "direction": sw.get("direction"), "drivers": sw.get("drivers")}}
        L = out["lights"]
        ib = out.get("insider_buy") or {}
        if ib.get("usd", 0) >= INSIDER_BUY_USD or ib.get("people", 0) >= INSIDER_BUY_PEOPLE:
            L.append({"kind": "insider", "tone": "good", "text": "Insiders bought %s (%d people, %d days)" % (
                _short(ib["usd"]), ib["people"], INSIDER_BUY_DAYS)})
        f = out.get("funds") or {}
        if f.get("net") is not None and f["net"] >= -FUND_NET and f.get("adds", 0) >= f.get("exits", 0):
            L.append({"kind": "funds", "tone": "good", "text": "13F funds added %+.0f%% (%s)" % (100 * f["net"], f.get("report_date"))})
        if c and (c.get("score") or 0) >= 55:
            L.append({"kind": "confluence", "tone": "good", "text": "Confluence %.0f (%s)" % (c["score"], c.get("band"))})
        if cl and (cl.get("n_buyers") or 0) >= 2:
            L.append({"kind": "cluster", "tone": "good", "text": "%d small funds buying (13F cluster)" % cl["n_buyers"]})
        if fc and fc.get("lane", "board") == "board" and fc.get("direction"):
            bull = str(fc["direction"]).lower().startswith("bull")
            L.append({"kind": "flow", "tone": "good" if bull else "bad", "text": "Options flow %s, score %.0f" % (
                "bullish" if bull else "bearish", fc.get("score") or 0)})
        if sw:
            L.append({"kind": "swing", "tone": "good" if sw.get("direction") == "BULL" else "bad",
                      "text": "Swing %s setup, score %.0f" % ("bullish" if sw.get("direction") == "BULL" else "bearish", abs(sw.get("score") or 0))})
        # the check you chose: smart money leaving
        parts, measured = [], False
        ins = out.get("insider_sell")
        if ins is not None:
            measured = True
            if ins.get("nonplan_usd", 0) >= market_mod.INSIDER_MIN_USD:
                parts.append("insiders sold %s outside 10b5-1 plans in %d days" % (_short(ins["nonplan_usd"]), ins.get("days", 30)))
        if f.get("holders"):
            measured = True
            if f.get("exits", 0) >= FUND_EXITS and f.get("net") is not None and f["net"] <= FUND_NET:
                parts.append("%d funds exited and institutions cut %.0f%% in the %s 13F" % (f["exits"], -100 * f["net"], f.get("report_date")))
        out["check"] = {"key": "smart", "label": "Smart money leaving", "status": "break" if parts else "ok" if measured else "n/a",
                        "detail": "; ".join(parts) if parts else (
                            "no heavy insider selling; 13F net %s" % ("%+.0f%%" % (100 * f["net"]) if f.get("net") is not None else "n/a")
                            if measured else "couldn't read insider or 13F data")}
        return out

    def view_all(self):
        mos = self.mos()
        items = []
        for t, is_hold, on_list, db, h in self.names():
            try:
                it = self.item(t, mos=mos, hold=h if is_hold else None, db=db)
            except Exception as exc:       # noqa: BLE001
                it = {"ticker": t, "holding": is_hold, "on_list": on_list, "error": str(exc), "checks": []}
            items.append(it)
        order = {"in": 0, "near": 1, "above": 2, None: 3}
        items.sort(key=lambda i: (not any(c["status"] == "break" for c in i.get("checks", [])) or not i.get("holding"),
                                  order.get(i.get("zone"), 3), i.get("to_buy") if i.get("to_buy") is not None else 9, i["ticker"]))
        return {"items": items, "mos": mos, "events": self.events(14),
                "rules": {"near_zone": NEAR_ZONE, "quality_drop": QUALITY_DROP, "fund_exits": FUND_EXITS, "fund_net": FUND_NET,
                          "insider_sell_usd": market_mod.INSIDER_MIN_USD, "insider_buy_usd": INSIDER_BUY_USD,
                          "retry_days": RETRY_DAYS, "give_up_days": GIVE_UP_DAYS}}

    def morning(self):
        """The Morning page's 'Your names' block: buy zones, broken theses (from the checks that are
        free to compute) and the last few days of re-valuations."""
        v = self.view_all()
        return {"in_zone": [i for i in v["items"] if i.get("zone") in ("in", "near")],
                "breaks": [i for i in v["items"] if any(c["status"] == "break" for c in i.get("checks", []))],
                "events": [e for e in v["events"] if e["kind"] in ("revalued", "stale")][:8],
                "count": len(v["items"])}

    # ------------------------------------------------------------ after earnings
    def refresh(self):
        """One pass: record upcoming report dates, and re-value names whose report has passed."""
        today = self.today()
        iso = today.isoformat()
        done = 0
        for t, is_hold, on_list, db, h in self.names():
            if not db:
                self._upsert(t, on_list=0)
                db = self._row(t)
            # 1) the upcoming report date, checked once a day, never overwriting one that has passed
            if db.get("er_checked") != iso:
                try:
                    er = (self.market.holding(t) or {}).get("earnings")
                except Exception:          # noqa: BLE001
                    er = None
                f = {"er_checked": iso}
                if er and er.get("date") and (not db.get("next_er") or db["next_er"] > iso):
                    f["next_er"] = er["date"]
                self._upsert(t, **f)
                db = self._row(t)
            # 2) the report has passed: re-value (the day AFTER, so after-close reports are in)
            er_day = db.get("next_er")
            if not er_day or er_day >= iso or done >= MAX_RERUNS:
                continue
            if db.get("rerun_on") == iso:
                continue
            age = (today - dt.date.fromisoformat(er_day)).days
            last = db.get("rerun_on")
            if last and (today - dt.date.fromisoformat(last)).days < RETRY_DAYS:
                continue
            before = self.valuation(t)
            if before and str(before.get("run_at") or "")[:10] > er_day and (db.get("rerun_tries") or 0) == 0:
                self._upsert(t, next_er=None, rerun_tries=0)       # you already re-ran it yourself
                continue
            code, res = self.rerun(t)
            done += 1
            if code != 200:
                self._upsert(t, rerun_on=iso, rerun_tries=(db.get("rerun_tries") or 0) + 1)
                self.event(t, "error", "Couldn't re-value after earnings: %s" % res.get("error"))
                continue
            after = self.valuation(t)
            fresh = not before or not after or after.get("period_end") != before.get("period_end")
            if fresh or age >= GIVE_UP_DAYS:
                self._upsert(t, next_er=None, rerun_on=iso, rerun_tries=0)
                self.event(t, "revalued" if fresh else "stale", describe_change(t, before, after, er_day, fresh),
                           {"before": before, "after": after, "report": er_day})
            else:
                self._upsert(t, rerun_on=iso, rerun_tries=(db.get("rerun_tries") or 0) + 1)
        return done

    def rerun(self, t):
        """Re-value one name on the Valuation desk (starting it if it's off)."""
        t = clean_ticker(t)
        if not t:
            return 400, {"error": "That isn't a ticker."}
        if self.sup is not None:
            try:
                self.sup.ensure("valuation")
                for _ in range(30):
                    if self.view.desk_ports().get("valuation"):
                        break
                    time.sleep(1)
                    self.sup.refresh(only="valuation")
            except Exception as exc:       # noqa: BLE001
                return 503, {"error": "couldn't start the Valuation desk (%s)" % exc}
        code, body = self.view.post("valuation", "/api/run", {"ticker": t}, timeout=180)
        if code == 200:
            self.view.cache.pop(("valuation", "/api/history?ticker=%s" % t), None)
        return code, body

    def rerun_now(self, t):
        """The page's Re-value button: run it and log what changed."""
        t = clean_ticker(t)
        before = self.valuation(t) if t else None
        code, res = self.rerun(t)
        if code == 200:
            after = self.valuation(t)
            self.event(t, "revalued", "%s re-valued by hand: %s" % (t, describe_change(t, before, after, "", True).split(": ", 1)[-1]),
                       {"before": before, "after": after})
            return 200, {"ok": True, "item": self.item(t)}
        return code, res

    def run_forever(self, interval=1800):
        def loop():
            time.sleep(90)
            while True:
                try:
                    n = self.refresh()
                    if n:
                        self.log("watchlist: re-valued %d name(s) after earnings" % n)
                except Exception as exc:   # noqa: BLE001
                    self.log("watchlist refresher error: %s" % exc)
                time.sleep(interval)
        th = threading.Thread(target=loop, daemon=True, name="watchlist")
        th.start()
        return th


# ---------------------------------------------------------------- data helpers
def insider_buying(client, t, today):
    """Open-market purchases (Form 4, code P) in the last INSIDER_BUY_DAYS."""
    start = (today - dt.timedelta(days=INSIDER_BUY_DAYS)).isoformat()
    raw = rows(client.get("/api/insider/transactions", {"ticker_symbol": t, "transaction_codes[]": ["P"],
                                                        "start_date": start, "limit": 200})[1])
    seen, people, usd = set(), set(), 0.0
    for r in raw:
        key = tuple(sorted(r.get("ids") or [])) or (r.get("id"),)
        if key in seen or (r.get("transaction_code") or "").upper() != "P":
            continue
        if str(r.get("formtype") or "4").upper() not in ("4", "4/A"):
            continue
        seen.add(key)
        px = num(r.get("price")) or num(r.get("stock_price")) or 0
        usd += abs(num(r.get("amount")) or 0) * px
        people.add(text(r.get("owner_name")) or "?")
    return {"usd": usd, "people": len(people), "days": INSIDER_BUY_DAYS}


def fund_changes(client, t):
    """Latest 13F quarter for the ticker: adds, cuts, full exits, net change of institutional shares."""
    raw = rows(client.get("/api/institution/%s/ownership" % urllib.parse.quote(t, safe=""), {"limit": 500})[1])
    if not raw:
        return {"holders": 0}
    latest = max(str(r.get("report_date") or "") for r in raw)
    raw = [r for r in raw if str(r.get("report_date") or "") == latest]
    adds = cuts = exits = 0
    now_units = prev_units = 0.0
    for r in raw:
        u = num(r.get("units")) or 0
        ch = num(r.get("units_changed")) or 0
        now_units += u
        prev_units += u - ch
        if ch > 0:
            adds += 1
        elif ch < 0:
            cuts += 1
            if u <= 0:
                exits += 1
    net = (now_units / prev_units - 1) if prev_units > 0 else None
    return {"holders": len(raw), "adds": adds, "cuts": cuts, "exits": exits, "net": net, "report_date": latest}


def describe_change(t, before, after, er_day, fresh):
    if not after:
        return "%s: re-valued after the %s report, but the run returned nothing" % (t, er_day)
    if not fresh:
        return "%s: no new financials %d+ days after the %s report; stopped retrying (last run kept)" % (t, GIVE_UP_DAYS, er_day)
    if not before:
        return "%s: first valuation after the %s report: %s, value %s" % (t, er_day, after.get("verdict"), _money(after["base_value"] or 0))
    bits = []
    if before.get("base_value") and after.get("base_value") and abs(after["base_value"] / before["base_value"] - 1) >= 0.005:
        bits.append("value %s -> %s (%+.0f%%)" % (_money(before["base_value"]), _money(after["base_value"]),
                                                   100 * (after["base_value"] / before["base_value"] - 1)))
    if before.get("verdict") != after.get("verdict"):
        bits.append("verdict %s -> %s" % (before.get("verdict"), after.get("verdict")))
    if before.get("quality") is not None and after.get("quality") is not None and abs(after["quality"] - before["quality"]) >= 0.1:
        bits.append("quality %.1f -> %.1f" % (before["quality"], after["quality"]))
    return "%s after the %s report: %s" % (t, er_day, "; ".join(bits) or "no change")
