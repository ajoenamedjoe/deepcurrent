"""
Read what the running desks say, through each desk's own HTTP API (its public
contract), never its database. A desk that isn't running is reported as such --
nothing here starts a desk.

Routes used (read from each desk's server.py on the PC, 2026-09-24):
  Valuation     GET /api/history?ticker=T -> {"runs": [{id, ticker, run_at, price, base_value,
                                              bull_value, bear_value, score, verdict, ...}]}
                GET /api/health
  Confluence    GET /api/board -> {"cards": [{ticker, score, band, carried_by, quote, name, ...}]}
                GET /api/health
  Institutional GET /api/clusters -> {"report_date", "clusters": [{ticker, score, n_buyers,
                                      n_new, buy_value, full_name, close, ...}]}
                GET /api/state
  Flow          GET /api/flow -> {"meta": {...}, "cards": [{ticker, score, direction, lane, ...}]}
                GET /api/health
  Swing         GET /api/scan -> {"alerts": [{ticker, score (signed), direction BULL|BEAR, drivers,
                                   horizon{detail}, price, persistence{streak}}], "market": {open}}
                GET /api/health
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 4.0
SWING_LABELS = {"flow": "flow", "oi": "OI", "dark": "dark pool", "gamma": "gamma", "tech": "chart"}


def http_json(port, path, timeout=TIMEOUT):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class Desks:
    """desk_ports() -> {desk_id: port or None} for desks that are RUNNING."""

    def __init__(self, desk_ports, fetch=None):
        self.desk_ports = desk_ports
        self.fetch = fetch or http_json
        self.cache = {}

    def _get(self, did, path, ttl=60, timeout=None):
        port = self.desk_ports().get(did)
        if not port:
            return None, "not running"
        key = (did, path)
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1], None
        try:
            val = self.fetch(port, path, **({"timeout": timeout} if timeout and self.fetch is http_json else {}))
        except Exception as exc:          # noqa: BLE001
            return None, "no answer (%s)" % exc.__class__.__name__
        self.cache[key] = (time.time(), val)
        return val, None

    def post(self, did, path, body, timeout=60):
        """POST JSON to a running desk (server to server: no browser Origin, so the desk's guard lets it
        through as a same-machine tool). Returns (http_code, body)."""
        port = self.desk_ports().get(did)
        if not port:
            return 503, {"error": "the %s desk is not running" % did}
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except Exception:          # noqa: BLE001
                return exc.code, {"error": "the %s desk answered HTTP %s" % (did, exc.code)}
        except Exception as exc:          # noqa: BLE001
            return 502, {"error": "the %s desk did not answer (%s)" % (did, exc.__class__.__name__)}

    # ------------------------------------------------------------ per ticker
    def for_ticker(self, t):
        t = t.upper()
        out = {}
        v, err = self._get("valuation", "/api/history?ticker=%s" % t, ttl=30)
        runs = (v or {}).get("runs") or []
        out["valuation"] = {"error": err, "found": bool(runs),
                            "run": runs[0] if runs else None, "runs": len(runs)}
        c, err = self._get("confluence", "/api/board")
        card = _find((c or {}).get("cards"), t)
        out["confluence"] = {"error": err, "found": bool(card), "card": _slim(card, (
            "ticker", "name", "score", "band", "carried_by", "quote", "rank", "people", "notional", "last_date")),
            "pending": bool((c or {}).get("pending"))}
        i, err = self._get("institutional", "/api/clusters")
        cl = _find((i or {}).get("clusters"), t)
        out["institutional"] = {"error": err, "found": bool(cl), "report_date": (i or {}).get("report_date"),
                                "cluster": _slim(cl, ("ticker", "full_name", "score", "n_buyers", "n_new",
                                                      "n_sellers", "buy_value", "best_vs_cost")),
                                "top_funds": [b.get("fund") for b in (cl or {}).get("buyers", [])[:3]]}
        f, err = self._get("flow", "/api/flow", ttl=30)
        fc = _find((f or {}).get("cards"), t)
        out["flow"] = {"error": err, "found": bool(fc), "card": _slim(fc, (
            "ticker", "score", "direction", "lane", "carried_by", "ask_premium", "ask_share",
            "alerts", "contracts", "session")),
            "scanned_at": ((f or {}).get("meta") or {}).get("scanned_at")}
        out["growth"] = self.growth_card(t)
        return out

    # ------------------------------------------------------------ growth leaders (r16)
    def growth_card(self, t):
        g, err = self._get("growth", "/api/ticker?t=%s" % urllib.parse.quote(t, safe=""), ttl=600, timeout=20)
        if err or not g or g.get("error"):
            return {"error": err or (g or {}).get("error") or "no answer", "found": False}
        return {"error": None, "found": True, "card": _slim(g, (
            "ticker", "score", "leader", "six_pass", "rs", "checks", "base", "passed", "measured", "earned",
            "in_universe", "model"))}

    def growth_market(self):
        """Market direction from the Growth Leaders desk: confirmed uptrend / under pressure / correction."""
        m, err = self._get("growth", "/api/market", ttl=300)
        if err or not m or not m.get("state"):
            return {"error": err or "no reading yet"}
        return {k: m.get(k) for k in ("state", "label", "ok", "text", "price_only")}

    # ------------------------------------------------------------ flow tape
    def flow_tape(self, alert=None, contract=None, date=None):
        """The Flow Desk's /api/tape, passed through (it holds the UW token and the
        validation). Returns (http_code, body)."""
        port = self.desk_ports().get("flow")
        if not port:
            return 503, {"error": "The Unusual Options Flow desk is not running - open it from Desks to start it."}
        q = {k: v for k, v in (("alert", alert), ("contract", contract), ("date", date)) if v}
        path = "/api/tape?" + urllib.parse.urlencode(q)
        try:
            return 200, self.fetch(port, path, **({"timeout": 20} if self.fetch is http_json else {}))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except Exception:          # noqa: BLE001
                return exc.code, {"error": "the flow desk answered HTTP %s" % exc.code}
        except Exception as exc:          # noqa: BLE001
            return 502, {"error": "the flow desk did not answer (%s)" % exc.__class__.__name__}

    # ------------------------------------------------------------ top picks
    def picks(self, now=None):
        """Each running desk's current top names, for the Morning page and the track record."""
        now = now or time.time()
        out = {}
        c, err = self._get("confluence", "/api/board")
        cards = sorted([x for x in (c or {}).get("cards") or [] if x.get("ticker")],
                       key=lambda x: -(x.get("score") or 0))
        out["confluence"] = {"error": err, "items": [
            {"ticker": x["ticker"], "score": x.get("score"), "direction": "long", "name": x.get("name"),
             "why": ", ".join(x.get("carried_by") or []) or None, "price": _num(x.get("quote"))} for x in cards[:5]]}
        f, err = self._get("flow", "/api/flow")
        fl = [x for x in (f or {}).get("cards") or [] if x.get("ticker") and x.get("lane", "board") == "board"]
        fl.sort(key=lambda x: -(x.get("score") or 0))
        out["flow"] = {"error": err, "items": [
            {"ticker": x["ticker"], "score": x.get("score"),
             "direction": "short" if (x.get("direction") or "").lower().startswith("bear") else "long",
             "name": x.get("name"), "why": x.get("direction") or "undirected"} for x in fl[:5]
            if x.get("direction")]}
        i, err = self._get("institutional", "/api/clusters")
        cls = sorted((i or {}).get("clusters") or [], key=lambda x: -(x.get("score") or 0))
        out["institutional"] = {"error": err, "report_date": (i or {}).get("report_date"), "items": [
            {"ticker": x["ticker"], "score": x.get("score"), "direction": "long", "name": x.get("full_name"),
             "why": "%s fund%s buying" % (x.get("n_buyers"), "" if x.get("n_buyers") == 1 else "s"),
             "price": _num(x.get("close"))} for x in cls[:5]]}
        v, err = self._get("valuation", "/api/history")
        runs = []
        seen = set()
        for r in (v or {}).get("runs") or []:
            if r.get("ticker") in seen:
                continue
            seen.add(r.get("ticker"))
            age = _age_days(r.get("run_at"), now)
            if "BUY" in str(r.get("verdict") or "").upper() and age is not None and age <= 2:
                runs.append(r)
        runs.sort(key=lambda r: -(r.get("score") or 0))
        out["valuation"] = {"error": err, "items": [
            {"ticker": r["ticker"], "score": r.get("score"), "direction": "long", "name": None,
             "why": "%s, value $%s" % (r.get("verdict"), _fmt(r.get("base_value"))), "price": _num(r.get("price"))}
            for r in runs[:5]]}
        s, err = self._get("swing", "/api/scan")
        alerts = sorted((s or {}).get("alerts") or [], key=lambda x: -abs(x.get("score") or 0))
        out["swing"] = {"error": err, "items": [
            {"ticker": x["ticker"], "score": abs(x.get("score") or 0),
             "direction": "short" if x.get("direction") == "BEAR" else "long",
             "name": x.get("name"), "price": _num(x.get("price")),
             "why": "%s; %s" % (" + ".join(SWING_LABELS.get(k, k) for k in x.get("drivers") or []) or "score",
                                (x.get("horizon") or {}).get("detail") or "")}
            for x in alerts[:5] if x.get("ticker")]}
        g, err = self._get("growth", "/api/board")
        cards = [x for x in (g or {}).get("cards") or [] if x.get("ticker") and x.get("score") is not None]
        cards.sort(key=lambda x: (not x.get("leader"), -(x.get("score") or 0)))
        out["growth"] = {"error": err or ((g or {}).get("pending") and "first scan running") or None, "items": [
            {"ticker": x["ticker"], "score": x.get("score"), "direction": "long", "name": x.get("name"),
             "price": _num(x.get("price")),
             "why": "%s%s" % ("Leader, " if x.get("leader") else "", GROWTH_STATE.get((x.get("base") or {}).get("state"), "no base"))}
            for x in cards[:5] if x.get("leader") or (x.get("score") or 0) >= 70]}
        return out

    # ------------------------------------------------------------ health
    def health(self, did):
        path = {"institutional": "/api/state"}.get(did, "/api/health")
        h, err = self._get(did, path, ttl=15)
        if err:
            return {"error": err}
        h = h or {}
        problems = [str(x) for x in (h.get("error"), h.get("client_error"), h.get("last_error")) if x]
        if h.get("ok") is False and not problems:
            problems.append("desk reports not OK")
        info = {}
        for k in ("api_calls_total", "calls", "distinct_days", "last_report_date", "scanning", "scan_status"):
            if k in h:
                info[k] = h[k]
        meta = h.get("meta") if isinstance(h.get("meta"), dict) else {}
        for k in ("scanned_at", "session"):
            if k in h or k in meta:
                info[k] = h.get(k, meta.get(k))
        return {"ok": not problems, "problems": problems, "info": info}


GROWTH_STATE = {"breakout": "breaking out", "in_range": "in the buy range", "near_pivot": "near its pivot",
                "weak_breakout": "above the pivot on light volume", "extended": "extended", "in_base": "in a base",
                "none": "no base"}


def _find(items, t):
    for x in items or []:
        if isinstance(x, dict) and str(x.get("ticker") or "").upper() == t:
            return x
    return None


def _slim(d, keys):
    return {k: d.get(k) for k in keys if k in d} if d else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(v):
    n = _num(v)
    return "%.2f" % n if n is not None else "?"


def _age_days(ts, now):
    """Age in days of a desk timestamp ('2026-09-24 10:15:00', ISO, or a date)."""
    import datetime as dt
    if not ts:
        return None
    try:
        d = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T")[:26])
    except ValueError:
        return None
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    return (now - d.timestamp()) / 86400
