"""Synthetic payloads in the shape of /api/option-trades/flow-alerts and
/api/screener/option-contracts.

Every ticker, price, premium, volume, id and timestamp below is invented. Only
the SHAPE follows the public API schema: strings stay strings, nulls stay null,
and each quirk the desk has to handle is reproduced. Each row exists because it
is a different SHAPE of problem, so the thresholds are tested against the
range of the population rather than one good example:

ALERTS (/api/option-trades/flow-alerts)
  ZENO_FLOOR      SweepsFollowedByFloor. ask+bid = 90% of total, NBBO is "0"/"0",
                  and it SUBSUMES ZENO_REPEATED below (identical start_time).
  ZENO_REPEATED   the RepeatedHits alert the floor alert swallows. Summing the
                  two double-counts $655k.
  QRTX_ZERO_OI    open_interest 0 AND volume_oi_ratio "0" -- the payload reports
                  the LOWEST possible ratio for the most-opening contract there
                  is. Also next_earnings_date is in the PAST.
  VOLT_MULTILEG   has_singleleg false, has_multileg true, expiry_count 2.
  ACME_FLOOR      LowHistoricVolumeFloor, trade_count 1, 100% ask side.
  DRVX_ASCENDING  RepeatedHitsAscendingFill on a put -- buyer paying up, bearish.
  PLNK_BID_PUT    100% BID side put. Put sold = mildly bullish. The sign trap.
  MEGA_0DTE       megacap 0DTE churn: huge volume, premium only $116k, and
                  99.9% bid side. Must not reach the board.
  KWLX_NULL_ER    next_earnings_date is null.
  GIGA_BID_CALL   ask + bid reconciles to total, but it is a CALL that is 99%
                  bid side, i.e. calls being sold.
  BLMP_ZERO_OI2   second open_interest 0 row, so the guard is not fitted to one.
  TNYX_TINY_OI    open_interest 3, volume 27 -- ratio 9, a vol/OI extreme on a
                  trivially small base. Guards against rewarding noise.

SCREENER (/api/screener/option-contracts)
  MEGA_SCR /
  TITN_SCR /
  GIGA_SCR /
  COLS_SCR        four high-premium OTM vol>OI megacap contracts, all 0DTE
                  noise. Any model that puts these on an unusual-flow board is
                  broken. ask/(ask+bid) is 48.8 / 56.1 / 41.4 / 51.8% -- the
                  aggression gate is what rejects them.
  FJRD_SCR        the genuine article: 82.4% ask, OI 402 vs volume 24,186,
                  days_of_oi_increases 4, ask_side_perc_7_day 0.714286.
  SNWX_SCR        floor-heavy (88% of volume), OI 21, 90.7% ask.
  CRSX_SCR        5,500 of 6,212 contracts are ONE multileg cross printed at
                  neutral. Premium that is really a spread leg.
  XLGX_SCR        98.8% one multileg cross. The same trap, more extreme.
  STVX_SCR        ask_side_volume 3,702 > volume 3,587. The side-volume fields
                  do NOT always sum to volume on the screener, so every share
                  computed off them needs clamping.
"""

# --- /api/option-trades/flow-alerts shaped rows ----------------------------

ZENO_FLOOR = {
    "has_floor": True, "strike": "88", "volume": 2741, "ask": "0",
    "underlying_price": "86.42", "iv_end": "0", "expiry_count": 1, "type": "put",
    "volume_oi_ratio": "0.93488745980707", "start_time": 1792167530118,
    "id": "ea642996-1dec-4c1c-84e3-60c448dcc07f", "total_ask_side_prem": "41275",
    "has_sweep": True, "iv_start": "0", "option_chain": "ZENO261023P00088000",
    "marketcap": "38214907755", "has_singleleg": True,
    "created_at": "2026-10-16T16:25:04.318207Z", "issue_type": "Common Stock",
    "total_size": 1210, "has_multileg": False, "open_interest": 2932,
    "sector": "Technology", "bid": "0",
    "rule_id": "564e38ae-90cc-4250-ae72-b707a4346943", "price": "5.3",
    "total_bid_side_prem": "603190", "next_earnings_date": "2026-11-05",
    "alert_rule": "SweepsFollowedByFloor", "er_time": "unknown",
    "end_time": 1792167902764, "all_opening_trades": False, "trade_count": 24,
    "ticker": "ZENO", "expiry": "2026-10-23", "total_premium": "712840",
}

