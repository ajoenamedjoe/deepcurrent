# UW Confluence Desk

**Where insiders, institutions and off-exchange size are all buying the same
company, scored 0–100.**

Fifth desk in the set. Siblings: **Swing Desk**, **Institutional Desk**,
**GEX ES Desk**, **Insider Desk**. Same stack (Python stdlib server +
single-file HTML + SQLite + Discord), same validated palette, same
no-install promise.

**Launch: double-click `START_HERE.bat`.** Do not open `index.html` directly —
the page talks to the server, and the server is what holds your API token.

---

## The score

Three legs, weighted to a maximum of **exactly 100**, so the composite can
never need clipping.

| leg | weight | what it is | how fresh |
|---|---|---|---|
| **Insider** | 45 | SEC Form 4 open-market purchases (code P) | ~2 days |
| **Dark pool** | 30 | off-exchange prints | same day |
| **Institutional** | 25 | 13F holdings from small, concentrated funds | **quarterly** |

**A card with no 13F coverage caps at 75 by construction.** That is deliberate:
the 13F leg is a *backdrop*, not a gate. It lifts a card into the top quartile,
and its absence never hides one.

### Insider leg (45)

| | weight | |
|---|---|---|
| conviction | 14.4 | best single stake growth, `sqrt(shares / shares_owned_before)` |
| cluster | 11.7 | distinct people buying: 1→0, 2→.5, 3→.75, 4→.9, 5+→1 |
| rank | 9.0 | seniority of the most senior buyer, parsed from `officer_title` |
| size | 9.9 | 0.7 × (notional/marketcap) + 0.3 × absolute dollars |

Modifiers multiply **down only**, scaled by the fraction of the cluster
affected: 10b5-1 ×0.75, corporate filer ×0.70. Floor: $50k per company per
window. This is the standalone Insider Desk's model, rescaled 100 → 45.

### Dark pool leg (30)

| | weight | |
|---|---|---|
| block | 12 | biggest print vs the ticker's own 30-day average volume, plus window notional vs market cap |
| sustain | 8 | distinct **sessions** where off-exchange blocks were ≥5% of *that day's* volume |
| proximity | 6 | did the blocks land within ±5 days of the insider's buy dates (scored only against insider dates that fall inside the dark pool window; "no data" otherwise) |
| lean | 4 | premium share at the offer minus premium share at the bid |

**`lean` is the smallest weight in the model on purpose.** Print location is
the one sub-signal whose *sign* could not be defended on the available
evidence: in replay, a small cap visibly working size with its price walking
steadily higher leaned to the **bid**. Either the lean is noise on a dirty
field, or an institution really was distributing off-exchange into lit
strength, and two tickers cannot tell those apart. So it ships small, the raw
number is written to the tape on every scan, and the card prints the ask and
bid shares side by side instead of only a verdict.

#### The activity floor took three wrong answers

Worth knowing, because the numbers on the card depend on it. The first live
board scored `sustain` **zero on all 111 cards**, and took `proximity` down
with it — 14 of the 30 dark pool points, dead everywhere.

The floor originally read "5% of **30-day average** volume", eyeballed on one
ticker. That ticker turned out to be the most block-heavy name in the sample
(median session above 5% of its own average volume), while an ordinary liquid
mid-cap runs under 2%. So the floor was calibrated to an outlier and
unreachable for a normal ticker. Lowering it to 2% over-corrected — nearly
every session of every name went active, and the gap between a name working
real size and a flat one collapsed to almost nothing. A ratio against each ticker's own median then punished exactly
what the component rewards: a name that is heavy *every* day has a high median.

The fault in all three: the numerator is filtered by `min_premium` and the
denominator was not, so the ratio's natural scale drifts with liquidity and
price, and no constant fits. Measuring against **the session's own volume**
puts numerator and denominator on the same footing — and is literally the
signal asked for: a name working size now shows most of its sessions
active, and ordinary names only a few.

`py -3 replay.py --sessions` prints the real distribution out of your own
tape. **Set this number from that, not by eye.**

### Institutional leg (25)

| | weight | |
|---|---|---|
| funds | 13 | distinct funds with a NEW or ADD: 1→.35, 2→.6, 3→.8, 4→.9, 5+→1 |
| weight | 7 | the largest position as a fraction of that fund's equity book |
| trajectory | 5 | new conviction / building > steady > volatile > harvesting |

Read straight out of `..\Institutional Desk\institutional.db`, read-only.
**This desk never re-runs the 13F scan** — that is ~3,000 API calls and 15–25
minutes, and its output is already exactly the universe you asked to inherit:
$50M–$2.5B AUM, hedge-fund tagged, ≤75 positions, profitable companies.

### There is no "N of 3 signals agree" readout

Swing Desk shipped one, then had to give it a materiality floor after a
ticker read "5/5 agree" on a score in the low 30s. Insider Desk still had to **delete** its
version, because every card in the top 25 read "4 of 4" — two of its four
components have floors, so the count discriminated nowhere near where it
mattered. The same trap is waiting here: the insider leg is a precondition for
a card existing, so it always agrees. The card shows each leg's **points out
of its maximum** and names whichever leg carried the score.

### Nothing reaches 100

Reaching 100 needs all eleven components maxed at the same moment. A replay
over plausible live inputs never got past about **77**. The board footer
prints the highest score this desk has actually recorded, so the scale
calibrates itself against your own tape rather than against a number nothing
can reach. `replay.py` reports the real distribution whenever you change a
weight.

---

## Cost

```
  4 calls          insider: 45 days of the ENTIRE market, limit=500
  0 calls          13F: read from the sibling desk's SQLite file
  1 call/candidate dark pool
```

