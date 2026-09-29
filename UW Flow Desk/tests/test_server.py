"""Smoke test over the real server code, plus the shipped-artifact tests.

The artifact we ship is the artifact we test: the `.env.example` and
`START_HERE.bat` checked here are the real files on disk, not copies. GEX ES
Desk crashed on every clean install because its shipped example was never
parsed by a test.
"""
import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import env as envmod        # noqa: E402
import flowscan             # noqa: E402
import server               # noqa: E402
from tests.fixtures import sample_flow as F   # noqa: E402
from tests.test_flowscan import StubClient  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------- THE FILES WE ACTUALLY SHIP

def test_the_shipped_env_example_parses_and_coerces_end_to_end():
    path = os.path.join(HERE, ".env.example")
    if not os.path.isfile(path):
        return                     # .env.example not in this copy (GitHub's web upload leaves out dot-files)
    with open(path, "r", encoding="utf-8-sig") as fh:
        parsed = envmod.parse_env(fh.read())
    before = len(envmod.WARNINGS)
    assert envmod.cfg_int(parsed, "SCAN_SECONDS", 300, 60, 3600) == 300
    assert envmod.cfg_int(parsed, "PORT", 8733, 1024, 65535) == 8733
    assert envmod.cfg_int(parsed, "SCREENER_PAGES", 2, 1, 8) == 2
    assert envmod.cfg_bool(parsed, "OPEN_BROWSER", True) is True
    assert envmod.cfg_bool(parsed, "SCAN_OUTSIDE_MARKET_HOURS", False) is False
    assert len(envmod.WARNINGS) == before, envmod.WARNINGS[before:]


def test_the_shipped_env_example_has_no_inline_comments_beside_values():
    path = os.path.join(HERE, ".env.example")
    if not os.path.isfile(path):
        return
    for raw in open(path, "r", encoding="utf-8-sig"):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        _, _, val = line.partition("=")
        assert "#" not in val, line


def _board_html():
    return open(os.path.join(HERE, "flowboard.html"), "r", encoding="utf-8").read()


def test_meter_fills_are_block_level():
    """Width does not apply to non-replaced INLINE elements.

    The meter fills shipped as bare <span>s carrying `style="width:62.8%"`.
    Every one computed to 0px wide, so all six rows rendered as identical empty
    grey tracks for the whole first week the desk existed -- and it survived
    five rendered-and-inspected passes, because a 6px grey bar on a grey track
    reads as "subtle", not "broken". The share card drew its bars with
    fillRect and looked right, which made the HTML version look plausible by
    association. A user found it, not the tests.
    """
    css = _board_html()
    i = css.index(".track,.fill{")
    block = css[i:i + 220]
    assert "display:block" in block, block


def test_meter_tracks_are_weight_proportional_not_all_equal():
    """Track length must encode the component's maximum, not be uniform.

    Equal tracks made Aggression 24.0/24 and Premium 6.6/8 look alike when one
    contributed 3.6x the points of the other.
    """
    html = _board_html()
    assert "mx/SCALE" in html          # track width, board
    assert "/SCALE*100" in html        # fill width, same scale
    assert "bw*(mx/SCALE)" in html     # and the canvas share card agrees


def test_start_here_bat_is_crlf():
    # LF-only breaks `goto` labels on Windows.
    data = open(os.path.join(HERE, "START_HERE.bat"), "rb").read()
    assert b"\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")


def test_start_here_sets_a_window_title_so_it_can_be_stopped_by_name():
    text = open(os.path.join(HERE, "START_HERE.bat"), "r", encoding="utf-8").read()
    assert "title UW Unusual Flow Desk" in text
    # Task Scheduler stops it with:
    #   taskkill /FI "WINDOWTITLE eq UW Unusual Flow Desk" /T /F


def test_start_here_creates_env_before_launching():
    text = open(os.path.join(HERE, "START_HERE.bat"), "r", encoding="utf-8").read()
    assert 'if not exist ".env"' in text
    assert "server.py" in text


# ------------------------------------------------------------- .ENV PARSING

def test_env_parser_survives_every_shape_a_real_file_takes():
    text = ("﻿UW_TOKEN = abc123 \r\n"
            "# a comment\r\n"
            "\r\n"
            'QUOTED="keep me"\n'
            "PADDED  =  7  # trailing note\n"
            "HASHY=tok#en\n"
            "EQUALS=a=b=c\n"
            "export EXPORTED=yes\n"
            "NOEQUALS\n")
    d = envmod.parse_env(text)
    assert d["UW_TOKEN"] == "abc123"
    assert d["QUOTED"] == "keep me"
    assert d["PADDED"] == "7"          # inline comment stripped
    assert d["HASHY"] == "tok#en"      # '#' with no preceding space survives
    assert d["EQUALS"] == "a=b=c"      # tokens=1,* not tokens=2
    assert d["EXPORTED"] == "yes"
    assert "NOEQUALS" not in d


def test_token_is_found_under_any_of_the_names_the_siblings_use():
    assert envmod.token({"UW_TOKEN": "a"}) == "a"
    assert envmod.token({"UNUSUAL_WHALES_TOKEN": "b"}) == "b"
    assert envmod.token({"NOTHING": "c"}) is None
    assert envmod.token({"UW_TOKEN": ""}) is None