ZENO_REPEATED = {
    "has_floor": False, "strike": "88", "volume": 1873, "ask": "5.65",
    "underlying_price": "85.91", "iv_end": "0.681924573310862",
    "expiry_count": 1, "type": "put", "volume_oi_ratio": "0.638813096862210",
    "start_time": 1792167530118, "id": "dde60335-44e1-4655-b3ef-63fa9dc1f9ee",
    "total_ask_side_prem": "41275", "has_sweep": True,
    "iv_start": "0.681924573310862", "option_chain": "ZENO261023P00088000",
    "marketcap": "38214907755", "has_singleleg": True,
    "created_at": "2026-10-16T16:18:51.770342Z", "issue_type": "Common Stock",
    "total_size": 1100, "has_multileg": False, "open_interest": 2932,
    "sector": "Technology", "bid": "5.45",
    "rule_id": "4b179ff9-5ab1-4f42-9cd4-fe19ae472f5e", "price": "5.6",
    "total_bid_side_prem": "3180", "next_earnings_date": "2026-11-05",
    "alert_rule": "RepeatedHits", "er_time": "unknown", "end_time": 1792167530121,
    "all_opening_trades": False, "trade_count": 31, "ticker": "ZENO",
    "expiry": "2026-10-23", "total_premium": "655310",
}

QRTX_ZERO_OI = {
    "has_floor": False, "strike": "70", "volume": 31, "ask": "18.9",
    "underlying_price": "83.47", "iv_end": "0.574103826615302",
    "expiry_count": 1, "type": "call", "volume_oi_ratio": "0",
    "start_time": 1792167611054, "id": "d67b6638-183f-497e-8c39-c8aaf11d68aa",
    "total_ask_side_prem": "54730", "has_sweep": True,
    "iv_start": "0.569872214090156", "option_chain": "QRTX261120C00070000",
    "marketcap": "3184066210", "has_singleleg": True,
    "created_at": "2026-10-16T16:20:14.052918Z", "issue_type": "Common Stock",
    "total_size": 31, "has_multileg": False, "open_interest": 0,
    "sector": "Consumer Cyclical", "bid": "14.2",
    "rule_id": "4b179ff9-5ab1-4f42-9cd4-fe19ae472f5e", "price": "17.65",
    "total_bid_side_prem": "2140", "next_earnings_date": "2026-10-15",
    "alert_rule": "RepeatedHits", "er_time": "postmarket",
    "end_time": 1792167611083, "all_opening_trades": False, "trade_count": 8,
    "ticker": "QRTX", "expiry": "2026-11-20", "total_premium": "56870",
}

BLMP_ZERO_OI2 = {
    "has_floor": False, "strike": "22.5", "volume": 34, "ask": "3.1",
    "underlying_price": "21.8841", "iv_end": "0.861207734519840",
    "expiry_count": 1, "type": "call", "volume_oi_ratio": "0",
    "start_time": 1792167804937, "id": "26776c83-ee26-4b25-8897-6f4ee430c745",
    "total_ask_side_prem": "0", "has_sweep": False,
    "iv_start": "0.861207734519840", "option_chain": "BLMP261204C00022500",
    "marketcap": "1742885301", "has_singleleg": True,
    "created_at": "2026-10-16T16:23:29.418865Z", "issue_type": "Common Stock",
    "total_size": 34, "has_multileg": False, "open_interest": 0,
    "sector": "Technology", "bid": "2.6",
    "rule_id": "4b179ff9-5ab1-4f42-9cd4-fe19ae472f5e", "price": "2.7",
    "total_bid_side_prem": "9180", "next_earnings_date": "2026-11-12",
    "alert_rule": "RepeatedHits", "er_time": "unknown", "end_time": 1792167804990,
    "all_opening_trades": False, "trade_count": 5, "ticker": "BLMP",
    "expiry": "2026-12-04", "total_premium": "9180",
}

