"""
Portfolio page: a published Google Sheet, shown two ways.

  * Sheet view  -- the sheet's own published HTML (/pubhtml) in a frame, so
                   the sheet's own formatting is kept exactly.
  * Table view  -- the published CSV fetched server-side, parsed, typed and
                   rendered sortable, with gains/losses coloured.

The CSV link 302-redirects to googleusercontent.com; urllib follows it.
A published sheet refreshes on Google's side about every 5 minutes, so a
60-second cache is plenty.
"""

import csv
import io
import re
import threading
import time
import urllib.parse
import urllib.request

TTL = 60


def sheet_urls(url):
    """From any published-sheet link, derive the CSV and embeddable HTML links."""
    url = (url or "").strip()
    if not url:
        return {"csv": None, "html": None, "source": None}
    m = re.search(r"/spreadsheets/d/e/([A-Za-z0-9_\-]+)/", url)
    gid = re.search(r"[?&#]gid=(\d+)", url)
    gid = gid.group(1) if gid else "0"
    if m:
        base = "https://docs.google.com/spreadsheets/d/e/%s/" % m.group(1)
        return {"csv": base + "pub?gid=%s&single=true&output=csv" % gid,
                "html": base + "pubhtml?gid=%s&single=true&widget=true&headers=false" % gid,
                "source": url}
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_\-]+)", url)
    if m:  # an ordinary (unpublished) link: CSV export works if link-shared
        base = "https://docs.google.com/spreadsheets/d/%s/" % m.group(1)
        return {"csv": base + "export?format=csv&gid=%s" % gid,
                "html": base + "htmlview?gid=%s&rm=minimal" % gid, "source": url}
    return {"csv": url if allowed_url(url) else None, "html": None, "source": url}