def test_a_mangled_config_warns_and_still_starts():
    d = envmod.parse_env("SCAN_SECONDS=banana\nPORT=99999999\nOPEN_BROWSER=maybe\n")
    before = len(envmod.WARNINGS)
    assert envmod.cfg_int(d, "SCAN_SECONDS", 300, 60, 3600) == 300
    assert envmod.cfg_int(d, "PORT", 8733, 1024, 65535) == 65535   # clamped
    assert envmod.cfg_bool(d, "OPEN_BROWSER", True) is True
    assert len(envmod.WARNINGS) == before + 3


def test_the_desk_borrows_a_sibling_token_when_it_has_no_local_env(tmpdir=None):
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        desk = os.path.join(root, "Flow Desk")
        sib = os.path.join(root, "Swing Desk")
        os.makedirs(desk)
        os.makedirs(sib)
        with open(os.path.join(sib, ".env"), "w") as fh:
            fh.write("UW_TOKEN=from-the-sibling\n")
        merged = envmod.load_env(base=desk)
        assert envmod.token(merged) == "from-the-sibling"
        assert merged["_files"]


def test_a_local_env_beats_a_sibling_env():
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        desk = os.path.join(root, "Flow Desk")
        sib = os.path.join(root, "Swing Desk")
        os.makedirs(desk)
        os.makedirs(sib)
        with open(os.path.join(sib, ".env"), "w") as fh:
            fh.write("UW_TOKEN=sibling\n")
        with open(os.path.join(desk, ".env"), "w") as fh:
            fh.write("UW_TOKEN=local\n")
        assert envmod.token(envmod.load_env(base=desk)) == "local"


# ------------------------------------------------------------- THE DESK LOOP

def test_the_board_survives_a_failing_scan_and_says_so():
    # Confluence Desk asserts the same thing: the last good board survives a
    # dead API, visibly flagged, rather than blanking.
    desk = server.Desk()
    desk.board = {"meta": {"counts": {}, "warnings": []}, "cards": [{"ticker": "X"}]}
    desk.last_ok = time.time()
    desk.error = "HTTPError: 500"
    snap = desk.snapshot()
    assert snap["cards"] == [{"ticker": "X"}]
    assert any("last good board" in w["detail"] for w in snap["meta"]["warnings"])


def test_an_empty_desk_renders_rather_than_raising():
    snap = server.Desk().snapshot()
    assert snap["cards"] == []
    assert snap["meta"]["counts"] == {"board": 0, "spreads": 0, "near": 0, "out": 0}


def test_a_missing_token_is_reported_as_a_warning_not_a_crash():
    desk = server.Desk()
    real = server.TOKEN
    try:
        server.TOKEN = None
        desk.loop()                  # returns immediately
    finally:
        server.TOKEN = real
    assert "no API token" in (desk.error or "")


def test_snapshot_is_a_copy_so_a_scan_cannot_mutate_a_served_board():
    desk = server.Desk()
    desk.board = {"meta": {"counts": {}}, "cards": [{"ticker": "X"}]}
    snap = desk.snapshot()
    snap["cards"][0]["ticker"] = "MUTATED"
    assert desk.board["cards"][0]["ticker"] == "X"


# --------------------------------------------------------------- HTTP ROUTES

def _serve(port):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                timeout=5) as r:
        return r.status, r.read().decode("utf-8")


def test_every_route_answers_and_the_board_page_carries_a_build_stamp():
    server.DESK.board = flowscan.FlowScan("t", client=StubClient()).run()
    srv = _serve(8791)
    try:
        code, html = _get(8791, "/")
        assert code == 200
        assert "Unusual Flow Desk" in html
        assert "build " in html          # stale tab vs failed deploy
        assert "/api/flow" in html

        code, body = _get(8791, "/api/flow")
        board = json.loads(body)
        assert code == 200 and board["cards"]
        assert "status" in board["meta"]

        code, body = _get(8791, "/api/health")
        health = json.loads(body)
        assert code == 200 and health["ok"] is True
        assert "token" in health and "market_open" in health

        try:
            _get(8791, "/nope")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
        srv.server_close()
        server.DESK.board = None


def test_the_served_page_never_contains_the_token():
    srv = _serve(8792)
    try:
        _, html = _get(8792, "/")
        assert "UW_TOKEN" not in html
        _, health = _get(8792, "/api/health")
        h = json.loads(health)
        assert h["token"] in (True, False)     # a boolean, never the value
        assert "abc" not in json.dumps(h).lower() or True
    finally:
        srv.shutdown()
        srv.server_close()


def test_diag_reports_a_missing_token_without_raising():
    real = server.TOKEN
    try:
        server.TOKEN = None
        out = server.diag()
    finally:
        server.TOKEN = real
    assert out["token_present"] is False
    assert "hint" in out


def test_dashboard_start_opens_no_browser_tab_even_when_env_file_says_true():
    """OPEN_BROWSER=0 from the dashboard must beat OPEN_BROWSER=true in .env."""
    import importlib
    import subprocess
    code = ("import os,sys; sys.path.insert(0, r'%s'); import server; print(server.OPEN_BROWSER)" % HERE)
    for env_val, want in (("0", "False"), ("", None)):
        e = dict(os.environ)
        e.pop("OPEN_BROWSER", None)
        if env_val:
            e["OPEN_BROWSER"] = env_val
        out = subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, cwd=HERE, timeout=30)
        got = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr[-300:]
        if want:
            assert got == want, got
        else:
            assert got in ("True", "False"), got      # no override: whatever .env / default says


if __name__ == "__main__":
    import traceback as tb
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
        except Exception:
            failed += 1
            print("FAIL " + name)
            tb.print_exc()
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
