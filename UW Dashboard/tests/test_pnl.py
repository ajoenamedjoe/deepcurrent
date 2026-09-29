"""P&L engine: every broker format, FIFO, and the honesty rules."""
import unittest

from _path import sample
import pnl

TODAY = "2026-09-24"


def run(name, **kw):
    broker, fills, _, notes = pnl.parse(sample(name), **kw)
    return broker, fills, pnl.compute(fills, today=TODAY), notes


def by_key(res):
    return {(t["key"], t["date"]): t for t in res["closed"]}


class Detection(unittest.TestCase):
    def test_each_sample_is_detected_as_its_broker(self):
        expect = {"tos.csv": "tos", "schwab.csv": "schwab", "fidelity.csv": "fidelity",
                  "robinhood.csv": "robinhood", "webull.csv": "webull", "ibkr.csv": "ibkr",
                  "tasty.csv": "tastytrade", "generic.csv": "generic", "tradovate_orders.csv": "tradovate",
                  "tradovate_fills.csv": "tradovate", "tradovate_performance.csv": "tradovate"}
        for f, b in expect.items():
            self.assertEqual(pnl.detect(sample(f)), b, f)

    def test_unknown_layout_asks_for_a_column_mapping(self):
        with self.assertRaises(pnl.ParseError) as cm:
            pnl.parse(sample("unknown.csv"))
        self.assertEqual(cm.exception.columns, ["When", "What", "How many", "At"])

    def test_mapping_reads_the_unknown_layout(self):
        _, fills, _, _ = pnl.parse(sample("unknown.csv"),
                                   mapping={"date": "When", "symbol": "What", "qty": "How many", "price": "At"})
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["qty"], 50)


class Brokers(unittest.TestCase):
    def test_tos_fees_come_from_cash_balance_by_timestamp(self):
        _, _, res, _ = run("tos.csv")
        spy = by_key(res)[("SPY 2026-09-25 570 C", "2026-09-22")]
        self.assertAlmostEqual(spy["fees"], 2.70, places=2)
        self.assertAlmostEqual(spy["net"], (1.75 - 1.20) * 2 * 100 - 2.70, places=2)

    def test_tos_futures_use_the_point_value(self):
        _, _, res, _ = run("tos.csv")
        es = by_key(res)[("/ESZ26", "2026-09-24")]
        self.assertAlmostEqual(es["gross"], 10.25 * 50, places=2)
        self.assertAlmostEqual(es["fees"], 7.40, places=2)

    def test_tos_spread_second_leg_inherits_the_exec_time(self):
        _, fills, _, _ = run("tos.csv")
        legs = [f for f in fills if f["underlying"] == "QQQ"]
        self.assertEqual(len(legs), 2)
        self.assertEqual(legs[0]["ts"], legs[1]["ts"])

    def test_tos_account_is_masked_to_last_four(self):
        _, fills, _, _ = run("tos.csv")
        self.assertEqual(fills[0]["account"], "TOS 5678")

    def test_schwab_as_of_date_and_expiry_row(self):
        _, _, res, _ = run("schwab.csv")
        k = by_key(res)
        self.assertIn(("MSFT", "2026-09-22"), k)
        amd = k[("AMD 2026-09-19 150 P", "2026-09-19")]
        self.assertEqual(amd["kind"], "expire")
        self.assertAlmostEqual(amd["net"], -90.66, places=2)

    def test_fidelity_short_occ_symbol_and_fee_columns(self):
        _, _, res, _ = run("fidelity.csv")
        spy = by_key(res)[("SPY 2026-09-25 570 C", "2026-09-23")]
        self.assertAlmostEqual(spy["net"], 50 - 1.35, places=2)

    def test_robinhood_expiration_row_is_read_as_expiry_not_stock(self):
        _, _, res, _ = run("robinhood.csv")
        pltr = by_key(res)[("PLTR 2026-09-19 30 P", "2026-09-19")]
        self.assertEqual(pltr["kind"], "expire")
        self.assertEqual(pltr["dir"], "short")
        self.assertGreater(pltr["net"], 79)

    def test_robinhood_fees_derived_from_amount(self):
        _, _, res, _ = run("robinhood.csv")
        tsla = by_key(res)[("TSLA 2026-09-26 300 C", "2026-09-23")]
        self.assertAlmostEqual(tsla["fees"], 0.09, places=2)

    def test_webull_skips_cancelled_orders(self):
        _, fills, _, notes = run("webull.csv")
        self.assertEqual(len(fills), 2)
        self.assertTrue(any("fees" in n for n in notes))

    def test_ibkr_multiplier_from_instrument_section(self):
        _, _, res, _ = run("ibkr.csv")
        mes = by_key(res)[("/MESZ6", "2026-09-24")]
        self.assertAlmostEqual(mes["gross"], -5 * 2 * 5, places=2)
        self.assertEqual(mes["account"], "IBKR 4321")

    def test_ibkr_subtotal_rows_are_not_trades(self):
        _, fills, _, _ = run("ibkr.csv")
        self.assertEqual(len([f for f in fills if f["underlying"] == "AMZN"]), 2)

    def test_tastytrade_per_contract_average_price_is_normalised(self):
        _, _, res, _ = run("tasty.csv")
        iwm = by_key(res)[("IWM 2026-09-26 210 P", "2026-09-23")]
        self.assertAlmostEqual(iwm["open_price"], 1.50)
        self.assertAlmostEqual(iwm["gross"], 90.0)


