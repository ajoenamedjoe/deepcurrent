"""
Position size calculator.

    risk $          = capital x risk %
    stop            = entry x (1 - stop %)   long
                      entry x (1 + stop %)   short
    risk per share  = |entry - stop|
    shares by risk  = floor(risk $ / risk per share)
    shares by size  = floor(capital x max position % / entry)
    shares          = the smaller of the two -- and the page says which one bound

Nothing rounds up: a fractional share would put more than the stated % at risk.
If the free cash in the portfolio is smaller than the position, that is a warning,
not a silent resize -- the trader decides.
"""

import math


def size(capital, entry, risk_pct, max_pos_pct, stop_pct, side="long", cash=None):
    """All percentages are fractions (0.01 = 1%). Returns a dict, or {"error": ...}."""
    try:
        capital, entry = float(capital), float(entry)
        risk_pct, max_pos_pct, stop_pct = float(risk_pct), float(max_pos_pct), float(stop_pct)
    except (TypeError, ValueError):
        return {"error": "Every field needs a number."}
    side = "short" if str(side).lower().startswith("s") else "long"
    if capital <= 0:
        return {"error": "Starting capital must be above zero (Settings -> Portfolio)."}
    if entry <= 0:
        return {"error": "Entry price must be above zero."}
    if not 0 < risk_pct <= 1:
        return {"error": "Risk must be between 0% and 100% of the account."}
    if not 0 < max_pos_pct <= 1:
        return {"error": "Max position must be between 0% and 100% of the account."}
    if not 0 < stop_pct < (1 if side == "long" else 10):
        return {"error": "Stop loss must be above 0%%%s." % (" and below 100% for a long" if side == "long" else "")}

    stop = entry * (1 - stop_pct) if side == "long" else entry * (1 + stop_pct)
    per_share = abs(entry - stop)
    risk_usd = capital * risk_pct
    cap_usd = capital * max_pos_pct
    by_risk = math.floor(risk_usd / per_share + 1e-9)
    by_size = math.floor(cap_usd / entry + 1e-9)
    shares = max(0, min(by_risk, by_size))
    bound = "risk" if by_risk <= by_size else "size"
    pos = shares * entry
    at_risk = shares * per_share
    sign = 1 if side == "long" else -1
    out = {
        "side": side, "capital": capital, "entry": entry, "stop": stop, "per_share": per_share,
        "risk_usd_budget": risk_usd, "max_pos_usd": cap_usd,
        "shares_by_risk": by_risk, "shares_by_size": by_size, "shares": shares, "bound": bound,
        "position_usd": pos, "position_pct": pos / capital,
        "at_risk_usd": at_risk, "at_risk_pct": at_risk / capital,
        "targets": [{"r": r, "price": entry + sign * r * per_share, "gain_usd": shares * r * per_share}
                    for r in (1, 2, 3)],
        "notes": [],
    }
    if shares == 0:
        out["notes"].append("Zero shares: one share (%s) is more than the %s allows."
                            % (_usd(entry if bound == "size" else per_share),
                               "max position size" if bound == "size" else "risk budget"))
    elif bound == "size":
        out["notes"].append("Capped by max position size: the risk budget alone would allow %d shares." % by_risk)
    else:
        out["notes"].append("Set by the risk budget: max position size would allow %d shares." % by_size)
    if cash is not None and pos > cash + 1e-6:
        out["notes"].append("That is %s but the portfolio shows %s in cash." % (_usd(pos), _usd(cash)))
        out["cash_short"] = True
    if side == "short":
        out["notes"].append("Short: the stop is above entry, and losses are not capped if it gaps through.")
    return out


def _usd(v):
    return "${:,.2f}".format(v)
