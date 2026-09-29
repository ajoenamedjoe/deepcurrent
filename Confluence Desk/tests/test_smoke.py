"""
Confluence Desk -- end-to-end smoke test.

Runs the REAL server code (scan.Scanner, store.Store, notify.Notifier,
server.Handler) against a stub client that returns synthetic payloads in
the shape of the API, plus an HTTP POST to a webhook receiver running
in-process.

What it proves, in order:
  1. a cold start records the baseline and posts NOTHING
  2. the second scan posts a genuine alert
  3. the cooldown then holds
  4. every HTTP route answers
  5. a scan that throws keeps the last good board on screen
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uw

# The notifier reads its webhook out of the environment at construction time,
# so the receiver has to exist before anything is imported that builds one.
RECEIVED = []


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        RECEIVED.append(json.loads(self.rfile.read(length).decode("utf-8")))
        self.send_response(204)
        self.end_headers()


# ---------------------------------------------------------- sample shapes
# Synthetic payload in the shape of /api/insider/transactions. Every value is
# invented; the shape keeps the awkward parts: a string marketcap, a null
# officer_title, a fill price that disagrees with the quote, and (via
# fixtures.sample_prints) an extended-hours dark pool print with a stale NBBO.

INSIDER_ROWS = [
    {"id": "i1", "ticker": "ACME", "amount": 38000, "transactions": 1,
     "price": "24.1500", "sector": "Industrials", "transaction_date": "2026-03-05",
     "is_officer": True, "is_ten_percent_owner": False, "is_s_p_500": False,
     "is_director": True, "owner_name": "TESTER ALICE",
     "reporter_is_public_company": False, "marketcap": "781406215",
     "next_earnings_date": "2026-05-07", "filing_date": "2026-03-06",
     "stock_price": "24.19", "formtype": "4", "security_title": "Common Stock",
     "transaction_code": "P", "is_10b5_1": False, "security_ad_code": "NA",
     "reporter_cik": "0000000001", "shares_owned_after": 57000,
     "officer_title": "Chief Executive Officer", "shares_owned_before": 19000,
     "director_indirect": "D", "natureofownership": None},
    {"id": "i2", "ticker": "ACME", "amount": 14000, "transactions": 1,
     "price": "24.6200", "sector": "Industrials", "transaction_date": "2026-03-06",
     "is_officer": False, "is_ten_percent_owner": False, "is_s_p_500": False,
     "is_director": True, "owner_name": "TESTER BOB",
     "reporter_is_public_company": False, "marketcap": "781406215",
     "next_earnings_date": "2026-05-07", "filing_date": "2026-03-09",
     "stock_price": "24.19", "formtype": "4", "security_title": "Common Stock",
     "transaction_code": "P", "is_10b5_1": False, "security_ad_code": "NA",
     "reporter_cik": "0000000002", "shares_owned_after": 18600,
     "officer_title": None, "shares_owned_before": 4600,
     "director_indirect": "D", "natureofownership": None},
    {"id": "i3", "ticker": "ACME", "amount": 8500, "transactions": 1,
     "price": "25.3800", "sector": "Industrials", "transaction_date": "2026-03-09",
     "is_officer": False, "is_ten_percent_owner": True, "is_s_p_500": False,
     "is_director": False, "owner_name": "TESTER CARLA",
     "reporter_is_public_company": False, "marketcap": "781406215",
     "next_earnings_date": "2026-05-07", "filing_date": "2026-03-10",
     "stock_price": "24.19", "formtype": "4", "security_title": "Common Stock",
     "transaction_code": "P", "is_10b5_1": False, "security_ad_code": "NA",
     "reporter_cik": "0000000003", "shares_owned_after": 11400,
     "officer_title": "", "shares_owned_before": 2900,
     "director_indirect": "D", "natureofownership": None},
    # A Total Return Swap that slipped through common_stock_only=true. This
    # row must never reach a card.
    {"id": "i4", "ticker": "SWAPCO", "amount": 900000, "transactions": 1,
     "price": "10.0000", "sector": "Financial Services",
     "transaction_date": "2026-03-09", "is_officer": True,
     "is_ten_percent_owner": False, "is_s_p_500": False, "is_director": True,
     "owner_name": "SWAP DESK", "reporter_is_public_company": False,
     "marketcap": "500000000", "next_earnings_date": None,
     "filing_date": "2026-03-10", "stock_price": "10.00", "formtype": "4",
     "security_title": "Total Return Swap", "transaction_code": "P",
     "is_10b5_1": False, "security_ad_code": "DA",
     "reporter_cik": "0000000009", "shares_owned_after": 900000,
     "officer_title": "CEO", "shares_owned_before": 0,
     "director_indirect": "D", "natureofownership": None},
]


def _dark_rows():
    from fixtures import sample_prints
    return sample_prints.acme()


class StubClient:
    """Stands in for uw.Client. Counts calls the same way the real one does."""

    def __init__(self, fail_on_scan=None):
        self.calls = 0
        self.errors = 0
        self.fail_on_scan = fail_on_scan
        self.scan_n = 0

    def get(self, path, params=None):
        self.calls += 1
        if path == "/api/insider/transactions":
            self.scan_n += 1
            if self.fail_on_scan and self.scan_n >= self.fail_on_scan:
                raise RuntimeError("GET /api/insider/transactions failed: simulated")
            # page 0 returns everything, page 1 is empty -> short page stops it
            if (params or {}).get("page", 0) == 0:
                return {"data": INSIDER_ROWS, "has_more": False}
            return {"data": [], "has_more": False}
        return {"data": []}

    def get_list(self, path, params=None):
        return uw.unwrap(self.get(path, params))

    def darkpool(self, ticker, limit=200, min_premium=50000):
        self.calls += 1
        return _dark_rows() if ticker == "ACME" else []


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        cls.httpd = HTTPServer(("127.0.0.1", 0), Receiver)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        os.environ["DISCORD_WEBHOOK_URL"] = "http://127.0.0.1:%d/hook" % cls.port
        os.environ["CDESK_ALERT_STREAK"] = "2"
        os.environ["CDESK_ALERT_SCORE"] = "35"
        os.environ["CDESK_ALERT_ALL_THREE"] = "false"
        uw.ENV = uw.load_env()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        RECEIVED.clear()
        self.tmp = tempfile.mkdtemp()
        import store as store_mod
        self.store = store_mod.Store(os.path.join(self.tmp, "t.db"))

    def _scanner(self, client=None):
        import scan
        return scan.Scanner(self.store, log=lambda *_a: None,
                            client=client or StubClient())

    # ---------------------------------------------------------------- 1-3

    def test_cold_start_then_alert_then_cooldown(self):
        import notify
        scanner = self._scanner()
        notifier = notify.Notifier(self.store, log=lambda *_a: None)
        self.assertTrue(notifier.enabled, "the test receiver must be wired up")

        # 1. cold start: the whole window is "new", so it must post nothing
        first = scanner.run()
        self.assertTrue(first["baseline"])
        self.assertTrue(first["cards"], "the stub data must produce a card")
        sent, reasons = notifier.process(first["cards"], baseline=first["baseline"])
        self.assertEqual(sent, 0)
        self.assertIn("baseline", reasons)
        self.assertEqual(RECEIVED, [], "a cold start must never page")

        card = first["cards"][0]
        self.assertEqual(card["ticker"], "ACME")
        self.assertEqual(card["distinct_buyers"], 3)

        # the swap row must not have produced a card of its own
        self.assertNotIn("SWAPCO", [c["ticker"] for c in first["cards"]])

        # 2. second scan: streak reaches 2, an actual POST goes out
        second = scanner.run()
        self.assertFalse(second["baseline"])
        sent, reasons = notifier.process(second["cards"], baseline=False)
        self.assertEqual(sent, 1, "reasons=%s" % reasons)
        self.assertEqual(len(RECEIVED), 1)
        embed = RECEIVED[0]["embeds"][0]
        self.assertIn("ACME", embed["title"])
        self.assertTrue(any("Conviction" == f["name"] for f in embed["fields"]))

        # 3. cooldown holds
        third = scanner.run()
        sent, reasons = notifier.process(third["cards"], baseline=False)
        self.assertEqual(sent, 0)
        self.assertEqual(reasons.get("cooldown"), 1)
        self.assertEqual(len(RECEIVED), 1)

    def test_streak_resets_when_a_card_drops_off(self):
        """
        The streak reset runs AFTER the qualifier loop, never before it.
        Running it first needs an exemption for the previous scan_id, and that
        exemption is what let dropped-out names keep their streak and stay
        alert-eligible (Swing Desk bug #4).
        """
        scanner = self._scanner()
        scanner.run()
        scanner.run()
        self.assertEqual(self.store.state("ACME")["streak"], 2)
        # a scan in which ACME does not appear
        self.store.bump(999, [])
        self.assertEqual(self.store.state("ACME")["streak"], 0)

    # ------------------------------------------------------------------ 4

    def test_every_http_route_answers(self):
        import server
        scanner = self._scanner()
        scanner.run()
        import notify
        server.STATE["store"] = self.store
        server.STATE["scanner"] = scanner
        server.STATE["notifier"] = notify.Notifier(self.store, log=lambda *_a: None)

        httpd = server.Server(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d" % port
            body = urllib.request.urlopen(base + "/", timeout=5).read().decode()
            self.assertIn("Confluence Desk", body)

            board = json.loads(urllib.request.urlopen(base + "/api/board", timeout=5).read())
            self.assertFalse(board["pending"])
            self.assertTrue(board["cards"])
            self.assertIn("build", board)
            self.assertIn("index_mtime", board["build"])
            # the board must expose the ceiling calibrator
            self.assertIn("best_ever", board)

            health = json.loads(urllib.request.urlopen(base + "/api/health", timeout=5).read())
            self.assertTrue(health["ok"])

            hist = json.loads(urllib.request.urlopen(base + "/api/history/ACME", timeout=5).read())
            self.assertTrue(hist["rows"])
            self.assertIn("score_parts", hist["rows"][0])

            logs = json.loads(urllib.request.urlopen(base + "/api/log", timeout=5).read())
            self.assertIn("lines", logs)
        finally:
            httpd.shutdown()

    # ------------------------------------------------------------------ 5

    def test_a_failing_api_keeps_the_last_good_board(self):
        """
        A scan that throws must NOT blank the board. The last good result stays
        on screen with an error banner over it, flagged stale.
        """
        client = StubClient(fail_on_scan=2)
        scanner = self._scanner(client)
        good = scanner.run()
        self.assertTrue(good["cards"])
        self.assertIsNone(good["error"])

        # force the insider cache to expire so the next scan really calls out
        scanner._insider_at = 0.0
        bad = scanner.run()
        self.assertIsNotNone(bad["error"])
        self.assertTrue(bad["cards"], "the last good cards must survive")
        self.assertTrue(bad.get("stale"))

    # --------------------------------------------------------- persistence

    def test_observations_are_recorded_before_any_outcome_exists(self):
        scanner = self._scanner()
        result = scanner.run()
        rows = self.store.history("ACME")
        self.assertTrue(rows)
        parts = json.loads(rows[0]["score_parts"])
        for leg in ("insider", "darkpool", "institutional"):
            self.assertIn(leg, parts)
        self.assertEqual(self.store.distinct_days(), 1)
        self.assertAlmostEqual(self.store.best_ever(), result["cards"][0]["score"])

    def test_the_card_carries_the_raw_title_beside_the_parsed_rank(self):
        """
        Putting a derived value next to its source is a free correctness
        check -- it is how the vice-president/president bug was caught.
        """
        scanner = self._scanner()
        card = scanner.run()["cards"][0]
        person = card["people"][0]
        self.assertIn("title", person)
        self.assertIn("rank", person)
        top = card["top_buyer"]
        self.assertEqual(top["title"], "Chief Executive Officer")
        self.assertAlmostEqual(top["rank"], 0.95)

    def test_the_dark_pool_leg_actually_scored_on_sample_shaped_prints(self):
        scanner = self._scanner()
        card = scanner.run()["cards"][0]
        self.assertGreater(card["legs"]["darkpool"]["points"], 0.0)
        self.assertGreater(card["dark"]["print_count"], 0)
        self.assertGreater(card["dark"]["active_sessions"], 0)
        # EVERY field index.html reads off card["dark"] must survive into the
        # payload. This has now been dropped TWICE -- `pressure`/`ask_share`/
        # `bid_share` (every card rendered "not enough clean prints") and
        # `testable_days` (every card rendered "no insider buy falls inside
        # the dark pool window"). The aggregate computed them correctly both
        # times; `_build_card` simply did not copy them, and the scoring path
        # reads the aggregate so nothing failed loudly.
        for key in ("print_count", "clean_count", "window_notional",
                    "max_size_vs_avg30", "max_size", "avg30_volume",
                    "weighted_position", "pressure", "ask_share", "bid_share",
                    "active_sessions", "window_sessions", "overlap_days",
                    "testable_days", "sessions", "top_prints",
                    "latest_session"):
            self.assertIn(key, card["dark"],
                          "index.html reads card.dark.%s" % key)
        self.assertIsNotNone(card["dark"]["pressure"])

        # ...and every per-session field the session bars read
        for key in ("date", "notional", "size", "prints", "day_volume",
                    "share_of_day", "share_of_avg30", "active", "partial"):
            self.assertIn(key, card["dark"]["sessions"][0],
                          "index.html reads session.%s" % key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
