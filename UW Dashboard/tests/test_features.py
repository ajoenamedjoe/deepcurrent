"""Holding warnings, position sizer, track record, backups, desk views, Eastern time."""
import datetime as dt
import os
import sqlite3
import tempfile
import time
import unittest

from _path import ROOT  # noqa: F401
from fixtures.sample_market import ACME_FLOW, ACME_INSIDER, SECTORS, TIDE
import backup
import deskview
import events
import market
import sizer
import store
import trackrec

TODAY = dt.date(2026, 10, 14)


class Warnings(unittest.TestCase):
    def test_non_plan_selling_counts_only_executed_form4_outside_plans(self):
        s = market.insider_selling(ACME_INSIDER, TODAY)
        # SPV 3,175 @ 418.64 + the partners fund 1,260 @ 409.14; the Form 144 notice and the
        # 10b5-1 director sale are excluded
        self.assertAlmostEqual(s["nonplan_usd"], 3175 * 418.6420 + 1260 * 409.1385, places=0)
        self.assertAlmostEqual(s["plan_usd"], 845 * 421.55, places=0)
        self.assertEqual(s["sellers"][0]["name"], "EXAMPLE SPV-1, L.P.")
        self.assertEqual(s["sellers"][0]["role"], "10% owner")

    def test_duplicate_grouped_filing_is_counted_once(self):
        doubled = ACME_INSIDER + [dict(ACME_INSIDER[3], id="other", ids=list(reversed(ACME_INSIDER[3]["ids"])))]
        self.assertAlmostEqual(market.insider_selling(doubled, TODAY)["nonplan_usd"],
                               market.insider_selling(ACME_INSIDER, TODAY)["nonplan_usd"])

    def test_wrong_fill_price_falls_back_to_the_quote(self):
        bad = [dict(ACME_INSIDER[3], price="64000")]      # ~155x the quote: an implausible fill
        self.assertAlmostEqual(market.insider_selling(bad, TODAY)["nonplan_usd"], 3175 * 412.37, places=0)

    def test_put_flow_measures_ask_premium_and_dedupes(self):
        f = market.put_flow(ACME_FLOW + [ACME_FLOW[0]], now_ms=ACME_FLOW[0]["start_time"])
        self.assertAlmostEqual(f["put_ask"], 18430 + 21185 + 9760)
        self.assertAlmostEqual(f["call_ask"], 47220 + 88415)

    def test_low_coverage_alert_is_unmeasured_not_zero(self):
        row = {"type": "put", "total_ask_side_prem": "1000", "total_bid_side_prem": "0", "total_premium": "900000",
               "option_chain": "X", "start_time": ACME_FLOW[0]["start_time"]}
        f = market.put_flow([row], now_ms=ACME_FLOW[0]["start_time"])
        self.assertEqual((f["unmeasured"], f["put_ask"]), (1, 0))

    def test_warns_on_insiders_not_on_flow_or_earnings(self):
        w = market.warnings_for("ACME", {}, [], ACME_INSIDER, ACME_FLOW, TODAY, now_ms=ACME_FLOW[0]["start_time"])
        kinds = [x["kind"] for x in w["warnings"]]
        self.assertEqual(kinds, ["insider"])
        self.assertEqual(w["earnings"]["date"], "2026-11-19")          # read off the insider/flow rows

    def test_earnings_this_week_warns_and_past_dates_are_ignored(self):
        info = {"next_earnings_date": "2026-09-30"}                   # stale: in the past
        er = [{"report_date": "2026-10-18", "report_time": "postmarket", "reported_eps": None}]
        w = market.warnings_for("X", info, er, [], [], TODAY)
        self.assertEqual(w["warnings"][0]["kind"], "earnings")
        self.assertIn("in 4 days", w["warnings"][0]["title"])
        w = market.warnings_for("X", info, [], [], [], TODAY)
        self.assertEqual(w["warnings"], [])

    def test_heavy_put_buying_warns(self):
        rows = [{"type": "put", "total_ask_side_prem": "300000", "total_bid_side_prem": "10000",
                 "total_premium": "310000", "option_chain": "A", "start_time": 1}]
        w = market.warnings_for("X", {}, [], [], rows, TODAY, now_ms=2)
        self.assertEqual([x["kind"] for x in w["warnings"]], ["flow"])

    def test_sectors_and_tide(self):
        s = {r["ticker"]: r for r in market.sector_rows(events.rows(SECTORS))}
        self.assertAlmostEqual(s["XLV"]["chg"], 151.36 / 150.12 - 1)
        self.assertLess(s["XLE"]["lean"], 0)
        t = market.tide_rows(events.rows(TIDE))
        self.assertEqual(len(t), 3)
        self.assertLess(t[1]["call"], 0)


