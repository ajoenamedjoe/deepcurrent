# Institutional Desk

A quarterly 13F alert board on the Unusual Whales API, built to one rule:
**small, concentrated funds buying small, genuinely profitable companies.**

Sits next to Swing Desk and shares its API token. Python stdlib only, no pip
installs, token never leaves your PC.

    Double-click START_HERE.bat  ->  http://127.0.0.1:8777

Do not open `index.html` directly; it needs the local server for data.

**The first launch starts a scan on its own** — there is nothing in the
database yet and it takes 15–25 minutes. Progress shows in the header; you can
close the tab and come back, and the scan survives being interrupted. After
that, refreshing is a deliberate click, and 13F only updates once a quarter.

---

## What it filters

**The funds.** All ~8,900 13F filers are loaded, then cut to those holding
$50M–$2.5B with **no more than 75 positions**. Everything with pages of
holdings — Vanguard, BlackRock, the big multi-strats — is gone. By default
only hedge funds and UW-tagged firms survive; flip `IDESK_REQUIRE_HEDGE_FUND`
to `false` to include the wealth-advisor crowd. On the September 2026 run
that left **roughly 600 funds** out of ~8,900.

**The companies.** A name only reaches the board if it clears all three:

| Gate | Default |
|---|---|
| Market cap | ≤ $10B |
| Total assets (latest annual balance sheet) | ≤ $10B |
| Net income positive in each of the last N audited fiscal years | N = 3 |

The asset cap does most of the work of excluding banks and insurers, which is
intentional — their balance sheets are their business model, not their size.

**The events.** Position changes above $1M, classified as:

| Kind | Meaning |
|---|---|
| `NEW` | `first_buy` equals this report date — a first-ever purchase |
| `ADD` | Existing position increased 25% or more |
| `TRIM` | Cut by half or more, still held |
| `EXIT` | Fully closed |

`NEW` and `ADD` drive the ranking. `TRIM` and `EXIT` appear as a warning line
on the card — they are context, not a signal to short.

---

## The score

0–100 per event, with every component stored in `events.score_parts` so the
number is auditable:

| Component | Max | Why |
|---|---|---|
| Base (NEW 35 / ADD 20) | 35 | A first purchase says more than a top-up |
| Position weight in the fund | 25 | 8%+ of the book is a real bet |
| Fund concentration | 12 | A 12-name fund is louder than a 70-name one |
| 8-quarter trajectory | 12 | Multi-quarter accumulation beats a one-off |
| Cluster (other qualifying funds in the name) | 20 | The strongest signal in 13F data |
| Earnings growth | 10 | Profitable *and* improving |
| Price vs the fund's average cost | 8 | Still buyable near where they got in |

**These weights are reasoned, not fitted.** Nothing here has been backtested.
Every event is written to SQLite each quarter, which is what eventually makes
fitting them honest rather than a guess.

---

## Timing

13F is quarterly and filed up to **45 days after quarter end**, so positions
shown are as of the report date, not today. The header shows when the next
wave is expected. The `now vs cost` column compares the current price against
the fund's average entry — that is the part that stays actionable between
filings.

---

## Cost of a scan

| Stage | Calls | Notes |
|---|---|---|
| Filer list | ~20 | Whole 13F universe |
| Holdings | 1 per qualifying fund (~1,000) | A full 500-row page means the fund is oversized and gets culled |
| Financials | 2 per shortlisted ticker (~950) | Cached 75 days, so later scans skip almost all of these |
| Scoring | 0 | Pure SQL |

First run: roughly **3,000 calls, 15–25 minutes**. Later runs in the same
quarter are mostly cached and finish in a few minutes. Interrupting a scan is
safe — everything lands in SQLite as it goes and the next run resumes.

Five worker threads share one global rate limiter, so the pool never exceeds
the API's ceiling. Raise `IDESK_MIN_INTERVAL` if you ever see 429s.

---

## Files

| File | Purpose |
|---|---|
| `uw.py` | API client — token loading, throttle, retries, numeric coercion |
| `store.py` | SQLite schema and upserts (`institutional.db`) |
| `scan.py` | The four-stage pipeline and all the scoring logic |
| `server.py` | Local HTTP server and JSON endpoints |
| `index.html` | The dashboard |
| `tests/test_logic.py` | 41 unit tests over the classifier, scorer and screen |

Run the tests with `py -3 tests\test_logic.py`.

---

## Gotchas found the hard way

- `reported_currency` comes back as the literal string `"None"` for both plain
  US companies *and* foreign-currency filers. Currency alone cannot be trusted,
  so a missing currency is cross-checked against market cap: revenue more than
  100× market cap is not dollars.
- The fundamentals **screener** endpoints mix quarterly rows with annual ones
  and offer no currency filter, which is why this reads per-ticker statements
  instead. A foreign filer's net income in the trillions of a low-value
  currency clears any dollar threshold you set.
- `close` in a holdings row is the *current* price; the report-date price is
  `value / units`. Both are needed — one for what the fund paid, one for
  whether it is still worth chasing.
- Market cap comes free from `shares_outstanding × close` in the holdings
  payload. No screener call needed.
- For a position opened *this* quarter, `avg_price` is the same value for
  every fund that bought it — the API is giving the quarter's average price,
  not each fund's real fill. So on a `NEW` row, read *now vs cost* as "against
  where the stock traded last quarter", not "against what they actually paid".
  On older positions it is a genuine blended cost basis.
- SQLite WAL mode fails with "disk I/O error" on network shares; the store
  falls back to a rollback journal automatically.
