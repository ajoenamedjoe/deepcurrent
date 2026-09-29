"""
Confluence Desk -- Discord notifier.

Gate (everything must pass):
  * score >= CDESK_ALERT_SCORE (default 70)
  * all three legs contributing -- this is a CONFLUENCE desk, so a 72 built
    entirely out of insider points is not what this desk should page about
  * held for >= CDESK_ALERT_STREAK consecutive scans (default 2)
  * per-ticker cooldown (default 12h)
  * at most CDESK_ALERT_MAX per scan (default 4)
  * never on the cold-start baseline scan
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import brandname
import uw


def utcnow():
    return datetime.now(timezone.utc)


# ------------------------------------------------------- US Eastern time
# zoneinfo needs the tzdata pip package on Windows, which would break the
# no-install promise. The DST rule is two lines; implement it.
def _nth_weekday(year, month, weekday, n):
    first = datetime(year, month, 1, tzinfo=timezone.utc)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def et_offset(dt_utc):
    """-4 during EDT, -5 during EST."""
    year = dt_utc.year
    start = _nth_weekday(year, 3, 6, 2).replace(hour=7)      # 2nd Sun Mar 07:00Z
    end = _nth_weekday(year, 11, 6, 1).replace(hour=6)       # 1st Sun Nov 06:00Z
    return -4 if start <= dt_utc < end else -5


def et_now(dt_utc=None):
    dt_utc = dt_utc or utcnow()
    return dt_utc + timedelta(hours=et_offset(dt_utc))


def _money(value):
    value = float(value or 0.0)
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= size:
            return "$%.1f%s" % (value / size, unit)
    return "$%.0f" % value


BAND_COLOUR = {
    "high": 0x1BAF7A,
    "elevated": 0xEDA100,
    "watch": 0x2A78D6,
    "low": 0x898781,
}


def _md(s):
    """Discord renders markdown in embeds: a name like [x](https://evil) would become a disguised link."""
    s = str(s)
    for ch in "\\[]()*_~`>|":
        s = s.replace(ch, "\\" + ch)
    return s


def build_embed(card):
    legs = card["legs"]
    top = card.get("top_buyer") or {}

    def bar(name):
        leg = legs[name]
        filled = int(round(10.0 * leg["points"] / leg["max"])) if leg["max"] else 0
        return "%s `%s` %.0f/%.0f" % (
            {"insider": "Insider", "darkpool": "Dark pool",
             "institutional": "13F"}[name],
            "#" * filled + "." * (10 - filled),
            leg["points"], leg["max"],
        )

    lines = [bar("insider"), bar("darkpool"), bar("institutional")]

    fields = [
        {"name": "Conviction", "value": "\n".join(lines), "inline": False},
        {
            "name": "Insiders",
            "value": "%d buyer%s, %s\nTop: %s%s" % (
                card["distinct_buyers"], "" if card["distinct_buyers"] == 1 else "s",
                _money(card["notional"]),
                _md(top.get("name", "?")),
                " (%s)" % _md(top["title"]) if top.get("title") else "",
            ),
            "inline": True,
        },
    ]
    dark = card.get("dark") or {}
    if dark.get("print_count"):
        fields.append({
            "name": "Dark pool",
            "value": "%s over %d session%s\nbiggest print %.1f%% of avg30 vol" % (
                _money(dark.get("window_notional")),
                dark.get("active_sessions", 0),
                "" if dark.get("active_sessions") == 1 else "s",
                100.0 * dark.get("max_size_vs_avg30", 0.0),
            ),
            "inline": True,
        })
    inst = card.get("inst")
    if inst:
        fields.append({
            "name": "13F (as of %s)" % inst.get("report_date", "?"),
            "value": "%d fund%s, %s\n%s" % (
                inst["distinct_funds"], "" if inst["distinct_funds"] == 1 else "s",
                _money(inst["total_value"]),
                ", ".join(_md(f["fund"]) for f in inst["funds"][:3]),
            ),
            "inline": False,
        })

    return {
        "title": "%s  -  %.0f / 100" % (card["ticker"], card["score"]),
        "description": "Insiders and institutions lining up%s" % (
            " - all three legs" if card.get("all_three") else ""),
        "color": BAND_COLOUR.get(card.get("band", "watch"), 0x2A78D6),
        "fields": fields,
        "footer": {"text": brandname.label("confluence", "Confluence Desk") + " - Data: Unusual Whales - "
                           "13F leg is quarterly data, see the card for the filing date"},
        "timestamp": utcnow().isoformat(),
    }


class Notifier:
    def __init__(self, store, log=print):
        self.store = store
        self.log = log
        self.url = uw.env_str("DISCORD_WEBHOOK_URL").strip()
        self.enabled = bool(self.url)
        self.min_score = uw.env_num("CDESK_ALERT_SCORE", 70.0)
        self.min_streak = uw.env_int("CDESK_ALERT_STREAK", 2)
        self.cooldown_h = uw.env_num("CDESK_ALERT_COOLDOWN_H", 12.0)
        self.max_per_scan = uw.env_int("CDESK_ALERT_MAX", 4)
        self.require_all_three = uw.env_bool("CDESK_ALERT_ALL_THREE", True)
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
                     "User-Agent": "uw-confluence-desk/1.0"})
        for attempt in range(retries + 1):
            with self._lock:
                gap = time.time() - self._last_send
                if gap < 1.0:
                    time.sleep(1.0 - gap)
                self._last_send = time.time()
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return 200 <= resp.status < 300
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < retries:
                    wait = 5.0
                    try:
                        wait = float(json.loads(exc.read().decode()).get("retry_after", 5))
                    except Exception:
                        pass
                    self.log("  [discord] rate limited, waiting %.1fs" % wait)
                    time.sleep(min(wait, 30))
                    continue
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                self.log("  [discord] HTTP %s: %s" % (exc.code, body))
                return False
            except Exception as exc:
                self.log("  [discord] %s: %s" % (type(exc).__name__, exc))
                return False
        return False

    def eligible(self, card):
        if card["score"] < self.min_score:
            return False, "score"
        if self.require_all_three and not card.get("all_three"):
            return False, "not_all_three"
        state = self.store.state(card["ticker"]) or {}
        if (state.get("streak") or 0) < self.min_streak:
            return False, "streak"
        last = state.get("last_alert_at")
        if last:
            try:
                when = datetime.fromisoformat(last)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if utcnow() - when < timedelta(hours=self.cooldown_h):
                    return False, "cooldown"
            except ValueError:
                pass
        return True, ""

    def process(self, cards, baseline=False):
        """Returns (sent, skipped_reasons)."""
        if baseline:
            self.log("  [discord] baseline scan -- alerts suppressed")
            return 0, {"baseline": len(cards)}
        if not self.enabled:
            return 0, {"disabled": len(cards)}

        reasons = {}
        sent = 0
        for card in sorted(cards, key=lambda c: -c["score"]):
            if sent >= self.max_per_scan:
                reasons["max_per_scan"] = reasons.get("max_per_scan", 0) + 1
                continue
            ok, why = self.eligible(card)
            if not ok:
                reasons[why] = reasons.get(why, 0) + 1
                continue
            if self._post({"embeds": [build_embed(card)]}):
                self.store.mark_alerted(card["ticker"])
                sent += 1
                self.log("  [discord] posted %s (%.0f)" % (card["ticker"], card["score"]))
            else:
                reasons["post_failed"] = reasons.get("post_failed", 0) + 1
        return sent, reasons

    def test(self):
        return self._post({
            "content": "%s connected. Alerts fire at score >= "
                       "%.0f with all three legs contributing." % (brandname.label("confluence", "Confluence Desk"),
                                                                   self.min_score)})