VOLT_MULTILEG = {
    "has_floor": True, "strike": "31", "volume": 1184, "ask": "1.55",
    "underlying_price": "30.64", "iv_end": "0.517640281937055",
    "expiry_count": 2, "type": "put", "volume_oi_ratio": "0.472284004786597",
    "start_time": 1792167552309, "id": "4c02275f-d35f-4c43-be94-f0da954d23ba",
    "total_ask_side_prem": "0", "has_sweep": False,
    "iv_start": "0.517640281937055", "option_chain": "VOLT261023P00031000",
    "marketcap": "5127390448", "has_singleleg": False,
    "created_at": "2026-10-16T16:19:13.604471Z", "issue_type": "Common Stock",
    "total_size": 1150, "has_multileg": True, "open_interest": 2507,
    "sector": "Financial Services", "bid": "1.25",
    "rule_id": "38803bf3-1505-44a2-ab2d-4023f5dd2175", "price": "1.3",
    "total_bid_side_prem": "149500", "next_earnings_date": "2026-11-10",
    "alert_rule": "FloorTradeMidCap", "er_time": "unknown",
    "end_time": 1792167552309, "all_opening_trades": False, "trade_count": 3,
    "ticker": "VOLT", "expiry": "2026-10-23", "total_premium": "149500",
}

ACME_FLOOR = {
    "has_floor": True, "strike": "57.5", "volume": 752, "ask": "2.35",
    "underlying_price": "54.19", "iv_end": "0.301846227509113",
    "expiry_count": 1, "type": "call", "volume_oi_ratio": "0.264321965628295",
    "start_time": 1792167915412, "id": "8f2a8611-588f-4d80-989b-a914a8a49c15",
    "total_ask_side_prem": "146380", "has_sweep": False,
    "iv_start": "0.301846227509113", "option_chain": "ACME270319C00057500",
    "marketcap": "24613058140", "has_singleleg": True,
    "created_at": "2026-10-16T16:25:18.902551Z", "issue_type": "Common Stock",
    "total_size": 638, "has_multileg": False, "open_interest": 2845,
    "sector": "Consumer Cyclical", "bid": "2.05",
    "rule_id": "aa7abd8d-94b1-46d5-a7bf-9389f793546c", "price": "2.29",
    "total_bid_side_prem": "0", "next_earnings_date": "2026-11-18",
    "alert_rule": "LowHistoricVolumeFloor", "er_time": "unknown",
    "end_time": 1792167915412, "all_opening_trades": False, "trade_count": 1,
    "ticker": "ACME", "expiry": "2027-03-19", "total_premium": "146380",
}

DRVX_ASCENDING = {
    "has_floor": False, "strike": "41", "volume": 11304, "ask": "0.39",
    "underlying_price": "41.08", "iv_end": "0.693570218804416",
    "expiry_count": 1, "type": "put", "volume_oi_ratio": "5.18302613480055",
    "start_time": 1792167944786, "id": "0d988542-5903-43b9-a857-3dcf28bac8d7",
    "total_ask_side_prem": "88614", "has_sweep": True,
    "iv_start": "0.662915470238175", "option_chain": "DRVX261016P00041000",
    "marketcap": "187402516300", "has_singleleg": True,
    "created_at": "2026-10-16T16:25:48.117036Z", "issue_type": "Common Stock",
    "total_size": 2431, "has_multileg": False, "open_interest": 2181,
    "sector": "Technology", "bid": "0.31",
    "rule_id": "1bd0ec34-fb32-4e7a-84df-f640b26e3ffa", "price": "0.37",
    "total_bid_side_prem": "7310", "next_earnings_date": "2026-11-03",
    "alert_rule": "RepeatedHitsAscendingFill", "er_time": "unknown",
    "end_time": 1792167944851, "all_opening_trades": False, "trade_count": 33,
    "ticker": "DRVX", "expiry": "2026-10-16", "total_premium": "97205",
}

