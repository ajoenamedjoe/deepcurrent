"""
Unusual Whales API client for the Growth Leaders desk.

Stdlib only -- no pip installs, same no-install promise as Swing Desk.

Every quirk documented in the UW project notes is handled here in one place:
  * numeric fields come back as strings ("12345678.00") and sometimes null
  * list responses wrap in "data" OR "result" depending on the endpoint
  * 429/5xx need backoff; the first daily scan makes ~1-2k calls, later ones fewer (earnings and 13F are cached)
"""

import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.unusualwhales.com"

# ---------------------------------------------------------------- env / token

def _parse_env_file(path):
    """Parse a .env file. Handles CRLF, BOM, quotes, '=' padding, comments."""
    out = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                # split on the FIRST '=' only -- tokens can contain '='
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                if key:
                    out[key] = val
    except OSError:
        pass
    return out


def load_env():
    """
    Config resolution order (first hit wins per key):
      1. real environment variables
      2. .env next to this file
      3. a sibling desk's .env -- so there is only ever one copy of the token
         and the Discord webhook on the machine.

    Both spellings of the Swing Desk folder are tried: it is 'Swing Desk' on
    one install and 'UW SwingDesk' on another, and a desk that
    silently cannot find a token is indistinguishable from a desk whose token
    is wrong.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    siblings = [
        os.path.join(parent, name, ".env")
        for name in ("Swing Desk", "UW SwingDesk", "UW Swing Desk", "Confluence Desk",
                     "Institutional Desk", "Valuation Desk", "UW Insider Desk")
    ]
    merged = {}
    for path in siblings + [os.path.join(here, ".env")]:
        merged.update(_parse_env_file(path))
    for key, val in os.environ.items():
        if key.startswith(("UW_", "DISCORD_", "GDESK_")):
            merged[key] = val
    return merged


ENV = load_env()


def env_str(key, default=""):
    val = ENV.get(key, default)
    return val if val is not None else default


def env_num(key, default):
    raw = ENV.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).replace("_", "").replace(",", ""))
    except (TypeError, ValueError):
        return default


def env_int(key, default):
    return int(env_num(key, default))


def env_bool(key, default=False):
    raw = ENV.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- coercion

def num(val, default=0.0):
    """Coerce a UW numeric field (string, null, int, float) to float."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        try:
            if val != val:  # NaN
                return default
        except TypeError:
            return default
        return float(val)
    try:
        text = str(val).strip().replace(",", "")
        if text == "" or text.lower() in ("none", "null", "nan"):
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def inum(val, default=0):
    """Coerce to int via float -- the API sends '123456.0' for integers."""
    return int(num(val, default))


def opt_num(val):
    """Like num() but preserves the null/absent distinction."""
    if val is None:
        return None
    if isinstance(val, str) and val.strip() == "":
        return None
    try:
        return float(str(val).replace(",", ""))
    except (TypeError, ValueError):
        return None


def unwrap(payload):
    """List responses wrap in 'data' or 'result'. Never assume which."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "result", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        # single-object responses
        return [payload] if payload else []
    return []


# ---------------------------------------------------------------- client

class RateLimitError(RuntimeError):
    pass


class Client:
    """Thread-safe, rate-limited UW REST client."""

    def __init__(self, token=None, min_interval=None, timeout=30):
        self.token = token or env_str("UW_API_TOKEN")
        if not self.token:
            raise RuntimeError(
                "No UW_API_TOKEN found. Put it in .env next to this file, or "
                "leave your existing 'Swing Desk\\.env' in place next door."
            )
        self.min_interval = (
            min_interval if min_interval is not None
            # ~3 requests/sec across all worker threads. Raise GDESK_MIN_INTERVAL
            # if the API ever starts answering 429.
            else env_num("GDESK_MIN_INTERVAL", 0.34)
        )
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.calls = 0
        self.errors = 0
        self._ctx = ssl.create_default_context()

    # -- internals -------------------------------------------------

    def _throttle(self):
        with self._lock:
            gap = time.time() - self._last_call
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last_call = time.time()
            self.calls += 1

    def get(self, path, params=None, retries=4):
        url = BASE + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean, doseq=True)

        delay = 1.0
        last_err = None
        for attempt in range(retries + 1):
            self._throttle()
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json, text/plain",
                    "Authorization": "Bearer " + self.token,
                    "User-Agent": "growth-leaders-desk/1.0",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_err = exc
                code = exc.code
                if code in (401, 403):
                    raise RuntimeError(
                        "UW API rejected the token (HTTP %d). Check UW_API_TOKEN." % code
                    ) from exc
                if code == 404:
                    return {}
                if code == 422:
                    # bad params for this particular resource -- not retryable
                    return {}
                if code == 429 or code >= 500:
                    if attempt < retries:
                        time.sleep(delay)
                        delay = min(delay * 2, 20.0)
                        continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(delay)
                    delay = min(delay * 2, 20.0)
                    continue
            break

        self.errors += 1
        raise RuntimeError("GET %s failed: %s" % (path, last_err))

    def get_list(self, path, params=None):
        return unwrap(self.get(path, params))

    def paged(self, path, params=None, page_size=500, max_pages=80, page_key="page"):
        """Yield rows from a page-index paginated endpoint until short page."""
        params = dict(params or {})
        params["limit"] = page_size
        page = 0
        while page < max_pages:
            params[page_key] = page
            rows = self.get_list(path, params)
            if not rows:
                return
            for row in rows:
                yield row
            if len(rows) < page_size:
                return
            page += 1

    # -- endpoints (paths from the UW public API docs; shapes MCP-verified 2026-09-28) --

    def screener_page(self, offset, limit=500, min_marketcap=300e6, min_price=5):
        """GET /api/screener/stocks -- the whole universe in pages of 500 (the REST cap), ordered by market cap.
        Returns: 3/6/12-month returns (fractions), 52-week high/low, sma_50/200, relative_volume, industry_type,
        shares_outstanding_growth_4q. `offset` is a PAGE index, not a row count."""
        return self.get_list("/api/screener/stocks", {
            "issue_types[]": ["Common Stock"], "min_marketcap": str(int(min_marketcap)),
            "min_underlying_price": str(min_price), "order": "marketcap", "order_direction": "desc",
            "limit": limit, "offset": offset})

    def screener_tickers(self, tickers):
        """The same rows for a list of tickers (intraday breakout watch, one call)."""
        return self.get_list("/api/screener/stocks", {"ticker": ",".join(tickers), "limit": min(500, max(1, len(tickers)))})

    def earnings(self, ticker):
        """GET /api/stock/{t}/earnings -- quarterly and annual rows; the newest is the NEXT report (eps null)."""
        return self.get_list("/api/stock/%s/earnings" % urllib.parse.quote(ticker, safe=""))

    def ownership(self, ticker):
        """GET /api/institution/{t}/ownership -- 13F holders with units, units_changed, historical_units."""
        return self.get_list("/api/institution/%s/ownership" % urllib.parse.quote(ticker, safe=""), {"limit": 500})

    def ohlc_daily(self, ticker, timeframe="1Y"):
        """GET /api/stock/{t}/ohlc/1d -- oldest first, extended-hours rows interleaved: keep market_time "r"."""
        rows = self.get_list("/api/stock/%s/ohlc/1d" % urllib.parse.quote(ticker, safe=""), {"timeframe": timeframe})
        return [r for r in rows if (r.get("market_time") or "r") == "r"]

if __name__ == "__main__":
    cli = Client()
    rows = cli.screener_tickers(["SPY"])
    print("ok, %d screener rows" % len(rows))
    sys.exit(0)
