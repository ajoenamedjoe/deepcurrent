"""Build UW_Dashboard_share.zip -- code only, safe to hand to someone else.

Allow-list of files; never .env, dashboard.db (your P&L and settings), logs.
Refuses to build if any file contains a real UW token from this or a sibling
desk's .env, a token-shaped string, or your Windows user name.
"""
import getpass
import os
import re
import sys
import zipfile

import env

HERE = env.HERE
FILES = ["server.py", "env.py", "desks.py", "events.py", "portfolio.py", "pnl.py", "store.py",
         "market.py", "openflow.py", "sizer.py", "deskview.py", "trackrec.py", "backup.py", "guide.py", "themes.py", "changelog.py", "CHANGELOG.md", "watchlist.py",
         "make_share.py", "index.html", "app.js", ".env.example", "README.md",
         "START_HERE.bat", "INSTALL_AUTOSTART.bat", "REMOVE_AUTOSTART.bat", "RUN_TESTS.bat", "MAKE_SHARE_ZIP.bat"]
TEST_DIRS = ["tests"]
TOKEN_SHAPE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def secrets():
    out = set()
    for rel in env.TOKEN_SOURCES:
        path = rel if os.path.isabs(rel) else os.path.normpath(os.path.join(HERE, rel))
        tok = env.parse_env_file(path).get("UW_API_TOKEN", "").strip()
        if len(tok) > 8:
            out.add(tok)
    for extra in (os.environ.get("SHARE_BLOCK") or "").split(","):
        if extra.strip():
            out.add(extra.strip())
    try:
        user = getpass.getuser()
        if len(user) > 2 and user.lower() not in ("root", "user", "admin", "administrator", "claude"):
            out.add(user)
    except Exception:   # noqa: BLE001
        pass
    return out


def collect():
    paths = [f for f in FILES if os.path.isfile(os.path.join(HERE, f))]
    fonts = os.path.join(HERE, "fonts")               # the theme fonts and their licences
    if os.path.isdir(fonts):
        paths += [os.path.join("fonts", f) for f in sorted(os.listdir(fonts)) if f.endswith((".woff2", ".txt"))]
    for d in TEST_DIRS:
        for root, dirs, files in os.walk(os.path.join(HERE, d)):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for f in files:
                if f.endswith((".py", ".csv")):
                    paths.append(os.path.relpath(os.path.join(root, f), HERE))
    return paths


def main():
    bad = []
    blocked = secrets()
    for rel in collect():
        with open(os.path.join(HERE, rel), "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for s in blocked:
            if re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(s), text, re.I):
                bad.append("%s contains a blocked value (token or user name)" % rel)
        if TOKEN_SHAPE.search(text):
            bad.append("%s contains a token-shaped string" % rel)
    if bad:
        print("\n  NOT BUILT:\n    " + "\n    ".join(sorted(set(bad))) + "\n")
        return 1
    out = os.path.join(HERE, "UW_Dashboard_share.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in collect():
            z.write(os.path.join(HERE, rel), os.path.join("UW Dashboard", rel))
    print("\n  Built %s -- no .env, no dashboard.db, no logs.\n" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