PLNK_BID_PUT = {
    "has_floor": False, "strike": "45", "volume": 364, "ask": "7.6",
    "underlying_price": "49.73", "iv_end": "0.758304619027714",
    "expiry_count": 1, "type": "put", "volume_oi_ratio": "2.84375",
    "start_time": 1792167967208, "id": "d41fb130-9950-433f-9218-c4c8cd184040",
    "total_ask_side_prem": "0", "has_sweep": False,
    "iv_start": "0.758304619027714", "option_chain": "PLNK270219P00045000",
    "marketcap": "4381520967", "has_singleleg": True,
    "created_at": "2026-10-16T16:26:11.740623Z", "issue_type": "Common Stock",
    "total_size": 410, "has_multileg": False, "open_interest": 128,
    "sector": "Healthcare", "bid": "7.35",
    "rule_id": "4b179ff9-5ab1-4f42-9cd4-fe19ae472f5e", "price": "7.35",
    "total_bid_side_prem": "301460", "next_earnings_date": "2026-11-09",
    "alert_rule": "RepeatedHits", "er_time": "unknown", "end_time": 1792167967209,
    "all_opening_trades": False, "trade_count": 29, "ticker": "PLNK",
    "expiry": "2027-02-19", "total_premium": "301460",
}

MEGA_0DTE = {
    "has_floor": False, "strike": "215", "volume": 301877, "ask": "0.47",
    "underlying_price": "213.884", "iv_end": "0.312047716390258",
    "expiry_count": 1, "type": "call", "volume_oi_ratio": "16.1259080626770",
    "start_time": 1792168013577, "id": "93d0fe82-eafc-4f3f-823d-6d266082b17f",
    "total_ask_side_prem": "97", "has_sweep": False,
    "iv_start": "0.305518290447312", "option_chain": "MEGA261016C00215000",
    "marketcap": "3918227405100", "has_singleleg": True,
    "created_at": "2026-10-16T16:26:57.035219Z", "issue_type": "Common Stock",
    "total_size": 2215, "has_multileg": False, "open_interest": 18720,
    "sector": "Technology", "bid": "0.45",
    "rule_id": "4b179ff9-5ab1-4f42-9cd4-fe19ae472f5e", "price": "0.46",
    "total_bid_side_prem": "115842", "next_earnings_date": "2026-11-05",
    "alert_rule": "RepeatedHits", "er_time": "unknown", "end_time": 1792168013690,
    "all_opening_trades": False, "trade_count": 51, "ticker": "MEGA",
    "expiry": "2026-10-16", "total_premium": "115939",
}

KWLX_NULL_ER = {
    "has_floor": False, "strike": "12", "volume": 2390, "ask": "0.84",
    "underlying_price": "11.87", "iv_end": "0.824915307731462",
    "expiry_count": 1, "type": "call", "volume_oi_ratio": "1.08885542168675",
    "start_time": 1792167796120, "id": "ab8fd32e-506d-4aa1-a804-ff1592aae9f8",
    "total_ask_side_prem": "93600", "has_sweep": False,
    "iv_start": "0.831760452118903", "option_chain": "KWLX261030C00012000",
    "marketcap": "9870341522", "has_singleleg": True,
    "created_at": "2026-10-16T16:23:20.664590Z", "issue_type": "Common Stock",
    "total_size": 1170, "has_multileg": False, "open_interest": 2195,
    "sector": "Financial Services", "bid": "0.77",
    "rule_id": "4b179ff9-5ab1-4f42-9cd4-fe19ae472f5e", "price": "0.8",
    "total_bid_side_prem": "0", "next_earnings_date": None,
    "alert_rule": "RepeatedHits", "er_time": "premarket",
    "end_time": 1792167796251, "all_opening_trades": False, "trade_count": 14,
    "ticker": "KWLX", "expiry": "2026-10-30", "total_premium": "93600",
}

