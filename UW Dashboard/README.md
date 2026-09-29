# UW Dashboard

One page that opens every desk, today's events, your portfolio sheet and an
interactive P&L calendar.

**Start it:** double-click `START_HERE.bat`. The page opens at
http://127.0.0.1:8700/. Do not open `index.html` directly.

**Start with Windows:** run `INSTALL_AUTOSTART.bat` once. After a reboot the
dashboard (and any desk ticked "start with the dashboard" in Settings) comes
back by itself. `REMOVE_AUTOSTART.bat` undoes it.

## What's in the menu

| Item | What it does |
|---|---|
| Today | The day on one page: market direction, the full economic calendar, warnings on your holdings, your watchlist names, sector heatmap + market tide, the top of each desk, earnings (your names first, the largest 12 others, Show all for the rest) and the day's journal note, written right on the page. The date strip (‹ › and a date box) shows the calendar, earnings and note for any other day. Sections fold and stay folded. Opens itself once on weekday mornings (4:00-9:30 ET). Replaced Morning + Today's Events in r17. |
| Ticker Lookup | One ticker, every desk's view (Valuation verdict, Confluence score, Flow card, 13F funds), your position, your past trades and journal mentions, the **position size calculator** at the live price, and **today's single-leg opening flow** (flow alerts with spreads and rolls removed, kept only when size or volume beat open interest; grouped by contract, bullish vs bearish). Built in `openflow.py`. |
| Track Record | Each running desk's top names are recorded during market hours and scored 5 / 20 / 60 sessions later, against SPY. Pending is pending, never zero. The Picks table lists every pick: click any column header to sort (again to reverse), and filter by desk, ticker and bullish/bearish. The choice is remembered. |
| Desks | Valuation, Unusual Options Flow, Confluence, Institutional, Swing, Growth Leaders (O'Neil-style). Clicking one opens it inside the dashboard. If it isn't running the dashboard starts it. |
| Invest / Trade / All | The switch at the top of the menu. Invest shows the valuation, fund and insider desks, Watchlist and Portfolio; Trade shows Flow, Swing, Growth Leaders and P&L; Morning and the Track Record lead with the chosen side. |
| Portfolio | Your published Google Sheet, as the sheet itself or as a sortable table. |
| Interactive P&L | Upload a broker CSV; get a green/red calendar of realised P&L. Click a day to see the trades you closed. |
| How it works | One page explaining every desk and dashboard page in plain language, with its scoring weights, bands, gates and alert rules. Numbers are read **live** from each running desk (`/api/method`, `/api/flow`, `/api/health`, `/api/state`) and fall back to the written values when a desk is off. Confluence doesn't publish its weights, so it always shows the written ones. `tests/test_guide.py` checks the written values against each desk's source whenever the desk folders sit next to the dashboard. |
| Market direction | The sidebar line under the clock and a Morning panel: SPY and QQQ in a confirmed uptrend, under pressure, or in correction (O'Neil-style, from the Growth Leaders desk). |
| Watchlist | Your holdings plus the names you'd like to own. Each has a buy price (intrinsic value less the margin of safety, or yours), a written thesis and sell rules, and three checks that flag a broken thesis: price vs value, business got worse since the thesis, smart money leaving. Shows insider, 13F and desk activity in each name, and re-values names on the Valuation desk the day after they report. Click any name anywhere (Watchlist, Lookup, Portfolio) to open it. Built in `watchlist.py`. |
| What's new | Every change to the dashboard and the desks, newest first, filterable by area, with click-to-expand details and what to do after updating. Built from `CHANGELOG.md`; a dot on the menu item marks releases you haven't seen. **Each release adds its entry at the top and bumps `RELEASE` in server.py** (tests/test_changelog.py fails otherwise). |
| Settings → Appearance | Eight built-in themes that restyle the dashboard, every desk and the share cards in one click: **Paper** (the original, light + dark), **Carbon** and **Colorblind-safe** (light + dark), **Newsprint** (light), **Terminal**, **Midnight**, **Market Terminal** and **Cyberpunk** (dark; Cyberpunk has an optional slow-motion setting that respects Windows' reduced-motion). Built in `themes.py`; desks pick the theme up from `http://127.0.0.1:8700/theme.js` and fall back to their own look when the dashboard is off. Fonts are bundled in `fonts/` (SIL Open Font License), nothing is downloaded. |
| Settings | Reorder/rename the menu, change the logo, add your own links, desk folders, theme, clock. |

## Desks

* Each desk still runs as its own program on its own port (Valuation 8790,
  Confluence 8770, Institutional 8777; the Flow Desk's port is read from its
  `server.py`). The dashboard shows the desk's own page, so whatever the desk
  serves is what you see.
* **Page changed on disk** -> the dashboard reloads it by itself.
* **Code changed on disk** (`.py` or `.env`) -> the desk has to restart to load
  it. By default the dashboard restarts it by itself once the files have been
  quiet for 20 seconds; turn that off in Settings to get a "Restart" button
  instead.
* Desks started by the dashboard run without a console window; their output
  goes to `logs\<desk>.log`, and the last lines show on the page if a desk
  fails to start. They keep running when the dashboard closes -- stop them
  from the desk's page.
* Each desk's folder (including the Flow Desk's) is configurable in
  Settings -> Desks.

## Interactive P&L

Recognised automatically: thinkorswim Account Statement, Schwab, Fidelity,
Robinhood, Webull, Interactive Brokers (Activity Statement or Flex), tastytrade, Tradovate (Orders, Fills or Performance export; futures point values from the product, or from Tradovate's notional value).
Anything else: the page asks you to match its columns once.

* FIFO lots per account; P&L after fees, on the day the position was closed.
* A close whose opening trade is older than the upload is **listed but not
  counted** (its cost is unknown) -- the day gets a `!`. Upload an older
  statement to include it.
* Options left open past expiry are closed at $0 on the expiry date and
  marked "expired worthless (inferred)".
* Re-uploading an overlapping statement never double-counts.

## Position size calculator (Ticker Lookup)

Account = the starting capital in Settings -> Portfolio. Enter risk % of the account, max
position % of the account, stop % from entry, long or short; entry is the live price (edit it
for a limit entry). Shares = the smaller of risk budget / risk per share and max position /
entry, rounded DOWN; the page says which limit set it, shows the stop, $ at risk and 1R/2R/3R
targets, and warns if the position is bigger than the cash left in your sheet.

## Warnings on your holdings

For every open position in the sheet: earnings within 7 days; insiders selling more than
$500k outside 10b5-1 plans in 30 days (Form 144 notices and plan sales don't count); more
than $250k of puts bought on the ask in 3 sessions and at least 1.5x the calls. Shown on
Morning and Portfolio, with a count on the Portfolio menu item. "What was
measured" shows the raw numbers even when nothing is flagged.

## Backups and restarts

Every night (after 2am, or when the PC next starts) `dashboard.db` is backed up with SQLite's own
backup API and integrity-checked. **Settings -> Backups** chooses where:

* **Browse...** opens a folder picker on this PC (it can appear behind the browser), or type/paste a
  full path and press Save. The folder is created if needed and tested for write access first.
* **Use default** = `OneDrive\UW Dashboard Backups` (or `backups` in this folder if OneDrive isn't set up).
* "Copy my existing backups to the new folder" brings the old nightly files along (never overwriting).
* **Keep the last** 3-365 backups (default 30); older ones in that folder are deleted.
* **Open backup folder** and **Back up now**.

Settings -> System health shows the last backup, each desk's status and last log line, and
"Restart dashboard" (desks keep running). To restore: close the dashboard, copy a backup over
`dashboard.db`, run START_HERE.

## Where things are kept

Everything (settings, logo, menu, uploads) is in `dashboard.db` in this
folder, so a closed tab or a reboot comes back as it was. The browser
remembers which page you were on.

The UW token is read from a sibling desk's `.env` (Valuation, Confluence,
Swing, Institutional or the Flow Desk). The page never sees it.

## Files

`server.py` (HTTP + routes), `desks.py` (start / check / restart desks),
`events.py` (UW calendar + earnings), `portfolio.py` (Google Sheet),
`pnl.py` (broker CSVs + FIFO), `store.py` (SQLite), `guide.py` (the How it works page), `changelog.py` + `CHANGELOG.md` (What's new), `watchlist.py` (Watchlist), `themes.py` + `fonts/` (Appearance), `index.html` + `app.js`
(the page). `RUN_TESTS.bat` runs the tests. `MAKE_SHARE_ZIP.bat` builds a
copy without your token, database or logs.

## Security (review of 2026-09-26)

Everything listens on 127.0.0.1 only. On top of that:

* **Only the dashboard's own page can change anything.** Every change must be JSON from exactly
  `http://127.0.0.1:8700` (or `localhost:8700`); a page on another port, a form on another site, or a
  `text/plain` request is refused. Another site's `<img>`/link can't make the dashboard spend UW calls.
* **No other site can frame the dashboard** (`X-Frame-Options: DENY`). The desks may only be framed by the dashboard.
* **Desk and backup folders can't be network shares** (`\\server\share`), so the dashboard never runs a desk from one.
* **The portfolio link must be an `https://` address on the internet** — never `file://`, this PC or your network.
* The desks got the same guard (Host check against DNS rebinding, same-site API only, JSON-only changes, framing
  only by the dashboard), and every place UW text reaches a page is escaped.

`tests/test_smoke.py` replays each attack from the review; the repo-level `test_security.py` does the same for every desk.
