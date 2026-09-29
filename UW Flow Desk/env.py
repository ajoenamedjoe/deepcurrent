"""`.env` loading, shared with the sibling desks' conventions.

Two rules here are load-bearing, both learned the hard way:

* **Config parsing must never stop the desk from starting.** GEX ES Desk's
  first shipped `.env.example` had an inline comment beside a value, `int()`
  raised at module import before `main()` ran, and `START_HERE.bat` copies the
  example verbatim — so it failed 100% of the time on a clean install while
  every test passed. Bad values now fall back, clamp, and append to
  `WARNINGS`, which the UI shows as a banner.

* **The artifact we ship is the artifact we test.** There is a test that parses
  the real `.env.example` off disk, not a synthetic copy.
"""

from __future__ import annotations

import os

WARNINGS = []

# Where to look for a token, in order. A local .env wins; otherwise borrow a
# sibling desk's, so there is only one copy of the token on the machine.
# Both spellings of every folder, because the Insider Desk machine uses
# `UW SwingDesk` and the others use `Swing Desk`.
ENV_CANDIDATES = [
    ".env",
    os.path.join("..", "Confluence Desk", ".env"),
    os.path.join("..", "Swing Desk", ".env"),
    os.path.join("..", "UW SwingDesk", ".env"),
    os.path.join("..", "Institutional Desk", ".env"),
    os.path.join("..", "UW Insider Desk", ".env"),
    os.path.join("..", "GEX ES Desk", ".env"),
]


def parse_env(text):
    """Parse a .env. Tolerates CRLF, a BOM, `=` padding and inline comments."""
    out = {}
    if text and text[:1] == "﻿":
        text = text[1:]
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        # tokens=1,* — a value containing '=' must not be truncated
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        quoted = len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"')
        if quoted:
            val = val[1:-1]
        else:
            # Strip an inline comment only when the '#' follows whitespace, so
            # a token containing '#' survives.
            cut = None
            for i, ch in enumerate(val):
                if ch == "#" and i > 0 and val[i - 1] in " \t":
                    cut = i
                    break
            if cut is not None:
                val = val[:cut].rstrip()
        if key:
            out[key] = val
    return out


def load_env(paths=None, base=None):
    """Merge the first readable candidate files. Earlier files win."""
    base = base or os.path.dirname(os.path.abspath(__file__))
    merged = {}
    found = []
    for rel in (paths or ENV_CANDIDATES):
        p = rel if os.path.isabs(rel) else os.path.join(base, rel)
        try:
            with open(p, "r", encoding="utf-8-sig") as fh:
                data = parse_env(fh.read())
        except (OSError, UnicodeDecodeError):
            continue
        found.append(p)
        for k, v in data.items():
            merged.setdefault(k, v)
    merged["_files"] = found
    return merged


def cfg_int(env, key, default, lo=None, hi=None):
    raw = env.get(key)
    if raw in (None, ""):
        return default
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        WARNINGS.append("%s=%r is not a number; using %s" % (key, raw, default))
        return default
    if lo is not None and v < lo:
        WARNINGS.append("%s=%s below minimum %s; clamped" % (key, v, lo))
        v = lo
    if hi is not None and v > hi:
        WARNINGS.append("%s=%s above maximum %s; clamped" % (key, v, hi))
        v = hi
    return v


def cfg_bool(env, key, default=False):
    raw = env.get(key)
    if raw in (None, ""):
        return default
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    WARNINGS.append("%s=%r is not true/false; using %s" % (key, raw, default))
    return default


def token(env):
    for k in ("UW_TOKEN", "UNUSUAL_WHALES_TOKEN", "UW_API_TOKEN", "API_TOKEN"):
        v = env.get(k)
        if v:
            return v
    return None
