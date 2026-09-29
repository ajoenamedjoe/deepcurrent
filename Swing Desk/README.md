# UW Swing Desk

A live swing-trading dashboard built on the Unusual Whales API. It scans the
optionable universe every 45 seconds **on its own during market hours
(09:35-16:00 ET, trading days) whether or not the page is open**, scores each
ticker across five independent signals, and surfaces setups where at least three
signals of real size point the same way. Each card names the signals that carried
it ("flow + OI + gamma") rather than an "N of 5 agree" count.

Your API token stays on the server and is never sent to the browser.

---

## Run it

```bash
cd uw-swing
export UW_API_TOKEN="your-token-here"
python3 server.py
```

Then open **http://127.0.0.1:8787**

> **Open that URL — do not double-click `index.html`.** The page and the data are served by
> the same process; opened straight off disk the browser looks for the data at
> `file:///api/scan` and every panel comes up empty. The dashboard detects this and
> tells you, but it's the most common way to get a blank screen.

Instead of the env var you can create a `.env` file next to `server.py`:

```
UW_API_TOKEN=your-token-here
```

No `pip install` needed — the server is Python standard library only.
Change the port with `UW_DASH_PORT=9000 python3 server.py`.

---

## What it's doing

**Stage 1 — one wide poll.** `/api/screener/stocks` returns the whole optionable
universe in a single call with net premium, ask/bid side volumes, open interest vs
yesterday, IV rank, relative volume and 52-week range. The three cheap signals
(flow, OI, dark pool) are scored across every ticker from that one payload.

**Stage 2 — enrich the leaders.** Only the top 14 by absolute rough score get
per-ticker calls: `/gex-levels` for the gamma walls and `/ohlc/1d` for six months
of candles, from which SMA20/50, RSI14 and ATR14 are computed locally. This keeps
the request budget at roughly 31 calls per scan instead of several hundred.

### The five signals

| Signal | Weight | What it reads |
|---|---|---|
| **Flow** | 30 | Net directional premium (`net_call_premium − net_put_premium`), ask-side conviction, amplified by relative option volume |
| **OI build** | 20 | Day-over-day call vs put open interest change — positions actually *opened*, which is what separates a swing from a scalp |
| **Dark pool** | 15 | Block premium vs the day's notional, leaning by print price vs NBBO mid |
| **Gamma** | 15 | Spot vs gamma flip, and the room between the put wall and the call wall |
| **Technicals** | 20 | Trend stack (close/SMA20/SMA50), RSI position, bonus for a shallow pullback inside an established trend |

Each resolves to −1…+1, so the composite runs −100…+100 and the sign is the
direction.

### Haircuts

- Earnings landing inside the horizon → ×0.65 (a binary event ruins a swing thesis)
- IV rank above 85 → ×0.85 (you're paying up for premium)
- Relative volume below 0.40 → ×0.70 (thin tape, bad fills)

### The gate

A setup is only promoted from *watchlist* to *alert* when **all** of these hold:

- `|score| ≥ 45`
- at least **3 of 5** signals agree in direction — and a signal only counts as
  agreeing if it contributes more than `agree_threshold` (0.15 of 1.0). At the old
  0.05 bar a signal doing almost nothing still counted, which made "5/5 agree" look
  stronger than it was. Agreement and score are independent gates: five *mildly*
  bullish signals give 5/5 agreement but a score in the 30s, which fails.
- reward:risk ≥ 1.3
- ATR is narrower than the trade band (if one ATR exceeds 25% of price, the
  stock's daily noise is wider than the trade — it's excluded at any score)

Seeing zero alerts is a normal reading, not a bug. On uncorrelated data the model
stays silent by design.

### Hold time

Every alert is tagged with the holding period its own evidence implies, from the
median DTE across that ticker's flow alerts (or, with no flow alerts, from how many
ATRs away the target sits):

| Internal key | Shown to the user | Median DTE |
|---|---|---|
| `SHORT` | **2-10 days** | ≤ 14 |
| `SWING` | **2-6 weeks** | 15–45 |
| `POSITION` | **2+ months** | > 45 |

The UI shows only the time range — never the internal key. "SHORT" in a trading tool
reads as *short the stock*, and it sits next to a direction column, so it was
genuinely ambiguous. The keys stay internal because the filter buttons and the
history database are keyed on them.

### Levels

- **Entry**: spot ± 0.25 ATR
- **Stop**: the put wall (bull) or call wall (bear) when it sits at a sane
  distance, otherwise 1.5 ATR — floored at 1 ATR so noise doesn't take you out
- **Target**: the opposing wall when usable, otherwise 2.5 ATR — capped at
  6 ATR or 25% of spot, whichever is tighter

---

## Tuning

Everything lives in the `CFG` dict at the top of `server.py`:

```python
"min_score": 45.0,       # loosen to see more setups
"min_agreeing": 3,       # the confluence requirement
"agree_threshold": 0.15, # how much a signal must contribute to count as agreeing
"min_rr": 1.3,
"enrich_top": 14,        # more = better coverage, more API calls
"universe_size": 120,
"w_flow": 30.0,          # reweight the signals to your own style
```

