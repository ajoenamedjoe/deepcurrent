"""Ticker Lookup: single-leg, likely-opening flow. Synthetic alerts in the live /flow-alerts shape (2026-09-28)."""
import datetime as dt
import unittest

import _path  # noqa: F401
import openflow as O

DAY = dt.date(2031, 6, 13)
T0 = 1970000000000          # ms, inside the session


def alert(chain, kind="call", prem=50000, ask=50000, bid=0, size=100, oi=50, vol=400, start=0, **kw):
    a = {"option_chain": chain, "type": kind, "strike": "100", "expiry": "2031-06-20", "total_premium": str(prem),
         "total_ask_side_prem": str(ask), "total_bid_side_prem": str(bid), "total_size": size, "open_interest": oi,
         "volume": vol, "start_time": T0 + start, "has_singleleg": True, "has_multileg": False, "expiry_count": 1,
         "all_opening_trades": False, "has_sweep": False, "has_floor": False, "underlying_price": "99.5", "price": "5"}
    a.update(kw)
    return a


class Filters(unittest.TestCase):
    def test_single_leg(self):
        self.assertTrue(O.is_single_leg(alert("X")))
        self.assertFalse(O.is_single_leg(alert("X", has_multileg=True)))
        self.assertFalse(O.is_single_leg(alert("X", has_singleleg=False)))
        self.assertFalse(O.is_single_leg(alert("X", expiry_count=2)))            # a roll
        self.assertFalse(O.is_single_leg(alert("X", multi_vol=10)))

    def test_opening_reasons(self):
        self.assertEqual(O.opening_reasons(alert("X", size=100, oi=50, vol=40)), ["size > OI"])
        self.assertEqual(O.opening_reasons(alert("X", size=10, oi=50, vol=400)), ["vol > OI"])
        self.assertEqual(O.opening_reasons(alert("X", size=10, oi=5000, vol=400)), [])   # could be closing
        self.assertEqual(O.opening_reasons(alert("X", size=10, oi=5000, vol=40, all_opening_trades=True)), ["UW opening"])

    def test_side_and_lean(self):
        self.assertEqual(O.side_of(alert("X", prem=100, ask=80, bid=20))[0], "bought")
        self.assertEqual(O.side_of(alert("X", prem=100, ask=10, bid=90))[0], "sold")
        self.assertEqual(O.side_of(alert("X", prem=100, ask=50, bid=50))[0], "mixed")
        self.assertEqual(O.side_of(alert("X", prem=100, ask=10, bid=5))[0], "unknown")   # 15% coverage: no guess
        self.assertEqual(O.lean_of("call", "bought"), "bullish")
        self.assertEqual(O.lean_of("put", "bought"), "bearish")
        self.assertEqual(O.lean_of("call", "sold"), "bearish")
        self.assertEqual(O.lean_of("put", "sold"), "bullish")
        self.assertEqual(O.lean_of("put", "mixed"), "neutral")


class Summary(unittest.TestCase):
    def test_groups_by_contract_and_counts_what_it_left_out(self):
        rows = [alert("C1", prem=60000, ask=60000, start=1000),
                alert("C1", prem=40000, ask=40000, start=5000, has_sweep=True),
                alert("C1", prem=40000, ask=40000, start=5000),                       # overlapping duplicate
                alert("P1", kind="put", prem=30000, ask=0, bid=30000, start=2000),    # puts sold: bullish
                alert("P2", kind="put", prem=90000, ask=90000, start=3000),           # puts bought: bearish
                alert("M1", has_multileg=True),
                alert("Z1", size=5, oi=9000, vol=100),                                # not opening
                alert("OLD", start=-10 ** 9)]
        s = O.summarize(rows, DAY, T0)
        c = {x["contract"]: x for x in s["contracts"]}
        self.assertEqual(sorted(c), ["C1", "P1", "P2"])
        self.assertEqual((c["C1"]["premium"], c["C1"]["alerts"], c["C1"]["sweep"]), (100000, 2, True))
        self.assertEqual((c["C1"]["side"], c["C1"]["lean"]), ("bought", "bullish"))
        self.assertEqual(c["P1"]["lean"], "bullish")
        self.assertEqual(c["P2"]["lean"], "bearish")
        self.assertEqual(c["C1"]["dte"], 7)
        self.assertEqual(s["dropped"], {"multi_leg": 1, "not_opening": 1, "older": 1, "duplicate": 1})
        self.assertEqual([x["contract"] for x in s["contracts"]], ["C1", "P2", "P1"])        # by premium
        self.assertAlmostEqual(s["lean"], (130000 - 90000) / 220000)

    def test_nothing_is_no_lean_not_neutral(self):
        s = O.summarize([], DAY, T0)
        self.assertEqual((s["contracts"], s["lean"], s["premium"]), ([], None, 0))


class Fetch(unittest.TestCase):
    def test_pages_back_to_the_session_start(self):
        day, start = O.session_start(dt.datetime(2031, 6, 13, 18, 0))      # Fri 2 pm ET
        self.assertEqual((day, start), (dt.date(2031, 6, 13), dt.datetime(2031, 6, 13, 8, 0)))
        since = (start - dt.datetime(1970, 1, 1)).total_seconds() * 1000
        calls = []

        class C:
            def get(self, path, params):
                calls.append(dict(params))
                n = len(calls)
                base = since + 10 ** 7 - n * 10 ** 5
                return 200, {"data": [dict(alert("C%d" % i), start_time=base + i) for i in range(200 if n < 3 else 7)]}
        out = O.fetch(C(), "ACME", dt.datetime(2031, 6, 13, 18, 0))
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["ticker_symbol"], "ACME")
        self.assertEqual(calls[0]["newer_than"], "2031-06-13T08:00:00Z")
        self.assertIn("older_than", calls[1])
        self.assertEqual((out["alerts_read"], out["truncated"]), (407, False))

    def test_weekend_and_before_4am_use_the_last_session(self):
        self.assertEqual(O.session_start(dt.datetime(2031, 6, 15, 15, 0))[0], dt.date(2031, 6, 13))   # Sunday
        self.assertEqual(O.session_start(dt.datetime(2031, 6, 16, 6, 0))[0], dt.date(2031, 6, 13))    # Mon 2 am ET
        self.assertEqual(O.session_start(dt.datetime(2031, 1, 16, 15, 0))[1], dt.datetime(2031, 1, 16, 9, 0))  # EST


if __name__ == "__main__":
    unittest.main()
