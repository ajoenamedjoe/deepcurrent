"""
Confluence Desk -- the dark pool leg.

One call per candidate ticker against /api/darkpool/{ticker}. Verified live
2026-09-10: a single call returns the most recent prints going back roughly
eight sessions for a small cap (a few dozen rows spanning about two
calendar weeks), which is enough for the multi-session read WITHOUT paying for a
call per ticker per day.

Payload fields that matter, all confirmed on live rows:
  size, premium, price          the print
  volume                        consolidated session volume at that moment
  avg30_volume                  30-day average volume -- TICKER level, the
                                same value on every row, so 'block vs normal'
                                needs no extra call
  nbbo_bid / nbbo_ask           quote at print time
  ext_hour_sold_codes           'extended_hours_trade' or null
  sale_cond_codes               'prior_reference_price' etc
  executed_at / trf_executed_at execution vs tape hit
  canceled                      busted prints
"""

import datetime as dt

import uw
from score import ASK_SIDE, BID_SIDE, spread_position

# Prints below this are retail-sized noise even in a thin name. The REST
# endpoint defaults min_premium to 0, so it must be sent explicitly -- do not
# rely on the default the way the MCP tool appears to (every MCP-returned row
# was >= $100k, which is a tool-side default and NOT the API's).
MIN_PRINT_PREMIUM = 50_000.0

# A session counts as "active" when its qualifying off-exchange prints are at
# least this share of everything that traded in the session. See aggregate().
ACTIVE_SHARE_OF_DAY = 0.05

# Sale conditions whose attached NBBO does not describe the fill.
BAD_SALE_CONDS = {"prior_reference_price", "average_price_trade", "contingent_trade"}


def fetch_prints(client, ticker, limit=200, min_premium=MIN_PRINT_PREMIUM):
    """
    Recent off-exchange prints for one ticker.

    NOTE the parameter-name trap that has bitten this project before: the REST
    path takes the ticker in the URL, while the MCP tool takes it as
    `ticker_symbol`. They are not interchangeable.
    """
    import urllib.parse

    return client.get_list(
        "/api/darkpool/%s" % urllib.parse.quote(ticker, safe=""),
        {"limit": limit, "min_premium": int(min_premium)},
    )


def _session_date(row):
    """
    The trading session a print belongs to.

    Timestamps are UTC. A print at 23:58Z belongs to the session that STARTED
    on the previous calendar day in New York -- a print stamped 23:58Z is
    6:58pm ET on that same New York date, not the next day. Shifting back 5 hours
    lands every US session (04:00-20:00 ET = 08:00Z-01:00Z next day) on its
    own date without needing tzdata, which is not installed on a stock Windows
    box and would break the no-install promise.
    """
    stamp = row.get("executed_at") or row.get("created_at") or ""
    try:
        clean = str(stamp).replace("Z", "+00:00")
        when = dt.datetime.fromisoformat(clean)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    return (when - dt.timedelta(hours=5)).date()


def is_regular_hours(row):
    return not (row.get("ext_hour_sold_codes") or "")


def clean_prints(rows):
    """
    The subset whose NBBO can support a bid/ask inference.

    Everything dropped here is dropped because the quote attached to the print
    does not describe the fill -- see the long note on `spread_position`.
    These rows still count for size and sustain; they are excluded only from
    the directional lean.
    """
    out = []
    for row in rows:
        if row.get("canceled"):
            continue
        if not is_regular_hours(row):
            continue
        conds = (row.get("sale_cond_codes") or "")
        if any(bad in conds for bad in BAD_SALE_CONDS):
            continue
        pos = spread_position(row.get("price"), row.get("nbbo_bid"), row.get("nbbo_ask"))
        if pos is None:
            continue
        out.append((row, pos))
    return out


