"""
Build a share-safe copy of this desk: Valuation_Desk_share_YYYY-MM-DD.zip in the
parent folder. Allow-list only (new files are left out until added here), then a
scan of every packed file for your real token and anything token-shaped.
Never packed: .env, reports/, valuation.db*, __pycache__/.
"""
import datetime, os, re, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["brandname.py", "desk.py", "diag.py", "screen.py", "docxw.py", "generate.py", "server.py", "store.py", "uw.py",
         "valuation.py", "make_share.py", "index.html", "README.md", ".env.example",
         "START_HERE.bat", "DIAG.bat", "RUN_TESTS.bat", "MAKE_SHARE_ZIP.bat",
         "tests/test_valuation.py", "tests/test_smoke.py",
         "tests/fixtures/sample_qrtx_acme.py", "tests/fixtures/sample_volt_blmp_zeno.py",
         "tests/fixtures/sample_mgmt.py"]
TOKENISH = re.compile(r"(UW_API_TOKEN\s*=\s*(?!your)[A-Za-z0-9-]{12,}|Bearer\s+[A-Za-z0-9-]{20,}"
                      r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b)", re.I)


def real_tokens():
    """Every token value in this desk's .env and its siblings' -- never printed."""
    out, parent = set(), os.path.dirname(HERE)
    for d in [HERE] + [os.path.join(parent, n) for n in os.listdir(parent)]:
        p = os.path.join(d, ".env")
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8-sig", errors="replace"):
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                if "TOKEN" in k.upper() and len(v) >= 12:
                    out.add(v)
    return out


def personal():
    """Windows username (and the one in this folder's path) -- must not travel."""
    names = {os.environ.get("USERNAME", ""), os.environ.get("USER", "")}
    m = re.search(r"[\\/]Users[\\/]([^\\/]+)", HERE)
    if m:
        names.add(m.group(1))
    return {n for n in names if len(n) >= 3 and not n.startswith("rcw-")} | set(filter(None, os.environ.get("SHARE_BLOCK", "").split(",")))


def main():
    secrets = real_tokens()
    problems, packed = [], []
    for rel in FILES:
        p = os.path.join(HERE, rel)
        if not os.path.isfile(p):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        if any(s in text for s in secrets):
            problems.append("%s contains your real API token" % rel)
        for who in personal():
            if who.lower() in text.lower():
                problems.append("%s mentions '%s' (your username/email)" % (rel, who))
        for m in TOKENISH.finditer(text):
            # UW row ids in the test fixtures are UUIDs too (insider `ids`); the real-token
            # check above still covers these files, so bare UUIDs there are allowed
            if rel.startswith("tests/fixtures/") and "=" not in m.group(0) and "Bearer" not in m.group(0):
                continue
            problems.append("%s: token-shaped text near '%s...'" % (rel, m.group(0)[:14]))
        packed.append(rel)
    if problems:
        print("NOT BUILT -- fix these first:\n  " + "\n  ".join(problems))
        return 1
    name = "Valuation_Desk_share_%s.zip" % datetime.date.today().isoformat()
    dest = os.path.join(os.path.dirname(HERE), name)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in packed:
            z.write(os.path.join(HERE, rel), "Valuation Desk/" + rel)
    print("Built %s (%d files). Scanned for %d real token(s) + token patterns: clean." % (dest, len(packed), len(secrets)))
    print("Left out: .env, reports\\, valuation.db*, __pycache__\\")
    print("Recipient: copy .env.example to .env, add their own UW_API_TOKEN, run START_HERE.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
