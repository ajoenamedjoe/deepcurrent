# Changelog

What changed in the UW Dashboard and the desks, newest first. The dashboard's **What's new** page is
built from this file.

Format (the page parses it, and a test checks it):

    ## <date> · <release id> · <title>
    Tags: <areas, comma separated>
    Do: <anything you need to do after updating>          (optional)
    - **<one-line headline>** — <details>

---

## 2026-09-28 · r22 · Discord alerts: private channel notice
Tags: Dashboard, Confluence, Swing, Growth
- **How it works has a Discord alerts section.** — Which desks post, where each webhook goes, and that alerts are for your own private channel: posting Unusual Whales data to other people may need a redistribution licence from Unusual Whales.
- **The same notice is in each desk's .env.example and README.** — The suite ships with no webhooks, and a repo test now fails if any .env.example ships one.

## 2026-09-28 · r21 · Security check
Tags: Dashboard, Valuation, Growth
Do: Settings → System health → Restart dashboard, then restart the Valuation and Growth Leaders desks.
- **The desk theme is for your own pages only.** — The theme hook now carries the dashboard's name, so another website can no longer load it or read it. Desks opened as 127.0.0.1 or localhost are unaffected.
- **Ticker Lookup refuses tickers that aren't tickers.** — Input like ".." is rejected before any Unusual Whales call.
- **Valuation and Growth Leaders refuse a bad request length.** — A malformed or negative Content-Length is answered at once instead of holding a connection open.
- **Growth Leaders scores are escaped on the page** like every other value.

## 2026-09-28 · r20 · Ticker Lookup layout
Tags: Dashboard
Do: Refresh the page (Ctrl+R).
- **Desk cards on one row.** — Valuation, Confluence, Flow, Institutional and Growth Leaders sit side by side across the top (they wrap on a narrow screen).
- **Opening flow next to Position size.** — The two panels share the next row; your history and the tape follow underneath.

## 2026-09-28 · r19 · Ticker Lookup shows today's single-leg opening flow
Tags: Dashboard, Flow
Do: Settings → System health → Restart dashboard, then look up any ticker.
- **Single-leg opening flow on every lookup.** — Today's Unusual Whales flow alerts for the ticker, spreads and rolls left out, grouped by contract: premium, contracts, bought or sold, bullish or bearish, and a one-line total (how much leans bullish vs bearish).
- **"Opening" is shown as likely, with the reason.** — Size bigger than yesterday's open interest, volume above open interest, or every print marked opening by Unusual Whales. Flow that could be someone closing is left out and counted underneath. Tomorrow's open interest confirms it.
- **Click a contract to see its prints** (needs the Flow desk running).
- **Ticker Lookup fits a phone screen.** — The desk cards no longer run off the right edge.

## 2026-09-28 · r18 · Tradovate in the Interactive P&L
Tags: Dashboard
Do: Settings → System health → Restart dashboard, then upload a Tradovate export on Interactive P&L.
- **Tradovate CSVs are read automatically.** — The Orders export (filled orders only; rejected and canceled are skipped), the Fills export (with commissions) and the Performance export (round trips) all work. Upload either Orders or Fills for a period, not both: they list the same trades.
- **Futures point values are right.** — MES $5, ES $50, MNQ $2 and the rest come from the product; a contract not in the table takes its point value from Tradovate's own notional value (or the Performance P&L), never a guess.
- **Orders and Performance exports have no commissions,** so P&L from them is before fees; the page says so.

## 2026-09-28 · r17 · Today: Morning and Today's Events in one page
Tags: Dashboard
Do: Settings → System health → Restart dashboard.
- **One page for the day, called Today.** — Market direction, the full economic calendar, warnings on your holdings, your names, market tide and sectors, desk highlights, earnings and your journal. Today's Events is gone from the menu; old links open Today.
- **A date strip.** — ‹ and › step through market days (weekends skipped), or pick any date. Other days show that day's calendar, earnings and note; the live parts stay on today.
- **Your names first in earnings.** — Holdings and watchlist names reporting that day are listed on top and marked. Then the 12 largest other companies, with Show all for the rest. Searching shows every match.
- **Write the journal right on the page.** — Today's note autosaves as before; past entries and search sit underneath.
- **Sections fold.** — Click a section's title to hide the calendar, earnings or journal. It stays that way.