def aggregate(rows, marketcap=None, insider_dates=None, window_days=10,
              proximity_days=5, now=None):
    """
    Collapse one ticker's prints into the shape `score.darkpool_leg` wants.

    `window_days` bounds the lookback in SESSIONS actually present in the
    payload, so a name whose call reached back 12 sessions is not scored on a
    longer history than a name whose call reached back 6.
    """
    insider_dates = insider_dates or []
    live = [r for r in rows if not r.get("canceled")]
    if not live:
        return {
            "print_count": 0, "clean_count": 0, "window_notional": 0.0,
            "max_size_vs_avg30": 0.0, "weighted_position": None,
            "pressure": None, "ask_share": None, "bid_share": None,
            "active_sessions": 0, "window_sessions": 0, "overlap_days": 0,
            "testable_days": [],
            "insider_days": [d.isoformat() if hasattr(d, "isoformat") else d
                             for d in insider_dates],
            "marketcap": marketcap, "sessions": [], "top_prints": [],
        }

    dated = [(r, _session_date(r)) for r in live]
    sessions_present = sorted({d for _r, d in dated if d}, reverse=True)
    keep = set(sessions_present[:window_days])
    dated = [(r, d) for r, d in dated if d in keep]

    avg30 = 0.0
    for row, _d in dated:
        avg30 = max(avg30, uw.num(row.get("avg30_volume")))

    per_session = {}
    max_size = 0.0
    total_notional = 0.0
    top_prints = []
    for row, day in dated:
        size = uw.num(row.get("size"))
        premium = uw.num(row.get("premium"))
        if premium <= 0:
            premium = size * uw.num(row.get("price"))
        total_notional += premium
        max_size = max(max_size, size)
        bucket = per_session.setdefault(
            day, {"notional": 0.0, "size": 0.0, "prints": 0, "volume": 0.0})
        bucket["notional"] += premium
        bucket["size"] += size
        bucket["prints"] += 1
        # `volume` is the running consolidated total at print time, so the
        # largest one seen in a session is that session's volume so far.
        bucket["volume"] = max(bucket["volume"], uw.num(row.get("volume")))
        top_prints.append({
            "date": day.isoformat(),
            "time": str(row.get("executed_at") or "")[11:16],
            "size": int(size),
            "price": uw.num(row.get("price")),
            "premium": premium,
            "size_vs_avg30": (size / avg30) if avg30 > 0 else 0.0,
            "extended": not is_regular_hours(row),
        })
    top_prints.sort(key=lambda p: -p["premium"])

    # A session is "active" when its qualifying off-exchange prints are at
    # least ACTIVE_SHARE_OF_DAY of THAT SESSION'S consolidated volume.
    #
    # The denominator is the session's own volume, taken as the largest
    # `volume` seen on any print in it -- that field is the running
    # consolidated total at print time, so its maximum is the session total so
    # far. It falls back to 30-day average volume when `volume` is absent.
    #
    # THIS TOOK THREE WRONG ANSWERS TO GET RIGHT, AND THE WRONG ONES ALL HAD
    # THE SAME SHAPE: an absolute constant tuned by eye on one or two tickers.
    #
    #   v1  "5% of 30-DAY AVERAGE volume". Eyeballed on one ticker, which turned
    #       out to be the most block-heavy name in the sample (median session
    #       above 5% of its own average volume) while an ordinary liquid
    #       mid-cap runs under 2%. Unreachable for a normal ticker: on the first
    #       live board `sustain` scored ZERO on every card and took
    #       `proximity` -- which needs an active session -- down with it.
    #       14 of the 30 dark pool points, dead, on every card.
    #   v2  the same constant lowered to 2%. Over-corrected: nearly every
    #       session active on both a busy name and a flat one, both saturated,
    #       and the gap between a name working real size and a flat one
    #       collapsed to under two points.
    #   v3  "1.3x the ticker's OWN median session". Scale-free, but it
    #       punishes exactly what the component is supposed to reward: a name
    #       that is heavy EVERY day has a high own-median, so consistency
    #       scores nothing. With a 6-8 session window the median is also
    #       unstable enough that half the sessions can never qualify.
    #
    # The real fault in all three: the numerator is filtered by `min_premium`
    # and the denominator (30-day average volume) is not, so the ratio's
    # natural scale drifts with liquidity and price and no constant fits.
    #
    # Share of the SESSION'S OWN volume fixes that -- numerator and
    # denominator are the same session -- and it is also literally the signal
    # asked for: "dark pool share of total volume elevated over several days".
    # Measured on real tape, a block-heavy name's median session share sat
    # several points above two ordinary names'. A 5% floor marks most of the
    # heavy name's sessions active and only a few of the others' --
    # discriminating across all three instead of all-or-nothing.
    #
    # Caveat kept honest: the CURRENT session is still forming, so its volume
    # is partial and its share runs high (several times its typical share mid-morning). The
    # session is flagged `partial` and the card says so.
    active = 0
    session_rows = []
    newest = max(per_session) if per_session else None
    for day in sorted(per_session, reverse=True):
        bucket = per_session[day]
        day_volume = bucket["volume"] or avg30
        share_of_day = (bucket["size"] / day_volume) if day_volume > 0 else 0.0
        share_avg30 = (bucket["size"] / avg30) if avg30 > 0 else 0.0
        is_active = share_of_day >= ACTIVE_SHARE_OF_DAY
        if is_active:
            active += 1
        session_rows.append({
            "date": day.isoformat(), "notional": bucket["notional"],
            "size": bucket["size"], "prints": bucket["prints"],
            "day_volume": bucket["volume"],
            "share_of_day": share_of_day,
            "share_of_avg30": share_avg30,
            "active": is_active,
            "partial": day == newest,
        })

    # Directional pressure: premium share printing UP at the offer minus the
    # share printing DOWN at the bid. NOT a weighted mean -- see the long note
    # on `score.lean_component` for why the mean scored a busy name and a flat
    # one identically and had to be thrown away.
    cleaned = clean_prints([r for r, _d in dated])
    pressure = None
    ask_share = bid_share = None
    weighted_position = None
    if cleaned:
        total_w = sum(max(uw.num(r.get("premium")), 1.0) for r, _p in cleaned)
        if total_w > 0:
            up = sum(max(uw.num(r.get("premium")), 1.0)
                     for r, pos in cleaned if pos >= ASK_SIDE)
            down = sum(max(uw.num(r.get("premium")), 1.0)
                       for r, pos in cleaned if pos <= BID_SIDE)
            ask_share = up / total_w
            bid_share = down / total_w
            pressure = ask_share - bid_share
            # Kept for the card only, so a reader can see the mean that the
            # pressure number replaced and why they disagree.
            weighted_position = sum(
                pos * max(uw.num(r.get("premium")), 1.0) for r, pos in cleaned
            ) / total_w

    # Proximity: an insider transaction date counts as covered when an active
    # dark pool session falls within +/- proximity_days CALENDAR days of it.
    active_days = {dt.date.fromisoformat(s["date"]) for s in session_rows if s["active"]}
    all_days = {dt.date.fromisoformat(s["date"]) for s in session_rows}

    # STRUCTURAL LIMIT, not a bug, but it has to be visible:
    # the insider window is 45 days and the dark pool call reaches back about
    # 8 SESSIONS. An insider buy from three weeks ago therefore has no dark
    # pool data anywhere near it and can NEVER score on proximity. Counting
    # it as a miss reads as "the blocks did not line up" when the truth is
    # "there is nothing to compare it to".
    # Only insider dates that fall inside (or within proximity_days of) the
    # dark pool window are testable; if none are, proximity is UNMEASURED.
    overlap = 0
    norm_insider = []
    testable = []
    if all_days:
        lo, hi = min(all_days), max(all_days)
        for day in insider_dates:
            if isinstance(day, str):
                try:
                    day = dt.date.fromisoformat(day[:10])
                except ValueError:
                    continue
            norm_insider.append(day)
            in_reach = (lo - dt.timedelta(days=proximity_days) <= day
                        <= hi + dt.timedelta(days=proximity_days))
            if not in_reach:
                continue
            testable.append(day)
            if any(abs((day - a).days) <= proximity_days for a in active_days):
                overlap += 1
    else:
        for day in insider_dates:
            if isinstance(day, str):
                try:
                    day = dt.date.fromisoformat(day[:10])
                except ValueError:
                    continue
            norm_insider.append(day)

    return {
        "print_count": len(dated),
        "clean_count": len(cleaned),
        "window_notional": total_notional,
        "max_size_vs_avg30": (max_size / avg30) if avg30 > 0 else 0.0,
        "max_size": max_size,
        "avg30_volume": avg30,
        "weighted_position": weighted_position,
        "pressure": pressure,
        "ask_share": ask_share,
        "bid_share": bid_share,
        "active_sessions": active,
        "window_sessions": len(session_rows),
        "overlap_days": overlap,
        "testable_days": [d.isoformat() for d in testable],
        "insider_days": [d.isoformat() for d in norm_insider],
        "marketcap": marketcap,
        "sessions": session_rows,
        "top_prints": top_prints[:8],
        "latest_session": session_rows[0]["date"] if session_rows else None,
    }
