# UW Valuation Desk

Type a ticker, get the three deliverables from the workflow brief, every time in
the same format:

| file | what |
|---|---|
| `TICKER_Valuation_Analysis.docx` | 11-section Word report (summary, metrics, quality, FCF, balance sheet, multiples, DCF, risks, recommendation, watch list, disclaimer) |
| `TICKER_Summary_Card.png` | 1080-wide share card in the Confluence Desk style, saved automatically (dark); light variant + copy/download in the page |
| `TICKER_Short_Card.png` | The **Summary** card: ticker, verdict and score, price vs base value, upside, quality, the bull/base/bear strip and the one-line summary. About 40% of the full card's height. Saved automatically (dark); pick Full or Summary in the page to copy or download either |
| `TICKER_analysis.py` | standalone script: the model + the raw payloads embedded. `python TICKER_analysis.py --discount 0.09` re-runs it anywhere |
| `TICKER_console.txt` | the script's output -- the desk EXECUTES the script and refuses the run if it does not reproduce the verdict |
| `TICKER_data.json` | raw Unusual Whales payloads, for audit |

Each run lands in `reports\TICKER\YYYY-MM-DD_HHMMSS\`. The History panel keeps
every run (price, base value, bear-bull range, score) and charts price vs base
value once a ticker has two runs.

## Start

Double-click `START_HERE.bat` (opens http://127.0.0.1:8790). The token is read
from a sibling desk's `.env` (Swing / Institutional / Confluence...), or put one
here. `DIAG.bat` probes the live API; `RUN_TESTS.bat` runs the suite.

## The model (valuation.py)

1. **Data**: `/info`, `/income-statements`, `/balance-sheets`, `/cash-flows` (REST returns annual and
   quarterly in one call). TTM from the last four quarters when newer than the last annual.
2. **Class** (first match wins): financial -> venture -> high-growth burner -> turnaround ->
   utility -> mature -> profitable FCF. Each has its discount band (6-8% mature/utility, 8-12% growth).
3. **One engine for every class** (model 3.0): a **10-year projection** -- 5 years at the scenario
   growth rate, then 5 years fading in a straight line to the terminal rate (the fade starts from at
   most 25%). **Growth has to be paid for**: each extra $1 of revenue needs $1 / sales-to-capital of
   new investment (revenue / operating capital excluding goodwill, clamped 0.8-5.0; banks use ROE),
   and growth is never allowed to subtract value. Cash margins are owner earnings (OCF - maintenance
   capex) minus stock comp; loss-makers ramp their cash margin to a target and are not charged twice.
   Gordon terminal value at year 10, + net cash (0 for banks). Discount = CAPM (4.25% + beta x 5%)
   clamped into the class band. Bull >= base >= bear by construction.
4. **Score 0-10, higher is better** (scale 2.0): `5 + 5 x base upside` (+100% -> 10, fair -> 5,
   -100% -> 0), minus red-flag points (only ever SUBTRACT, capped at 2). No scenario reaches the
   price -> at most 3; even the bear case clears it -> at least 6. Bands: 8-10 strong buy,
   6-7 buy, 4-5 hold, 2-3 reduce, 0-1 avoid; 6-10 green, 4-5 yellow, 0-3 red.
   Runs made before scale 2.0 are converted automatically on first start (the database is
   backed up to `valuation.db.before-scale-2.bak`). Word reports and PNG cards already on disk
   from those runs still show the old numbers -- re-run a ticker to refresh them.
5. **Overrides**: Discount % and Growth % in the header re-run with your numbers; every
   deliverable says OVERRIDE.

Foreign reporters (TSM reports in TWD) need `VDESK_FX_TWD=...` in `.env`, or the desk refuses.
Educational use only. Not financial advice.

## Sharing this desk

Double-click `MAKE_SHARE_ZIP.bat`. It builds `Valuation_Desk_share_<date>.zip` next to this folder
from an allow-list of code files only (never `.env`, `reports\`, `valuation.db*`), and refuses to
build if any file contains your API token, anything token-shaped, or your Windows username.
The recipient copies `.env.example` to `.env` and adds their own UW token.

## Model 3.2 -- the Buffett layer

- **Quality & management score (0-10)** next to the value score: return on capital (25),
  cash-flow consistency (10), share count / buybacks vs dilution (15), acquisition
  discipline (10), debt (10), earnings quality (10), insider behaviour (10), earnings
  predictability (10). Anything with no data is shown as "n/m" and left out -- never scored zero.
- **Buffett gates** (they can only lower a score, and every run says which passed):
  BUY or better needs a **25% margin of safety** (price at most 75% of base value) and
  quality 4+; STRONG BUY needs quality 6+. "Why this verdict" spells it out.
- **What a 5-10 year holder earns**: the annual return if the price reaches the base value
  by year 5 or 10, the return if it never re-rates, and a **reverse DCF** (the growth
  today's price assumes).
- **Screener**: paste tickers, use your history, or scan an ETF's holdings (SPY = S&P 500).
  About 2 seconds per ticker in the background; resumes after a restart. Ranked
  cheap + good first. Click a row for the full report.
- A run now makes ~6 API calls (adds insider filings and earnings history).