## 2026-09-28 · r16 · Growth Leaders, Invest / Trade modes, your brand everywhere
Tags: Dashboard, Growth, Valuation, Flow, Confluence, Institutional, Swing, GitHub
Do: Settings → System health → Restart dashboard. The Growth Leaders desk starts by itself and runs its first scan (about 10 minutes).
- **New desk: Growth Leaders (O'Neil-style).** — Seven checks inspired by William O'Neil's growth-stock method: current and annual earnings, new highs, supply and demand, leadership, institutional buying and market direction. After every close it ranks ~3,000 stocks by relative strength (our own 1–99 rating), checks every leader in full and scores them 0–100. A Leader badge means all seven pass. Not affiliated with Investor's Business Daily.
- **Bases, buy points and breakouts.** — Each stock's latest base and pivot, the buy range (to 5% above the pivot), the 8% stop and the 20–25% profit zone. In market hours it re-checks the names near a pivot every 15 minutes and flags breakouts on volume. Discord only for a Leader breaking out in a confirmed uptrend.
- **Market direction on Morning and in the sidebar.** — Confirmed uptrend, under pressure, or in correction, from SPY and QQQ.
- **Growth checks in Ticker Lookup and on every name's card.** — The seven checks, the score and the buy point, next to your thesis.
- **Invest / Trade / All switch.** — At the top of the menu. Invest shows Valuation, Confluence, Institutional, Watchlist and Portfolio; Trade shows Flow, Swing, Growth Leaders and P&L. Morning and the Track Record lead with the chosen side.
- **Your dashboard's name on everything the desks make.** — Share cards, desk headers, the Valuation Word report and Discord posts now say "Deep Current · Valuation" (your brand and each desk's name from Settings) instead of "UW Valuation Desk". Unusual Whales stays credited as the data source.
- **A new desk follows your autostart habit.** — When every desk starts with the dashboard, a newly shipped one does too.

## 2026-09-26 · r15 · Watchlist, buy zones and theses
Tags: Dashboard, Valuation
Do: Settings → System health → Restart dashboard. Then open Watchlist: your holdings are already there; add the names you'd like to own.
- **Watchlist page.** — Your holdings from the sheet plus any names you add, sorted so broken theses and names in their buy zone come first.
- **Buy zones.** — Buy below = the Valuation desk's intrinsic value less its 25% margin of safety, or your own price. Names at or below it are "in the buy zone"; within 10% above is "near".
- **A thesis and sell rules for every name.** — Click any name (on the Watchlist, in Ticker Lookup or on a Portfolio card) to write why you own it and what would make you sell, with an optional sell target and stop. Saving records today's valuation as the baseline.
- **Checks that say when a thesis breaks.** — Price vs value (above intrinsic value, your target or below your stop), business got worse (verdict or quality fell since the baseline), and smart money leaving (heavy insider selling outside plans, or funds exiting in the latest 13F). Each says why, in numbers.
- **Smart money in your names.** — For each name: insider buying and selling, the latest 13F quarter's adds, cuts and exits, and what the Confluence, Flow, Institutional and Swing desks say about it.
- **Fresh valuations after earnings.** — The day after a name reports, the Valuation desk re-values it by itself (starting the desk if needed) and logs what changed; if the new financials aren't filed yet it retries every 3 days for up to 45. There's also a Re-value now button.
- **Morning shows your names.** — What's in its buy zone, which theses broke, and the latest re-valuations.
- **A P&L test no longer depends on today's date.** — It pinned nothing, so it started failing once the sample's options had expired.

## 2026-09-26 · r14 · What's new page
Tags: Dashboard
- **This page.** — A dated history of every change to the dashboard and the desks, with a filter by area. Click an item for the details. A dot on the menu item means there's something you haven't seen yet. It's kept in `CHANGELOG.md` in the dashboard folder, and a test fails if a release ships without its entry.
- **The share zip includes the theme fonts.** — `MAKE_SHARE_ZIP` left out the `fonts` folder, so a shared copy fell back to plain fonts in Cyberpunk.