class Sizer(unittest.TestCase):
    def test_risk_bound_long(self):
        r = sizer.size(100000, 50, 0.01, 0.20, 0.05, "long")
        self.assertAlmostEqual(r["stop"], 47.5)
        self.assertEqual(r["shares_by_risk"], 400)          # $1,000 / $2.50
        self.assertEqual(r["shares_by_size"], 400)          # $20,000 / $50
        self.assertEqual(r["shares"], 400)
        self.assertAlmostEqual(r["at_risk_usd"], 1000)

    def test_size_bound_names_the_cap(self):
        r = sizer.size(100000, 50, 0.01, 0.10, 0.02, "long")
        self.assertEqual((r["shares_by_risk"], r["shares_by_size"], r["shares"], r["bound"]), (1000, 200, 200, "size"))
        self.assertIn("Capped by max position size", r["notes"][0])
        self.assertLess(r["at_risk_pct"], 0.01)

    def test_short_stop_above_entry(self):
        r = sizer.size(50000, 20, 0.02, 0.25, 0.10, "short")
        self.assertAlmostEqual(r["stop"], 22.0)
        self.assertEqual(r["shares"], 500)
        self.assertAlmostEqual(r["targets"][1]["price"], 16.0)      # 2R below entry

    def test_never_rounds_up(self):
        r = sizer.size(10000, 33.33, 0.01, 1.0, 0.07, "long")
        self.assertLessEqual(r["at_risk_usd"], 100.0 + 1e-9)

    def test_zero_shares_says_why(self):
        r = sizer.size(1000, 900, 0.01, 0.10, 0.05)
        self.assertEqual(r["shares"], 0)
        self.assertIn("Zero shares", r["notes"][0])

    def test_cash_warning(self):
        r = sizer.size(100000, 100, 0.02, 0.30, 0.05, cash=5000)
        self.assertTrue(r.get("cash_short"))

    def test_bad_inputs(self):
        self.assertIn("error", sizer.size(100000, 0, 0.01, 0.1, 0.05))
        self.assertIn("error", sizer.size(100000, 10, 0.01, 0.1, 1.2, "long"))
        self.assertIn("error", sizer.size(100000, 10, "x", 0.1, 0.05))


def bars(start, closes):
    d = dt.date.fromisoformat(start)
    out = []
    for c in closes:
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        out.append({"date": d.isoformat(), "close": c})
        d += dt.timedelta(days=1)
    return out


class Track(unittest.TestCase):
    def test_measure_long_short_and_excess(self):
        stock = bars("2026-06-01", [100 + i for i in range(70)])
        spy = bars("2026-06-01", [500] * 70)
        m = trackrec.measure(stock, spy, "2026-06-01", "long")
        self.assertAlmostEqual(m["r5"], 0.05)
        self.assertAlmostEqual(m["x5"], 0.05)
        s = trackrec.measure(stock, spy, "2026-06-01", "short")
        self.assertAlmostEqual(s["r5"], -0.05)

    def test_weekend_pick_enters_next_session_and_unreached_horizon_is_pending(self):
        stock = bars("2026-06-01", [100 + i for i in range(12)])
        m = trackrec.measure(stock, stock, "2026-06-06", "long")      # Saturday
        self.assertEqual(m["entry_day"], "2026-06-08")
        self.assertIn("r5", m)
        self.assertNotIn("r20", m)

    def test_record_dedupes_inside_the_window(self):
        st = store.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        tr = trackrec.TrackRecord(st, None, None, log=lambda m: None)
        picks = {"confluence": {"items": [{"ticker": "QRTX", "score": 70, "direction": "long"}]}}
        self.assertEqual(tr.record(picks, today="2026-09-01"), 1)
        self.assertEqual(tr.record(picks, today="2026-09-10"), 0)
        self.assertEqual(tr.record(picks, today="2026-10-15"), 1)     # 30-day window has passed
        inst = {"institutional": {"items": [{"ticker": "BLMP", "score": 80}]}}
        tr.record(inst, today="2026-09-01")
        self.assertEqual(tr.record(inst, today="2026-11-20"), 0)       # 13F window is 120 days
        rep = tr.report()
        conf = [d for d in rep["summary"] if d["desk"] == "confluence"][0]
        self.assertEqual((conf["picks"], conf["days"]), (2, 2))
        self.assertEqual(conf["horizons"]["5"]["pending"], 2)
        self.assertIsNone(conf["horizons"]["5"]["hit"])                # pending is not zero

    def test_report_lists_every_pick_for_the_sortable_table(self):
        st = store.Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        tr = trackrec.TrackRecord(st, None, None, log=lambda m: None)
        items = [{"ticker": "T%03d" % i, "score": i, "direction": "long"} for i in range(250)]
        tr.record({"flow": {"items": items}}, today="2026-09-01")
        rep = tr.report()
        self.assertEqual(rep["total"], 250)
        self.assertEqual(len(rep["recent"]), 250)                      # was capped at 200
        for k in ("desk", "ticker", "direction", "score", "why", "picked_day", "picked_at", "r5", "r20", "r60"):
            self.assertIn(k, rep["recent"][0])                         # every column the table sorts on

    def test_eastern_time_dst(self):
        self.assertEqual(trackrec.et_now(dt.datetime(2026, 7, 1, 13, 30)).hour, 9)   # EDT
        self.assertEqual(trackrec.et_now(dt.datetime(2026, 12, 1, 14, 30)).hour, 9)  # EST
        self.assertTrue(trackrec.market_hours(dt.datetime(2026, 9, 24, 10, 0)))
        self.assertFalse(trackrec.market_hours(dt.datetime(2026, 9, 26, 10, 0)))   # Saturday


