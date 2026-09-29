# UW Unusual Flow Desk

Single-leg option flow where the buyer is lifting the offer, scored 0–100.

## Start it

1. Extract this folder into the folder that holds the other desks, so it sits
   beside them as `UW Flow Desk`.
2. **Double-click `START_HERE.bat`.**
3. The board opens in your browser at `http://127.0.0.1:8733/`.

Leave the black window open — that is the desk. Close it to stop.

**Do not open `flowboard.html` directly.** The page asks the server for the
board, and the server is what holds your API token.

### The token

The first launch creates `.env` from `.env.example` and tells you what to do.
You have two options:

- **Do nothing.** If `Confluence Desk`, `Swing Desk`, `Institutional Desk`,
  `UW SwingDesk`, `GEX ES Desk` or `UW Insider Desk` is next door with a `.env`
  in it, this desk borrows that token automatically. One copy on the machine.
- **Or** open `.env` in Notepad and paste your token after `UW_TOKEN=`.

If it cannot find a token the board loads anyway and says so in red at the top,
rather than failing silently.

## What you are looking at

Four lanes across the top:

| lane | what is in it |
|---|---|
| **Board** | ask side ≥ 75% of directional volume, ≥ $100k bought, single-leg, evidence clean |
| **Spreads** | real institutional size, ambiguous direction — a spread leg can print 100% on the ask and still be the short half of someone else's trade |
| **Near miss** | 60–75% ask side, one step below the gate |
| **Filtered out** | everything rejected, each with a sentence saying why |

Nothing ever vanishes without a reason. If a name is not on the board, the
Filtered out lane tells you what stopped it.

Each card shows the raw option symbol above its plain-English decode
(`ACME261016P00034000` / `ACME $34 PUT · 16 OCT 26`) so a bad read is obvious at a
glance, the ask-vs-bid split with the 75% gate drawn on it, the six score
components out of their maximums, and the evidence chips.

**Share card** on any card draws it as a PNG. Detailed or no-ticker, dark or
light, copy to clipboard or download. The caption is editable and never baked
into the image.

## The score

Six components totalling exactly 100:

| | |
|---|---|
| Aggression 24 | ask share of directional volume |
| Sweep 18 | sweeps, floor prints, and fills walking up the offers |
| Opening 20 | volume over OI, size over OI, OI confirmation, multi-day build |
| Vs ticker 18 | how unusual this is *for this name*, not in dollar terms |
| Urgency 12 | tenor choice and repeat stacking |
| Premium 8 | absolute dollars — the smallest weight on purpose |

Premium is last because it is the number that ranks the biggest, most liquid
names first no matter what they are doing. On a typical session the
highest-premium unusual contracts in the entire market are megacap calls
expiring that same day, traded close to 50/50 between bid and ask; the 75% ask
gate rejects them.

**Open interest is tomorrow's news.** OI updates once the next morning, so
today's flow is scored on proxies and marked *awaiting confirmation* until the
morning print confirms or contradicts it. Every card says which.

## The tape behind a card

Every card with alerts or contracts has a **Show tape** button. It opens the actual
option trades:

* **This alert** — the prints UW grouped into the flow alert
  (`/api/option-trades/flow-alerts/{id}`): for a RepeatedHits alert, every fill in the
  cluster, milliseconds apart. Pick between a card's largest alerts at the top.
* **Contract today** — that contract's last 50 prints of the session
  (`/api/option-contract/{OSI}/flow`), alert or not.

Each row shows the time (US Eastern), contracts, fill price, the bid × ask at that
moment, which side it hit (at the ask = a buyer paid up), premium, exchange, flags
(sweep / floor / cross / multi-leg), stock price, IV, and the contract's volume vs open
interest. Canceled prints are struck through and not counted. The trades are fetched only
when you open the panel and cached for a minute. **Open on Unusual Whales** opens the
ticker's live flow page.

## When it scans

Every 5 minutes, 09:50–16:00 ET, weekdays, on its own background thread —
whether or not a browser tab is open. About 3 API calls per scan, ~234 per
session.

It warms up for 20 minutes after the open because ask-side percentages early in
the session are computed over a handful of prints and swing wildly.

## Settings (`.env`)

| | |
|---|---|
| `UW_TOKEN` | your API token; blank borrows a sibling desk's |
| `SCAN_SECONDS` | 300 |
| `PORT` | 8733 |
| `SCREENER_PAGES` | 2 (250 contracts each) |
| `MIN_CONTRACT_PREMIUM` | 0 — keeps the API's own $10k unusual preset |
| `OPEN_BROWSER` | true |
| `SCAN_OUTSIDE_MARKET_HOURS` | false |

Comments go on their own line, never beside a value. A bad value never stops
the desk starting — it falls back and warns.

## If something looks wrong

- `http://127.0.0.1:8733/api/health` — token found? how many scans? any config
  warnings?
- `http://127.0.0.1:8733/api/diag` — probes both endpoints and reports what came
  back. No secrets in the output.
- The footer prints the build date and the folder it is running from, so a
  stale browser tab is distinguishable from a bad install. Hard-reload first.
- Run the tests: `py -3 tests\test_flow.py`, `py -3 tests\test_flowscan.py`,
  `py -3 tests\test_server.py`. 107 tests, no dependencies.

## Folding it into the Confluence Desk

This desk also ships as a fourth leg of the Confluence composite — insider 35 /
dark pool 25 / 13F 20 / **flow 20**. See `INTEGRATION.md`. It is a separate job
from running this desk, and this desk works standalone either way.