## 2026-09-26 · r13 · Security hardening
Tags: Security, Dashboard, Valuation, Flow, Confluence, Institutional, Swing, GitHub
Do: Settings → System health → Restart dashboard. The desks restart by themselves; reload any desk open in its own tab.
- **Full backup first.** — Before any change, everything (dashboard, desks, GitHub copy, databases, settings) was zipped to `Downloads\UW-Backups` with a second copy in OneDrive.
- **Only the dashboard's own page can change settings.** — Another program or page on this PC (even on another local port) can no longer rewrite a desk's folder and get it started. Changes must be JSON from the dashboard itself.
- **Desk and backup folders can't be network shares.** — The dashboard never runs a desk from, or copies your database to, a `\\server\share` path.
- **The portfolio link must be an https:// address on the internet.** — `file://` links, this PC and your home network are refused, so the sheet setting can't be used to read local files. Your Google Sheet works as before.
- **The Valuation desk cleans the ticker before writing its analysis script.** — Hostile data from the API could otherwise have run code when the desk executed the script.
- **UW text is escaped everywhere it's shown.** — The Flow board (ticker, sector, alert rule), and the Share buttons on Confluence and Institutional, could have run script from hostile data.
- **Every desk refuses other websites.** — A Host check stops DNS-rebinding tricks; another site can't drive a desk's data, trigger the Discord test posts or burn your UW quota with hidden image tags; only the dashboard may embed a desk, and nothing may embed the dashboard.
- **Smaller fixes.** — Upload size caps, no full folder paths in responses, Discord posts can't ping anyone and escape formatting in names, the Word report tolerates odd characters, custom menu links lost clipboard access, `.gitignore` covers journal files, backups and captured Flow boards.
- **Attack replays in the tests.** — Each attack from the review is replayed by the dashboard's tests and by `test_security.py` for every desk.

## 2026-09-26 · r12 · Themes
Tags: Dashboard, Valuation, Flow, Confluence, Institutional, Swing
Do: Settings → System health → Restart dashboard, then Settings → Appearance.
- **Eight built-in themes.** — Paper (the original, light and dark), Carbon, Colorblind-safe, Newsprint, Terminal, Midnight, Market Terminal and Cyberpunk, picked from a gallery in Settings → Appearance.
- **Cyberpunk has an optional Motion switch.** — A slow scrolling grid and pulsing glow, which stays still if Windows asks for reduced motion.
- **The desks and share cards follow the theme.** — One click restyles the dashboard, every desk and all five desks' share cards. With the dashboard off, a desk keeps its own look.
- **Fonts are bundled.** — Nothing is downloaded from the internet for a theme.

## 2026-09-26 · r11 · Sortable Track Record, Swing and Valuation share cards
Tags: Dashboard, Swing, Valuation
- **Track Record: sort and filter every pick.** — Click any column to sort (again to reverse); filter by desk, ticker and bullish/bearish. The table now lists every pick instead of the last 60, and remembers your choice.
- **Swing Desk: Share card on every alert.** — Ticker, direction, score, the five signal bars, entry / stop / target / reward:risk and haircuts, with Detailed or No-ticker and Dark or Light.
- **Valuation: Full or Summary share card.** — The summary card is the headline, four key numbers, the bull/base/bear strip and the one-line summary. Both dark cards are saved into each run's folder.
- **Valuation: the "How this desk works" block is gone.** — The dashboard's How it works page covers it.
- **Swing Desk: alert cards fit a phone screen.**

## 2026-09-25 · r10 · How it works page
Tags: Dashboard
- **One page explaining every desk and dashboard page.** — Plain-language descriptions with each desk's scoring weights, bands, gates and alert rules, read live from the running desks and falling back to the written values when a desk is off.
- **The written numbers are checked against the desks' code.** — A test fails if a desk's weights change without the page.

## 2026-09-25 · r9 · The tape behind a flow alert
Tags: Flow, Dashboard, GEX
- **Show tape on every Flow Desk card.** — The actual prints behind an alert, and the contract's last 50 trades, in two tabs, with side, flags, premium and a link to Unusual Whales.
- **Show tape in Ticker Lookup too.**
- **The Flow Desk moved to `Downloads\UW Flow Desk`.** — The dashboard follows the move by itself.
- **No more extra browser tab.** — Starting or restarting the Flow Desk from the dashboard no longer opens a second tab.
- **The GEX / ES desk was retired.** — It's off the dashboard and out of the GitHub copy.