class Backups(unittest.TestCase):
    def make_db(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "dashboard.db")
        st = store.Store(path)
        st.set("settings", {"brand": "x"})
        st.journal_put("2026-09-24", "note")
        return d, path, st

    def test_backup_of_an_open_wal_database_has_the_data(self):
        d, path, st = self.make_db()
        res = backup.run(path, os.path.join(d, "bk"), stamp="2026-09-24")
        self.assertEqual(res["counts"]["journal"], 1)
        c = sqlite3.connect(res["file"])
        self.assertEqual(c.execute("SELECT body FROM journal").fetchone()[0], "note")

    def test_prune_keeps_the_newest(self):
        d, path, st = self.make_db()
        dest = os.path.join(d, "bk")
        for i in range(1, 6):
            backup.run(path, dest, keep=3, stamp="2026-09-%02d" % i)
        names = [f["name"] for f in backup.listing(dest)]
        self.assertEqual(names, ["dashboard-2026-09-05.db", "dashboard-2026-09-04.db", "dashboard-2026-09-03.db"])

    def test_due_once_a_day_after_2am(self):
        d, path, st = self.make_db()
        n = backup.Nightly(path, d, st, log=lambda m: None, get_override=lambda: os.path.join(d, "bk"))
        base = time.mktime((2026, 9, 24, 3, 0, 0, 0, 0, -1))
        self.assertTrue(n.due(base))
        st.set("backup_last", {"ok": True, "at": base})
        self.assertFalse(n.due(base + 3600))
        self.assertTrue(n.due(base + 24 * 3600))
        st.set("backup_last", {"ok": False, "at": base})
        self.assertTrue(n.due(base + 60))                              # a failed backup retries

    def test_destination_prefers_onedrive(self):
        d = tempfile.mkdtemp()
        os.environ["OneDrive"] = d
        try:
            dest, where = backup.destination("/x")
            self.assertEqual((dest, where), (os.path.join(d, backup.FOLDER_NAME), "OneDrive"))
        finally:
            del os.environ["OneDrive"]

    def test_check_folder_creates_validates_and_rejects(self):
        d = tempfile.mkdtemp()
        p, err = backup.check_folder(os.path.join(d, "new", "deeper"))
        self.assertIsNone(err)
        self.assertTrue(os.path.isdir(p))
        self.assertEqual(os.listdir(p), [])                             # the write probe is cleaned up
        self.assertIn("full path", backup.check_folder("relative/folder")[1])
        self.assertEqual(backup.check_folder("  ")[1], "Choose a folder.")
        f = os.path.join(d, "afile")
        open(f, "w").close()
        self.assertIn("file", backup.check_folder(f)[1])
        self.assertEqual(backup.check_folder('"%s"' % d)[0], os.path.normpath(d))   # pasted with quotes

    def test_copy_backups_never_overwrites(self):
        d, path, st = self.make_db()
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        for day in ("01", "02"):
            backup.run(path, a, stamp="2026-09-" + day)
        os.makedirs(b)
        with open(os.path.join(b, "dashboard-2026-09-02.db"), "w") as fh:
            fh.write("newer")
        self.assertEqual(backup.copy_backups(a, b), 1)
        with open(os.path.join(b, "dashboard-2026-09-02.db")) as fh:
            self.assertEqual(fh.read(), "newer")
        self.assertEqual(backup.copy_backups(a, a), 0)
        self.assertEqual(backup.copy_backups(os.path.join(d, "missing"), b), 0)

    def test_keep_setting_is_clamped_and_used(self):
        self.assertEqual((backup.clamp_keep(1), backup.clamp_keep(9999), backup.clamp_keep("x")), (3, 365, 30))
        d, path, st = self.make_db()
        dest = os.path.join(d, "bk")
        n = backup.Nightly(path, d, st, log=lambda m: None, get_override=lambda: dest, get_keep=lambda: 3)
        for i in range(1, 6):
            backup.run(path, dest, keep=99, stamp="2026-08-%02d" % i)
        n.backup_now()
        self.assertEqual(len(backup.listing(dest)), 3)
        self.assertEqual(n.status()["keep"], 3)


