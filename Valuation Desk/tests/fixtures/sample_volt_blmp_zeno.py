"""
Synthetic payloads in the shape of /api/stock/{ticker}/info, /income-statements,
/balance-sheets and /cash-flows, trimmed to the fields the model reads. Every
ticker, name and number is invented.

VOLT  -- sector "Utilities" but an IPP: revenue FELL 12% in the latest year,
         operating income fell 79%, capex (3.51B) > D&A (2.69B), net debt ~18.2B
         against 4.6B of book equity.
BLMP  -- bank. operating_cashflow is -127.2B in a year it earned +75.2B;
         capex is "0". Any FCF model is meaningless. Buybacks arrive as a
         NEGATIVE proceeds_from_repurchase_of_equity, not in
         payments_for_repurchase_of_common_stock. No balance sheet on
         purpose: the model must survive a missing statement.
ZENO  -- ADR. Statements in TWD (reported_currency) while /info quotes USD per
         ADR and `outstanding` is ADR units (4.87B, vs ~24.4B ordinary shares).
         Mixing them gives a nonsense P/E.
"""

VOLT_INFO = {"data": {"symbol": "VOLT", "beta": "1.3894", "full_name": "VOLT POWER", "sector": "Utilities",
    "issue_type": "Common Stock", "outstanding": "312448901", "marketcap": "37071812096",
    "next_earnings_date": "2026-11-12", "has_dividend": True,
    "short_description": "volt power is an example integrated retail electricity and power generation company."},
    "price": "118.27"}

