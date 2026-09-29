"""
Confluence Desk -- the insider leg.

One wide market-wide poll of SEC Form 4 open-market purchases (transaction
code P), aggregated per company over a rolling window.

COST: /api/insider/transactions takes limit=500 (the REST maximum -- the MCP
tool caps at 50, which is a different ceiling and not the one that matters
here). 90 days of the entire market is FOUR calls. Never make a per-ticker
insider call for anything this endpoint already carries.
"""

import datetime as dt
import re

import uw

# ------------------------------------------------------------- eligibility

# `common_stock_only=true` is NOT sufficient. A 90-day sample through that
# filter still returned 16 Total Return Swap rows plus assorted LP/LLC units.
# Filter locally on BOTH the acquisition/disposition code and the title.
#   NA = non-derivative acquisition (what we want)
#   DA = derivative acquisition     (what leaks through)
SECURITY_AD_OK = {"NA"}

_TITLE_OK = re.compile(
    r"\b(COMMON\s+STOCK|COMMON\s+SHARES|CLASS\s+[A-Z]\s+COMMON|ORDINARY\s+SHARES"
    r"|COMMON\s+UNITS?\s+OF\s+BENEFICIAL|SHARES?\s+OF\s+BENEFICIAL\s+INTEREST)\b",
    re.I,
)
_TITLE_BAD = re.compile(
    r"\b(SWAP|OPTION|WARRANT|RIGHT|CONVERTIBLE|PREFERRED|NOTE|DEBENTURE|RSU"
    r"|RESTRICTED\s+STOCK\s+UNIT|PHANTOM|DERIVATIVE|LLC\s+UNIT|LP\s+UNIT"
    r"|PARTNERSHIP\s+UNIT)\b",
    re.I,
)

MIN_COMPANY_NOTIONAL = 50_000.0     # per company per window

# The `price` field can be flatly wrong. Seen on a real filing: a director
# buying 1,000 shares reported at ~110x the stock_price, a discrepancy that
# would have booked a six-figure purchase into a small-cap company and
# rocketed the card to the top of the board.
# Cross-check every fill against the quote and fall back when it is absurd.
PRICE_SANITY_RATIO = 5.0


def is_eligible(row):
    """Row-level filter. Returns (ok, reason)."""
    if (row.get("transaction_code") or "").upper() != "P":
        return False, "not_a_purchase"
    if (row.get("security_ad_code") or "").upper() not in SECURITY_AD_OK:
        return False, "derivative_or_disposition"
    title = row.get("security_title") or ""
    if _TITLE_BAD.search(title):
        return False, "excluded_security_title"
    if not _TITLE_OK.search(title):
        return False, "unrecognised_security_title"
    if not (row.get("ticker") or "").strip():
        return False, "no_ticker"
    if uw.num(row.get("amount")) <= 0:
        return False, "no_shares"
    return True, ""


def fill_price(row):
    """
    The price actually paid, with the bad-data guard.

    `price` is the fill and `stock_price` the current quote. `price` is null
    on ~0.5% of rows and `stock_price` on ~6.5%; a row with neither silently
    books $0 of notional and drops through the dollar floor, which looks like
    "no insider buying" rather than "bad row".

    Returns (price, flag) where flag is '', 'fallback' or 'suspect'.
    """
    price = uw.opt_num(row.get("price"))
    quote = uw.opt_num(row.get("stock_price"))
    if price and price > 0:
        if quote and quote > 0:
            ratio = price / quote
            if ratio > PRICE_SANITY_RATIO or ratio < 1.0 / PRICE_SANITY_RATIO:
                # The fill disagrees with the quote by more than 5x. Trust the
                # quote -- a genuine multi-month drawdown never gets near 5x,
                # so this only fires on bad data.
                return quote, "suspect"
        return price, ""
    if quote and quote > 0:
        return quote, "fallback"
    return 0.0, "suspect"


# ----------------------------------------------------------------- fetch

def fetch_purchases(client, start_date, page_size=500, max_pages=12):
    """
    Market-wide Form 4 purchases since `start_date`.

    Envelope is {data, has_more}; `uw.unwrap` handles data/result/results, and
    pagination stops on a short page.
    """
    rows = []
    page = 0
    while page < max_pages:
        payload = client.get(
            "/api/insider/transactions",
            {
                "transaction_codes[]": "P",
                "common_stock_only": "true",
                "start_date": start_date,
                "limit": page_size,
                "page": page,
            },
        )
        batch = uw.unwrap(payload)
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        if isinstance(payload, dict) and payload.get("has_more") is False:
            break
        page += 1
    return rows


# ------------------------------------------------------------- aggregate

