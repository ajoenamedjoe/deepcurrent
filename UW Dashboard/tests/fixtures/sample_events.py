"""Synthetic payloads in the shape of the UW economic-calendar and upcoming-earnings endpoints.

Every economic event appears TWICE, as the calendar tool can return duplicates --
the de-duplication in events.norm_econ is tested against this payload. The
earnings rows follow the upcoming-earnings field names (strings for numbers,
a row with no logo / market_time and null estimates, reactions as strings).
All tickers, names and numbers are invented.
"""

MARKET_EVENTS = {"data": [
    {"type": "report", "time": "2026-10-15T14:00:00Z", "prev": "55.2", "event": "Consumer Sentiment (Final)", "forecast": "53.8", "reported_period": "October"},
    {"type": "report", "time": "2026-10-15T12:30:00Z", "prev": "0.4%", "event": "Core Capital Goods Orders", "forecast": "-0.2%", "reported_period": "September"},
    {"type": "report", "time": "2026-10-14T15:00:00Z", "prev": "-4", "event": "Regional Fed Manufacturing Survey", "forecast": None, "reported_period": "October"},
    {"type": "report", "time": "2026-10-14T14:00:00Z", "prev": "3940000", "event": "Existing Home Sales", "forecast": "3985000", "reported_period": "September"},
    {"type": "report", "time": "2026-10-14T12:30:00Z", "prev": None, "event": "Fed Governor Remarks", "forecast": None, "reported_period": None},
    {"type": "report", "time": "2026-10-14T12:30:00Z", "prev": "218000", "event": "Initial Jobless Claims", "forecast": "221000", "reported_period": "October"},
    {"type": "report", "time": "2026-10-15T14:00:00Z", "prev": "55.2", "event": "Consumer Sentiment (Final)", "forecast": "53.8", "reported_period": "October"},
    {"type": "report", "time": "2026-10-15T12:30:00Z", "prev": "0.4%", "event": "Core Capital Goods Orders", "forecast": "-0.2%", "reported_period": "September"},
    {"type": "report", "time": "2026-10-14T15:00:00Z", "prev": "-4", "event": "Regional Fed Manufacturing Survey", "forecast": None, "reported_period": "October"},
    {"type": "report", "time": "2026-10-14T14:00:00Z", "prev": "3940000", "event": "Existing Home Sales", "forecast": "3985000", "reported_period": "September"},
    {"type": "report", "time": "2026-10-14T12:30:00Z", "prev": None, "event": "Fed Governor Remarks", "forecast": None, "reported_period": None},
    {"type": "report", "time": "2026-10-14T12:30:00Z", "prev": "218000", "event": "Initial Jobless Claims", "forecast": "221000", "reported_period": "October"},
]}

EARNINGS = {"result": [
    {"prev": "11.42", "symbol": "QRTX", "curr": "11.57", "logo": "https://example.com/logos/QRTX.png", "full_name": "QUARTEX SYSTEMS", "sector": "Technology", "is_s_p_500": False, "has_options": True, "market_time": "postmarket", "marketcap": "2384019553", "report_date": "2026-10-14", "report_time": "premarket", "expected_move": "1.2375", "eps_mean_est": "-0.05", "street_mean_est": "-0.04", "implied_move": "1.237466", "last_1d_reactions": ["-0.04243383478645382234", "0.09350322850940298580", "0.05644875161972712219", "0.01617977946133053457"]},
    {"prev": "739.12", "symbol": "ZMRT", "curr": "742.5", "logo": "https://example.com/logos/ZMRT.png", "full_name": "ZEEMART WHOLESALE", "sector": "Consumer Defensive", "is_s_p_500": True, "has_options": True, "market_time": "postmarket", "marketcap": "328640917205", "report_date": "2026-10-14", "report_time": "postmarket", "expected_move": "18.4127", "eps_mean_est": "5.12", "street_mean_est": "5.117", "implied_move": "18.412693", "last_1d_reactions": ["0.03132366797127603131", "-0.06883972623856265960", "0.08373661365396106726", "-0.05525072244183744374"]},
    {"prev": "5.86", "symbol": "NULX", "curr": "5.86", "full_name": "NULLEX MOTORS LTD", "sector": "Consumer Cyclical", "is_s_p_500": False, "has_options": True, "marketcap": "1296703418", "report_date": "2026-10-14", "report_time": "unknown", "expected_move": "0.7412", "eps_mean_est": None, "street_mean_est": None, "last_1d_reactions": ["-0.10672706841247625587", "-0.00913871499780916086", "-0.10160992565079871031", "-0.11296167638982548831"]},
    {"prev": "164.03", "symbol": "DYNZ", "curr": "164.03", "logo": "https://example.com/logos/DYNZ.png", "full_name": "DYNZO RESTAURANTS", "sector": "Consumer Cyclical", "is_s_p_500": True, "has_options": True, "marketcap": "19844172630", "report_date": "2026-10-14", "report_time": "premarket", "expected_move": "9.5520", "eps_mean_est": "1.74", "street_mean_est": "1.736", "last_1d_reactions": ["-0.02101517197065480569", "0.10714073126125092661", "0.00395219376856033311", "-0.06492879206928191260"]},
]}