class DeskViews(unittest.TestCase):
    PAYLOADS = {
        (8790, "/api/history?ticker=ACME"): {"runs": [{"id": 9, "ticker": "ACME", "run_at": "2026-09-24 10:00:00",
                                                        "price": 536, "base_value": 610.5, "score": 6.8, "verdict": "BUY"}]},
        (8790, "/api/history"): {"runs": [{"id": 9, "ticker": "ACME", "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                           "price": 536, "base_value": 610.5, "score": 6.8, "verdict": "BUY"},
                                          {"id": 8, "ticker": "ZENO", "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                           "score": 3.0, "verdict": "REDUCE"}]},
        (8770, "/api/board"): {"cards": [{"ticker": "QRTX", "score": 61.2, "carried_by": ["insider"]},
                                         {"ticker": "ACME", "score": 40.0}]},
        (8733, "/api/flow"): {"meta": {"scanned_at": "x"}, "cards": [
            {"ticker": "VOLT", "score": 55, "direction": "bearish", "lane": "board", "session": "2031-06-13",
             "alerts": [{"id": "5f0c2a4e-8b1d-4c3e-9a77-1e2d3c4b5a69", "option_chain": "VOLT310620P00040000"}],
             "contracts": [{"option_symbol": "VOLT310620P00040000", "premium": "250000"}]},
            {"ticker": "BLMP", "score": 80, "direction": None, "lane": "out"}]},
        (8787, "/api/scan"): {"market": {"open": True}, "alerts": [
            {"ticker": "ZENO", "score": -47.5, "direction": "BEAR", "drivers": ["flow", "tech"],
             "horizon": {"detail": "2-6 weeks"}, "price": 41.2},
            {"ticker": "ACME", "score": 52.0, "direction": "BULL", "drivers": ["oi", "flow", "gamma"],
             "horizon": {"detail": "2-10 days"}, "price": 187.4}]},
        (8760, "/api/board"): {"pending": False, "cards": [
            {"ticker": "ZENO", "score": 91, "leader": False, "base": {"state": "extended"}, "price": 40},
            {"ticker": "QRTX", "score": 74, "leader": True, "base": {"state": "breakout"}, "price": 12},
            {"ticker": "BLMP", "score": 55, "leader": False, "base": {"state": "in_base"}},
            {"ticker": "VOLT", "score": None, "leader": False, "base": {}}]},
        (8760, "/api/ticker?t=ACME"): {"ticker": "ACME", "score": 81, "leader": True, "rs": 94, "checks": [],
                                       "base": {"state": "near_pivot", "pivot": 190.0}, "secret_extra": 1},
        (8760, "/api/market"): {"state": "pressure", "label": "Uptrend under pressure", "ok": False, "text": "SPY ...",
                                "indexes": {}},
    }

    def desks(self, running=("valuation", "confluence", "flow")):
        ports = {"valuation": 8790, "confluence": 8770, "flow": 8733, "institutional": 8777,
                 "swing": 8787, "growth": 8760}

        def fetch(port, path):
            if (port, path) in self.PAYLOADS:
                return self.PAYLOADS[(port, path)]
            raise OSError("nothing")
        return deskview.Desks(lambda: {k: v for k, v in ports.items() if k in running}, fetch=fetch)

    def test_for_ticker(self):
        v = self.desks().for_ticker("acme")
        self.assertEqual(v["valuation"]["run"]["verdict"], "BUY")
        self.assertEqual(v["confluence"]["card"]["score"], 40.0)
        self.assertFalse(v["flow"]["found"])
        self.assertEqual(v["institutional"]["error"], "not running")

    def test_picks_only_buys_and_directed_board_flow(self):
        p = self.desks().picks()
        self.assertEqual([x["ticker"] for x in p["valuation"]["items"]], ["ACME"])
        self.assertEqual(p["flow"]["items"], [{"ticker": "VOLT", "score": 55, "direction": "short", "name": None, "why": "bearish"}])
        self.assertEqual(p["confluence"]["items"][0]["ticker"], "QRTX")
        self.assertEqual(p["institutional"]["error"], "not running")
        self.assertEqual(p["swing"]["error"], "not running")

    def test_growth_leaders_feed_lookup_picks_and_morning(self):
        dv = self.desks(running=("growth",))
        v = dv.for_ticker("acme")
        self.assertEqual((v["growth"]["card"]["score"], v["growth"]["card"]["leader"]), (81, True))
        self.assertNotIn("secret_extra", v["growth"]["card"])          # only the fields the page shows
        p = dv.picks()
        self.assertEqual([x["ticker"] for x in p["growth"]["items"]], ["QRTX", "ZENO"])   # Leaders first; <70 left out
        self.assertIn("Leader, breaking out", p["growth"]["items"][0]["why"])
        m = dv.growth_market()
        self.assertEqual((m["state"], m["ok"]), ("pressure", False))
        self.assertNotIn("indexes", m)
        off = self.desks(running=())
        self.assertEqual(off.growth_market()["error"], "not running")
        self.assertEqual(off.for_ticker("acme")["growth"]["error"], "not running")

    def test_lookup_flow_card_carries_what_the_tape_panel_needs(self):
        v = self.desks().for_ticker("volt")
        c = v["flow"]["card"]
        self.assertEqual(c["alerts"][0]["id"], "5f0c2a4e-8b1d-4c3e-9a77-1e2d3c4b5a69")
        self.assertEqual(c["contracts"][0]["option_symbol"], "VOLT310620P00040000")
        self.assertEqual(c["session"], "2031-06-13")

    def test_flow_tape_is_passed_through_with_the_desks_status_code(self):
        import io
        import urllib.error
        seen = []

        def fetch(port, path):
            seen.append((port, path))
            if "alert=bad" in path:
                raise urllib.error.HTTPError(path, 400, "bad", {}, io.BytesIO(b'{"error": "alert must be a flow-alert id"}'))
            return {"kind": "contract", "trades": [{"price": 2.5}]}
        dv = deskview.Desks(lambda: {"flow": 8733}, fetch=fetch)
        code, body = dv.flow_tape(contract="VOLT310620P00040000", date="2031-06-13")
        self.assertEqual((code, body["kind"]), (200, "contract"))
        self.assertEqual(seen[0], (8733, "/api/tape?contract=VOLT310620P00040000&date=2031-06-13"))
        self.assertEqual(dv.flow_tape(alert="bad"), (400, {"error": "alert must be a flow-alert id"}))
        code, body = deskview.Desks(lambda: {}, fetch=fetch).flow_tape(alert="x")
        self.assertEqual(code, 503)
        self.assertIn("not running", body["error"])

    def test_swing_picks_rank_by_size_and_keep_direction(self):
        p = self.desks(running=("swing",)).picks()
        items = p["swing"]["items"]
        self.assertEqual([x["ticker"] for x in items], ["ACME", "ZENO"])
        self.assertEqual(items[1]["direction"], "short")          # a bearish pick wins when the stock falls
        self.assertEqual(items[1]["score"], 47.5)                  # magnitude; direction carries the sign
        self.assertEqual(items[0]["why"], "OI + flow + gamma; 2-10 days")

    def test_track_record_records_swing_picks_with_direction(self):
        d = tempfile.mkdtemp()
        st = store.Store(os.path.join(d, "t.db"))
        tr = trackrec.TrackRecord(st, self.desks(running=("swing",)), market=None, log=lambda m: None)
        self.assertEqual(tr.record(today="2031-06-13"), 2)
        rows = {r["ticker"]: r["direction"] for r in st.conn.execute("SELECT ticker, direction FROM picks WHERE desk='swing'")}
        self.assertEqual(rows, {"ACME": "long", "ZENO": "short"})
        self.assertEqual(tr.record(today="2031-06-14"), 0)          # once per name per window


if __name__ == "__main__":
    unittest.main()