GIGA_BID_CALL = {
    "has_floor": False, "strike": "440", "volume": 117, "ask": "88.3",
    "underlying_price": "452.6", "iv_end": "0.579318602446025", "expiry_count": 1,
    "type": "call", "volume_oi_ratio": "0.164556962025316",
    "start_time": 1792167882491, "id": "78c6aef9-d88b-44df-a4cd-baf2e4c2e45f",
    "total_ask_side_prem": "11480", "has_sweep": True,
    "iv_start": "0.578104935620817", "option_chain": "GIGA261120C00440000",
    "marketcap": "612840377902", "has_singleleg": True,
    "created_at": "2026-10-16T16:24:46.297802Z", "issue_type": "Common Stock",
    "total_size": 104, "has_multileg": False, "open_interest": 711,
    "sector": "Technology", "bid": "86.1",
    "rule_id": "4b179ff9-5ab1-4f42-9cd4-fe19ae472f5e", "price": "86.4",
    "total_bid_side_prem": "987215", "next_earnings_date": "2026-11-24",
    "alert_rule": "RepeatedHits", "er_time": "postmarket",
    "end_time": 1792167882604, "all_opening_trades": False, "trade_count": 7,
    "ticker": "GIGA", "expiry": "2026-11-20", "total_premium": "998695",
}

TNYX_TINY_OI = {
    "has_floor": False, "strike": "64", "volume": 27, "ask": "5.4",
    "underlying_price": "68.95", "iv_end": "0.498271530661204", "expiry_count": 1,
    "type": "call", "volume_oi_ratio": "9", "start_time": 1792167990843,
    "id": "2ca2c9f8-1af4-4a43-b200-f25cb7537652", "total_ask_side_prem": "14310",
    "has_sweep": True, "iv_start": "0.498271530661204",
    "option_chain": "TNYX261016C00064000", "marketcap": "3406718853",
    "has_singleleg": True, "created_at": "2026-10-16T16:26:34.481190Z",
    "issue_type": "Common Stock", "total_size": 27, "has_multileg": False,
    "open_interest": 3, "sector": "Consumer Defensive", "bid": "3.9",
    "rule_id": "4b179ff9-5ab1-4f42-9cd4-fe19ae472f5e", "price": "5.3",
    "total_bid_side_prem": "0", "next_earnings_date": "2026-11-12",
    "alert_rule": "RepeatedHits", "er_time": "unknown", "end_time": 1792167990843,
    "all_opening_trades": False, "trade_count": 11, "ticker": "TNYX",
    "expiry": "2026-10-16", "total_premium": "14310",
}

ALERTS = [
    ZENO_FLOOR, ZENO_REPEATED, QRTX_ZERO_OI, BLMP_ZERO_OI2, VOLT_MULTILEG,
    ACME_FLOOR, DRVX_ASCENDING, PLNK_BID_PUT, MEGA_0DTE, KWLX_NULL_ER,
    GIGA_BID_CALL, TNYX_TINY_OI,
]

# --- /api/screener/option-contracts shaped rows ----------------------------
# Trimmed to the fields the desk reads. The four megacap rows are the ones the
# aggression gate must reject.

MEGA_SCR = {
    "ticker_symbol": "MEGA", "option_symbol": "MEGA261016C00215000",
    "option_type": "call", "strike": "215", "expiry": "2026-10-16",
    "date": "2026-10-16", "stock_price": "213.91", "volume": 298430,
    "open_interest": 18720, "prev_oi": 12405, "ticker_vol": 2481093,
    "ask_side_volume": 131870, "bid_side_volume": 138215, "mid_volume": 28345,
    "neutral_volume": 0, "cross_volume": 0, "multileg_volume": 15230,
    "sweep_volume": 11482, "floor_volume": 38, "stock_multi_leg_volume": 3,
    "premium": "27458210.00", "trades": 69114, "days_of_oi_increases": 2,
    "days_of_vol_greater_than_oi": 4, "is_new": False, "iv": "0.3120477163902581",
    "delta": "0.3915028417736204", "ask_side_perc_7_day": "0.285714",
    "bid_side_perc_7_day": "0.714286", "next_earnings_date": "2026-11-05",
    "er_time": "unknown", "issue_type": "Common Stock", "sector": "Technology",
    "vol_pctile_15d": None, "tape_time": "2026-10-16T16:26:41Z",
}

