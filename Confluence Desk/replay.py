"""
Confluence Desk -- score distribution replay.

    py -3 replay.py            replay the live window and report the spread
    py -3 replay.py --days 90  a longer insider window

WHY THIS EXISTS AS A SHIPPED TOOL RATHER THAN A ONE-OFF SCRIPT.

Two of this project's most expensive bugs were distribution bugs that no unit
test could have caught and that only showed up when the model was replayed on
real data and the numbers were actually read:

  * the Institutional Desk clipped its composite at 100, which put SIX
    different names at exactly 100 and destroyed the ordering at the top of
    the board -- the only part of a board anyone reads;
  * the Insider Desk's "4 of 4 signals agree" readout was unanimous on every
    card in its top 25, because two of its four components have floors. It
    was decoration, and it was deleted before shipping only because the model
    was replayed first.

This desk hit a third one during its own build: the dark pool lean started
out as a premium-weighted mean spread position, and scored a small cap visibly
working size and a flat name with prints on the bid at an IDENTICAL value.

So: after any change to a weight or a curve, run this and read three numbers.

  DISTINCT VALUES IN THE TOP 25   if this is much below 25, the top of the
                                  board is a wall of ties and the ordering
                                  is meaningless
  THE PRACTICAL CEILING           nothing reaches 100 -- every one of eleven
                                  components would have to max at once. Know
                                  what "good" actually looks like
  COMPONENT SPREAD                a component whose value is the same on
                                  nearly every card is not discriminating.
                                  It is decoration, and its weight belongs
                                  somewhere else
"""

import argparse
import collections
import json
import datetime as dt
import statistics
import sys

