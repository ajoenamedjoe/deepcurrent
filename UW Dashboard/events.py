"""
Today's Events: the UW economic calendar and the day's earnings, side by side.

Endpoints (looked up in the UW public API docs, 2026-09-24 -- never invented):
  GET /api/market/economic-calendar          no parameters; returns a window of events
  GET /api/earnings/premarket?date=&limit=&page=    limit max 100, page from 0
  GET /api/earnings/afterhours?date=&limit=&page=

Landmines handled here:
  * numbers arrive as strings, null, or the literal "None"  -> num()
  * envelopes differ (data / result / bare list)            -> rows()
  * the MCP market-events tool returned every event TWICE   -> de-dupe on (event, time)
  * `expected_move` is DOLLARS, not percent                 -> pct = move / price
  * earnings REST row keys are unverified from the build box (neither shell can
    reach api.unusualwhales.com), so every field is read through a list of
    candidate names and /api/diag prints the first row's real keys.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import env

BASE = "https://api.unusualwhales.com"
MIN_INTERVAL = 0.35
TTL_CAL = 600
TTL_EARN = 300


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if s in ("", "None", "null", "NaN", "nan", "-"):
        return None
    pct = s.endswith("%")
    try:
        x = float(s.rstrip("%"))
    except ValueError:
        return None
    return x / 100.0 if pct else x


def text(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "None", "null") else s


def rows(payload):
    """Breadth-first: the first list of dicts found in the payload."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    queue = [payload]
    while queue:
        cur = queue.pop(0)
        if isinstance(cur, dict):
            for key in ("data", "result", "results", "items"):
                if isinstance(cur.get(key), list):
                    return [r for r in cur[key] if isinstance(r, dict)]
            queue.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list) and cur and isinstance(cur[0], dict):
            return cur
    return []


def pick(row, *names):
    for n in names:
        if n in row and text(row[n]) is not None:
            return row[n]
    return None


# ------------------------------------------------------------------ normalise
def norm_econ(raw):
    seen, out = set(), []
    for r in raw:
        ev = text(pick(r, "event", "name", "title"))
        t = text(pick(r, "time", "datetime", "date"))
        if not ev:
            continue
        key = (ev, t)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "event": ev, "time": t, "type": text(r.get("type")),
            "period": text(pick(r, "reported_period", "period")),
            "forecast": text(pick(r, "forecast", "consensus", "estimate")),
            "prev": text(pick(r, "prev", "previous", "prior")),
            "actual": text(pick(r, "actual", "value", "reported")),
        })
    out.sort(key=lambda e: e["time"] or "")
    return out


def norm_earn(raw, session):
    seen, out = set(), []
    for r in raw:
        sym = text(pick(r, "symbol", "ticker"))
        if not sym or sym in seen:
            continue
        seen.add(sym)
        price = num(pick(r, "curr", "price", "close", "last_price", "prev"))
        move = num(pick(r, "expected_move", "implied_move"))
        move_pct = num(r.get("expected_move_perc"))
        if move_pct is None and move is not None and price:
            move_pct = move / price
        est = num(pick(r, "street_mean_est", "eps_mean_est", "estimated_eps", "eps_estimate"))
        act = num(pick(r, "actual_eps", "reported_eps", "eps_actual"))
        reactions = [num(x) for x in (r.get("last_1d_reactions") or []) if num(x) is not None]
        out.append({
            "symbol": sym, "name": text(pick(r, "full_name", "name", "company_name")),
            "session": text(pick(r, "report_time", "market_time")) or session,
            "marketcap": num(pick(r, "marketcap", "market_cap")),
            "price": price, "move": move, "move_pct": move_pct,
            "eps_est": est, "eps_act": act,
            "surprise": (act - est) / abs(est) if act is not None and est not in (None, 0) else None,
            "sector": text(r.get("sector")),
            "sp500": str(r.get("is_s_p_500")).lower() == "true",
            "has_options": str(r.get("has_options")).lower() == "true",
            "logo": text(r.get("logo")),
            "avg_abs_reaction": (sum(abs(x) for x in reactions) / len(reactions)) if reactions else None,
            "report_date": text(r.get("report_date")),
        })
    out.sort(key=lambda e: -(e["marketcap"] or 0))
    return out