TITN_SCR = {
    "ticker_symbol": "TITN", "option_symbol": "TITN261016C00145000",
    "option_type": "call", "strike": "145", "expiry": "2026-10-16",
    "date": "2026-10-16", "stock_price": "143.62", "volume": 187650,
    "open_interest": 36204, "prev_oi": 11873, "ticker_vol": 1720448,
    "ask_side_volume": 95120, "bid_side_volume": 74410, "mid_volume": 18090,
    "neutral_volume": 30, "cross_volume": 30, "multileg_volume": 5412,
    "sweep_volume": 8806, "floor_volume": 19, "stock_multi_leg_volume": 17,
    "premium": "21307745.00", "trades": 30918, "days_of_oi_increases": 3,
    "days_of_vol_greater_than_oi": 2, "is_new": False, "iv": "0.3688120957314402",
    "delta": "0.2917734508226151", "ask_side_perc_7_day": "0.571429",
    "bid_side_perc_7_day": "0.428571", "next_earnings_date": "2026-11-19",
    "er_time": "unknown", "issue_type": "Common Stock", "sector": "Technology",
    "vol_pctile_15d": None, "tape_time": "2026-10-16T16:26:39Z",
}

GIGA_SCR = {
    "ticker_symbol": "GIGA", "option_symbol": "GIGA261016C00460000",
    "option_type": "call", "strike": "460", "expiry": "2026-10-16",
    "date": "2026-10-16", "stock_price": "452.35", "volume": 51284,
    "open_interest": 1528, "prev_oi": 904, "ticker_vol": 590317,
    "ask_side_volume": 20410, "bid_side_volume": 28937, "mid_volume": 1937,
    "neutral_volume": 0, "cross_volume": 0, "multileg_volume": 1306,
    "sweep_volume": 2415, "floor_volume": 63, "stock_multi_leg_volume": 0,
    "premium": "19846320.00", "trades": 11932, "days_of_oi_increases": 1,
    "days_of_vol_greater_than_oi": 3, "is_new": False, "iv": "0.6120843907725512",
    "delta": "0.2518460172093316", "ask_side_perc_7_day": "0.250000",
    "bid_side_perc_7_day": "0.750000", "next_earnings_date": "2026-11-24",
    "er_time": "postmarket", "issue_type": "Common Stock", "sector": "Technology",
    "vol_pctile_15d": None, "tape_time": "2026-10-16T16:26:37Z",
}

COLS_SCR = {
    "ticker_symbol": "COLS", "option_symbol": "COLS261016C00282500",
    "option_type": "call", "strike": "282.5", "expiry": "2026-10-16",
    "date": "2026-10-16", "stock_price": "280.17", "volume": 133907,
    "open_interest": 8413, "prev_oi": 6290, "ticker_vol": 1498206,
    "ask_side_volume": 63240, "bid_side_volume": 58815, "mid_volume": 11844,
    "neutral_volume": 8, "cross_volume": 8, "multileg_volume": 3104,
    "sweep_volume": 4918, "floor_volume": 0, "stock_multi_leg_volume": 0,
    "premium": "19204583.00", "trades": 35021, "days_of_oi_increases": 2,
    "days_of_vol_greater_than_oi": 5, "is_new": False, "iv": "0.4812096637042873",
    "delta": "0.2461830955102217", "ask_side_perc_7_day": "0.571429",
    "bid_side_perc_7_day": "0.428571", "next_earnings_date": "2026-11-17",
    "er_time": "unknown", "issue_type": "Common Stock",
    "sector": "Consumer Cyclical", "vol_pctile_15d": None,
    "tape_time": "2026-10-16T16:26:42Z",
}

