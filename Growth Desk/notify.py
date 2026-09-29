"""
Discord for the Growth Leaders desk: one post when a Leader (all six stock checks pass) breaks out of a base on
volume while the market is in a confirmed uptrend. Nothing else is posted -- the dashboard shows every other
breakout. Webhook: GDESK_DISCORD_WEBHOOK_URL, else the shared DISCORD_WEBHOOK_URL (this or a sibling desk's .env).
"""

import json
import re
import threading
import time
import urllib.error
import urllib.request

import brandname
import uw

GREEN = 0x2E9E5B
DISCORD_RE = re.compile(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")


def load_webhook():
    url = (uw.env_str("GDESK_DISCORD_WEBHOOK_URL") or uw.env_str("DISCORD_WEBHOOK_URL")).strip()
    return url if DISCORD_RE.match(url) else ""


def _md(s):
    """Discord markdown and mentions escaped: a company name is never formatting or a ping."""
    return re.sub(r"([\\*_`~|>\[\]()@#])", r"\\\1", str(s or ""))[:200]


def breakout_embed(card, market):
    b = card.get("base") or {}
    marks = "  ".join("%s %s" % (c["key"], "✓" if c["passed"] else ("·" if not c["measured"] else "✗"))
                      for c in card["checks"])
    fields = [
        {"name": "Score", "value": "%s / 100 (Leader)" % card.get("score"), "inline": True},
        {"name": "Buy range", "value": "$%.2f – $%.2f" % (b.get("buy_from") or 0, b.get("buy_to") or 0), "inline": True},
        {"name": "Stop (−8%)", "value": "$%.2f" % (b.get("stop") or 0), "inline": True},
        {"name": "Volume", "value": ("%.1fx average" % b["vol_ratio"]) if b.get("vol_ratio") else "?", "inline": True},
        {"name": "Relative strength", "value": str(card.get("rs") or "?"), "inline": True},
        {"name": "Checks", "value": marks, "inline": False},
        {"name": "Why", "value": _md(" ".join(c["text"] for c in card["checks"][:2]))[:1024], "inline": False},
    ]
    return {
        "title": "▲ %s breaking out" % card["ticker"],
        "description": _md(card.get("name") or card.get("industry") or card["ticker"]) + "\n" + _md(b.get("text")),
        "color": GREEN,
        "fields": fields,
        "footer": {"text": "%s · Data: Unusual Whales · %s · Not investment advice" % (
            brandname.label("growth", "Growth Leaders"), (market or {}).get("label", ""))},
    }


class Notifier:
    def __init__(self, log=print):
        self.log = log
        self.url = load_webhook()
        self.enabled = bool(self.url)
        self._lock = threading.Lock()
        self._last = 0.0

    def _post(self, payload, retries=2):
        if not self.enabled:
            return False
        payload = dict(payload, allowed_mentions={"parse": []})
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json",
                                                                   "User-Agent": "growth-leaders-desk/1.0"})
        for attempt in range(retries + 1):
            with self._lock:
                gap = time.time() - self._last
                if gap < 1.0:
                    time.sleep(1.0 - gap)
                self._last = time.time()
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return 200 <= r.status < 300
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries:
                    time.sleep(5)
                    continue
                self.log("discord: HTTP %s" % e.code)
                return False
            except Exception as e:          # noqa: BLE001
                self.log("discord: %s" % e.__class__.__name__)
                return False
        return False

    def breakout(self, card, market):
        return self._post({"embeds": [breakout_embed(card, market)]})

    def test(self):
        return self._post({"content": "%s connected. Posts come only when a Leader breaks out in a confirmed uptrend."
                                      % brandname.label("growth", "Growth Leaders")})