import darkpool
import idb
import insider
import scan
import score as scoring
import uw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=scan.CFG["window_days"])
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N candidates (saves API calls while testing)")
    ap.add_argument("--sessions", action="store_true",
                    help="print the live distribution of per-session dark pool "
                         "share straight out of confluence.db, and stop. This "
                         "is how the activity floor gets set from data instead "
                         "of by eye -- setting it by eye on one ticker is what "
                         "killed 14 of 30 dark pool points on the first board.")
    args = ap.parse_args()

    if args.sessions:
        return session_distribution()

    client = uw.Client()
    inst = idb.Institutional()
    cov = inst.coverage()
    print("13F backdrop: %s" % ("%s quarter, %d tickers" % (cov["report_date"], cov["tickers"])
                                if cov["available"] else "UNAVAILABLE -- all cards cap at 75"))

    start = (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    print("insider window: %s .. today (%d days)" % (start, args.days))
    rows = insider.fetch_purchases(client, start)
    agg = insider.aggregate(rows)
    print("%d raw filings -> %d companies clear the $%.0fk floor\n"
          % (len(rows), len(agg), insider.MIN_COMPANY_NOTIONAL / 1000.0))

    ranked = sorted(agg.values(), key=lambda a: -a["notional"])
    if args.limit:
        ranked = ranked[:args.limit]

    cards = []
    for i, one in enumerate(ranked, 1):
        sys.stdout.write("\r  dark pool %d/%d  " % (i, len(ranked)))
        sys.stdout.flush()
        try:
            prints = client.darkpool(one["ticker"], limit=200,
                                     min_premium=scan.CFG["min_print_premium"])
        except RuntimeError:
            prints = []
        dk = darkpool.aggregate(prints, marketcap=one["marketcap"],
                                insider_dates=one["dates"],
                                window_days=scan.CFG["dark_window_sessions"],
                                proximity_days=scan.CFG["proximity_days"])
        cards.append((one, dk, inst.get(one["ticker"]),
                      scoring.composite(one, dk, inst.get(one["ticker"]))))
    print("\r%s\r" % (" " * 40), end="")

    cards.sort(key=lambda c: -c[3]["score"])
    scores = [c[3]["score"] for c in cards]
    live = [s for s in scores if s >= scan.CFG["min_score"]]

    print("=" * 66)
    print("SCORE DISTRIBUTION   (%d companies, %d above the %.0f floor)"
          % (len(scores), len(live), scan.CFG["min_score"]))
    print("=" * 66)
    if not live:
        print("nothing cleared the floor -- no distribution to report")
        return 0

    top = live[:25]
    distinct = len({round(s, 1) for s in top})
    print("  max %.1f   p90 %.1f   median %.1f   min %.1f"
          % (max(live), _pct(live, 0.90), statistics.median(live), min(live)))
    print("  >= 70: %d      >= 55: %d      >= 35: %d"
          % (sum(1 for s in live if s >= 70), sum(1 for s in live if s >= 55),
             sum(1 for s in live if s >= 35)))
    print("  DISTINCT VALUES IN THE TOP 25: %d of %d %s"
          % (distinct, len(top),
             "" if distinct >= 0.8 * len(top)
             else "  <-- TOO MANY TIES, the top of the board is a wall"))
    print("  PRACTICAL CEILING: %.1f of 100. Read scores against this." % max(live))

    all_three = sum(1 for c in cards if c[3]["all_three"])
    no_13f = sum(1 for c in cards if not c[3]["legs"]["institutional"]["measured"])
    no_dark = sum(1 for c in cards if c[1]["print_count"] == 0)
    print("\n  all three legs: %d      no 13F coverage: %d      no dark pool prints: %d"
          % (all_three, no_13f, no_dark))

    print("\n" + "=" * 66)
    print("COMPONENT SPREAD   (a component that never varies is decoration)")
    print("=" * 66)
    buckets = collections.defaultdict(list)
    for _one, _dk, _in, comp in cards:
        for leg, data in comp["legs"].items():
            for name, value in data["parts"].items():
                if name == "modifier":
                    continue
                buckets["%s.%s" % (leg, name)].append(value)
    for name in sorted(buckets):
        vals = buckets[name]
        zeros = sum(1 for v in vals if v <= 1e-9)
        spread = max(vals) - min(vals)
        flag = ""
        if spread < 0.15:
            flag = "  <-- FLAT, contributes almost nothing"
        elif zeros > 0.9 * len(vals):
            flag = "  <-- zero on %d%% of cards" % round(100.0 * zeros / len(vals))
        print("  %-28s min %.2f  median %.2f  max %.2f  zero on %3d/%d%s"
              % (name, min(vals), statistics.median(vals), max(vals),
                 zeros, len(vals), flag))

    print("\n" + "=" * 66)
    print("TOP 15")
    print("=" * 66)
    print("  %-7s %6s  %5s %5s %5s   %s" % ("ticker", "score", "ins", "dark", "13F", "carried by"))
    for one, dk, _in, comp in cards[:15]:
        legs = comp["legs"]
        print("  %-7s %6.1f  %5.1f %5.1f %5.1f   %s"
              % (one["ticker"], comp["score"], legs["insider"]["points"],
                 legs["darkpool"]["points"], legs["institutional"]["points"],
                 " + ".join(comp["carried_by"])))
    print("\n%d API calls used.\n" % client.calls)
    return 0


def session_distribution():
    """The real per-session share distribution, from the tape."""
    import os
    import sqlite3

    import darkpool

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "confluence.db")
    if not os.path.isfile(path):
        print("no confluence.db yet -- run the desk for one scan first")
        return 1
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT MAX(scan_id) AS s FROM observations").fetchone()
    scan_id = row["s"]
    shares, partial_shares, per_ticker = [], [], {}
    for r in conn.execute(
            "SELECT ticker, dark_sessions FROM observations WHERE scan_id=?",
            (scan_id,)):
        try:
            sess = json.loads(r["dark_sessions"] or "[]")
        except ValueError:
            continue
        vals = [s["s"] for s in sess if not s.get("p")]
        for s in sess:
            (partial_shares if s.get("p") else shares).append(s["s"])
        if vals:
            per_ticker[r["ticker"]] = vals
    conn.close()

    if not shares:
        print("no session data recorded yet. This column was added after the\n"
              "first release -- run one more scan and try again.")
        return 1

    shares.sort()
    print("=" * 66)
    print("PER-SESSION DARK POOL SHARE OF THAT DAY'S VOLUME   (scan %s)" % scan_id)
    print("=" * 66)
    print("  %d complete sessions across %d tickers"
          % (len(shares), len(per_ticker)))
    for q in (0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99):
        print("    p%-3d  %6.2f%%" % (100 * q, 100 * _pct(shares, q)))
    print("    max   %6.2f%%" % (100 * shares[-1]))
    if partial_shares:
        partial_shares.sort()
        print("  (%d partial/current sessions excluded, median %.2f%% -- they "
              "read high\n   because the day's volume is still forming)"
              % (len(partial_shares), 100 * _pct(partial_shares, 0.5)))
    print()
    floor = darkpool.ACTIVE_SHARE_OF_DAY
    print("  CURRENT FLOOR %.1f%% -> %d of %d sessions active (%.0f%%)"
          % (100 * floor, sum(1 for s in shares if s >= floor), len(shares),
             100.0 * sum(1 for s in shares if s >= floor) / len(shares)))
    print("  A floor that marks nearly every session active, or nearly none,")
    print("  is not discriminating. Aim for roughly the top quartile.")
    print()
    for f in (0.02, 0.03, 0.04, 0.05, 0.07, 0.10):
        n = sum(1 for s in shares if s >= f)
        tick = sum(1 for v in per_ticker.values() if any(x >= f for x in v))
        print("    floor %4.1f%%  ->  %5d/%d sessions (%3.0f%%), "
              "%3d/%d tickers with at least one"
              % (100 * f, n, len(shares), 100.0 * n / len(shares),
                 tick, len(per_ticker)))
    return 0


def _pct(values, q):
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[idx]


if __name__ == "__main__":
    sys.exit(main())