# ------------------------------------------------------------------ client
class Client:
    def __init__(self, token=None, opener=None):
        self.token, self.token_src = (token, "given") if token else env.find_token()
        self.opener = opener or self._http
        self.lock = threading.Lock()
        self.last = 0.0
        self.calls = 0

    def _http(self, url):
        req = urllib.request.Request(url, headers={
            "Authorization": "Bearer %s" % self.token, "Accept": "application/json",
            "User-Agent": "uw-dashboard/1"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def get(self, path, params=None, **kw):
        """GET a UW path. List values (e.g. "transaction_codes[]": ["S"]) repeat the key.
        429 and 5xx are retried twice with a short backoff."""
        if not self.token:
            raise RuntimeError("No UW_API_TOKEN found. Put it in the dashboard's .env, or keep a "
                               "desk's .env next door (Valuation / Confluence / Swing Desk).")
        merged = dict(params or {}, **kw)
        q = urllib.parse.urlencode({k: v for k, v in merged.items() if v is not None}, doseq=True)
        url = BASE + path + ("?" + q if q else "")
        delay = 1.0
        for attempt in range(3):
            with self.lock:
                wait = MIN_INTERVAL - (time.time() - self.last)
                if wait > 0:
                    time.sleep(wait)
                self.last = time.time()
                self.calls += 1
            try:
                return self.opener(url)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    raise RuntimeError("UW rejected the token (HTTP %d). Check UW_API_TOKEN." % exc.code)
                if exc.code == 404:
                    raise RuntimeError("UW has nothing at %s (HTTP 404)." % path)
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError("UW returned HTTP %d for %s" % (exc.code, path))


class Events:
    def __init__(self, client=None):
        self.client = client or Client()
        self.cache = {}
        self.lock = threading.Lock()

    def _cached(self, key, ttl, fn):
        now = time.time()
        with self.lock:
            hit = self.cache.get(key)
        if hit and now - hit["at"] < ttl:
            return dict(hit["value"], stale=False, fetched_at=hit["at"])
        try:
            value = fn()
            with self.lock:
                self.cache[key] = {"at": now, "value": value}
            return dict(value, stale=False, fetched_at=now)
        except Exception as exc:          # noqa: BLE001 -- keep the last good copy
            if hit:
                return dict(hit["value"], stale=True, error=str(exc), fetched_at=hit["at"])
            return {"rows": [], "stale": True, "error": str(exc), "fetched_at": None}

    def calendar(self):
        def fetch():
            _, payload = self.client.get("/api/market/economic-calendar")
            return {"rows": norm_econ(rows(payload))}
        return self._cached(("cal",), TTL_CAL, fetch)

    def earnings(self, date):
        def fetch():
            out = {}
            for session, path in (("premarket", "/api/earnings/premarket"),
                                  ("afterhours", "/api/earnings/afterhours")):
                got = []
                for page in range(4):
                    _, payload = self.client.get(path, date=date, limit=100, page=page)
                    batch = rows(payload)
                    got.extend(batch)
                    if len(batch) < 100:
                        break
                out[session] = norm_earn(got, session)
            return {"rows": out["premarket"] + out["afterhours"],
                    "counts": {k: len(v) for k, v in out.items()}}
        return self._cached(("earn", date), TTL_EARN, fetch)

    def diag(self, date):
        """Every endpoint this page calls: status, envelope, rows, first row's keys."""
        out = []
        for path, params in (("/api/market/economic-calendar", {}),
                             ("/api/earnings/premarket", {"date": date, "limit": 5}),
                             ("/api/earnings/afterhours", {"date": date, "limit": 5})):
            try:
                status, payload = self.client.get(path, **params)
                rs = rows(payload)
                out.append({"path": path, "ok": True, "status": status,
                            "envelope": list(payload.keys()) if isinstance(payload, dict) else "list",
                            "rows": len(rs), "first_keys": sorted(rs[0].keys()) if rs else []})
            except Exception as exc:      # noqa: BLE001
                out.append({"path": path, "ok": False, "error": str(exc)})
        return {"token_source": self.client.token_src, "checks": out}
