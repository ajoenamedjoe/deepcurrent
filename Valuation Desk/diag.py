"""
Valuation Desk -- diagnostic. Double-click DIAG.bat or run `python diag.py IONQ`.

Checks, against the LIVE API, every assumption the model makes about the four
endpoints: that each answers, that the fields the model reads exist, that both
report types arrive from one call, where the price comes from, and whether the
statements are in USD. Also served at /api/diag?ticker=X.

Written because neither build shell could reach api.unusualwhales.com: every
payload shape in tests/fixtures came through the MCP tools, and REST is a
different surface (envelopes, limits and parameters differ). This is the first
thing to run on the PC.
"""

import sys

import uw
import valuation as V

NEED = {
    "income-statements": ["fiscal_date_ending", "report_type", "reported_currency", "total_revenue",
                          "operating_income", "net_income", "depreciation_and_amortization"],
    "balance-sheets": ["fiscal_date_ending", "report_type", "cash_and_cash_equivalents",
                       "short_term_investments", "short_long_term_debt_total", "total_shareholder_equity"],
    "cash-flows": ["fiscal_date_ending", "report_type", "operating_cashflow", "capital_expenditures"],
}


def run_checks(client, ticker="AAPL"):
    out = []

    def add(level, text):
        out.append({"level": level, "text": text})

    t = "".join(ch for ch in ticker.upper() if ch.isalnum() or ch in ".-")[:12]
    try:
        info = client.info(t)
        inner = V.info_of(info)
        if inner.get("symbol"):
            add("ok", "/info %s -- %s, sector %s, outstanding %s, beta %s" % (
                t, inner.get("full_name"), inner.get("sector"), inner.get("outstanding"), inner.get("beta")))
        else:
            add("bad", "/info %s returned no symbol: keys %s" % (t, sorted(info)[:12]))
        price, src = uw.pick_price(("info", info), ("info", inner))
        add("ok" if price else "warn", "price on /info: %s" % (("%s via %s" % (price, src)) if price else
            "none -- the desk falls back to the screener, then daily candles"))
    except Exception as exc:
        add("bad", "/info %s -- %s" % (t, exc))
        return {"ticker": t, "checks": out, "calls": client.calls}

    for name, fn in (("income-statements", client.income_statements),
                     ("balance-sheets", client.balance_sheets), ("cash-flows", client.cash_flows)):
        try:
            payload = fn(t)
            rows = V.rows_of(payload)
            env = [k for k in ("data", "result", "results") if isinstance(payload, dict) and k in payload]
            kinds = sorted({r.get("report_type") for r in rows})
            missing = [f for f in NEED[name] if rows and f not in rows[0]]
            cur = sorted({r.get("reported_currency") for r in rows if r.get("reported_currency")})
            level = "ok" if rows and not missing and {"annual", "quarterly"} <= set(kinds) else "warn"
            add(level, "/%s -- %d rows, envelope %s, report types %s%s%s" % (
                name, len(rows), env or "bare", kinds,
                (", MISSING %s" % missing) if missing else "",
                (", currency %s" % cur) if cur else ""))
        except Exception as exc:
            add("bad", "/%s -- %s" % (name, exc))

    # model 3.2 endpoints: insiders, earnings, ETF holdings (screener universe)
    import datetime as _dt
    try:
        ins = client.insider_trades(t, (_dt.date.today() - _dt.timedelta(days=730)).isoformat())
        rows = V.rows_of(ins)
        need = ["transaction_code", "amount", "price", "is_10b5_1", "shares_owned_after", "ids"]
        miss = [f for f in need if rows and f not in rows[0]]
        codes = sorted({r.get("transaction_code") for r in rows})
        add("ok" if not miss else "warn", "/insider/transactions -- %d rows, codes %s%s" % (
            len(rows), codes, (", MISSING %s" % miss) if miss else ""))
    except Exception as exc:
        add("warn", "/insider/transactions -- %s (insider component will be unmeasured)" % exc)
    try:
        rows = V.rows_of(client.earnings(t))
        rep = [r for r in rows if r.get("reported_eps") not in (None, "")]
        add("ok" if rep else "warn", "/earnings -- %d rows, %d reported%s" % (
            len(rows), len(rep), "" if rows and "surprise_percentage" in rows[0] else ", no surprise_percentage field"))
    except Exception as exc:
        add("warn", "/earnings -- %s (predictability will be unmeasured)" % exc)
    try:
        import screen
        raw = client.get("/api/etfs/SPY/holdings")
        rows = V.rows_of(raw)
        tick = screen.etf_tickers(client, "SPY")
        add("ok" if len(tick) > 400 else "warn",
            "/etfs/SPY/holdings -- %d rows, %d tickers parsed; first row keys %s" % (
                len(rows), len(tick), sorted(rows[0])[:12] if rows else []))
    except Exception as exc:
        add("warn", "/etfs/SPY/holdings -- %s (ETF screens unavailable; paste tickers instead)" % exc)

    try:
        payloads, meta = uw.fetch_all(client, t)
        res = V.run(payloads, fx=uw.fx_table())
        add("ok", "full model run: %s, %s, %d/10 %s, base %s vs %s (price via %s)" % (
            res["class"], res["fin"]["basis"], res["score"]["badge"], res["score"]["verdict"],
            V.px(res["valuation"]["scenarios"]["base"]["per_share"]), V.px(res["fin"]["price"]),
            meta["price_source"]))
        q_ = res.get("quality") or {}
        add("ok", "quality %s (%s of 100 points measured); gates: %s" % (
            q_.get("score"), q_.get("measured"),
            ", ".join("%s %s" % (g["key"], "pass" if g["passed"] else "FAIL") for g in res["score"]["gates"])))
        for q in res["fin"]["quality"]:
            add("warn", "data quality: %s" % q)
    except Exception as exc:
        add("bad", "full model run -- %s" % exc)
    return {"ticker": t, "checks": out, "calls": client.calls}


if __name__ == "__main__":
    tk = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    try:
        c = uw.Client()
    except RuntimeError as e:
        print("BAD  %s" % e)
        sys.exit(2)
    r = run_checks(c, tk)
    for ch in r["checks"]:
        print("%-5s %s" % (ch["level"].upper(), ch["text"]))
    print("\n%d API calls." % r["calls"])
    if sys.stdin and sys.stdin.isatty():
        input("\nPress Enter to close.")
