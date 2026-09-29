"""
Unusual Whales API client for the Valuation Desk (adapted from the Confluence Desk).

Stdlib only -- no pip installs, same no-install promise as Swing Desk.

Every quirk documented in the UW project notes is handled here in one place:
  * numeric fields come back as strings ("12345678.00") and sometimes null
  * list responses wrap in "data" OR "result" depending on the endpoint
  * 429/5xx need backoff; a quarterly scan makes ~1-3k calls
"""

import datetime
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
    one machine and 'UW SwingDesk' on the other one, and a desk that
    silently cannot find a token is indistinguishable from a desk whose token
    is wrong.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    siblings = [
        os.path.join(parent, name, ".env")
        for name in ("Swing Desk", "UW SwingDesk", "UW Swing Desk",
                     "Institutional Desk", "UW Insider Desk", "GEX ES Desk",
                     "Confluence Desk")
    ]
    merged = {}
    for path in siblings + [os.path.join(here, ".env")]:
        merged.update(_parse_env_file(path))
    for key, val in os.environ.items():
        if key.startswith(("UW_", "VDESK_")):
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
            # ~3 requests/sec across all worker threads. Raise IDESK_MIN_INTERVAL
            # if the API ever starts answering 429.
            else env_num("VDESK_MIN_INTERVAL", 0.32)
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
                    "User-Agent": "uw-valuation-desk/1.0",
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

    # -- endpoints (paths verified in get_public_api_docs 2026-09-22) --
    #
    # /info, /income-statements, /balance-sheets and /cash-flows take ONLY the
    # ticker path parameter on REST -- no report_type -- so one call returns
    # annual AND quarterly rows mixed, and the model splits them on
    # `report_type`. (The MCP tools take a report_type filter; REST does not.
    # Same data, different surface -- do not port the parameter across.)

    def _t(self, ticker):
        return urllib.parse.quote(ticker.upper())

    def info(self, ticker):
        return self.get("/api/stock/%s/info" % self._t(ticker))

    def income_statements(self, ticker):
        return self.get("/api/stock/%s/income-statements" % self._t(ticker))

    def balance_sheets(self, ticker):
        return self.get("/api/stock/%s/balance-sheets" % self._t(ticker))

    def cash_flows(self, ticker):
        return self.get("/api/stock/%s/cash-flows" % self._t(ticker))

    def insider_trades(self, ticker, start_date):
        """
        GET /api/insider/transactions (path + params verified in
        get_public_api_docs 2026-09-23). Open-market P and S only, executed
        Form 4s, non-derivative. One page of 500 (REST max) covers two years for
        all but the most-traded megacaps; the model de-dupes grouped rows.
        """
        return self.get("/api/insider/transactions", {
            "ticker_symbol": ticker.upper(), "transaction_codes[]": ["P", "S"],
            "security_ad_codes[]": ["NA", "ND"], "form_types[]": ["4", "4/A"],
            "start_date": start_date, "limit": 500})

    def earnings(self, ticker):
        """GET /api/stock/{t}/earnings -- reported vs estimated EPS, full history."""
        return self.get("/api/stock/%s/earnings" % self._t(ticker))

    def screener_row(self, ticker):
        rows = self.get_list("/api/screener/stocks", {"ticker": ticker.upper(), "limit": 1})
        return rows[0] if rows else {}

    def ohlc_daily(self, ticker, timeframe="1M"):
        """
        GET /api/stock/{t}/ohlc/1d -- interleaves extended-hours rows with the
        regular session (Confluence Desk note); keep market_time == "r".
        """
        rows = self.get_list("/api/stock/%s/ohlc/1d" % self._t(ticker), {"timeframe": timeframe})
        return [r for r in rows if (r.get("market_time") or "r") == "r"]


PRICE_FIELDS = ("price", "last", "last_price", "close", "prev_close")


def pick_price(*rows):
    """First positive price among the known field names, with where it came from."""
    for label, row in rows:
        if not isinstance(row, dict):
            continue
        for key in PRICE_FIELDS:
            v = num(row.get(key), 0.0)
            if v > 0:
                return v, "%s.%s" % (label, key)
    return None, None


def fetch_all(client, ticker, log=None):
    """
    Everything one valuation needs: 4 calls, +1-2 only if /info carries no price.
    Returns (payloads, meta). Payload shapes are passed to the model VERBATIM.
    """
    t = ticker.upper()
    info = client.info(t)
    if not info or not (info.get("data") or info.get("symbol")):
        raise ValueError("Unusual Whales has no ticker called %s." % t)
    inner = info.get("data") if isinstance(info.get("data"), dict) else info
    price, src = pick_price(("info", info), ("info", inner))
    if price is None:
        try:
            price, src = pick_price(("screener", client.screener_row(t)))
        except RuntimeError:
            price = None
    if price is None:
        bars = client.ohlc_daily(t)
        if bars:
            bars.sort(key=lambda r: str(r.get("start_time") or r.get("date") or ""))
            price, src = pick_price(("ohlc", bars[-1]))
    if price is None:
        raise ValueError("No price available for %s from /info, the screener or daily candles." % t)
    info = dict(info)
    info["price"] = price
    payloads = {
        "info": info,
        "is_a": client.income_statements(t),
        "bs_a": client.balance_sheets(t),
        "cf_a": client.cash_flows(t),
    }
    # One endpoint, both report types: hand the same payload to both slots.
    payloads["is_q"], payloads["bs_q"], payloads["cf_q"] = payloads["is_a"], payloads["bs_a"], payloads["cf_a"]
    # Model 3.2: management footprints. Never fatal -- a failure leaves the
    # component UNMEASURED and says so, it does not stop the valuation.
    today = datetime.date.today()
    payloads["as_of"] = today.isoformat()
    missing = []
    if hasattr(client, "insider_trades"):
        try:
            payloads["insiders"] = client.insider_trades(t, (today - datetime.timedelta(days=730)).isoformat())
        except RuntimeError as exc:
            missing.append("insider filings (%s)" % exc)
    if hasattr(client, "earnings"):
        try:
            payloads["earnings"] = client.earnings(t)
        except RuntimeError as exc:
            missing.append("earnings history (%s)" % exc)
    return payloads, {"price_source": src, "api_calls": client.calls, "missing": missing}


def fx_table():
    """VDESK_FX_TWD=32.1 -> {"TWD": 32.1}. Bad values are ignored, never fatal."""
    out = {}
    for key, val in ENV.items():
        if key.startswith("VDESK_FX_"):
            v = num(val, 0.0)
            if v > 0:
                out[key[len("VDESK_FX_"):].upper()] = v
    return out


if __name__ == "__main__":
    cli = Client()
    payloads, meta = fetch_all(cli, sys.argv[1] if len(sys.argv) > 1 else "AAPL")
    print("ok: price %s via %s, %d calls" % (payloads["info"]["price"], meta["price_source"], cli.calls))