class Tradovate(unittest.TestCase):
    def test_orders_export_reads_filled_orders_with_point_values(self):
        broker, fills, res, notes = run("tradovate_orders.csv")
        self.assertEqual(broker, "tradovate")
        self.assertEqual(len(fills), 6)                                   # rejected and canceled orders skipped
        t = by_key(res)
        es = t[("/MESZ6", "2026-09-14")]                                  # a short: sold 6000.25, bought back 6004
        self.assertAlmostEqual(es["gross"], (6000.25 - 6004.00) * 5)
        nq = t[("/MNQZ6", "2026-09-15")]
        self.assertAlmostEqual(nq["gross"], 12.5 * 2 * 2)                 # MNQ $2 a point, 2 contracts
        zz = t[("/ZZZZ6", "2026-09-16")]                                  # not in the table: Notional / (qty x price)
        self.assertAlmostEqual(zz["gross"], 0.5 * 25)
        self.assertTrue(any("no commissions" in n for n in notes))
        self.assertTrue(all(f["account"] == "DEMO001" for f in fills))

    def test_fills_export_carries_commission(self):
        broker, fills, res, notes = run("tradovate_fills.csv")
        self.assertEqual(broker, "tradovate")
        self.assertEqual([f["fees"] for f in fills], [0.62, 0.62])
        tr = res["closed"][0]
        self.assertAlmostEqual(tr["gross"], -18.75)
        self.assertAlmostEqual(tr["fees"], 1.24)

    def test_performance_export_becomes_open_and_close_fills(self):
        broker, fills, res, notes = run("tradovate_performance.csv")
        self.assertEqual(len(fills), 6)
        t = by_key(res)
        self.assertAlmostEqual(t[("/MNQZ6", "2026-09-15")]["gross"], 50.0)
        self.assertAlmostEqual(t[("/MESZ6", "2026-09-14")]["gross"], -18.75)      # sold first: a short
        self.assertAlmostEqual(t[("/ABCZ6", "2026-09-16")]["gross"], 30.0)        # short; point value from the pnl column
        self.assertEqual(res["unmatched"], [])