Raising `enrich_top` is the main lever if you want wider coverage; each extra
ticker costs two API calls per scan (cached 60s for GEX, 10min for candles).

---

## Endpoints

The server exposes these if you want to wire anything else up:

| Route | Returns |
|---|---|
| `/api/scan` | Full scored scan: alerts, watchlist, every component and its detail |
| `/api/tape` | Recent flow alerts and dark pool blocks |
| `/api/tide` | Market tide series |
| `/api/ticker/AAPL` | GEX levels, technicals, and that ticker's alerts and blocks |
| `/api/health` | Token status and per-feed success/failure |

---

## Score calibration (2026-09-25)

The first three weeks of history topped out at a score of 46, while the alert gate
was 45 and the Discord gate 60, so the desk surfaced 4 setups in 4,984 readings and
never posted. Measuring each signal on a full session showed why: options flow
reached only half strength at its 90th percentile, the open-interest and gamma
signals under a third, and the chart signal was above half strength for 69% of
names. `flow_gain`, `oi_gain`, `gamma_flip_gain` and `gamma_room_gain` in `CFG`
stretch each so a strong reading is worth ~0.8 of its weight. The alert gate is
now 40 and Discord 50. Every scan now stores each signal's value (`parts` in
`observations`), so the next calibration comes from the tape. The page shows
the day's top score against the theoretical 100.

## Share card

Every alert card has a **Share card** button. It draws a 1080-px image of the setup: ticker, direction, score,
the five signal bars (contribution out of each weight), entry / stop / target / reward:risk, any haircuts, and a
footer with the date, time and data source. Options: **Detailed** or **No ticker** (hides the name and shows the
levels as % from the price), **Dark** or **Light**. Copy the image, download it as a PNG, or copy the draft caption.

## Discord alerts

For your own private channel only: posting Unusual Whales data to other people (a public or paid server) may need a
redistribution licence from Unusual Whales. Keep the webhook secret.

Add your webhook URL to `.env`:

```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

To get one: right-click your Discord channel -> **Edit Channel** -> **Integrations**
-> **Webhooks** -> **New Webhook** -> **Copy Webhook URL**.

Restart, then visit <http://127.0.0.1:8787/api/notify/test> to fire a test post.

**A setup is only posted when all of these hold** (`NOTIFY` in `notify.py`):

| Gate | Default | Why |
|---|---|---|
| `min_score` | 50 | High conviction only (about the top 2% of names) |
| `min_agreeing` | 4 signals | Broad confluence, not one loud signal |
| `min_streak` | 3 consecutive scans | ~2 minutes. Kills threshold flicker |
| `cooldown_hours` | 6 | One post per ticker+direction, not a stream |
| `max_per_scan` | 4 | Burst guard |

Plus one **daily wrap at 4:00 PM ET** each weekday listing what fired and how each
setup behaved afterwards. The server must still be running at 4pm for it to send.

Discord failures never affect the scan - they're logged and swallowed.

---

## History

Every scan is written to `swing_history.db` (SQLite, created automatically). That's
what powers the "held N scans" badge, the notification de-duplication, and the wrap's
outcome column.

It also means you can eventually ask the real question - do these scores predict
anything? The `observations` table has every ticker's score and price at every scan,
which is exactly what you need to check whether a score of 70 behaves differently
from a score of 45.

```sql
-- scores vs what happened next, straight from the db
SELECT ticker, score, price, ts FROM observations WHERE qualified=1 ORDER BY ts;
```

---

## Troubleshooting

The dashboard diagnoses its own failure states in the banner, and `server.py` verifies your
token against the live API on startup before serving anything.

| What you see | What it means |
|---|---|
| "This page has to be served by the Python server" | You opened `index.html` directly. Run `python3 server.py` and visit http://127.0.0.1:8787 |
| "Can't reach the dashboard server" | The Python process exited. Check the terminal you started it in |
| "No Unusual Whales API token" | Server is running without a token — set `UW_API_TOKEN` and restart |
| "Unusual Whales rejected the token" (401/403) | Token is mistyped, expired, or the account lacks API access. UW's API is a paid add-on separate from the site subscription |
| "Rate limited" (429) | Lower `enrich_top` or raise the `ttl_*` values in `CFG` |
| "N feeds degraded" | Some endpoints are failing; healthy panels keep updating, the rest hold their last good values. Hover the status pill for the exact errors |

Port already in use? `UW_DASH_PORT=9000 python3 server.py`

## Tests

```bash
python3 tests/test_model.py       # model math against synthetic API-shaped payloads
python3 tests/mock_uw.py &        # mock UW API for offline end-to-end runs
```

---

## A note on what this is

This is descriptive aggregation of market data, not trade advice or a
recommendation. The levels are arithmetic from ATR and gamma walls, not
predictions. Scores measure signal agreement, not probability of profit. Verify
everything against your own broker data before risking capital.