## 2026-09-25 · r8 · Swing Desk runs on its own, Morning shows it
Tags: Swing, Dashboard, GEX
- **Swing scans by itself during market hours.** — Every ~45 seconds from 09:35 to 16:00 ET, page open or not, skipping NYSE holidays.
- **Swing scores recalibrated.** — The old gates sat above what the model could reach, so nothing ever alerted. Alerts now start at 40 and Discord at 50 with four signals agreeing.
- **Cards show which signals drove the score.**
- **Morning shows Swing's top setups, and they're graded in the Track Record.** — Bearish setups win when the stock falls.
- **GEX desk fixes.** — Calls no longer stopped on a stale basis, the SPX/SPY ratio was fixed and it ran in market hours only (retired in r9).

## 2026-09-25 · r7 · Choose where backups go
Tags: Dashboard
- **Settings → Backups.** — Type a folder or use Browse…, go back to the default, copy existing backups across, choose how many to keep (3–365), open the folder, or back up now.

## 2026-09-25 · suite · The GitHub copy for the hackathon
Tags: GitHub
- **`Downloads\UW-Desk-Suite`.** — A copy of the dashboard and five desks for GitHub, with synthetic test data only, no tokens or personal info, one-step setup (`SETUP.bat`), `RUN_ALL_TESTS.bat` and a README mapped to the judging criteria.

## 2026-09-24 · r6 · Swing (and GEX) join the dashboard
Tags: Dashboard, Swing
- **The Swing Desk is in the Desks menu.** — Started, framed and restarted by the dashboard like the others.
- **The API token is found in more places.** — Any sibling desk's `.env`, under either key name, skipping placeholders.

## 2026-09-24 · r4 · Morning, Ticker Lookup, Track Record
Tags: Dashboard
- **Morning page.** — Before the bell: today's releases, warnings on your holdings, sectors and market tide, the top of each desk and yesterday's journal. It opens by itself on weekday mornings.
- **Warnings on your holdings.** — Earnings within 7 days, insider selling outside 10b5-1 plans, heavy put buying.
- **Ticker Lookup with a position-size calculator.** — Every desk's view of one ticker, your position, and how many shares fit your risk rules.
- **Track Record.** — Each desk's top picks recorded during market hours and graded 5, 20 and 60 sessions later against SPY.
- **Nightly backups, a Restart dashboard button and a System health panel.**

## 2026-09-24 · r3 · Journal
Tags: Dashboard
- **A notepad under Today's Events.** — One note per day that follows the date you're looking at, autosaves, and can be searched. Nothing is lost if you close the tab mid-sentence.

## 2026-09-24 · r2 · Portfolio page
Tags: Dashboard
- **Your Google Sheet as a visual portfolio.** — A donut with your centre image, click a slice or row for details, a summary line and your closed-trade history, plus Sheet and Table views.

## 2026-09-24 · r1 · The dashboard
Tags: Dashboard
- **One window for everything.** — A menu with your logo and a clock, Today's Events (economic calendar and earnings), every desk opened and started from the menu, and Settings to reorder, rename and add links.
- **Interactive P&L.** — Upload a broker CSV (thinkorswim, Schwab, Fidelity, Robinhood, Webull, IBKR, tastytrade or any CSV) and get a green/red calendar of realised P&L. Lots are matched first-in, first-out, and overlapping uploads never double-count.
- **Desks restart when their code changes, and survive a reboot.** — Optional autostart and keep-alive.

## Before 2026-09-24 · desks · The desks
Tags: Valuation, Flow, Confluence, Institutional, Swing
- **Valuation Desk.** — DCF + bull/base/bear scenarios, an 11-section Word report, a share card and a script that reproduces the numbers. Model 3.2 (2026-09-23) added the Buffett layer: a quality & management score, margin-of-safety and quality gates, expected returns, a screener and the "in a tweet" summary.
- **Unusual Options Flow Desk.** — Scores who is lifting the offer on single-leg options, 0–100, with board, spreads, near-miss and out lanes.
- **Confluence Desk.** — Where insiders, dark-pool size and 13F funds are all buying the same company.
- **Institutional Desk.** — Small, concentrated funds adding to small, profitable companies, from 13F filings.
- **Swing Desk.** — Five independent signals (flow, open interest, dark pool, gamma, chart) that must agree on a swing setup.
