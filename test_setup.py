"""Checks that guard the public repo itself: setup writes tokens only to .env, and no
secret-shaped string or personal path is committed."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import setup

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".git", "__pycache__", "logs", "reports"}
TOKENISH = re.compile(
    r"(?:UW_API_TOKEN|UW_TOKEN)\s*=\s*(?!your|paste|$)(?![\w\-]*(?:test|sibling|example|fake|dummy))[A-Za-z0-9_\-]{12,}"
    r"|Bearer\s+[A-Za-z0-9_\-]{20,}"
    r"|discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]+"
    r"|docs\.google\.com/spreadsheets/d/e/2PACX-1v[A-Za-z0-9_\-]{20,}"
    r"|[A-Za-z]:\\Users\\(?!you\\|<)[A-Za-z0-9_.\-]+\\")


def shipped_files():
    for dp, dn, fn in os.walk(HERE):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f == ".env" or f.endswith((".db", ".db-wal", ".db-shm", ".log", ".pyc")):
                continue
            yield os.path.join(dp, f)


class Repo(unittest.TestCase):
    def test_no_secrets_or_personal_paths_in_any_file(self):
        hits = []
        for p in shipped_files():
            with open(p, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if TOKENISH.search(line):
                        hits.append("%s:%d" % (os.path.relpath(p, HERE), n))
        self.assertEqual(hits, [])

    def test_env_examples_ship_no_webhook_and_say_private_channel(self):
        for d in setup.DESKS + ["UW Dashboard"]:
            with open(os.path.join(HERE, d, ".env.example"), encoding="utf-8") as fh:
                text = fh.read()
            for m in re.finditer(r"^\s*(\w*WEBHOOK\w*)\s*=(.*)$", text, re.M):
                self.assertEqual(m.group(2).strip(), "", "%s: %s must ship empty" % (d, m.group(1)))
            if "WEBHOOK" in text:
                self.assertIn("Private channel only", text, d)

    def test_every_desk_has_an_env_example_and_gitignore_covers_env(self):
        for d in setup.DESKS + ["UW Dashboard"]:
            self.assertTrue(os.path.isfile(os.path.join(HERE, d, ".env.example")), d)
        with open(os.path.join(HERE, ".gitignore")) as fh:
            ignored = fh.read().split()
        for pat in (".env", "*.db", "__pycache__/", "logs/"):
            self.assertIn(pat, ignored)

    def test_git_would_not_commit_env_or_databases(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        d = tempfile.mkdtemp()
        shutil.copy(os.path.join(HERE, ".gitignore"), d)
        os.makedirs(os.path.join(d, "Swing Desk"))
        for f in (".env", "swing_history.db", ".env.example"):
            open(os.path.join(d, "Swing Desk", f), "w").close()
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        r = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=d, capture_output=True, text=True)
        self.assertIn(".env.example", r.stdout)
        self.assertNotIn("Swing Desk/.env\n", r.stdout + "\n")
        self.assertNotIn(".db", r.stdout)


class Setup(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        for d, line in (("Swing Desk", "UW_API_TOKEN="), ("UW Flow Desk", "UW_TOKEN=paste_your_token_here")):
            os.makedirs(os.path.join(self.root, d))
            with open(os.path.join(self.root, d, ".env.example"), "w") as fh:
                fh.write("# comment\n%s\nPORT=1\n" % line)
        self.saved = setup.HERE
        setup.HERE = self.root
        self.addCleanup(setattr, setup, "HERE", self.saved)
        os.environ.pop("UW_API_TOKEN", None)

    def read(self, d):
        with open(os.path.join(self.root, d, ".env")) as fh:
            return fh.read()

    def test_writes_the_right_key_per_desk(self):
        self.assertEqual(setup.main([], ask=lambda p: "testTOKEN123456"), 0)
        self.assertIn("UW_API_TOKEN=testTOKEN123456\n", self.read("Swing Desk"))
        self.assertIn("UW_TOKEN=testTOKEN123456\nPORT=1", self.read("UW Flow Desk"))

    def test_rerun_only_swaps_the_token_and_keeps_other_edits(self):
        setup.main([], ask=lambda p: "testTOKEN123456")
        p = os.path.join(self.root, "Swing Desk", ".env")
        with open(p, "a") as fh:
            fh.write("DISCORD_WEBHOOK_URL=x\n")
        setup.main([], ask=lambda p: "testTOKEN987654")
        self.assertIn("UW_API_TOKEN=testTOKEN987654", self.read("Swing Desk"))
        self.assertIn("DISCORD_WEBHOOK_URL=x", self.read("Swing Desk"))

    def test_rejects_junk(self):
        self.assertEqual(setup.main([], ask=lambda p: "hi"), 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "Swing Desk", ".env")))


if __name__ == "__main__":
    unittest.main()