PRIVATE_HOST = re.compile(r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|0\.|\[|::)", re.I)


def allowed_url(url):
    """The sheet link may only be an https address on the internet: never file://, never this PC or the
    local network (before 2026-09-26 a file:// link read any file, the desks' .env included)."""
    u = urllib.parse.urlparse(str(url or "").strip())
    host = (u.hostname or "").lower()
    return u.scheme == "https" and bool(host) and not PRIVATE_HOST.match(host) and "." in host


NUM_RE = re.compile(r"^\(?-?[$€£]?\s*-?[\d,]*\.?\d+\s*%?\)?$")


def parse_cell(s):
    """'$1,234.50' -> 1234.5 ; '(12.5%)' -> -0.125 ; text -> None."""
    t = (s or "").strip()
    if not t or not NUM_RE.match(t.replace(" ", "")):
        return None
    neg = t.startswith("(") and t.endswith(")") or "-" in t
    pct = t.endswith("%") or t.endswith("%)")
    core = re.sub(r"[^\d.]", "", t)
    if not core or core == ".":
        return None
    v = float(core)
    v = -v if neg else v
    return v / 100.0 if pct else v


def parse(text):
    reader = list(csv.reader(io.StringIO(text)))
    reader = [r for r in reader if any((c or "").strip() for c in r)]
    if not reader:
        return {"columns": [], "rows": [], "types": []}
    # header = first row with >= 2 non-empty cells that are mostly not numbers
    hi = 0
    for i, r in enumerate(reader[:10]):
        filled = [c for c in r if c.strip()]
        if len(filled) >= 2 and sum(parse_cell(c) is None for c in filled) >= len(filled) * 0.6:
            hi = i
            break
    header = reader[hi]
    width = max(len(r) for r in reader[hi:])
    keep = [j for j in range(width)
            if (j < len(header) and header[j].strip()) or any(j < len(r) and r[j].strip() for r in reader[hi + 1:])]
    cols = [(header[j].strip() if j < len(header) else "") or "Col %d" % (j + 1) for j in keep]
    body = [[(r[j] if j < len(r) else "") for j in keep] for r in reader[hi + 1:]]
    types = []
    for k in range(len(cols)):
        vals = [row[k] for row in body if row[k].strip()]
        nums = [v for v in vals if parse_cell(v) is not None]
        kind = "num" if vals and len(nums) >= 0.7 * len(vals) else "text"
        signed = kind == "num" and bool(re.search(
            r"gain|loss|p/?l|change|chg|return|%|profit|unreal|today|day", cols[k], re.I))
        types.append({"kind": kind, "signed": signed,
                      "pct": kind == "num" and sum(v.strip().endswith("%") or v.strip().endswith("%)")
                                                   for v in nums) > len(nums) / 2})
    return {"columns": cols, "rows": body, "types": types, "title_rows": reader[:hi]}


class Portfolio:
    def __init__(self, fetch=None):
        self.fetch = fetch or self._fetch
        self.cache = {}
        self.lock = threading.Lock()

    @staticmethod
    def _fetch(url):
        if not allowed_url(url):
            raise ValueError("Only https links to a published sheet are allowed.")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 uw-dashboard"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if not allowed_url(r.geturl()):     # a redirect to this PC / the LAN is refused too
                raise ValueError("The sheet link redirected somewhere that isn't allowed.")
            return r.read(10_000_000).decode("utf-8-sig", "replace")

    def get(self, url, force=False):
        urls = sheet_urls(url)
        if not urls["csv"]:
            return dict(urls, error="No sheet link set. Paste it in Settings.", columns=[], rows=[])
        now = time.time()
        with self.lock:
            hit = self.cache.get(urls["csv"])
        if hit and not force and now - hit["at"] < TTL:
            return dict(hit["value"], **urls, fetched_at=hit["at"], stale=False)
        try:
            text = self.fetch(urls["csv"])
            if text.lstrip().lower().startswith("<!doctype html") or "<html" in text[:500].lower():
                raise RuntimeError("Google returned a web page, not CSV -- the sheet is probably not "
                                   "published. In Google Sheets: File > Share > Publish to web > CSV.")
            value = parse(text)
            with self.lock:
                self.cache[urls["csv"]] = {"at": now, "value": value}
            return dict(value, **urls, fetched_at=now, stale=False)
        except Exception as exc:          # noqa: BLE001
            if hit:
                return dict(hit["value"], **urls, fetched_at=hit["at"], stale=True, error=str(exc))
            return dict(urls, columns=[], rows=[], types=[], error=str(exc), stale=True)


# ------------------------------------------------------------------ visual model
# The expected sheet layout (from the original portfolio widget):
#   Open/Closed, Date, Close Date, Ticker, Amt, Entry, Mark, % P&L (Unreal), $$ P&L (Unreal), Thoughts
# Column names are matched loosely so a renamed header still works.
ALIASES = {
    "status": ["open/closed", "status", "open closed", "state"],
    "date": ["date", "open date", "opened", "entry date"],
    "close_date": ["close date", "closed", "closed date", "exit date"],
    "ticker": ["ticker", "symbol"],
    "amt": ["amt", "amount", "shares", "qty", "quantity", "size"],
    "entry": ["entry", "entry price", "cost", "avg cost", "buy price"],
    "mark": ["mark", "price", "current", "last", "exit", "exit price"],
    "pct": ["% p&l (unreal)", "% p&l", "p&l %", "% gain", "gain %", "return", "result"],
    "usd": ["$$ p&l (unreal)", "$ p&l (unreal)", "$ p&l", "p&l $", "p&l"],
    "notes": ["thoughts", "notes", "note", "comment", "comments", "thesis"],
}


def _colmap(columns):
    low = [re.sub(r"\s+", " ", c.strip().lower()) for c in columns]
    out = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in low:
                out[key] = low.index(n)
                break
    return out


def _num(s):
    return parse_cell(s) if isinstance(s, str) else s


def model(parsed, capital=100000.0):
    """Open positions sized against starting capital, closed trades, and the summary line.

    Weight = market value / starting capital (so weights + cash = 100%). The donut
    splits the DEPLOYED money only. Unrealised % comes from the sheet when it has the
    column, otherwise Mark / Entry - 1. Missing numbers stay None, never 0.
    """
    cols = parsed.get("columns") or []
    cm = _colmap(cols)
    missing = [k for k in ("ticker", "entry", "mark") if k not in cm]
    if missing:
        return {"ok": False, "missing": missing, "columns": cols}

    def get(row, key):
        i = cm.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    opens, closed, skipped = [], [], []
    for row in parsed.get("rows") or []:
        tk = get(row, "ticker").upper()
        if not tk:
            continue
        status = get(row, "status").lower()
        entry, mark, amt = _num(get(row, "entry")), _num(get(row, "mark")), _num(get(row, "amt"))
        pct = _num(get(row, "pct"))
        if pct is None and entry and mark is not None:
            pct = mark / entry - 1
        item = {"ticker": tk, "date": get(row, "date") or None, "close_date": get(row, "close_date") or None,
                "amt": amt, "entry": entry, "mark": mark, "pct": pct, "usd": _num(get(row, "usd")),
                "notes": get(row, "notes")}
        if status.startswith("clos") or (not status and item["close_date"]):
            closed.append(item)
        elif amt is None or mark is None:
            skipped.append("%s: no share count or mark" % tk)
        else:
            opens.append(item)

    for p in opens:
        p["mv"] = p["amt"] * p["mark"]
        p["cost"] = p["amt"] * p["entry"] if p["entry"] is not None else None
        if p["usd"] is None and p["cost"] is not None:
            p["usd"] = p["mv"] - p["cost"]
        p["weight"] = p["mv"] / capital if capital else None
    total_mv = sum(p["mv"] for p in opens)
    total_cost = sum(p["cost"] for p in opens if p["cost"] is not None)
    for p in opens:
        p["share"] = p["mv"] / total_mv if total_mv else 0.0
    # colour follows the position, not its rank: order by the day it was opened
    for i, p in enumerate(sorted(opens, key=lambda p: (_dkey(p["date"]), p["ticker"]))):
        p["color"] = i
    opens.sort(key=lambda p: _dkey(p["date"]))

    # realised: cost-weighted when the sheet still has the size, equal-weighted otherwise
    rw = [(c["pct"], (c["amt"] or 0) * (c["entry"] or 0)) for c in closed if c["pct"] is not None]
    wsum = sum(w for _, w in rw)
    realized = (sum(p * w for p, w in rw) / wsum) if wsum else (sum(p for p, _ in rw) / len(rw) if rw else None)
    closed.sort(key=lambda c: _dkey(c["close_date"] or c["date"]), reverse=True)
    deployed = total_mv / capital if capital else None
    return {
        "ok": True, "open": opens, "closed": closed, "skipped": skipped, "capital": capital,
        "summary": {
            "positions": len(opens), "deployed": deployed,
            "cash": (1 - deployed) if deployed is not None else None,
            "market_value": total_mv, "unrealized_usd": total_mv - total_cost if opens else None,
            "unrealized": ((total_mv - total_cost) / total_cost) if total_cost else None,
            "realized": realized, "closed": len(closed),
        },
    }


def _dkey(s):
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", s or "")
    if not m:
        m2 = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", s or "")
        return (int(m2.group(1)), int(m2.group(2)), int(m2.group(3))) if m2 else (9999, 0, 0)
    y = int(m.group(3)) if m.group(3) else 0
    y = y + 2000 if 0 < y < 100 else y
    return (y, int(m.group(1)), int(m.group(2)))