About **115 calls per full scan**, 40–80 seconds. Dark pool is cached 30
minutes, so a desk left open all day costs roughly 500 calls/hour — about a
third of Swing Desk.

**Every candidate is enriched — there is no "top N only" slice.** Swing Desk
enriches only its top 14, so a ticker slipping out of that slice silently
loses points and drops off the board: an architecture artifact that looks
exactly like a real fade. Here the dark pool call is cheap enough to give one
to every candidate, so a card's score only moves when the market moves.

---

## Alerts

Discord alerts are for your own private channel: posting Unusual Whales data to others (a public or paid server)
may need a redistribution licence from Unusual Whales.

Discord fires only when **all** of these pass:

* score ≥ 70 (`CDESK_ALERT_SCORE`)
* **all three legs contributing** (`CDESK_ALERT_ALL_THREE`) — this is a
  confluence desk; a 72 built entirely out of insider points is not the page
  you asked for
* held ≥ 2 consecutive scans
* 12h cooldown per ticker, max 4 per scan

**The first scan posts nothing.** On a cold start the whole 45-day window is
"new", which on the Insider Desk meant hundreds of filings and Discord immediately
posting its per-scan maximum about filings that were weeks old. The first run
records a baseline and says so in a banner on the board.

Scans run on an internal background thread whether or not a browser is open.
Form 4s land in an evening wave, long after the tab is closed.

---

## Configuration

Everything is optional. `uw.py` looks for a `.env` in this folder, then in
`..\Swing Desk`, `..\UW SwingDesk`, `..\Institutional Desk`,
`..\UW Insider Desk` and `..\GEX ES Desk` — so the token and webhook live in
exactly one place on the machine. See `.env.example` for every knob.

The ones worth knowing:

| | default | |
|---|---|---|
| `CDESK_WINDOW_DAYS` | 45 | insider lookback. 90 is the longest that is still one rolling window |
| `CDESK_ALERT_SCORE` | 70 | Discord threshold |
| `CDESK_REQUIRE_INSTITUTIONAL` | false | true = only show companies in the 13F universe |
| `CDESK_SCAN_SECONDS` | 600 | time between scans |
| `CDESK_PORT` | 8770 | |

---

## Tools

```
py -3 diag.py              every layer, in the order it fails in practice
py -3 diag.py --discord    ...and send a real test post
py -3 replay.py            score distribution: ties, ceiling, flat components
py -3 replay.py --sessions the live per-session dark pool share distribution,
                           straight from confluence.db — how to set the
                           activity floor from data instead of by eye
py -3 -m unittest discover -s tests
```

Run `diag.py` first whenever the board looks wrong. It names the failing
layer instead of the symptom.

---

## Sharing a card

"Share card" renders the card as a portrait PNG, 1080px wide, height following
content, capped at 1400 so it survives a 4:5 crop. **Copy image** and
**Download PNG**; no platform links. Toggles for Detailed / No names and
Light / Dark, and an editable caption drafted from the card's own numbers that
is never baked into the image.

The image is **drawn on a canvas, not screenshotted from the DOM** —
rasterising live HTML needs `foreignObject` plus inlined fonts and taints the
canvas differently in every browser. Drawing makes it deterministic and lets
the image carry its own legend and provenance footer, which a standalone PNG
needs and the on-screen card does not.

The no-names variant keeps each buyer's **role** and drops their identity —
the role is the part that carries the signal.

Copying an image needs `ClipboardItem`; `http://127.0.0.1` counts as a secure
context, so it works in Chrome and Edge. Where it is missing, the Copy button
disables itself and the hint points at Download.

---

## Files

```
server.py      HTTP + the internal scan thread
scan.py        orchestration: insider -> 13F -> dark pool -> score
score.py       the 0-100 model. Read this one first.
insider.py     Form 4 fetch, eligibility, per-person aggregation
darkpool.py    off-exchange fetch, session bucketing, clean-print filter
idb.py         read-only reader for the Institutional Desk's SQLite
store.py       confluence.db: observations, outcomes, alert state
notify.py      Discord
uw.py          API client: throttle, retry, coercion, .env
diag.py        diagnostics
replay.py      score distribution report
index.html     the board and the share renderer
tests/         86 tests, each named after the bug it guards
```

---

## Known gaps

* **The weights are reasoned, not fitted.** Same honest position as all four
  siblings. `observations` stores every component per company per scan, before
  any outcome exists, so the tape is the fix. The footer counts **distinct
  days**, not rows — a thousand scans in one afternoon is still one day of
  evidence.
* **No outcome tracking yet.** `outcomes` records the price at first
  appearance; marking it up at +5/+20/+60 sessions is the obvious next build.
  Insider and 13F signal plays out over months, so this needs a quarter before
  it says anything.
* **The `lean` sign is unverified** — see above. This is the first thing the
  tape should settle.
* **The activity floor is set from three tickers**, not a distribution. Run
  `replay.py --sessions` after a scan and move it if the live spread says so;
  aim for roughly the top quartile of sessions being "active".
* **The newest session is still forming**, so its share of the day's volume
  reads high (mid-morning it can show several times its typical share). It is
  flagged `partial` on the card and in the tape, but it does still count
  toward `sustain`.
* **The 13F leg does not decay with age.** By choice: a backdrop, not a
  time-weighted signal. Adding decay means touching only
  `score.institutional_leg`.
* The 13F universe is one quarter (2026-06-30). Q3 filings land around
  2026-11-14 — re-run the Institutional Desk scan then and this desk picks the
  new quarter up with no change.
* No "share the whole board" option, and the Discord post is still a text
  embed rather than the PNG. Attaching the image would mean moving the
  renderer server-side; it is browser JS today.