FJRD_SCR = {
    "ticker_symbol": "FJRD", "option_symbol": "FJRD261106C00036000",
    "option_type": "call", "strike": "36", "expiry": "2026-11-06",
    "date": "2026-10-16", "stock_price": "34.21", "volume": 24186,
    "open_interest": 402, "prev_oi": 371, "ticker_vol": 41730,
    "ask_side_volume": 18342, "bid_side_volume": 3905, "mid_volume": 1939,
    "neutral_volume": 0, "cross_volume": 0, "multileg_volume": 540,
    "sweep_volume": 1510, "floor_volume": 9120, "stock_multi_leg_volume": 0,
    "premium": "1488320.00", "trades": 1043, "days_of_oi_increases": 4,
    "days_of_vol_greater_than_oi": 2, "is_new": False, "iv": "0.5384216093571184",
    "delta": "0.3302871145906217", "ask_side_perc_7_day": "0.714286",
    "bid_side_perc_7_day": "0.142857", "next_earnings_date": "2026-11-18",
    "er_time": "unknown", "issue_type": "Common Stock",
    "sector": "Consumer Cyclical", "vol_pctile_15d": None,
    "tape_time": "2026-10-16T16:26:08Z",
}

SNWX_SCR = {
    "ticker_symbol": "SNWX", "option_symbol": "SNWX261023C00120000",
    "option_type": "call", "strike": "120", "expiry": "2026-10-23",
    "date": "2026-10-16", "stock_price": "116.48", "volume": 5872,
    "open_interest": 21, "prev_oi": 21, "ticker_vol": 7015,
    "ask_side_volume": 5304, "bid_side_volume": 541, "mid_volume": 27,
    "neutral_volume": 0, "cross_volume": 0, "multileg_volume": 0,
    "sweep_volume": 212, "floor_volume": 5190, "stock_multi_leg_volume": 0,
    "premium": "1297410.00", "trades": 64, "days_of_oi_increases": 0,
    "days_of_vol_greater_than_oi": 1, "is_new": False, "iv": "0.4938105572164310",
    "delta": "0.3480916623045193", "ask_side_perc_7_day": "0.600000",
    "bid_side_perc_7_day": "0.400000", "next_earnings_date": "2026-11-11",
    "er_time": "unknown", "issue_type": "Common Stock",
    "sector": "Consumer Cyclical", "vol_pctile_15d": None,
    "tape_time": "2026-10-16T16:26:11Z",
}

CRSX_SCR = {
    "ticker_symbol": "CRSX", "option_symbol": "CRSX261127C00055000",
    "option_type": "call", "strike": "55", "expiry": "2026-11-27",
    "date": "2026-10-16", "stock_price": "52.84", "volume": 6212,
    "open_interest": 1183, "prev_oi": 1174, "ticker_vol": 15906,
    "ask_side_volume": 285, "bid_side_volume": 271, "mid_volume": 156,
    "neutral_volume": 5500, "cross_volume": 5500, "multileg_volume": 5500,
    "sweep_volume": 231, "floor_volume": 0, "stock_multi_leg_volume": 48,
    "premium": "3017460.00", "trades": 241, "days_of_oi_increases": 1,
    "days_of_vol_greater_than_oi": 1, "is_new": False, "iv": "0.6617029354108263",
    "delta": "0.4730518862047115", "ask_side_perc_7_day": "0.285714",
    "bid_side_perc_7_day": "0.428571", "next_earnings_date": "2026-11-20",
    "er_time": "unknown", "issue_type": "Common Stock", "sector": "Healthcare",
    "vol_pctile_15d": None, "tape_time": "2026-10-16T15:41:37Z",
}

