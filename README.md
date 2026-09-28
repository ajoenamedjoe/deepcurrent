# UW Desk Suite

**One dashboard plus six research desks built on the Unusual Whales API.**
Each desk asks a single question of the market, and the dashboard brings their answers together in one place.
Everything runs locally on Python's standard library, with no pip installs. Your API token never reaches the browser.

> Entry for **Unusual Whales Hackathon #1**. Not financial advice. See the note at the end.

<!-- screenshots: add docs/screenshots/*.png and link them here -->

---

## What's inside

| | Question it answers | Main UW endpoints |
|---|---|---|
| **UW Dashboard** (`UW Dashboard/`) | *What do I need to know today, and what did each desk find?* | `market/economic-calendar`, `earnings/premarket`, `earnings/afterhours`, `market/market-tide`, `market/sector-etfs`, `insider/transactions`, `option-trades/flow-alerts`, `stock/{t}/info`, `stock/{t}/ohlc` |
| **Valuation Desk** | *What is this company worth, and is it cheap?* Produces an 11-section report, a full or summary share card, and a standalone script that must re-run to the same verdict. | `stock/{t}/income-statements`, `balance-sheets`, `cash-flows`, `earnings`, `info`, `ohlc`, `insider/transactions`, `screener/stocks`, `etfs/{t}/holdings` |
| **Unusual Options Flow Desk** | *Who is lifting the offer on single-leg options right now?* Scores each flow 0–100, and **Show tape** opens the actual prints behind each alert. | `option-trades/flow-alerts`, `option-trades/flow-alerts/{id}`, `option-contract/{OSI}/flow`, `option-activity/unusual`, `screener/option-contracts` |
| **Confluence Desk** | *Where are insiders, 13F institutions and dark-pool size all buying the same company?* | `insider/transactions`, `darkpool/{t}`, `darkpool/recent`, `stock/{t}/ohlc`, plus the Institutional Desk's 13F results |
| **Institutional Desk** | *Which small, concentrated funds are adding to small, profitable companies?* Built from 13F filings. | `institutions`, `institution/{cik}/holdings`, `screener/stocks`, `stock/{t}/income-statements`, `balance-sheets` |
| **Swing Desk** | *Where do at least 3 of 5 independent signals agree on a swing setup?* Each alert has a **Share card** image. | `screener/stocks`, `option-trades/flow-alerts`, `darkpool/recent`, `market/market-tide`, `stock/{t}/gex-levels`, `stock/{t}/ohlc` |
| **Growth Leaders (O'Neil-style)** (`Growth Desk/`) | *Which growth stocks look like leaders, is the market in an uptrend, and is anything breaking out?* Seven checks (current and annual earnings, new highs, supply and demand, relative strength, institutional buying, market direction), a 0–100 score, bases, buy points and a stop. Inspired by William O'Neil's published method; not affiliated with Investor's Business Daily. | `screener/stocks`, `stock/{t}/earnings`, `institution/{t}/ownership`, `stock/{t}/ohlc` |

### Dashboard highlights

- **Today:** one page for the day: market direction (O'Neil-style, SPY and QQQ), the economic calendar, warnings on your holdings, your watchlist names, a sector heatmap, market tide, the top of each desk, earnings (your names first) and a journal you write on the page. A date strip shows any other day's calendar, earnings and note.
- **Invest / Trade / All:** one switch filters the menu to the long-term desks (Valuation, Confluence, Institutional, Watchlist, Portfolio) or the trading desks (Flow, Swing, Growth Leaders, P&L), and reorders Today and the Track Record to match.
- **Ticker Lookup:** one ticker across every desk: the Valuation verdict, the Confluence score, the flow card, the 13F funds and the Growth Leaders checks. It also shows **today's single-leg opening options flow** (spreads and rolls removed, grouped by contract, bullish vs bearish, with the reason each is likely opening), your position, your past trades, and a **position-size calculator** at the live price.
- **Track Record:** each desk's picks are recorded during market hours and scored against SPY 5, 20 and 60 sessions later. A pick still waiting for its result shows as *pending*, not zero. The picks table sorts by any column and filters by desk, ticker and direction.
- **Desks in one window:**
  - Every desk opens inside the dashboard.
  - A desk that isn't running is started, with no console window.
  - A desk whose code changes on disk is restarted after 20 quiet seconds.
- **How it works:** a plain-language page for every desk and dashboard page, with its scoring weights, bands and alert rules. The numbers are read live from each running desk, and a test checks the written fallbacks against each desk's source code.
- **Watchlist & theses:** holdings plus the names you'd like to own, each with a buy price from the Valuation desk's margin of safety, a written thesis and sell rules, checks that flag a broken thesis, smart-money activity in each name, and an automatic re-valuation the day after each earnings report.
- **What's new:** a dated changelog of every change to the dashboard and the desks (`UW Dashboard/CHANGELOG.md`), shown as a page with filters by area.
- **Your name on it:** the dashboard's name (set in Settings) appears on every desk page, share card, Word report and Discord post, with Unusual Whales credited as the data source.
- **Themes:** eight built-in looks, including Paper (the original, light and dark), Market Terminal and an 80s Cyberpunk theme with optional motion. One click restyles the dashboard, every desk and the share cards, and a test checks every theme for text contrast and distinguishable gain/loss colours.
- **Interactive P&L:** upload a broker CSV and get a green/red calendar of realised P&L.
  - Supported brokers: thinkorswim, Schwab, Fidelity, Robinhood, Webull, IBKR, tastytrade and Tradovate (futures, with point values per contract).
  - Lots are matched first-in, first-out (FIFO).
  - Re-uploading overlapping statements never double-counts.
- **Portfolio:** a published Google Sheet shown as a visual portfolio. Add warnings for earnings within 7 days, insider selling outside 10b5-1 plans, and heavy put buying.
- **Survives reboots:** everything is kept in SQLite, backed up nightly to a folder you pick (OneDrive by default), and an optional autostart brings it back after a restart.

---

## How it uses the Unusual Whales API, and the UW MCP

- **REST API.** The suite calls about 30 UW REST endpoints, listed in the table above.
  - Each desk has a small `uw.py` client with retries, rate-limit backoff and a single place for auth.
  - The browser only ever talks to `127.0.0.1`. The token stays in the desk's server process.
- **Built agent-first with the UW MCP server.** The suite was designed and written with an AI agent (Claude) connected to the **Unusual Whales MCP server**:
  - `get_public_api_docs` was used to confirm REST paths and parameters before code called them, rather than guessing endpoints.
  - The MCP data tools (`get_flow_alerts`, `get_insider_transactions`, `get_market_tide`, `get_market_events` and others) were used to look at real response shapes. The quirks those revealed are handled in code: numbers sent as strings, Form 144 notices that aren't sales, stale extended-hours quotes, and ADRs reported in foreign currency.
  - Those quirks became the edge cases in the test suites. The fixtures are **synthetic payloads in the same shape**, so the repo contains no UW data.

---

## Quick start (Windows)

1. Install **Python 3.10+** from python.org and tick **"Add python.exe to PATH"**.
2. Download this repo (green **Code** button → **Download ZIP**) and unzip it anywhere.
3. Double-click **`SETUP.bat`** and paste your Unusual Whales API token. It is saved to a private `.env` in each desk folder. A free-trial key works.
4. Double-click **`START_HERE.bat`**. The dashboard opens at **http://127.0.0.1:8700/**. Click any desk in the menu and it starts by itself.

**Mac / Linux:**

```bash
python3 setup.py
python3 "UW Dashboard/server.py"
```

Every desk can also run on its own. Use `START_HERE.bat` inside its folder, or `python3 server.py`.

| Program | Port |
|---|---|
| Dashboard | 8700 |
| Flow | 8733 |
| Confluence | 8770 |
| Institutional | 8777 |
| Growth Leaders | 8760 |
| Swing | 8787 |
| Valuation | 8790 |

Each desk's `.env.example` lists its optional settings, such as thresholds, Discord webhooks and ports.

### Discord alerts

Confluence, Swing and Growth Leaders can post alerts to Discord. **The suite ships with no webhooks**: nothing is posted until you paste your own webhook URL into that desk's `.env`.

- **Your own private channel only.** Posting Unusual Whales data to other people, in a public or paid server, may need a redistribution licence from Unusual Whales. Check their terms first.
- **Keep the webhook secret.** Anyone with the URL can post to your channel. `.env` is git-ignored, and the repo guard fails if a webhook appears in any committed file.

## Tests

Double-click **`RUN_ALL_TESTS.bat`**, or run `python3 run_all_tests.py`. No token or network connection is needed.

| Suite | Tests |
|---|---|
| Dashboard | 196 |
| Valuation | 101 |
| Confluence | 86 |
| Institutional | 43 |
| Flow | 119 |
| Swing model checks | all |
| Growth Leaders | 45 |
| Repo guard | 15 |

The repo guard fails if a token-shaped string, a webhook, a private sheet link or a personal path appears in any file.

## Privacy and safety

- **No secrets in this repo.** Tokens live only in each desk's `.env`, which `.gitignore` excludes. `SETUP.bat` writes those files from the committed `.env.example` templates.
- **No Unusual Whales data in this repo.** All test fixtures are synthetic, with invented tickers such as ACME, ZENO and QRTX. Databases, logs and generated reports stay on your machine and are git-ignored.
- **Local only.** Every server binds to `127.0.0.1`, checks the Host header (no DNS rebinding), accepts changes only as same-origin JSON, won't let other sites drive its API or frame its pages, and escapes all Unusual Whales text before showing it. `test_security.py` replays each attack from a security review.

## Project layout

```
UW Dashboard/        dashboard server (server.py), page (index.html + app.js), SQLite store, tests
Valuation Desk/      valuation.py model, report + card generators, tests
UW Flow Desk/        flow.py scoring, flowscan.py screener, board page, tests
Confluence Desk/     score.py, insider.py, darkpool.py, idb.py (13F), tests
Institutional Desk/  scan.py 13F screen, tests
Swing Desk/          five-signal model in server.py, mock UW server for offline runs, tests
Growth Desk/         model.py (seven checks, bases, market direction), scan.py daily screen, tests
setup.py / SETUP.bat          one-time token setup
START_HERE.bat                start the dashboard
run_all_tests.py / RUN_ALL_TESTS.bat
```

---

**Not financial advice.** This is a research tool. Scores, verdicts and warnings are heuristics built on public market data and can be wrong. Do your own research.

**Rights.** © 2026 the author. All rights reserved. The code is shared publicly for Unusual Whales Hackathon #1 judging, under the hackathon's official rules. Unusual Whales® is a trademark of its owner, and this project is not affiliated with or endorsed by Unusual Whales.