class Fifo(unittest.TestCase):
    def fill(self, ts, key, qty, price, effect=None, asset="stock", mult=1.0, expiry=None, fees=0.0, kind="trade"):
        inst = {"asset": asset, "underlying": key.split()[0], "key": key, "expiry": expiry,
                "strike": None, "right": None, "mult": mult}
        f = pnl.make_fill("t", "A", ts, inst, qty, price, fees=fees, effect=effect, kind=kind)
        return f

    def test_close_with_no_open_is_unmatched_not_profit(self):
        res = pnl.compute([self.fill("2026-09-01T10:00:00", "XYZ", -10, 50.0, effect="close")], today=TODAY)
        self.assertEqual(res["closed"], [])
        self.assertEqual(len(res["unmatched"]), 1)

    def test_stock_sell_without_history_is_unmatched_but_short_sale_opens(self):
        res = pnl.compute([self.fill("2026-09-01T10:00:00", "XYZ", -10, 50.0)], today=TODAY)
        self.assertEqual(len(res["unmatched"]), 1)
        f = self.fill("2026-09-01T10:00:00", "XYZ", -10, 50.0)
        f["side_text"] = "Sell Short"
        res = pnl.compute([f, self.fill("2026-09-02T10:00:00", "XYZ", 10, 45.0)], today=TODAY)
        self.assertAlmostEqual(res["closed"][0]["net"], 50.0)
        self.assertEqual(res["closed"][0]["dir"], "short")

    def test_fifo_partial_closes_and_fee_split(self):
        fl = [self.fill("2026-09-01T10:00:00", "XYZ", 100, 10.0, fees=1.0),
              self.fill("2026-09-02T10:00:00", "XYZ", 100, 12.0, fees=1.0),
              self.fill("2026-09-03T10:00:00", "XYZ", -150, 13.0, fees=1.5)]
        res = pnl.compute(fl, today=TODAY)
        t = res["closed"][0]
        self.assertAlmostEqual(t["gross"], 100 * 3 + 50 * 1)
        self.assertAlmostEqual(t["fees"], 1.0 + 0.5 + 1.5)
        self.assertAlmostEqual(res["open"][0]["qty"], 50)
        self.assertAlmostEqual(res["open"][0]["avg_price"], 12.0)

    def test_position_flip_closes_then_opens(self):
        fl = [self.fill("2026-09-01T10:00:00", "/ES", 1, 5800.0, asset="future", mult=50),
              self.fill("2026-09-01T11:00:00", "/ES", -2, 5810.0, asset="future", mult=50)]
        res = pnl.compute(fl, today=TODAY)
        self.assertAlmostEqual(res["closed"][0]["gross"], 500.0)
        self.assertEqual(res["open"][0]["qty"], -1)

    def test_expired_option_left_open_is_closed_at_zero_on_expiry(self):
        fl = [self.fill("2026-09-10T10:00:00", "ABC 2026-09-19 10 C", 2, 1.5, effect="open",
                        asset="option", mult=100, expiry="2026-09-19")]
        res = pnl.compute(fl, today=TODAY)
        t = res["closed"][0]
        self.assertEqual(t["kind"], "expired_inferred")
        self.assertEqual(t["date"], "2026-09-19")
        self.assertAlmostEqual(t["net"], -300.0)

    def test_unexpired_option_stays_open(self):
        fl = [self.fill("2026-09-22T10:00:00", "ABC 2026-10-17 10 C", 1, 1.0, effect="open",
                        asset="option", mult=100, expiry="2026-10-17")]
        res = pnl.compute(fl, today=TODAY)
        self.assertEqual(res["closed"], [])
        self.assertEqual(len(res["open"]), 1)

    def test_same_day_date_only_rows_open_before_close(self):
        # Schwab lists newest first with no times: the close row sits ABOVE its open.
        _, _, res, _ = run("schwab.csv")
        self.assertEqual(res["unmatched"], [])

    def test_daily_and_stats(self):
        _, _, res, _ = run("tos.csv")
        days = pnl.daily(res["closed"], res["unmatched"])
        d = {x["date"]: x for x in days}
        self.assertEqual(d["2026-09-18"]["unmatched"], 1)
        self.assertEqual(d["2026-09-18"]["trades"], 0)
        st = pnl.stats(res["closed"], days)
        self.assertEqual(st["trades"], 3)
        self.assertAlmostEqual(st["net"], sum(t["net"] for t in res["closed"]))


