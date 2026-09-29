"""Scanner tests: envelope shapes, DST, warnings, and the end-to-end scan.

The stub client returns synthetic payloads in the shape of the flow-alerts
and option-contracts screener endpoints. GEX ES Desk shipped a smoke test whose
stub had a looser signature than the real client and hid a TypeError that only
surfaced at runtime; the fix recorded there is to keep stubs honest to the real
signature and feed them payloads in the real shape.
"""
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flow            # noqa: E402
import flowscan        # noqa: E402
from tests.fixtures import sample_flow as F  # noqa: E402

TODAY = date(2026, 10, 16)


class StubClient:
    """Same signature as flowscan.Client.get, on purpose."""
    def __init__(self, contracts=None, alerts=None, wrap="result", fail=None):
        self.contracts = contracts if contracts is not None else F.CONTRACTS
        self.alerts = alerts if alerts is not None else F.ALERTS
        self.wrap = wrap
        self.fail = fail or set()
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path in self.fail:
            return {"error": "nope"}
        rows = self.contracts if "option-activity" in path else self.alerts
        if params and params.get("page", 0) > 0:
            rows = []
        if self.wrap is None:
            return rows
        if self.wrap == "bare_object":
            return {"data": {"results": rows}}
        return {self.wrap: rows}


# ------------------------------------------------------------------- ENVELOPE

def test_dig_finds_rows_through_every_envelope_seen_in_the_wild():
    rows = F.CONTRACTS
    for env in ({"result": rows}, {"data": rows}, {"results": rows},
                {"response": {"data": rows}}, rows,
                {"data": {"results": rows}}):
        got = flowscan.dig(env, "option_symbol")
        assert isinstance(got, list) and len(got) == len(rows)


def test_dig_scans_deep_enough_into_a_long_flat_list():
    # greek-flow returns 400+ rows and the first 20 are not enough -- the GEX
    # desk's tests caught that before shipping.
    padded = [{"junk": i} for i in range(300)] + [dict(F.FJRD_SCR)]
    assert flowscan.dig(padded, "option_symbol") is not None


def test_dig_is_cycle_safe():
    a = {}
    a["self"] = a
    assert flowscan.dig(a, "option_symbol") is None


def test_dig_returns_none_rather_than_guessing():
    assert flowscan.dig({"nothing": 1}, "option_symbol") is None


# ------------------------------------------------------------- NEVER SILENT

def test_a_bad_envelope_produces_a_named_warning_not_an_empty_board():
    # GEX bug #2 showed a blank chart and no error for two rounds of guessing.
    s = flowscan.FlowScan("t", client=StubClient(
        fail={"/api/option-trades/flow-alerts"}))
    board = s.run(today=TODAY)
    assert board["meta"]["warnings"]
    w = board["meta"]["warnings"][0]
    assert w["endpoint"] == "/api/option-trades/flow-alerts"
    assert "object keys" in w["shape"]
    assert board["cards"], "contracts still scored when alerts fail"


def test_the_shape_description_does_not_leak_payload_contents():
    assert flowscan._shape({"token": "secret", "b": 1}) == "object keys: b, token"
    assert flowscan._shape([1, 2, 3]) == "list of 3"


# ---------------------------------------------------------------- CALL BUDGET

def test_a_scan_is_a_handful_of_wide_calls_not_one_per_ticker():
    c = StubClient()
    board = flowscan.FlowScan("t", client=c).run(today=TODAY)
    assert len(c.calls) <= 3, c.calls
    assert board["meta"]["tickers"] > 5
    paths = {p for p, _ in c.calls}
    assert not any("/api/stock/" in p for p in paths)


def test_pagination_stops_as_soon_as_a_short_page_comes_back():
    c = StubClient()
    flowscan.FlowScan("t", client=c, pages=5).run(today=TODAY)
    contract_calls = [p for p, _ in c.calls if "option-activity" in p]
    assert len(contract_calls) == 1   # 9 rows < limit, so no page 1


def test_the_rest_only_unusual_preset_is_actually_sent():
    c = StubClient()
    flowscan.FlowScan("t", client=c).run(today=TODAY)
    for path, params in c.calls:
        assert params.get("unusual") is True, path
        assert params.get("issue_types") == ["Common Stock"], path


