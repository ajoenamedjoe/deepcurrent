"""Synthetic payloads in the shape of the UW insider-transactions, flow-alerts, sector-ETF
and market-tide endpoints (trimmed to the fields read). All names and numbers are invented.

ACME insider rows: a 10% owner fund and its SPV sold outside a 10b5-1 plan, a director sold
under a plan, and one row is a Form 144 NOTICE (formtype "144", security_ad_code null) --
not a sale. Flow: puts and calls both bought on the ask, below the warning threshold.
"""

ACME_INSIDER = [
    {"id": "b99cfac4", "ticker": "ACME", "amount": -845, "price": "421.5500", "transaction_date": "2026-10-08",
     "is_officer": False, "is_ten_percent_owner": False, "is_director": True, "owner_name": "DOE JANE",
     "stock_price": "412.37", "formtype": "4", "transaction_code": "S", "is_10b5_1": True, "security_ad_code": "ND",
     "next_earnings_date": "2026-11-19", "ids": ["489b43ec"]},
    {"id": "a70a00bb", "ticker": "ACME", "amount": -2210, "price": "415.0300", "transaction_date": "2026-10-12",
     "is_officer": False, "is_ten_percent_owner": False, "is_director": True, "owner_name": "ROE RICHARD",
     "stock_price": "412.37", "formtype": "144", "transaction_code": "S", "is_10b5_1": False, "security_ad_code": None,
     "next_earnings_date": "2026-11-19", "ids": ["6c06846b"]},
    {"id": "a7ad23ca", "ticker": "ACME", "amount": -1260, "price": "409.1385", "transaction_date": "2026-10-05",
     "is_officer": False, "is_ten_percent_owner": True, "is_director": True, "owner_name": "EXAMPLE CAPITAL PARTNERS III, L.P.",
     "stock_price": "412.37", "formtype": "4", "transaction_code": "S", "is_10b5_1": False, "security_ad_code": "ND",
     "next_earnings_date": "2026-11-19", "ids": ["f86d681f", "0dc80241"]},
    {"id": "823d4ff4", "ticker": "ACME", "amount": -3175, "price": "418.6420", "transaction_date": "2026-09-30",
     "is_officer": False, "is_ten_percent_owner": True, "is_director": True, "owner_name": "EXAMPLE SPV-1, L.P.",
     "stock_price": "412.37", "formtype": "4", "transaction_code": "S", "is_10b5_1": False, "security_ad_code": "ND",
     "next_earnings_date": "2026-11-19", "ids": ["7cfe76ad", "93177d6a"]},
]

_T = 1792007232381   # ms, synthetic "now" for the flow rows

ACME_FLOW = [
    {"type": "put", "total_ask_side_prem": "18430", "total_bid_side_prem": "612", "total_premium": "19042",
     "option_chain": "ACME261016P00400000", "start_time": _T, "next_earnings_date": "2026-11-19"},
    {"type": "put", "total_ask_side_prem": "21185", "total_bid_side_prem": "3140", "total_premium": "24325",
     "option_chain": "ACME261023P00392500", "start_time": _T - 4117},
    {"type": "put", "total_ask_side_prem": "9760", "total_bid_side_prem": "845", "total_premium": "10605",
     "option_chain": "ACME261023P00397500", "start_time": _T - 3861},
    {"type": "call", "total_ask_side_prem": "47220", "total_bid_side_prem": "0", "total_premium": "47220",
     "option_chain": "ACME261016C00425000", "start_time": _T - 187553},
    {"type": "call", "total_ask_side_prem": "88415", "total_bid_side_prem": "104930", "total_premium": "193345",
     "option_chain": "ACME261016C00415000", "start_time": _T - 912406},
]

SECTORS = {"result": [
    {"ticker": "SPY", "full_name": "S&P 500 Index", "last": "702.44", "prev_close": "705.1",
     "bullish_premium": "846211930.0000", "bearish_premium": "912447205.0000"},
    {"ticker": "XLK", "full_name": "Technology", "last": "231.08", "prev_close": "229.95",
     "bullish_premium": "4412870.00", "bearish_premium": "3987215.00"},
    {"ticker": "XLV", "full_name": "Health Care", "last": "151.36", "prev_close": "150.12",
     "bullish_premium": "2976410.00", "bearish_premium": "3310552.00"},
    {"ticker": "XLE", "full_name": "Energy", "last": "88.14", "prev_close": "88.73",
     "bullish_premium": "4120938.00", "bearish_premium": "9876012.00000000"},
]}

TIDE = {"data": [
    {"timestamp": "2026-10-14T09:30:00-04:00", "net_call_premium": "8841205.0000", "net_put_premium": "-1207733.0000", "net_volume": 18422},
    {"timestamp": "2026-10-14T12:00:00-04:00", "net_call_premium": "-92416830.0000", "net_put_premium": "-15088461.0000", "net_volume": -307615},
    {"timestamp": "2026-10-14T16:00:00-04:00", "net_call_premium": "-41377902.0000", "net_put_premium": "-63019544.0000", "net_volume": 44870},
], "date": "2026-10-14"}
