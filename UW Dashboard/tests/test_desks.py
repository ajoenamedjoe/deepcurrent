"""Desk supervisor: port discovery, start-if-not-running, code-change detection."""
import os
import tempfile
import time
import unittest

from _path import ROOT  # noqa: F401
import desks
import env


def make_desk(src, env_text=None):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "server.py"), "w") as fh:
        fh.write(src)
    with open(os.path.join(d, "index.html"), "w") as fh:
        fh.write("<html></html>")
    if env_text is not None:
        with open(os.path.join(d, ".env"), "w") as fh:
            fh.write(env_text)
    return d


VAL_SRC = 'PORT = uw.env_int("VDESK_PORT", 8790)\nOPEN_BROWSER = uw.env_bool("VDESK_OPEN_BROWSER", True)\n'
INST_SRC = 'PORT = uw.env_int("IDESK_PORT", 8777)\n    if uw.env_bool("IDESK_OPEN_BROWSER", True):\n'


class FakeProc:
    def __init__(self):
        self.pid = 4242
        self.code = None

    def poll(self):
        return self.code


class Detect(unittest.TestCase):
    def test_port_and_browser_switch_from_source(self):
        det = desks.detect(make_desk(VAL_SRC))
        self.assertEqual((det["port"], det["port_var"]), (8790, "VDESK_PORT"))
        self.assertEqual(det["browser_vars"], ["VDESK_OPEN_BROWSER"])

    def test_desk_env_moves_the_port(self):
        det = desks.detect(make_desk(VAL_SRC, "VDESK_PORT=8899\n"))
        self.assertEqual(det["port"], 8899)

    def test_institutional_style_inline_switch(self):
        det = desks.detect(make_desk(INST_SRC))
        self.assertEqual(det["port"], 8777)
        self.assertEqual(det["browser_vars"], ["IDESK_OPEN_BROWSER"])

    def test_sibling_desks_when_present(self):
        """When the three sibling desks are installed next door, check their sources."""
        expect = {"Valuation Desk": 8790, "Confluence Desk": 8770, "Institutional Desk": 8777}
        found = 0
        for folder, port in expect.items():
            path = os.path.join(env.PARENT, folder)
            if not os.path.isfile(os.path.join(path, "server.py")):
                continue
            found += 1
            det = desks.detect(path)
            local = env.parse_env_file(os.path.join(path, ".env")).get(det["port_var"] or "")
            self.assertEqual(det["port"], int(local) if local else port, folder)
            self.assertTrue(det["browser_vars"], folder)
        if not found:
            self.skipTest("sibling desks not present in this checkout")

    def test_netstat_parse(self):
        text = ("  Proto  Local Address          Foreign Address        State           PID\n"
                "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1000\n"
                "  TCP    127.0.0.1:8790         0.0.0.0:0              LISTENING       5555\n"
                "  TCP    127.0.0.1:8790         127.0.0.1:50000        ESTABLISHED     5555\n")
        self.assertEqual(desks.parse_netstat(text, 8790), 5555)
        self.assertIsNone(desks.parse_netstat(text, 8770))


class Supervise(unittest.TestCase):
    def setUp(self):
        self.folder = make_desk(VAL_SRC)
        self.up = set()
        self.spawned = []
        cfg = {"desks": [{"id": "valuation", "name": "Valuation", "folder": self.folder, "port": 8790}]}
        self.cfg = cfg

        def spawn(d):
            self.spawned.append(d)
            p = FakeProc()
            self.proc = p
            return p
        self.sup = desks.Supervisor(lambda: self.cfg, log=lambda m: None, spawn=spawn,
                                    prober=lambda port, timeout=1.5: port in self.up)

    def status(self):
        return self.sup.snapshot()[0]

    def test_opening_a_stopped_desk_starts_it_once(self):
        self.assertEqual(self.sup.ensure("valuation"), "starting")
        self.assertEqual(self.sup.ensure("valuation"), "starting")
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(self.spawned[0]["detected"]["browser_vars"], ["VDESK_OPEN_BROWSER"])
        self.up.add(8790)
        self.sup.refresh()
        self.assertEqual(self.status()["status"], "running")

    def test_running_desk_is_not_started_again(self):
        self.up.add(8790)
        self.assertEqual(self.sup.ensure("valuation"), "running")
        self.assertEqual(self.spawned, [])

    def test_code_change_marks_desk_for_restart(self):
        self.up.add(8790)
        self.sup.refresh()
        self.assertFalse(self.status()["updated"])
        p = os.path.join(self.folder, "server.py")
        future = time.time() + 30
        os.utime(p, (future, future))
        self.sup.refresh()
        self.assertTrue(self.status()["updated"])

    def test_auto_restart_waits_for_the_files_to_settle(self):
        self.cfg["auto_restart_on_update"] = True
        self.up.add(8790)
        self.sup.refresh()
        restarted = []
        self.sup.restart = lambda did: restarted.append(did)
        p = os.path.join(self.folder, "server.py")
        now = time.time() + 5                      # "just written"
        os.utime(p, (now, now))
        self.sup.refresh()
        self.assertEqual(restarted, [])
        old = time.time() - 60                     # written a minute ago, quiet since
        st = self.sup.state["valuation"]
        st["code_at_start"] = old - 100
        os.utime(p, (old, old))
        self.sup.refresh()
        self.assertEqual(restarted, ["valuation"])

    def test_page_change_alone_is_not_a_restart(self):
        self.up.add(8790)
        self.sup.refresh()
        before = self.status()["page_mtime"]
        p = os.path.join(self.folder, "index.html")
        future = time.time() + 30
        os.utime(p, (future, future))
        self.sup.refresh()
        self.assertFalse(self.status()["updated"])
        self.assertGreater(self.status()["page_mtime"], before)

    def test_a_desk_that_exits_reports_an_error(self):
        self.sup.ensure("valuation")
        self.proc.code = 2
        self.sup.refresh()
        self.assertEqual(self.status()["status"], "error")
        self.assertIn("exit code 2", self.status()["error"])

    def test_missing_folder_says_so(self):
        self.cfg["desks"][0]["folder"] = os.path.join(self.folder, "nope")
        self.assertEqual(self.sup.ensure("valuation"), "missing")
        self.assertIn("Folder not found", self.status()["error"])

    def test_keep_alive_restarts_but_is_rate_limited(self):
        self.cfg["keep_alive"] = True
        for _ in range(6):
            self.sup.refresh()
            if self.proc:
                self.proc.code = 1
            self.sup.refresh()
        self.assertLessEqual(len(self.spawned), desks.MAX_RETRIES)

    def test_relative_folder_resolves_next_to_the_dashboard(self):
        self.assertEqual(desks.resolve("Valuation Desk"), os.path.join(env.PARENT, "Valuation Desk"))
        self.assertTrue(desks.resolve(r"D:\UW-Desks\UW Flow Desk").endswith("UW Flow Desk"))


if __name__ == "__main__":
    unittest.main()