def test_the_screener_limit_is_the_rest_maximum_not_the_mcp_one():
    # REST max is 250; the MCP tool hands back 50 whatever you ask for.
    assert flowscan.CONTRACT_PARAMS["limit"] == 250
    assert flowscan.ALERT_PARAMS["limit"] == 200


def test_every_candidate_is_scored_with_no_top_n_slice():
    c = StubClient()
    board = flowscan.FlowScan("t", client=c).run(today=TODAY)
    tickers = {r["ticker_symbol"] for r in F.CONTRACTS} | {a["ticker"] for a in F.ALERTS}
    assert {x["ticker"] for x in board["cards"]} == tickers


# ---------------------------------------------------------------- THE BOARD

def test_the_scan_dedupes_alerts_before_grouping():
    c = StubClient()
    board = flowscan.FlowScan("t", client=c).run(today=TODAY)
    zeno = [x for x in board["cards"] if x["ticker"] == "ZENO"][0]
    assert zeno["alert_count"] == 1   # the floor alert subsumes its sibling


def test_every_card_carries_a_lane_and_a_reason_when_rejected():
    board = flowscan.FlowScan("t", client=StubClient()).run(today=TODAY)
    for card in board["cards"]:
        assert card["lane"] in ("board", "spreads", "near", "out")
        if card["lane"] == "out":
            assert card["excluded"]
        if card["lane"] == "board":
            assert card["excluded"] is None


def test_meta_reports_the_observed_maximum_not_a_theoretical_100():
    board = flowscan.FlowScan("t", client=StubClient()).run(today=TODAY)
    assert board["meta"]["observed_max"] == board["cards"][0]["score"]
    assert board["meta"]["observed_max"] < 100


def test_an_empty_market_does_not_raise():
    board = flowscan.FlowScan("t", client=StubClient([], [])).run(today=TODAY)
    assert board["cards"] == []
    assert board["meta"]["observed_max"] == 0.0


# --------------------------------------------------------------------- CLOCK

def test_et_offset_across_both_dst_boundaries():
    cases = [
        ("2026-03-08T06:59:00Z", -5), ("2026-03-08T07:00:00Z", -4),
        ("2026-06-15T12:00:00Z", -4),
        ("2026-11-01T05:59:00Z", -4), ("2026-11-01T06:00:00Z", -5),
        ("2026-01-15T12:00:00Z", -5), ("2026-12-31T23:00:00Z", -5),
        ("2027-03-14T07:00:00Z", -4), ("2027-11-07T06:00:00Z", -5),
        ("2026-10-16T16:00:00Z", -4),
    ]
    for iso, want in cases:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        assert flowscan.et_offset(dt) == want, iso


def test_market_open_window_and_the_warmup():
    def at(iso):
        return flowscan.market_open(
            datetime.fromisoformat(iso.replace("Z", "+00:00")))
    assert at("2026-10-16T13:31:00Z") is False   # 09:31 ET, inside warmup
    assert at("2026-10-16T13:50:00Z") is True    # 09:50 ET
    assert at("2026-10-16T19:59:00Z") is True    # 15:59 ET
    assert at("2026-10-16T20:01:00Z") is False   # 16:01 ET
    assert at("2026-10-17T15:00:00Z") is False   # Saturday


def test_the_two_clocks_are_deliberately_different():
    # flow.session_day buckets a print with a fixed -5; flowscan.et_offset
    # follows real DST. Asserting the difference on purpose so nobody
    # "harmonises" them and opens the desk an hour late for eight months.
    summer = datetime(2026, 10, 16, 16, 0, tzinfo=timezone.utc)
    assert flowscan.et_offset(summer) == -4
    assert flow.ET_OFFSET_HOURS == -5
    # ...and the fixed offset still buckets both ends of a DST session right.
    assert flow.session_day(int(summer.timestamp() * 1000)).isoformat() == "2026-10-16"
    late = datetime(2026, 10, 16, 23, 59, tzinfo=timezone.utc)   # 19:59 EDT
    assert flow.session_day(int(late.timestamp() * 1000)).isoformat() == "2026-10-16"


def test_session_today_uses_et_not_utc():
    # 2026-10-16T23:30Z is still the 16th in New York.
    dt = datetime(2026, 10, 16, 23, 30, tzinfo=timezone.utc)
    assert flowscan.session_today(dt).isoformat() == "2026-10-16"


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
        except Exception:
            failed += 1
            print("FAIL " + name)
            traceback.print_exc()
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
