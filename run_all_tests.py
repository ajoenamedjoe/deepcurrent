"""Run every desk's test suite. No token or network needed -- all fixtures are synthetic.

    python run_all_tests.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
SUITES = [
    ("UW Dashboard", [[PY, "-m", "unittest", "discover", "-s", "tests"]]),
    ("Valuation Desk", [[PY, "-m", "unittest", "discover", "-s", "tests"]]),
    ("Confluence Desk", [[PY, "-m", "unittest", "discover", "-s", "tests"]]),
    ("Institutional Desk", [[PY, os.path.join("tests", "test_logic.py")]]),
    ("UW Flow Desk", [[PY, os.path.join("tests", t)] for t in ("test_flow.py", "test_flowscan.py", "test_server.py", "test_tape.py")]),
    ("Swing Desk", [[PY, os.path.join("tests", "test_model.py")], [PY, "-m", "unittest", "tests.test_desk"]]),
    ("Growth Desk", [[PY, "-m", "unittest", "discover", "-s", "tests"]]),
    ("repo", [[PY, "-m", "unittest", "test_setup"], [PY, "-m", "unittest", "test_security"],
              [PY, "-m", "unittest", "test_brand"]]),
]


def main():
    failed = []
    for folder, cmds in SUITES:
        cwd = HERE if folder == "repo" else os.path.join(HERE, folder)
        for cmd in cmds:
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
            tail = [l for l in (r.stdout + r.stderr).strip().splitlines() if l.strip()][-1:] or [""]
            status = "ok " if r.returncode == 0 else "FAIL"
            print("  %s  %-20s %s" % (status, folder, tail[0][:70]))
            if r.returncode != 0:
                failed.append((folder, r.stdout + r.stderr))
    for folder, out in failed:
        print("\n==== %s ====\n%s" % (folder, out[-3000:]))
    print("\nAll suites passed." if not failed else "\n%d suite(s) failed." % len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
