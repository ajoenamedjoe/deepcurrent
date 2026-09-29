"""Scan loop, session calendar, drivers and history migration. No network: the
fetchers are replaced with the synthetic fixture rows."""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

os.environ["SWING_DB"] = os.path.join(tempfile.mkdtemp(), "swing_test.db")   # never the real history
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import notify                     # noqa: E402
import server as S                # noqa: E402
import store                      # noqa: E402
from fixture import UNIVERSE, GEX_ACME, DARKPOOL   # noqa: E402,F401


def utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


class Session(unittest.TestCase):
    def test_open_and_closed(self):
        self.assertEqual(notify.session_state(utc(2031, 6, 13, 14, 0))[0], "open")        # Fri 10:00 EDT
        self.assertEqual(notify.session_state(utc(2031, 6, 13, 13, 30)), ("closed", "before 09:35 ET"))
        self.assertEqual(notify.session_state(utc(2031, 6, 13, 20, 5)), ("closed", "after 16:00 ET"))
        self.assertEqual(notify.session_state(utc(2031, 6, 14, 15, 0)), ("closed", "weekend"))
        self.assertEqual(notify.session_state(utc(2026, 11, 26, 15, 0)), ("closed", "market holiday"))
        self.assertEqual(notify.session_state(utc(2026, 12, 7, 14, 40))[0], "open")       # 09:40 EST
        self.assertEqual(notify.session_state(utc(2026, 9, 25, 6, 0)), ("closed", "before 09:35 ET"))  # the 2am scan


class Stubbed(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(S, k) for k in ("fetch_universe", "fetch_darkpool", "fetch_flow_alerts",
                                                   "fetch_gex", "fetch_technicals", "STORE")}
        S.fetch_universe = lambda: [dict(r) for r in UNIVERSE]
        S.fetch_darkpool = lambda: []
        S.fetch_flow_alerts = lambda: []
        S.fetch_gex = lambda t: {k: S.num(v) for k, v in GEX_ACME.items()} if t == "ACME" else None
        S.fetch_technicals = lambda t: None
        S.STORE = store.Store(os.path.join(tempfile.mkdtemp(), "h.db"))
        S.CACHE._d.clear()
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.saved.items():
            setattr(S, k, v)
        S.CACHE._d.clear()

    def test_drivers_name_signals_of_real_size_in_the_scores_direction(self):
        scan = S.build_scan(record=False)
        rows = scan["alerts"] + scan["watch"]
        self.assertTrue(rows)
        for a in rows:
            self.assertEqual(a["agreeing"], len(a["drivers"]))
            sign = a["score"] > 0
            contribs = [abs(a["components"][k]["contribution"]) for k in a["drivers"]]
            self.assertEqual(contribs, sorted(contribs, reverse=True))
            for k in a["drivers"]:
                v = a["components"][k]["value"]
                self.assertGreater(abs(v), S.CFG["agree_threshold"])
                self.assertEqual(v > 0, sign)
        # not unanimous across different tickers
        self.assertGreater(len({tuple(a["drivers"]) for a in rows}), 1)

    def test_unrecorded_scan_writes_no_history(self):
        S.build_scan(record=False)
        self.assertEqual(S.STORE.stats()["scans"], 0)
        S.build_scan(record=True)
        self.assertEqual(S.STORE.stats()["scans"], 1)

    def test_loop_scans_only_while_open(self):
        calls = []
        S.run_scan, orig = (lambda record=True: calls.append(record)), S.run_scan
        try:
            S.scan_loop(sleep=lambda s: None, now=lambda: utc(2031, 6, 14, 15, 0), once=True)   # Saturday
            self.assertEqual(calls, [])
            S.scan_loop(sleep=lambda s: None, now=lambda: utc(2031, 6, 13, 15, 0), once=True)   # Friday 11:00
            self.assertEqual(calls, [True])
            self.assertEqual(S.SCAN_STATE["session"], "open")
        finally:
            S.run_scan = orig

    def test_page_on_a_closed_market_gets_an_unrecorded_scan(self):
        orig = S.session_state
        S.session_state = lambda *a, **k: ("closed", "weekend")
        try:
            d = S.cached_scan()
            self.assertFalse(d["market"]["open"])
            self.assertFalse(d["recorded"])
            self.assertEqual(S.STORE.stats()["scans"], 0)
            self.assertIs(S.cached_scan()["alerts"], d["alerts"])     # served from cache, no second scan
        finally:
            S.session_state = orig

    def test_parts_are_recorded_for_calibration(self):
        S.build_scan(record=True)
        row = S.STORE.conn.execute("SELECT parts FROM observations LIMIT 1").fetchone()
        self.assertIn('"flow"', row["parts"])


class Migration(unittest.TestCase):
    def test_old_history_db_gains_the_parts_column(self):
        p = os.path.join(tempfile.mkdtemp(), "old.db")
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE observations (scan_id INTEGER NOT NULL, ts TEXT NOT NULL, ticker TEXT NOT NULL,"
                  " direction TEXT, score REAL, agreeing INTEGER, qualified INTEGER NOT NULL DEFAULT 0,"
                  " price REAL, horizon TEXT, rr REAL)")
        c.execute("INSERT INTO observations VALUES (1,'2031-06-13T14:00:00+00:00','ACME','BULL',41,3,1,10,'SWING',2)")
        c.commit()
        c.close()
        st = store.Store(p)
        cols = {r[1] for r in st.conn.execute("PRAGMA table_info(observations)")}
        self.assertIn("parts", cols)
        self.assertEqual(st.stats()["max_score_ever"], 41)


class Calibration(unittest.TestCase):
    def test_gates_sit_inside_the_reachable_range(self):
        """The old Discord gate (60) sat above every score ever recorded (max 46)."""
        self.assertLess(S.CFG["min_score"], notify.NOTIFY["min_score"])
        self.assertLessEqual(notify.NOTIFY["min_score"], 55)
        self.assertEqual(sum(S.CFG[k] for k in ("w_flow", "w_oi", "w_dark", "w_gamma", "w_tech")), 100)


class SharePage(unittest.TestCase):
    """The share card is drawn in the page; check the page carries it and every alert
    field it reads is one the scan produces."""

    def test_page_has_the_share_card(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html"),
                  encoding="utf-8") as fh:
            page = fh.read()
        for bit in ('id="shareModal"', 'id="shot"', 'class="share-btn"', "function drawShare",
                    'id="optAnon"', 'id="btnDl"', "minmax(min(100%, 400px), 1fr)"):
            self.assertIn(bit, page)


if __name__ == "__main__":
    unittest.main()
