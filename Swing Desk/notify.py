"""
Discord notifications for the Swing Desk.

Fires only on HIGH-CONVICTION setups that have actually held:
  - |score| >= 50 (recalibrated 2026-09-25: the old 60 was above anything the
    model had ever scored, so nothing was ever posted)
  - at least 4 signals of real size pointing the same way
  - survived N consecutive scans (default 3, about 2 minutes)
  - not already posted for this ticker+direction inside the cooldown

Plus one daily wrap at 4:00 PM ET listing what fired and how it behaved.

Stdlib only. If Discord is unreachable the scan is never affected - failures
are logged and swallowed.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import brandname
from store import iso, utcnow

# ---------------------------------------------------------------- config
NOTIFY = {
    "min_score": 50.0,        # high conviction only (about the top 2% of names)
    "min_agreeing": 4,        # signals of real size pointing the same way
    "min_streak": 3,          # consecutive scans it must hold (~2 min)
    "cooldown_hours": 6.0,    # don't re-post the same ticker+direction
    "daily_wrap": True,
    "wrap_hour_et": 16,       # 4:00 PM ET
    "max_per_scan": 4,        # burst guard
}

GREEN = 0x0CA30C
RED = 0xD03B3B
GREY = 0x898781


# ------------------------------------------------------- US Eastern time
# zoneinfo needs the tzdata package on Windows, which would mean a pip
# install. The DST rule is simple enough to just implement.
def _nth_weekday(year, month, weekday, n):
    d = datetime(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def et_offset(dt_utc):
    """-4 during EDT, -5 during EST."""
    y = dt_utc.year
    # 2nd Sunday of March, 07:00 UTC -> 1st Sunday of November, 06:00 UTC
    start = _nth_weekday(y, 3, 6, 2).replace(hour=7, tzinfo=timezone.utc)
    end = _nth_weekday(y, 11, 6, 1).replace(hour=6, tzinfo=timezone.utc)
    return -4 if start <= dt_utc < end else -5


def et_now(dt_utc=None):
    dt_utc = dt_utc or utcnow()
    return dt_utc + timedelta(hours=et_offset(dt_utc))


# NYSE full-day closures (early closes count as sessions).
NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}


def session_state(dt_utc=None, start="09:35", end="16:00"):
    """('open'|'closed', reason). The scan loop runs only while open."""
    et = et_now(dt_utc)
    if et.weekday() >= 5:
        return "closed", "weekend"
    if et.strftime("%Y-%m-%d") in NYSE_HOLIDAYS:
        return "closed", "market holiday"
    hm = et.strftime("%H:%M")
    if hm < start:
        return "closed", "before %s ET" % start
    if hm > end:
        return "closed", "after %s ET" % end
    return "open", ""


def load_webhook():
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if url:
        return url
    envfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(envfile):
        with open(envfile) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "DISCORD_WEBHOOK_URL":
                    return v.strip().strip('"').strip("'")
    return ""


# ------------------------------------------------------------- formatting
def _money(n):
    if n is None:
        return "n/a"
    a = abs(n)
    s = "-" if n < 0 else ""
    if a >= 1e9:
        return f"{s}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{s}${a/1e6:.1f}M"
    if a >= 1e3:
        return f"{s}${a/1e3:.0f}K"
    return f"{s}${a:.0f}"


def _sig_line(a):
    order = [("flow", "Flow"), ("oi", "OI"), ("dark", "Dark"),
             ("gamma", "Gamma"), ("tech", "Tech")]
    parts = []
    for k, label in order:
        c = (a.get("components") or {}).get(k) or {}
        v = c.get("contribution", 0)
        parts.append(f"{label} {v:+.0f}")
    return " · ".join(parts)


LABELS = {"flow": "options flow", "oi": "open interest", "dark": "dark pool",
          "gamma": "gamma", "tech": "chart"}


def drivers_text(a):
    """Name what carried the score instead of an 'N of 5 agree' count, which reads
    unanimous and hides how little each signal contributed."""
    d = a.get("drivers") or []
    return " + ".join(LABELS.get(k, k) for k in d) if d else "no single signal"


def build_embed(a, persistence=None):
    bull = a["direction"] == "BULL"
    lv, ctx = a["levels"], a["context"]
    arrow = "▲" if bull else "▼"

    fields = [
        {"name": "Score", "value": f"**{abs(a['score']):.0f}** · driven by {drivers_text(a)}",
         "inline": True},
        {"name": "Hold time", "value": a["horizon"].get("detail") or a["horizon"]["label"],
         "inline": True},
        {"name": "Price", "value": f"${a['price']:.2f} ({a['change_pct']:+.1f}%)",
         "inline": True},
        {"name": "Entry", "value": f"{lv['entry_low']:.2f} – {lv['entry_high']:.2f}",
         "inline": True},
        {"name": "Stop", "value": f"{lv['stop']:.2f}", "inline": True},
        {"name": "Target", "value": f"{lv['target']:.2f}  (R:R {lv['rr']:.2f})",
         "inline": True},
        {"name": "Signals", "value": _sig_line(a), "inline": False},
    ]

    context_bits = [f"ATR {lv['atr14']:.2f}"]
    if ctx.get("iv_rank"):
        context_bits.append(f"IV rank {ctx['iv_rank']:.0f}")
    if ctx.get("relative_volume"):
        context_bits.append(f"RVOL {ctx['relative_volume']:.2f}x")
    if a.get("sector"):
        context_bits.append(a["sector"])
    if ctx.get("earnings_in") is not None and ctx["earnings_in"] >= 0:
        context_bits.append(f"earnings in {ctx['earnings_in']}d")
    fields.append({"name": "Context", "value": " · ".join(context_bits), "inline": False})

    walls = []
    if lv.get("call_wall"):
        walls.append(f"call wall ${lv['call_wall']:.2f}")
    if lv.get("put_wall"):
        walls.append(f"put wall ${lv['put_wall']:.2f}")
    if lv.get("gamma_flip"):
        walls.append(f"gamma flip ${lv['gamma_flip']:.2f}")
    if walls:
        fields.append({"name": "Gamma levels", "value": " · ".join(walls), "inline": False})

    if a.get("penalties"):
        fields.append({"name": "⚠ Cautions",
                       "value": "\n".join(f"• {p}" for p in a["penalties"])[:1024],
                       "inline": False})

    foot = "Descriptive signal aggregation, not trade advice. Verify before risking capital."
    if persistence:
        foot = (f"Held {persistence['streak']} consecutive scans · "
                f"{persistence['held']}/{persistence['window']} recent · " + foot)

    foot = brandname.label("swing", "Swing Desk") + " · Data: Unusual Whales · " + foot
    return {
        "title": f"{arrow} {a['ticker']} — {'BULLISH' if bull else 'BEARISH'}",
        "description": a.get("name") or a["ticker"],
        "color": GREEN if bull else RED,
        "fields": fields[:25],
        "footer": {"text": foot[:2048]},
        "timestamp": iso(),
    }


# ---------------------------------------------------------------- sending
class Discord:
    def __init__(self, store, log=print):
        self.store = store
        self.log = log
        self.url = load_webhook()
        self.enabled = bool(self.url)
        self._lock = threading.Lock()
        self._last_send = 0.0

    def _post(self, payload, retries=2):
        if not self.enabled:
            return False
        payload = dict(payload, allowed_mentions={"parse": []})   # never @everyone / @role from UW text
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "uw-swing-dashboard/1.0"})
        for attempt in range(retries + 1):
            with self._lock:
                gap = time.time() - self._last_send
                if gap < 1.0:
                    time.sleep(1.0 - gap)      # stay well inside Discord's rate limit
                self._last_send = time.time()
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return 200 <= r.status < 300
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries:
                    wait = 5.0
                    try:
                        wait = float(json.loads(e.read().decode()).get("retry_after", 5))
                    except Exception:
                        pass
                    self.log(f"  [discord] rate limited, waiting {wait:.1f}s")
                    time.sleep(min(wait, 30))
                    continue
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                self.log(f"  [discord] HTTP {e.code}: {body}")
                return False
            except Exception as e:
                self.log(f"  [discord] {type(e).__name__}: {e}")
                return False
        return False

    # ----------------------------------------------------------- alerting
    def eligible(self, a, persistence):
        """High-conviction gate. Everything must pass."""
        if abs(a["score"]) < NOTIFY["min_score"]:
            return False, "score"
        if a["agreeing"] < NOTIFY["min_agreeing"]:
            return False, "agreeing"
        p = persistence.get(a["ticker"]) or {}
        if p.get("streak", 0) < NOTIFY["min_streak"]:
            return False, "streak"
        return True, "ok"

    def process(self, alerts, persistence):
        if not self.enabled or not alerts:
            return []
        recent = self.store.notified_since(NOTIFY["cooldown_hours"])
        sent = []
        for a in sorted(alerts, key=lambda x: -abs(x["score"])):
            if len(sent) >= NOTIFY["max_per_scan"]:
                break
            ok, _why = self.eligible(a, persistence)
            if not ok:
                continue
            key = (a["ticker"], a["direction"])
            if key in recent:
                continue
            embed = build_embed(a, persistence.get(a["ticker"]))
            if self._post({"embeds": [embed]}):
                self.store.mark_notified(a["ticker"], a["direction"], a["score"],
                                         "new", json.dumps({"score": a["score"]}))
                sent.append(a["ticker"])
                self.log(f"  [discord] posted {a['ticker']} {a['direction']} "
                         f"{a['score']:+.0f}")
        return sent

    # --------------------------------------------------------- daily wrap
    def maybe_daily_wrap(self):
        if not (self.enabled and NOTIFY["daily_wrap"]):
            return False
        now_et = et_now()
        if now_et.weekday() >= 5 or now_et.hour < NOTIFY["wrap_hour_et"]:
            return False
        today = now_et.strftime("%Y-%m-%d")
        if self.store.get_meta("last_wrap") == today:
            return False

        start_utc = iso(utcnow() - timedelta(hours=now_et.hour + 1))
        fired = self.store.todays_notifications(start_utc)

        if not fired:
            desc = ("No high-conviction setups fired today.\n\n"
                    "That's a normal reading — the gate needs a score of "
                    f"{NOTIFY['min_score']:.0f}+, {NOTIFY['min_agreeing']} signals of real size "
                    "pointing the same way, and the setup to hold for several scans.")
            fields = []
        else:
            desc = f"**{len(fired)}** setup{'s' if len(fired) != 1 else ''} fired today."
            fields = []
            for n in fired[:20]:
                out = self.store.outcome_for(n["ticker"], n["direction"], n["ts"])
                t_et = et_now(datetime.fromisoformat(n["ts"]))
                when = t_et.strftime("%-I:%M %p") if os.name != "nt" else t_et.strftime("%I:%M %p").lstrip("0")
                if out:
                    verdict = "still up" if out["still_up"] else "faded"
                    val = (f"{when} · score {abs(n['score']):.0f} · "
                           f"moved {out['favourable_move_pct']:+.1f}% in favour · "
                           f"held {out['held_scans']}/{out['total_scans']} scans · {verdict}")
                else:
                    val = f"{when} · score {abs(n['score']):.0f}"
                arrow = "▲" if n["direction"] == "BULL" else "▼"
                fields.append({"name": f"{arrow} {n['ticker']}", "value": val[:1024],
                               "inline": False})

        embed = {
            "title": f"Daily wrap — {now_et.strftime('%a %b %d')}",
            "description": desc,
            "color": GREY,
            "fields": fields[:25],
            "footer": {"text": "Move is measured from the price when the alert fired, "
                               "expressed in the direction of the setup. Descriptive only."},
            "timestamp": iso(),
        }
        if self._post({"embeds": [embed]}):
            self.store.set_meta("last_wrap", today)
            self.log(f"  [discord] posted daily wrap ({len(fired)} setups)")
            return True
        return False

    def test(self):
        """Send a probe so the user can confirm the webhook works."""
        return self._post({"embeds": [{
            "title": "✅ %s connected" % brandname.label("swing", "Swing Desk"),
            "description": ("Discord notifications are wired up.\n\n"
                            f"You'll get a post when a setup scores **{NOTIFY['min_score']:.0f}+** "
                            f"with **{NOTIFY['min_agreeing']}** signals pointing the same way and holds for "
                            f"**{NOTIFY['min_streak']} consecutive scans**, plus a wrap at "
                            f"{NOTIFY['wrap_hour_et']}:00 ET each weekday."),
            "color": GREEN,
            "timestamp": iso(),
        }]})
