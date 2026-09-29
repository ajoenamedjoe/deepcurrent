"""Security regression tests (2026-09-26 review): every desk refuses other sites and other hosts, the
dashboard's own page still works, and the fixed injection points stay fixed.

    python -m unittest test_security
"""
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DESKS = ["Valuation Desk", "Confluence Desk", "Institutional Desk", "UW Flow Desk", "Swing Desk", "Growth Desk"]

# Runs inside each desk folder: start that desk's own Handler on a free port, fire the requests, print results.
PROBE = r'''
import json, os, sys, threading, http.client, tempfile
os.environ.setdefault("UW_API_TOKEN", "test-token-not-real")
os.environ["SWING_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.getcwd())
import server
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
def req(method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": "127.0.0.1:%d" % port}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse(); r.read()
    return r.status, dict(r.getheaders())
out = {}
out["rebind"] = req("GET", "/api/health", {"Host": "attacker.example"})[0]
out["xsite_api"] = req("GET", "/api/health", {"Sec-Fetch-Site": "cross-site"})[0]
out["otherport_api"] = req("GET", "/api/health", {"Sec-Fetch-Site": "same-site"})[0]
out["own_api"] = req("GET", "/api/health", {"Sec-Fetch-Site": "same-origin"})[0]
out["tool_api"] = req("GET", "/api/health")[0]
st, hd = req("GET", "/", {"Sec-Fetch-Site": "cross-site"})
out["page_status"], out["csp"] = st, hd.get("Content-Security-Policy", "")
out["post_form"] = req("POST", "/api/run", {"Content-Type": "application/x-www-form-urlencoded"}, "ticker=ACME")[0]
out["post_text"] = req("POST", "/api/run", {"Content-Type": "text/plain"}, '{"ticker":"ACME"}')[0]
out["post_xorigin"] = req("POST", "/api/nothing", {"Content-Type": "application/json", "Origin": "http://127.0.0.1:8700"}, "{}")[0]
out["post_own"] = req("POST", "/api/nothing", {"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % port}, "{}")[0]
print("RESULT " + json.dumps(out))
srv.shutdown()
'''


def probe(folder):
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=os.path.join(HERE, folder), capture_output=True,
                       text=True, timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    line = [l for l in r.stdout.splitlines() if l.startswith("RESULT ")]
    if not line:
        raise AssertionError("%s: probe failed\n%s\n%s" % (folder, r.stdout[-1500:], r.stderr[-3000:]))
    return json.loads(line[0][7:])


class DeskGuard(unittest.TestCase):
    def test_every_desk(self):
        for folder in DESKS:
            with self.subTest(folder):
                o = probe(folder)
                self.assertEqual(o["rebind"], 403)                # DNS rebinding: wrong Host
                self.assertEqual(o["xsite_api"], 403)             # another website's <img>/fetch
                self.assertEqual(o["otherport_api"], 403)         # another localhost port
                self.assertNotEqual(o["own_api"], 403)            # the desk's own page still works
                self.assertNotEqual(o["tool_api"], 403)           # the dashboard's server-side reads still work
                self.assertEqual(o["page_status"], 200)           # the page itself can be opened
                self.assertIn("frame-ancestors 'self' http://127.0.0.1:* http://localhost:*", o["csp"])
                self.assertEqual(o["post_form"], 403)             # cross-site form post
                self.assertEqual(o["post_text"], 403)             # text/plain "simple" request
                self.assertEqual(o["post_xorigin"], 403)          # a POST from another origin
                self.assertNotEqual(o["post_own"], 403)           # same-origin JSON reaches the route (404/501 is fine)


class FixedInjections(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(HERE, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_no_inline_handlers_built_from_data(self):
        for rel in ("Confluence Desk/index.html", "Institutional Desk/index.html"):
            page = self.read(rel)
            self.assertNotIn("onclick=\"openShare(", page, rel)
            self.assertIn("share-open", page, rel)

    def test_flow_escapes_uw_fields(self):
        body = self.read("UW Flow Desk/body.html")
        self.assertIn("esc(c.ticker)", body)
        self.assertIn("esc(c.sector||'')", body)
        self.assertIn("esc(x[1])", body)
        self.assertRegex(body, r"function esc\(s\)\{[^}]*&quot;[^}]*&#39;")
        self.assertNotRegex(body, r"'\+c\.ticker\+'")

    def test_generated_valuation_script_cannot_be_broken_out_of(self):
        code = ("import sys; sys.path.insert(0, '.'); import generate, valuation as V, inspect;"
                "src = inspect.getsource(generate.analysis_script); print('RE_SUB' if 're.sub(' in src.split('head =')[0] else 'RAW')")
        r = subprocess.run([sys.executable, "-c", code], cwd=os.path.join(HERE, "Valuation Desk"),
                           capture_output=True, text=True, timeout=60)
        self.assertIn("RE_SUB", r.stdout, r.stderr)

    def test_tape_ids_are_anchored_to_the_end(self):
        tape = self.read("UW Flow Desk/tape.py")
        for name in ("UUID_RE", "OSI_RE", "DATE_RE"):
            line = [l for l in tape.splitlines() if l.startswith(name)][0]
            self.assertIn("\\Z", line, name)


class Gitignore(unittest.TestCase):
    def test_runtime_files_are_ignored(self):
        if not os.path.isfile(os.path.join(HERE, ".gitignore")):
            self.skipTest("no .gitignore in this copy (GitHub's web upload leaves out dot-files)")
        with open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as fh:
            gi = fh.read()
        for pat in ("*.db-*", ".env.*", "!.env.example", "backups/", "board.min.json", "flow-board.html", ".vscode/", ".idea/"):
            self.assertIn(pat, gi.splitlines(), pat)


if __name__ == "__main__":
    unittest.main()
