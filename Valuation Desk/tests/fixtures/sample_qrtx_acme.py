"""
Synthetic payloads in the shape of /api/stock/{ticker}/info, /income-statements,
/balance-sheets and /cash-flows, trimmed to the fields the model reads. Every
ticker, name and number is invented. Values are strings / null, exactly as the
API types them -- that is the point of a fixture.

QRTX  -- venture-stage, cash burning, wildly non-cash net income
         (one quarter NI +602M, the next NI -2.12B on ~$87M revenue: warrant marks).
         `ebit` disagrees with `operating_income` by $1.4B in the newest quarter.
         cash_and_short_term_investments == cash_and_cash_equivalents, while
         short_term_investments is reported separately (1.11B).
ACME  -- profitable, but the latest year's capex (139.7B) ~= operating cash flow
         (147.9B), so reported FCF is ~1% of revenue. A naive FCF DCF calls it a zero.
"""

QRTX_INFO = {"data": {"symbol": "QRTX", "beta": "3.1287", "full_name": "QRTX QUANTUM",
    "sector": "Technology", "issue_type": "Common Stock", "outstanding": "318774062",
    "marketcap": "10964118253", "next_earnings_date": "2026-10-08",
    "short_description": "qrtx quantum is an example developer of general-purpose quantum processors."},
    "price": "38.66"}