VOLT_IS_A = {"result": [
 {"fiscal_date_ending": "2025-09-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "15108000000", "gross_profit": "3317000000", "operating_income": "1204000000", "depreciation_and_amortization": "2688000000", "interest_expense": "1302000000", "ebit": "2477000000", "ebitda": "5165000000", "net_income": "861000000"},
 {"fiscal_date_ending": "2024-09-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "17236000000", "gross_profit": "7108000000", "operating_income": "5687000000", "depreciation_and_amortization": "2402000000", "interest_expense": "1187000000", "ebit": "4215000000", "ebitda": "6617000000", "net_income": "2394000000"},
 {"fiscal_date_ending": "2023-09-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "14092000000", "gross_profit": "4839000000", "operating_income": "3544000000", "depreciation_and_amortization": "1731000000", "interest_expense": "812000000", "ebit": "2506000000", "ebitda": "4237000000", "net_income": "1368000000"},
]}

VOLT_BS_A = {"result": [
 {"fiscal_date_ending": "2025-09-30", "report_type": "annual", "reported_currency": "USD", "total_assets": "38214000000", "total_current_assets": "8627000000", "total_current_liabilities": "10948000000", "cash_and_cash_equivalents": "702000000", "cash_and_short_term_investments": "702000000", "short_term_investments": None, "long_term_investments": "4618000000", "goodwill": "2493000000", "intangible_assets": "2206000000", "total_liabilities": "33578000000", "short_term_debt": "3806000000", "long_term_debt": "14512000000", "capital_lease_obligations": None, "short_long_term_debt_total": "18944000000", "total_shareholder_equity": "4636000000", "common_stock_shares_outstanding": "316200000"},
]}

VOLT_CF_A = {"result": [
 {"fiscal_date_ending": "2025-09-30", "report_type": "annual", "operating_cashflow": "3631000000", "capital_expenditures": "3512000000", "depreciation_depletion_and_amortization": "2688000000", "stock_based_compensation": "104000000", "dividend_payout": "437000000", "payments_for_repurchase_of_common_stock": None, "proceeds_from_repurchase_of_equity": "-836000000"},
 {"fiscal_date_ending": "2024-09-30", "report_type": "annual", "operating_cashflow": "4102000000", "capital_expenditures": "1847000000", "depreciation_depletion_and_amortization": "2402000000", "stock_based_compensation": "91000000", "dividend_payout": "421000000", "payments_for_repurchase_of_common_stock": None, "proceeds_from_repurchase_of_equity": "-1117000000"},
 {"fiscal_date_ending": "2023-09-30", "report_type": "annual", "operating_cashflow": "4987000000", "capital_expenditures": "1487000000", "depreciation_depletion_and_amortization": "1731000000", "stock_based_compensation": "68000000", "dividend_payout": "409000000", "payments_for_repurchase_of_common_stock": None, "proceeds_from_repurchase_of_equity": "-1391000000"},
]}

BLMP_INFO = {"data": {"symbol": "BLMP", "beta": "0.8413", "full_name": "BLMP BANCORP", "sector": "Financial Services",
    "issue_type": "Common Stock", "outstanding": "2412905388", "marketcap": "703291447120",
    "next_earnings_date": "2026-10-21", "has_dividend": True,
    "short_description": "BLMP Bancorp is an example multinational bank holding company offering consumer and investment banking."},
    "price": "291.47"}

BLMP_IS_A = {"result": [
 {"fiscal_date_ending": "2025-06-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "251702000000", "gross_profit": "158923000000", "operating_income": "85678000000", "depreciation_and_amortization": "9381000000", "interest_expense": "110188000000", "ebit": "85678000000", "ebitda": "95059000000", "net_income": "75196000000"},
 {"fiscal_date_ending": "2024-06-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "230554000000", "gross_profit": "136332000000", "operating_income": "79855000000", "depreciation_and_amortization": "6396000000", "interest_expense": "114816000000", "ebit": "79855000000", "ebitda": "86251000000", "net_income": "66033000000"},
 {"fiscal_date_ending": "2023-06-30", "report_type": "annual", "reported_currency": "USD", "total_revenue": "198410000000", "gross_profit": "121876000000", "operating_income": "60846000000", "depreciation_and_amortization": "6424000000", "interest_expense": "97299000000", "ebit": "60846000000", "ebitda": "67270000000", "net_income": "49148000000"},
]}

BLMP_CF_A = {"result": [
 {"fiscal_date_ending": "2025-06-30", "report_type": "annual", "operating_cashflow": "-127207000000", "capital_expenditures": "0", "depreciation_depletion_and_amortization": "9381000000", "stock_based_compensation": None, "dividend_payout": "22728000000", "payments_for_repurchase_of_common_stock": None, "proceeds_from_repurchase_of_equity": "-47219000000"},
 {"fiscal_date_ending": "2024-06-30", "report_type": "annual", "operating_cashflow": "-45137000000", "capital_expenditures": "0", "depreciation_depletion_and_amortization": "6396000000", "stock_based_compensation": None, "dividend_payout": "13770000000", "payments_for_repurchase_of_common_stock": None, "proceeds_from_repurchase_of_equity": "-37875000000"},
 {"fiscal_date_ending": "2023-06-30", "report_type": "annual", "operating_cashflow": "16669000000", "capital_expenditures": "1226000000", "depreciation_depletion_and_amortization": "6424000000", "stock_based_compensation": "3376000000", "dividend_payout": "16520000000", "payments_for_repurchase_of_common_stock": None, "proceeds_from_repurchase_of_equity": "-10234000000"},
]}

ZENO_INFO = {"data": {"symbol": "ZENO", "beta": "1.9832", "full_name": "ZENO SEMICONDUCTOR", "sector": "Technology",
    "issue_type": "ADR", "outstanding": "4873219506", "marketcap": "1883677011452",
    "next_earnings_date": "2026-10-22", "has_dividend": True,
    "short_description": "Zeno Semiconductor Limited is an example foreign contract chip manufacturer listed through ADRs."},
    "price": "386.2217"}

ZENO_IS_A = {"result": [
 {"fiscal_date_ending": "2026-03-31", "report_type": "annual", "reported_currency": "TWD", "total_revenue": "3412806551000", "gross_profit": "1988317260000", "operating_income": "1702548113000", "depreciation_and_amortization": "741902655000", "interest_expense": "10318283000", "ebit": "1851460992000", "ebitda": "2593363647000", "net_income": "1511294708000"},
 {"fiscal_date_ending": "2025-03-31", "report_type": "annual", "reported_currency": "TWD", "total_revenue": "2617440093000", "gross_profit": "1432118774000", "operating_income": "1164372106000", "depreciation_and_amortization": "706288431000", "interest_expense": "12331940000", "ebit": "1164372106000", "ebitda": "1870660537000", "net_income": "1046925318000"},
]}
