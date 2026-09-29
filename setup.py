"""
One-time setup: asks for your Unusual Whales API token once and writes a private
.env file into every desk folder (copied from that desk's .env.example).

    python setup.py            # or double-click SETUP.bat on Windows

The token is only ever written to the .env files on your own machine. .env is in
.gitignore, so it never goes to GitHub. Run it again any time to change the token.
"""
import getpass
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DESKS = ["Valuation Desk", "UW Flow Desk", "Confluence Desk", "Institutional Desk",
         "Swing Desk", "Growth Desk"]
TOKEN_LINE = re.compile(r"^(UW_API_TOKEN|UW_TOKEN)\s*=.*$")


def fill(example_text, token):
    out, done = [], False
    for line in example_text.splitlines():
        m = TOKEN_LINE.match(line.strip())
        if m:
            line, done = "%s=%s" % (m.group(1), token), True
        out.append(line)
    if not done:
        out.append("UW_API_TOKEN=%s" % token)
    return "\n".join(out) + "\n"


def main(argv=None, ask=None):
    ask = ask or (lambda p: getpass.getpass(p))
    argv = sys.argv[1:] if argv is None else argv
    token = (os.environ.get("UW_API_TOKEN") or "").strip() or ask(
        "Paste your Unusual Whales API token (it won't show as you type), then press Enter: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{12,}", token or ""):
        print("That doesn't look like an API token. Nothing was written.")
        return 1
    overwrite = "--force" in argv
    wrote = []
    for desk in DESKS:
        folder = os.path.join(HERE, desk)
        example = os.path.join(folder, ".env.example")
        target = os.path.join(folder, ".env")
        if not os.path.isfile(example):
            continue
        if os.path.isfile(target) and not overwrite:
            with open(target, encoding="utf-8-sig") as fh:
                text = fh.read()
            text = fill(text, token)            # replaces the token line, or adds one if there was none
        else:
            with open(example, encoding="utf-8-sig") as fh:
                text = fill(fh.read(), token)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(text)
        if os.name == "posix":
            os.chmod(target, 0o600)             # Mac/Linux: only you can read the token file
        wrote.append(desk)
    print("Token saved for: " + ", ".join(wrote))
    print("Next: double-click START_HERE.bat (or run: python \"UW Dashboard/server.py\").")
    return 0


if __name__ == "__main__":
    sys.exit(main())