QRTX_IS_Q = {"result": [
 {"fiscal_date_ending": "2026-05-31", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "87210000", "gross_profit": "26520000", "operating_income": "-355850000", "depreciation_and_amortization": "51049000", "interest_expense": None, "ebit": "1010403000", "ebitda": "1061452000", "net_income": "-2121237000"},
 {"fiscal_date_ending": "2026-02-28", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "80406000", "gross_profit": "12056000", "operating_income": "-249853000", "depreciation_and_amortization": "33634000", "interest_expense": None, "ebit": "1001270000", "ebitda": "1034904000", "net_income": "602120000"},
 {"fiscal_date_ending": "2025-11-30", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "84681000", "gross_profit": "47423000", "operating_income": "-279526000", "depreciation_and_amortization": "51183000", "interest_expense": None, "ebit": "600159000", "ebitda": "651342000", "net_income": "751873000"},
 {"fiscal_date_ending": "2025-08-31", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "30270000", "gross_profit": "16178000", "operating_income": "-148502000", "depreciation_and_amortization": "14340000", "interest_expense": None, "ebit": "-1352626000", "ebitda": "-1338286000", "net_income": "-1205384417"},
 {"fiscal_date_ending": "2025-05-31", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "21726000", "gross_profit": "2026000", "operating_income": "-132368000", "depreciation_and_amortization": "14634000", "interest_expense": None, "ebit": "-265514000", "ebitda": "-250880000", "net_income": "-209934000"},
 {"fiscal_date_ending": "2025-02-28", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "7022000", "gross_profit": "-2885000", "operating_income": "-68926000", "depreciation_and_amortization": "5028000", "interest_expense": None, "ebit": "-39518000", "ebitda": "-34490000", "net_income": "-43615000"},
 {"fiscal_date_ending": "2024-11-30", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "14980000", "gross_profit": "4801000", "operating_income": "-66530000", "depreciation_and_amortization": "7270000", "interest_expense": None, "ebit": "-276111000", "ebitda": "-268841000", "net_income": "-229357000"},
 {"fiscal_date_ending": "2024-08-31", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "15299000", "gross_profit": "5285000", "operating_income": "-41313000", "depreciation_and_amortization": "4594000", "interest_expense": None, "ebit": "-71178000", "ebitda": "-66584000", "net_income": "-41885000"},
 {"fiscal_date_ending": "2024-05-31", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": "10045000", "gross_profit": "1143000", "operating_income": "-37172000", "depreciation_and_amortization": "5364000", "interest_expense": None, "ebit": "-31436000", "ebitda": "-26072000", "net_income": "-31771000"},
 {"fiscal_date_ending": "2019-05-31", "report_type": "quarterly", "reported_currency": "USD", "total_revenue": None, "gross_profit": None, "operating_income": "-4507000", "depreciation_and_amortization": "274000", "interest_expense": None, "ebit": "-4507000", "ebitda": "-4233000", "net_income": "-3155000"},
]}

QRTX_IS_A = {"result": [
 {"fiscal_date_ending": "2025-11-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "143699000", "gross_profit": "62742000", "operating_income": "-629222000", "depreciation_and_amortization": "85185000", "interest_expense": None, "ebit": "-725934000", "ebitda": "-640749000", "net_income": "-655269000"},
 {"fiscal_date_ending": "2024-11-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "49259000", "gross_profit": "17671000", "operating_income": "-319147000", "depreciation_and_amortization": "16056000", "interest_expense": None, "ebit": "-295267000", "ebitda": "-279211000", "net_income": "-310790000"},
 {"fiscal_date_ending": "2023-11-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "20181000", "gross_profit": "10707000", "operating_income": "-122966000", "depreciation_and_amortization": "11460000", "interest_expense": None, "ebit": "-122934000", "ebitda": "-111474000", "net_income": "-123008000"},
]}

QRTX_BS_Q = {"result": [
 {"fiscal_date_ending": "2026-05-31", "report_type": "quarterly", "reported_currency": "USD", "total_assets": "8331496000", "total_current_assets": "3113223000", "total_current_liabilities": "190044000", "cash_and_cash_equivalents": "1015435000", "cash_and_short_term_investments": "1015435000", "short_term_investments": "1112661000", "long_term_investments": "743445000", "goodwill": "1847732000", "intangible_assets": "940895000", "total_liabilities": "4610571000", "short_term_debt": "9905000", "long_term_debt": None, "capital_lease_obligations": "73414000", "short_long_term_debt_total": "73414000", "total_shareholder_equity": "3720925000", "common_stock_shares_outstanding": "301556000"},
 {"fiscal_date_ending": "2025-05-31", "report_type": "quarterly", "reported_currency": "USD", "total_assets": "1690918000", "total_current_assets": "848308000", "total_current_liabilities": "70744000", "cash_and_cash_equivalents": "165983000", "cash_and_short_term_investments": "165983000", "short_term_investments": "509653000", "long_term_investments": "122394000", "goodwill": "338715000", "intangible_assets": "119719000", "total_liabilities": "201026000", "short_term_debt": "4231000", "long_term_debt": None, "capital_lease_obligations": "16775000", "short_long_term_debt_total": "16775000", "total_shareholder_equity": "1489892000", "common_stock_shares_outstanding": "212449000"},
]}

QRTX_CF_Q = {"result": [
 {"fiscal_date_ending": "2026-05-31", "report_type": "quarterly", "operating_cashflow": "-93894000", "capital_expenditures": "13538000", "stock_based_compensation": "121224000", "dividend_payout": None, "payments_for_repurchase_of_common_stock": None},
 {"fiscal_date_ending": "2026-02-28", "report_type": "quarterly", "operating_cashflow": "-110389000", "capital_expenditures": "7513000", "stock_based_compensation": "97660000", "dividend_payout": None, "payments_for_repurchase_of_common_stock": None},
 {"fiscal_date_ending": "2025-11-30", "report_type": "quarterly", "operating_cashflow": "-62315000", "capital_expenditures": "9647000", "stock_based_compensation": "86063000", "dividend_payout": None, "payments_for_repurchase_of_common_stock": None},
 {"fiscal_date_ending": "2025-08-31", "report_type": "quarterly", "operating_cashflow": "-118034000", "capital_expenditures": "5398000", "stock_based_compensation": "54264000", "dividend_payout": None, "payments_for_repurchase_of_common_stock": None},
 {"fiscal_date_ending": "2025-05-31", "report_type": "quarterly", "operating_cashflow": "-60648000", "capital_expenditures": "2686000", "stock_based_compensation": "109653000", "dividend_payout": None, "payments_for_repurchase_of_common_stock": None},
]}

ACME_INFO = {"data": {"symbol": "ACME", "beta": "1.2716", "full_name": "ACME ",
    "sector": "Consumer Cyclical", "issue_type": "Common Stock", "outstanding": "11302847190",
    "marketcap": "2129294837700", "next_earnings_date": "2026-11-19",
    "short_description": "Acme Example Corp. is a fictional multinational company focusing on online retail, cloud hosting, advertising, and ..."},
    "price": "187.63"}

ACME_IS_A = {"result": [
 {"fiscal_date_ending": "2026-01-31", "report_type": "annual", "reported_currency": "USD", "total_revenue": "742318000000", "gross_profit": "371902000000", "operating_income": "84160000000", "depreciation_and_amortization": "71233000000", "interest_expense": "2689000000", "ebit": "104882000000", "ebitda": "176115000000", "net_income": "81407000000"},
 {"fiscal_date_ending": "2025-01-31", "report_type": "annual", "reported_currency": "USD", "total_revenue": "655207000000", "gross_profit": "318553000000", "operating_income": "62918000000", "depreciation_and_amortization": "57604000000", "interest_expense": "2599000000", "ebit": "66735000000", "ebitda": "124339000000", "net_income": "54120000000"},
 {"fiscal_date_ending": "2024-01-31", "report_type": "annual", "reported_currency": "USD", "total_revenue": "590114000000", "gross_profit": "281377000000", "operating_income": "41306000000", "depreciation_and_amortization": "44921000000", "interest_expense": "3730000000", "ebit": "41306000000", "ebitda": "86227000000", "net_income": "33184000000"},
]}

ACME_BS_A = {"result": [
 {"fiscal_date_ending": "2026-01-31", "report_type": "annual", "reported_currency": "USD", "total_assets": "869442000000", "total_current_assets": "251545000000", "total_current_liabilities": "236118000000", "cash_and_cash_equivalents": "95888000000", "cash_and_short_term_investments": "95888000000", "short_term_investments": "27995000000", "long_term_investments": None, "goodwill": "26578000000", "intangible_assets": "12651000000", "total_liabilities": "437892000000", "short_term_debt": "546000000", "long_term_debt": "71308000000", "capital_lease_obligations": "92417000000", "short_long_term_debt_total": "161410000000", "total_shareholder_equity": "431550000000", "common_stock_shares_outstanding": "11285000000"},
]}

ACME_CF_A = {"result": [
 {"fiscal_date_ending": "2026-01-31", "report_type": "annual", "operating_cashflow": "147902000000", "capital_expenditures": "139655000000", "stock_based_compensation": "16974000000", "dividend_payout": None, "payments_for_repurchase_of_common_stock": None},
 {"fiscal_date_ending": "2025-01-31", "report_type": "annual", "operating_cashflow": "121468000000", "capital_expenditures": "88217000000", "stock_based_compensation": "24775000000", "dividend_payout": None, "payments_for_repurchase_of_common_stock": None},
 {"fiscal_date_ending": "2024-01-31", "report_type": "annual", "operating_cashflow": "92315000000", "capital_expenditures": "55904000000", "stock_based_compensation": "20361000000", "dividend_payout": None, "payments_for_repurchase_of_common_stock": None},
]}
