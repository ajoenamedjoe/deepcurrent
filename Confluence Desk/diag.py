"""
Confluence Desk -- diagnostics.

    py -3 diag.py

Checks, in the order they fail in practice:
  1. the token resolves, and from WHERE
  2. every endpoint this desk calls answers with the shape it expects
  3. the 13F backdrop file is present, readable and how stale
  4. the Discord webhook is configured (does not post -- use --discord)
  5. the local database opens (the WAL fallback path)

Run this first whenever the board looks wrong. It is faster than reading the
scan log and it names the failing layer instead of the symptom.
"""

import os
import sys

import uw


def ok(msg):
    print("  [ok]   " + msg)


def bad(msg):
    print("  [FAIL] " + msg)


def warn(msg):
    print("  [warn] " + msg)


def main():
    failures = 0
    print("\nConfluence Desk -- diagnostics")
    print("running in %s\n" % os.path.dirname(os.path.abspath(__file__)))

    # ------------------------------------------------------------- 1 token
    print("1. Configuration")
    token = uw.env_str("UW_API_TOKEN")
    if not token:
        bad("no UW_API_TOKEN anywhere. Checked this folder and every sibling desk.")
        return 1
    ok("token found, %d chars, ends ...%s" % (len(token), token[-4:]))
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    for name in ("Swing Desk", "UW SwingDesk", "UW Swing Desk",
                 "Institutional Desk", "UW Insider Desk", "GEX ES Desk"):
        path = os.path.join(parent, name, ".env")
        if os.path.isfile(path):
            ok("reading config from sibling: %s" % path)
    if os.path.isfile(os.path.join(here, ".env")):
        ok("reading config from this folder's .env (overrides siblings)")

    # ---------------------------------------------------------- 2 endpoints
    print("\n2. Unusual Whales endpoints")
    client = uw.Client()

    import datetime as dt
    start = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    try:
        payload = client.insider_purchases(start, limit=5)
        rows = uw.unwrap(payload)
        if rows:
            row = rows[0]
            missing = [f for f in ("ticker", "amount", "price", "stock_price",
                                   "shares_owned_before", "security_ad_code",
                                   "officer_title", "marketcap", "reporter_cik")
                       if f not in row]
            if missing:
                warn("/api/insider/transactions is missing fields: %s" % missing)
            else:
                ok("/api/insider/transactions -- %d rows, all expected fields "
                   "present (newest %s %s)"
                   % (len(rows), row.get("ticker"), row.get("transaction_date")))
            probe = row.get("ticker")
        else:
            warn("/api/insider/transactions answered but returned no purchases "
                 "in the last 7 days. Possible on a quiet week.")
            probe = "AAPL"
    except RuntimeError as exc:
        bad("/api/insider/transactions -- %s" % exc)
        failures += 1
        probe = "AAPL"

    try:
        prints = client.darkpool(probe, limit=20)
        if prints:
            row = prints[0]
            missing = [f for f in ("size", "premium", "price", "avg30_volume",
                                   "nbbo_bid", "nbbo_ask", "executed_at",
                                   "ext_hour_sold_codes", "sale_cond_codes")
                       if f not in row]
            if missing:
                warn("/api/darkpool/%s is missing fields: %s" % (probe, missing))
            else:
                import darkpool
                days = {darkpool._session_date(r) for r in prints}
                days.discard(None)
                ok("/api/darkpool/%s -- %d prints across %d session(s), "
                   "avg30_volume present"
                   % (probe, len(prints), len(days)))
                clean = darkpool.clean_prints(prints)
                ok("  %d of %d prints have a quote clean enough to infer a "
                   "direction from" % (len(clean), len(prints)))
        else:
            warn("/api/darkpool/%s answered with no prints above the premium "
                 "floor. Normal for a thin name." % probe)
    except RuntimeError as exc:
        bad("/api/darkpool/%s -- %s" % (probe, exc))
        failures += 1

    print("  %d API calls, %d errors" % (client.calls, client.errors))

    # ------------------------------------------------------------ 3 13F db
    print("\n3. 13F backdrop")
    import idb
    inst = idb.Institutional()
    cov = inst.coverage()
    if cov["available"]:
        ok("institutional.db at %s" % cov["path"])
        ok("quarter ended %s, filed around %s (%s days ago)"
           % (cov["report_date"], cov["filed_around"], cov["age_days"]))
        ok("%d tickers with bullish 13F activity, from %d funds"
           % (cov["tickers"], cov["funds"]))
        if (cov["age_days"] or 0) > 135:
            warn("that quarter is more than 135 days old -- a newer 13F wave "
                 "has probably landed. Re-run the Institutional Desk scan.")
    else:
        warn("no 13F backdrop: %s" % cov["error"])
        warn("the board still works. Cards cap at 75 of 100 without this leg.")

    # ----------------------------------------------------------- 4 discord
    print("\n4. Discord")
    hook = uw.env_str("DISCORD_WEBHOOK_URL").strip()
    if hook:
        ok("webhook configured (%s...)" % hook[:38])
        if "--discord" in sys.argv:
            import store as store_mod
            import notify
            store = store_mod.Store(os.path.join(here, "confluence.db"))
            sent = notify.Notifier(store, log=print).test()
            (ok if sent else bad)("test post %s" % ("delivered" if sent else "FAILED"))
            failures += 0 if sent else 1
        else:
            print("       (pass --discord to send a real test post)")
    else:
        warn("no DISCORD_WEBHOOK_URL -- the board runs, nothing gets posted")

    # ---------------------------------------------------------- 5 database
    print("\n5. Local database")
    try:
        import store as store_mod
        store = store_mod.Store(os.path.join(here, "confluence.db"))
        with store.connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        ok("confluence.db opens, journal mode %s" % mode)
        if mode.lower() != "wal":
            warn("not WAL -- expected on a network or mounted path, harmless")
        ok("%d distinct day(s) of observations recorded so far"
           % store.distinct_days())
        best = store.best_ever()
        if best is not None:
            ok("highest score recorded so far: %.1f" % best)
    except Exception as exc:
        bad("confluence.db -- %s: %s" % (type(exc).__name__, exc))
        failures += 1

    print("\n%s\n" % ("ALL GREEN" if failures == 0
                      else "%d CHECK(S) FAILED" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
