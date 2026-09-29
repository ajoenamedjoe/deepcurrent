"""
Growth Leaders desk -- diagnostic. Calls every endpoint the desk uses once and prints what came back, so a
field that is missing on the real API (e.g. daily volume) shows up here instead of as a silent zero.

    python diag.py [TICKER]
"""

import sys

import model as M
import uw


def main(t="NVDA"):
    print("\nGrowth Leaders desk -- diagnostics\n")
    try:
        c = uw.Client()
    except RuntimeError as exc:
        print("  NO TOKEN:", exc)
        return 2
    checks = []

    def step(name, fn):
        try:
            msg = fn()
            checks.append((name, True, msg))
        except Exception as exc:        # noqa: BLE001
            checks.append((name, False, "%s: %s" % (type(exc).__name__, exc)))

    def screener():
        rows = c.screener_page(0, limit=50)
        r = rows[0] if rows else {}
        need = ("three_month_perc", "one_year_perc", "week_52_high", "industry_type", "shares_outstanding_growth_4q",
                "avg30_volume", "stock_volume")
        miss = [k for k in need if k not in r]
        return "%d rows; first %s; missing fields: %s" % (len(rows), r.get("ticker"), ", ".join(miss) or "none")

    def earnings():
        q = M.eps_series(c.earnings(t), None)
        return "%d reported quarters; latest %s EPS %s" % (len(q), q[0]["end"] if q else "-", q[0]["eps"] if q else "-")

    def ownership():
        o = M.ownership(c.ownership(t))
        return "latest 13F %s: %s holders, %s adds, %s cuts" % (o and o["report_date"], o and o["holders"], o and o["adds"], o and o["cuts"])

    def bars():
        rows = c.ohlc_daily(t, "1Y")
        vol = sum(1 for r in rows if M.num(r.get("volume")))
        keys = sorted(rows[-1].keys()) if rows else []
        return "%d regular-session days; %d with volume; fields: %s" % (len(rows), vol, ", ".join(keys))

    def index():
        rows = c.ohlc_daily("SPY", "1Y")
        return "SPY %d days, %d with volume (needed for distribution days)" % (
            len(rows), sum(1 for r in rows if M.num(r.get("volume"))))

    step("/api/screener/stocks", screener)
    step("/api/stock/%s/earnings" % t, earnings)
    step("/api/institution/%s/ownership" % t, ownership)
    step("/api/stock/%s/ohlc/1d" % t, bars)
    step("/api/stock/SPY/ohlc/1d", index)
    for name, ok, msg in checks:
        print("  %s  %-36s %s" % ("OK  " if ok else "FAIL", name, msg))
    print("\n  %d API calls.\n" % c.calls)
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:2] or ["NVDA"])))
