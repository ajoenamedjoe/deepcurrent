"""
Synthetic payloads in the shape of the insider and earnings endpoints, trimmed
to the fields the model reads. Every name, id and number is invented.

ACME_INSIDERS: synthetic payload in the shape of /api/insider/transactions
  ?ticker_symbol=ACME&transaction_codes[]=P,S&security_ad_codes[]=NA,ND -- note the
  SAME grouped filing appearing twice (ROE 2025-10-06, identical `ids` in a
  different order, one copy with reporter_cik null), a quirk the API is known for.
ACME_EARNINGS: synthetic payload in the shape of /api/stock/{ticker}/earnings --
  newest row is the NEXT report (reported_eps null) and must not count as a miss.
"""

def _ins(owner, code, amount, price, date, plan, after, cik, ids, officer=True, director=False,
         title=None, stock_price="183.12"):
    return {"ticker": "ACME", "owner_name": owner, "transaction_code": code, "amount": amount,
            "price": price, "stock_price": stock_price, "transaction_date": date, "is_10b5_1": plan,
            "shares_owned_after": after, "reporter_cik": cik, "ids": ids, "is_officer": officer,
            "is_director": director, "officer_title": title, "security_ad_code": "ND",
            "is_ten_percent_owner": False, "marketcap": "2069587310000"}


ACME_INSIDERS = {"data": [
    _ins("DOE JANE", "S", -1500, "181.4400", "2026-05-18", True, 402117, "0009100001",
         ["39143f9a-2a2b-499d-87c1-84848548ca50"], title="President, Example Retail"),
    _ins("ROE RICHARD", "S", -24000, "184.9031", "2026-04-09", True, 1873402, "0009100002",
         ["5d5b1c62-ccf3-47df-8996-0d0a091836a8", "5f5ec76e-9877-4a23-94c9-f751a32cb0cf"],
         director=True, title="Chief Executive Officer"),
    _ins("STONE MARTIN", "S", -950000, "176.2200", "2026-03-02", True, 1104662318, "0009100003",
         ["73e290e0-6727-4a2a-b3f4-d32a7b9650dc"], director=True, title="Chair of the Board"),
    _ins("LANE PRIYA", "S", -2800, "169.7500", "2025-12-12", True, 61240, "0009100004",
         ["333f9096-b41e-4c27-9864-d0d728b7f556"], officer=False, director=True),
    # the duplicate pair: identical ids, reporter_cik null on one copy
    _ins("ROE RICHARD", "S", -22150, "158.3120", "2025-10-06", True, 1851002, None,
         ["45f0815f-aeb8-4b74-afb7-69ac4d73a4a2", "15b56524-2f1d-484e-bff4-8794f18a6e8a"],
         director=True, title="Chief Executive Officer"),
    _ins("ROE RICHARD", "S", -22150, "158.3120", "2025-10-06", True, 1851002, "0009100002",
         ["15b56524-2f1d-484e-bff4-8794f18a6e8a", "45f0815f-aeb8-4b74-afb7-69ac4d73a4a2"],
         director=True, title="Chief Executive Officer"),
], "has_more": True}


def _e(fd, rep, est, sp):
    return {"ticker": "ACME", "fiscal_date_ending": fd, "report_type": "quarterly",
            "reported_eps": rep, "estimated_eps": est, "surprise_percentage": sp}


ACME_EARNINGS = {"result": [
    _e("2026-07-31", None, "1.87", None),
    _e("2026-04-30", "6.48", "2.14", "202.8037"),
    _e("2026-01-31", "3.94", "2.07", "90.3382"),
    _e("2025-10-31", "2.21", "2.04", "8.3333"),
    _e("2025-07-31", "2.66", "1.98", "34.3434"),
    _e("2025-04-30", "1.97", "1.91", "3.1414"),
    _e("2025-01-31", "2.44", "1.83", "33.3333"),
    _e("2024-10-31", "1.79", "1.77", "1.1299"),
    _e("2024-07-31", "1.93", "1.68", "14.881"),
    _e("2024-04-30", "1.71", "1.66", "3.012"),
    _e("2024-01-31", "1.62", "1.34", "20.8955"),
    _e("2023-10-31", "1.41", "1.37", "2.9197"),
    _e("2023-07-31", "1.33", "1.24", "7.2581"),
    _e("2023-01-31", "0.87", "1.02", "-14.7059"),
]}