class Dedupe(unittest.TestCase):
    def test_ids_are_stable_and_identical_rows_stay_distinct(self):
        a = pnl.parse(sample("tos.csv"))[1]
        b = pnl.parse(sample("tos.csv"))[1]
        self.assertEqual([f["id"] for f in a], [f["id"] for f in b])
        text = "Trade Date,Ticker,Buy/Sell,Shares,Fill Price\n2026-09-21 09:31,XOM,BUY,50,110\n2026-09-21 09:31,XOM,BUY,50,110\n"
        f = pnl.parse(text)[1]
        self.assertEqual(len({x["id"] for x in f}), 2)

    def test_store_skips_duplicates_across_overlapping_uploads(self):
        import os, tempfile
        import store
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        st = store.Store(path)
        fills = pnl.parse(sample("tos.csv"))[1]
        self.assertEqual(st.add_import("a.csv", "tos", "A", 1, fills)[1], len(fills))
        self.assertEqual(st.add_import("b.csv", "tos", "A", 1, fills)[1], 0)
        self.assertEqual(st.fill_count(), len(fills))


class JournalStore(unittest.TestCase):
    def test_existing_database_gets_the_journal_table(self):
        import os, sqlite3, tempfile
        import store
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)")
        c.commit(); c.close()                                   # a db from deploy-r1, no journal
        st = store.Store(path)
        st.journal_put("2026-09-24", "first note")
        self.assertEqual(st.journal_get("2026-09-24")["body"], "first note")
        created = st.journal_get("2026-09-24")["created_at"]
        st.journal_put("2026-09-24", "edited")
        self.assertEqual(st.journal_get("2026-09-24")["created_at"], created)


class Helpers(unittest.TestCase):
    def test_money(self):
        self.assertEqual(pnl.money("($1,234.50)"), -1234.5)
        self.assertEqual(pnl.money("-$5"), -5)
        self.assertEqual(pnl.money("2S"), 2)
        self.assertIsNone(pnl.money("--"))

    def test_dates(self):
        self.assertEqual(pnl.parse_dt("9/23/26 09:31:05"), "2026-09-23T09:31:05")
        self.assertEqual(pnl.parse_dt("09/22/2026 as of 09/21/2026"), "2026-09-22T00:00:00")
        self.assertEqual(pnl.parse_dt("2026-09-23, 10:15:32"), "2026-09-23T10:15:32")
        self.assertEqual(pnl.parse_dt("2026-09-23T14:05:00-0400"), "2026-09-23T14:05:00")
        self.assertEqual(pnl.parse_dt("20260923;101532"), "2026-09-23T10:15:32")
        self.assertEqual(pnl.parse_dt("20260923"), "2026-09-23T00:00:00")
        self.assertEqual(pnl.parse_dt("09/23/2026 09:31:05 EDT"), "2026-09-23T09:31:05")
        self.assertEqual(pnl.parse_dt("9/23/2026 1:05 PM"), "2026-09-23T13:05:00")

    def test_symbols(self):
        self.assertEqual(pnl.parse_symbol("SPY260927C00570000")["key"], "SPY 2026-09-27 570 C")
        self.assertEqual(pnl.parse_symbol("IWM   260926P00210500")["key"], "IWM 2026-09-26 210.5 P")
        self.assertEqual(pnl.parse_symbol(" -SPY260927C570")["key"], "SPY 2026-09-27 570 C")
        self.assertEqual(pnl.parse_symbol("NVDA 09/26/2026 180.00 C")["key"], "NVDA 2026-09-26 180 C")
        self.assertEqual(pnl.parse_symbol("QQQ 26SEP26 480 P")["key"], "QQQ 2026-09-26 480 P")
        self.assertEqual(pnl.parse_symbol("TSLA", "TSLA 9/26/2026 Call $300.00")["key"], "TSLA 2026-09-26 300 C")
        self.assertEqual(pnl.parse_symbol("/ESZ26")["mult"], 50)
        self.assertEqual(pnl.parse_symbol("BRK.B")["asset"], "stock")

    def test_side_text(self):
        self.assertEqual(pnl.side_of("Buy to Cover"), (1, "close"))
        self.assertEqual(pnl.side_of("Sell Short"), (-1, "open"))
        self.assertEqual(pnl.side_of("SELL_TO_OPEN"), (-1, "open"))
        self.assertEqual(pnl.side_of("YOU BOUGHT APPLE"), (1, None))
        self.assertEqual(pnl.side_of("Short"), (-1, None))


if __name__ == "__main__":
    unittest.main()