XLGX_SCR = {
    "ticker_symbol": "XLGX", "option_symbol": "XLGX261030C00008500",
    "option_type": "call", "strike": "8.5", "expiry": "2026-10-30",
    "date": "2026-10-16", "stock_price": "8.37", "volume": 42716,
    "open_interest": 1702, "prev_oi": 1611, "ticker_vol": 176480,
    "ask_side_volume": 301, "bid_side_volume": 197, "mid_volume": 18,
    "neutral_volume": 42200, "cross_volume": 42200, "multileg_volume": 42255,
    "sweep_volume": 7, "floor_volume": 0, "stock_multi_leg_volume": 0,
    "premium": "1186204.00", "trades": 97, "days_of_oi_increases": 6,
    "days_of_vol_greater_than_oi": 1, "is_new": False, "iv": "0.4902275184360117",
    "delta": "0.4815530927716408", "ask_side_perc_7_day": "0.375000",
    "bid_side_perc_7_day": "0.500000", "next_earnings_date": "2026-12-08",
    "er_time": "unknown", "issue_type": "Common Stock", "sector": "Technology",
    "vol_pctile_15d": None, "tape_time": "2026-10-16T16:14:52Z",
}

# ask_side_volume (3702) EXCEEDS volume (3587). Sides sum to 6948, not 3587.
STVX_SCR = {
    "ticker_symbol": "STVX", "option_symbol": "STVX261023C00135000",
    "option_type": "call", "strike": "135", "expiry": "2026-10-23",
    "date": "2026-10-16", "stock_price": "133.06", "volume": 3587,
    "open_interest": 412, "prev_oi": 338, "ticker_vol": 8104,
    "ask_side_volume": 3702, "bid_side_volume": 204, "mid_volume": 2,
    "neutral_volume": 3040, "cross_volume": 0, "multileg_volume": 3,
    "sweep_volume": 0, "floor_volume": 3350, "stock_multi_leg_volume": 0,
    "premium": "1561870.00", "trades": 71, "days_of_oi_increases": 1,
    "days_of_vol_greater_than_oi": 1, "is_new": False, "iv": "0.3617720835941306",
    "delta": "0.4691204473158822", "ask_side_perc_7_day": "0.428571",
    "bid_side_perc_7_day": "0.428571", "next_earnings_date": "2026-11-13",
    "er_time": "unknown", "issue_type": "Common Stock",
    "sector": "Financial Services", "vol_pctile_15d": None,
    "tape_time": "2026-10-16T16:19:33Z",
}

CONTRACTS = [
    MEGA_SCR, TITN_SCR, GIGA_SCR, COLS_SCR, FJRD_SCR, SNWX_SCR,
    CRSX_SCR, XLGX_SCR, STVX_SCR,
]

# Synthetic population deciles in the shape the thresholds in flow.py were
# reasoned against (0th..9th, then max). Invented values; what matters is the
# shape, e.g. that the sweep share of volume never gets near a useful range.
SAMPLE_DISTRIBUTIONS = {
    # ask_side_volume / (ask_side_volume + bid_side_volume)
    "ask_share": [0.012, 0.405, 0.458, 0.483, 0.497, 0.511, 0.526, 0.557,
                  0.624, 0.829, 1.000],
    # sweep_volume / volume -- the maximum stays far below any useful ramp, so
    # a component scored on this share could never pay out. This is why the
    # sweep leg reads the alerts endpoint instead.
    "sweep_share": [0.0, 0.002, 0.006, 0.011, 0.018, 0.022, 0.029, 0.037,
                    0.041, 0.052, 0.071],
    "multileg_share": [0.0, 0.014, 0.031, 0.040, 0.052, 0.067, 0.158, 0.241,
                       0.402, 0.851, 1.000],
    "volume_over_oi": [0.21, 0.58, 0.87, 1.19, 2.34, 3.61, 5.92, 11.73,
                       17.08, 31.40, 1950.0],
    "oi_growth": [-0.142, -0.011, 0.0, 0.012, 0.041, 0.083, 0.197, 0.354,
                  0.648, 2.115, 71.0],
    "contract_share_of_ticker_vol": [0.005, 0.009, 0.013, 0.019, 0.027, 0.041,
                                     0.052, 0.071, 0.093, 0.158, 0.612],
}

SAMPLE_FACTS = {
    "rows": 40,
    "zero_dte_rows": 21,
    "distinct_tickers": 13,
    "share_ask_ge_75": 0.15,
    "share_ask_ge_60": 0.25,
}
