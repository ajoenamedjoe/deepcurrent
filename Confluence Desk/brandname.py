"""
What this desk calls itself on the things it writes (Word reports, Discord posts, generated scripts).

The dashboard owns the name: "<brand> · <the desk's name in Settings>", e.g. "Deep Current · Valuation".
It starts every desk with UW_DASHBOARD_URL set; the desk asks it (GET /api/brand?desk=<id>) at most once
a minute, so a rename in Settings shows up without a restart. With the dashboard off, the desk uses its
own plain name. Unusual Whales is credited separately, as the data source, wherever data is shown.

Same file in every desk folder (a test checks they match).
"""

import json
import os
import re
import time
import urllib.request

TTL = 60.0
_CACHE = {}
# Never through a proxy: this is a call to this PC.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _plain(s, n=80):
    return re.sub(r"[\x00-\x1f\x7f<>]", "", str(s or "")).strip()[:n]


def info(desk, fallback):
    """{"brand", "desk", "label"} for this desk. Never raises; never waits more than ~1 s."""
    now = time.time()
    hit = _CACHE.get(desk)
    if hit and now - hit[0] < TTL:
        return hit[1]
    val = hit[1] if hit else {"brand": "", "desk": fallback, "label": fallback}
    base = os.environ.get("UW_DASHBOARD_URL", "http://127.0.0.1:8700").rstrip("/")
    if re.match(r"^http://(127\.0\.0\.1|localhost):\d{2,5}$", base):
        try:
            with _OPENER.open("%s/api/brand?desk=%s" % (base, re.sub(r"[^a-z]", "", desk)), timeout=1.0) as r:
                d = json.loads(r.read().decode("utf-8"))
            label = _plain(d.get("label"))
            if label:
                val = {"brand": _plain(d.get("brand")), "desk": _plain(d.get("desk")), "label": label}
        except Exception:
            pass                          # dashboard off: keep the last answer, or the desk's own name
    _CACHE[desk] = (now, val)
    return val


def label(desk, fallback):
    return info(desk, fallback)["label"]
