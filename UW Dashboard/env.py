"""
Configuration for the UW Dashboard.

Resolution order for every key: real environment variable, then this folder's
.env, then (token only) the sibling desks' .env files -- so the UW token lives
in one place and the dashboard never needs its own copy.

Config parsing never stops the dashboard from starting: a bad value degrades
to its default and is reported in WARNINGS, which the page shows.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
WARNINGS = []

# Where the desks normally live. Paths are resolved relative to the dashboard's
# parent folder unless absolute. Settings -> Desks can point any of them elsewhere.
def _first_existing(*candidates):
    for c in candidates:
        p = c if os.path.isabs(c) or c[1:3] in (":\\", ":/") else os.path.join(PARENT, c)
        if os.path.isdir(p):
            return c
    return candidates[0]


DEFAULT_DESKS = [
    {"id": "valuation", "name": "Valuation", "folder": "Valuation Desk", "port": 8790},
    {"id": "flow", "name": "Unusual Options Flow",
     "folder": _first_existing("UW Flow Desk", r"D:\UW-Desks\UW Flow Desk"), "port": None},
    {"id": "confluence", "name": "Confluence", "folder": "Confluence Desk", "port": 8770},
    {"id": "institutional", "name": "Institutional", "folder": "Institutional Desk", "port": 8777},
    {"id": "swing", "name": "Swing", "folder": "Swing Desk", "port": 8787},
    {"id": "growth", "name": "Growth Leaders (O'Neil-style)", "folder": "Growth Desk", "port": 8760},   # r16
]

# Desks that used to ship and were retired. Saved settings drop them on load.
RETIRED_DESKS = {"gex"}     # GEX / ES desk, retired 2026-09-25

# Desks whose default folder moved: a saved folder equal to the OLD default follows
# the new one once the new folder exists (it has a server.py).
MOVED_DESKS = {"flow": (r"D:\UW-Desks\UW Flow Desk", "UW Flow Desk")}

# Sibling .env files that may hold the UW token, in the order they are tried.
TOKEN_SOURCES = [
    ".env",
    os.path.join("..", "Valuation Desk", ".env"),
    os.path.join("..", "Confluence Desk", ".env"),
    os.path.join("..", "Swing Desk", ".env"),
    os.path.join("..", "Institutional Desk", ".env"),
    os.path.join("..", "UW Flow Desk", ".env"),
    r"D:\UW-Desks\UW Flow Desk\.env",
]
TOKEN_KEYS = ("UW_API_TOKEN", "UW_TOKEN")


def parse_env_file(path):
    out = {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k.strip()] = v
    except OSError:
        pass
    return out


def _resolve(p):
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(HERE, p))


def load():
    env = parse_env_file(os.path.join(HERE, ".env"))
    for k, v in os.environ.items():
        if k.startswith(("UW_", "DASH_")):
            env[k] = v
    return env


ENV = load()


def env_str(key, default=""):
    v = ENV.get(key)
    return default if v in (None, "") else v


def env_int(key, default):
    raw = ENV.get(key)
    if raw in (None, ""):
        return default
    try:
        return int(float(raw))
    except ValueError:
        WARNINGS.append("%s=%r is not a number; using %s." % (key, raw, default))
        return default


def env_bool(key, default=False):
    raw = ENV.get(key)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def find_token():
    """Return (token, source_path) or ("", None)."""
    if env_str("UW_API_TOKEN"):
        return env_str("UW_API_TOKEN"), "dashboard .env / environment"
    for rel in TOKEN_SOURCES[1:]:
        path = _resolve(rel)
        vals = parse_env_file(path)
        for key in TOKEN_KEYS:
            tok = vals.get(key, "").strip()
            if tok and not tok.startswith(("your", "paste")):
                return tok, path
    return "", None


PORT = env_int("DASH_PORT", 8700)
OPEN_BROWSER = env_bool("DASH_OPEN_BROWSER", True)
