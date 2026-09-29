"""The tape behind an alert. Synthetic rows in the shape of the option-trade tape
(field names verified through the MCP option-trades tool on 2026-09-25)."""
import json
import os
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flow                 # noqa: E402
import server               # noqa: E402
import tape                 # noqa: E402
from tests.fixtures import sample_flow as F   # noqa: E402

ALERT_ID = "5f0c2a4e-8b1d-4c3e-9a77-1e2d3c4b5a69"
OSI = "ACME261016C00200000"


def trade(ms, price, size, bid, ask, tags=("ask_side", "bullish"), flags=(), code="auto", canceled=False):
    return {"executed_at": "2031-06-13T17:%02d:%02d.%03dZ" % (ms // 60000 % 60, ms // 1000 % 60, ms % 1000),
            "price": "%.2f" % price, "size": size, "premium": "%.2f" % (price * size * 100),
            "nbbo_bid": "%.2f" % bid, "nbbo_ask": "%.2f" % ask, "tags": list(tags),
            "report_flags": list(flags), "exchange": "XCBO", "upstream_condition_detail": code,
            "underlying_price": "187.40", "implied_volatility": "0.4120", "delta": "0.31",
            "volume": 5400, "open_interest": 1200, "option_chain_id": OSI, "canceled": canceled}


ALERT_PAYLOAD = {"data": {"id": ALERT_ID, "alert_rule": "RepeatedHits", "trades": [
    trade(2000, 2.45, 120, 2.40, 2.45, flags=("sweep",)),
    trade(1000, 2.45, 300, 2.40, 2.45, flags=("sweep",)),
    trade(3000, 2.43, 50, 2.40, 2.45, tags=("mid_side",)),
]}}
CONTRACT_PAYLOAD = {"result": [
    trade(1000, 2.40, 10, 2.40, 2.45, tags=("bid_side", "bearish")),
    trade(5000, 2.50, 40, 2.45, 2.50, tags=()),                        # no tag: priced at the ask
    trade(9000, 2.50, 5, 0, 0, tags=(), flags=("floor",)),               # NBBO "0" on a floor print
    trade(7000, 2.55, 25, 2.45, 2.50, tags=(), code="mlat"),             # multi-leg condition code
    trade(8000, 2.50, 99, 2.45, 2.50, canceled=True),
]}


class Stub:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path.startswith("/api/option-trades/flow-alerts/"):
            return ALERT_PAYLOAD
        if path.startswith("/api/option-contract/"):
            return CONTRACT_PAYLOAD
        raise urllib.error.HTTPError(path, 404, "nf", {}, None)


def test_alert_prints_come_back_in_fill_order_with_sides_and_flags():
    t = tape.Tape(Stub())
    code, body = t.lookup(alert=ALERT_ID)
    assert code == 200, body
    rows = body["trades"]
    assert [r["contracts"] for r in rows] == [300, 120, 50]          # chronological
    assert [r["side"] for r in rows] == ["ask", "ask", "mid"]
    assert rows[0]["flags"] == ["sweep"]
    assert rows[0]["time_et"] == "13:00:01.000"                          # EDT
    s = body["summary"]
    assert s["contracts"] == 470 and s["sweeps"] == 2
    assert abs(s["premium"] - (2.45 * 420 * 100 + 2.43 * 50 * 100)) < 0.01
    assert abs(s["ask_share"] - 102900 / 115050) < 1e-3                 # mid print dilutes it


def test_contract_tape_newest_first_and_every_side_rule():
    stub = Stub()
    code, body = tape.Tape(stub).lookup(contract=OSI.lower(), date="2031-06-13")
    assert code == 200
    assert stub.calls[0] == ("/api/option-contract/%s/flow" % OSI, {"limit": 50, "date": "2031-06-13"})
    rows = body["trades"]
    assert rows[0]["executed_at"] > rows[-1]["executed_at"]
    side = {r["time_et"][-6:]: r["side"] for r in rows}
    assert side["01.000"] == "bid"            # UW's tag wins
    assert side["05.000"] == "ask"            # no tag: price at the ask
    assert side["09.000"] == "none"           # NBBO zero never divides or guesses
    assert side["07.000"] == "ask"
    assert [r for r in rows if r["time_et"].endswith("07.000")][0]["flags"] == ["multileg"]
    assert body["summary"]["canceled"] == 1 and body["summary"]["trades"] == 4


def test_bad_input_is_refused_before_any_call():
    stub = Stub()
    t = tape.Tape(stub)
    assert t.lookup(alert="../../etc")[0] == 400
    assert t.lookup(contract="ACME; DROP")[0] == 400
    assert t.lookup(contract=OSI, date="13/06/2031")[0] == 400
    assert t.lookup()[0] == 400
    assert stub.calls == []


def test_uw_errors_are_reported_not_raised():
    class Broken:
        def get(self, path, params=None):
            raise urllib.error.HTTPError(path, 403, "forbidden", {}, None)
    code, body = tape.Tape(Broken()).lookup(alert=ALERT_ID)
    assert code == 502 and "HTTP 403" in body["error"] and body["trades"] == []


def test_cached_for_a_minute():
    stub, now = Stub(), [1000.0]
    t = tape.Tape(stub, clock=lambda: now[0])
    t.lookup(alert=ALERT_ID)
    t.lookup(alert=ALERT_ID)
    assert len(stub.calls) == 1
    now[0] += 61
    t.lookup(alert=ALERT_ID)
    assert len(stub.calls) == 2


def test_empty_answer_says_so():
    class Empty:
        def get(self, path, params=None):
            return {"data": []}
    code, body = tape.Tape(Empty()).lookup(contract=OSI)
    assert code == 200 and body["trades"] == [] and "no trades" in body["note"]


def test_find_trades_ignores_non_trade_lists():
    payload = {"data": {"legs": [{"a": 1}], "rows": [trade(1000, 1, 1, 1, 1)]}}
    assert len(tape.find_trades(payload)) == 1
    assert tape.find_trades({"data": [{"x": 1}]}) == []


def test_uw_link_is_the_live_flow_page_for_the_ticker():
    assert tape.uw_link("acme") == "https://unusualwhales.com/live-options-flow?ticker_symbol=ACME"
    assert tape.uw_link("") is None


def test_cards_carry_alert_ids_and_contracts_for_the_panel():
    card = flow.build_card("ZENO", [F.ZENO_FLOOR, F.ZENO_REPEATED], F.CONTRACTS[:3])
    assert card["alerts"] and all(tape.UUID_RE.match(a["id"]) for a in card["alerts"])
    prem = [float(a["total_premium"]) for a in card["alerts"]]
    assert prem == sorted(prem, reverse=True)
    assert all(tape.OSI_RE.match(c["option_symbol"]) for c in card["contracts"])
    json.dumps(card)                          # still serialisable


def test_server_route_validates_and_needs_a_token():
    real_tok, real_t = server.TOKEN, server.TAPE["t"]
    try:
        server.TOKEN = None
        assert server.tape_lookup("alert=" + ALERT_ID)[0] == 503
        server.TOKEN = "test-token"
        server.TAPE["t"] = tape.Tape(Stub())
        code, body = server.tape_lookup("contract=" + OSI)
        assert code == 200 and body["kind"] == "contract"
        srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = "http://127.0.0.1:%d/api/tape?alert=%s" % (srv.server_address[1], ALERT_ID)
            with urllib.request.urlopen(url, timeout=5) as r:
                assert json.loads(r.read())["summary"]["contracts"] == 470
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/api/tape?alert=nope" % srv.server_address[1], timeout=5)
                assert False, "expected 400"
            except urllib.error.HTTPError as e:
                assert e.code == 400
        finally:
            srv.shutdown()
    finally:
        server.TOKEN, server.TAPE["t"] = real_tok, real_t


if __name__ == "__main__":
    import traceback as tb
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