def _parse_date(text):
    try:
        return dt.date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def aggregate(rows):
    """
    Group eligible purchases by ticker into the shape `score.insider_leg`
    wants, plus everything the card needs to display.

    Grouping is by (ticker, reporter_cik) inside each company, because the
    share image and the stake arithmetic both need PEOPLE, not filings: one
    ticker once carried 27 filings from 2 people, and each filing's stake percentage
    is measured against THAT filing's starting holding, so they cannot be
    summed. The grouped stake is (everything that person bought) over (what
    they held before their FIRST purchase).
    """
    from collections import defaultdict

    by_ticker = defaultdict(lambda: {"people": {}, "rows": [], "rejects": 0})

    for row in rows:
        ok, _reason = is_eligible(row)
        if not ok:
            by_ticker[(row.get("ticker") or "?").upper()]["rejects"] += 1
            continue
        ticker = row["ticker"].upper()
        bucket = by_ticker[ticker]
        price, flag = fill_price(row)
        shares = uw.num(row.get("amount"))
        notional = shares * price
        tx_date = _parse_date(row.get("transaction_date"))

        cik = str(row.get("reporter_cik") or row.get("owner_name") or "?")
        person = bucket["people"].get(cik)
        if person is None:
            person = bucket["people"][cik] = {
                "cik": cik,
                "name": (row.get("owner_name") or "").title() or "Unknown",
                "title": row.get("officer_title") or "",
                "is_director": bool(row.get("is_director")),
                "is_officer": bool(row.get("is_officer")),
                "is_ten_percent_owner": bool(row.get("is_ten_percent_owner")),
                "is_corporate": bool(row.get("reporter_is_public_company")),
                "shares": 0.0,
                "notional": 0.0,
                "filings": 0,
                "planned_filings": 0,
                "first_before": None,
                "first_date": None,
                "last_date": None,
                "dates": set(),
                "price_flags": set(),
            }
        # A richer officer_title on a later filing wins -- the same person
        # sometimes files once with a blank title and once with a full one.
        if len(row.get("officer_title") or "") > len(person["title"]):
            person["title"] = row.get("officer_title") or ""
        person["shares"] += shares
        person["notional"] += notional
        person["filings"] += 1
        if row.get("is_10b5_1"):
            person["planned_filings"] += 1
        if flag:
            person["price_flags"].add(flag)
        if tx_date:
            person["dates"].add(tx_date)
            if person["first_date"] is None or tx_date < person["first_date"]:
                person["first_date"] = tx_date
                person["first_before"] = uw.opt_num(row.get("shares_owned_before"))
            if person["last_date"] is None or tx_date > person["last_date"]:
                person["last_date"] = tx_date
        bucket["rows"].append(row)

    out = {}
    for ticker, bucket in by_ticker.items():
        people = list(bucket["people"].values())
        if not people:
            continue
        notional = sum(p["notional"] for p in people)
        if notional < MIN_COMPANY_NOTIONAL:
            continue

        last_row = bucket["rows"][0]
        marketcap = uw.opt_num(last_row.get("marketcap"))
        quote = uw.opt_num(last_row.get("stock_price"))

        # Conviction is the BEST single stake growth in the cluster, not the
        # average -- one person tripling their holding is the signal, and
        # averaging it against four directors' token buys erases it.
        conviction = 0.0
        for person in people:
            from score import conviction_component
            person["stake_growth"] = conviction_component(
                person["shares"], person["first_before"]
            )
            person["new_stake"] = (
                person["first_before"] is None or person["first_before"] <= 0
            )
            # The SCORE clamps stake growth at 100% on purpose -- doubling a
            # holding is already maximum conviction and quadrupling it should
            # not buy more points. But the CARD must show the real number:
            # deriving the display value back out of the clamped score made a
            # director who tripled their stake read "+100%", identical to one
            # who merely doubled, and identical to each other. Keep the honest
            # ratio separately and never reconstruct it from the score.
            person["stake_pct"] = (
                None if person["new_stake"]
                else 100.0 * person["shares"] / float(person["first_before"])
            )
            conviction = max(conviction, person["stake_growth"])

        from score import seniority
        for person in people:
            person["rank"] = seniority(
                person["title"], person["is_director"],
                person["is_ten_percent_owner"], person["is_officer"],
            )
        top = max(people, key=lambda p: (p["rank"], p["notional"]))

        planned = sum(p["planned_filings"] for p in people)
        filings = sum(p["filings"] for p in people) or 1
        corporate_notional = sum(p["notional"] for p in people if p["is_corporate"])

        all_dates = sorted({d for p in people for d in p["dates"]})
        avg_price = (notional / sum(p["shares"] for p in people)) if notional else 0.0

        out[ticker] = {
            "ticker": ticker,
            "sector": last_row.get("sector"),
            "marketcap": marketcap,
            "quote": quote,
            "next_earnings_date": last_row.get("next_earnings_date"),
            "distinct_buyers": len(people),
            "filings": filings,
            "notional": notional,
            "shares": sum(p["shares"] for p in people),
            "avg_price": avg_price,
            "conviction": conviction,
            "top_rank": top["rank"],
            "top_buyer": top,
            "people": sorted(people, key=lambda p: -p["notional"]),
            "frac_10b5_1": planned / float(filings),
            "frac_corporate": (corporate_notional / notional) if notional else 0.0,
            "dates": all_dates,
            "first_date": all_dates[0].isoformat() if all_dates else None,
            "last_date": all_dates[-1].isoformat() if all_dates else None,
            # DISPLAY ONLY -- see the warning in score.py. Scoring this would
            # systematically rank falling knives to the top of the board.
            "vs_fills_pct": (
                100.0 * (quote - avg_price) / avg_price
                if (quote and avg_price > 0) else None
            ),
            "price_flags": sorted({f for p in people for f in p["price_flags"]}),
        }
    return out
