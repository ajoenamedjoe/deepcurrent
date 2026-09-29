# Folding the flow leg into Confluence Desk

Six files drop into the `Confluence Desk` folder (in the folder that holds the other desks):

    flow.py                     the model
    flowscan.py                 two wide REST polls, joined and scored
    flowboard.js                the Flow board tab + share renderer
    tests/test_flow.py          71 tests
    tests/test_flowscan.py      19 tests
    tests/fixtures/sample_flow.py synthetic payloads in the shape of the two endpoints

Nothing existing is overwritten. Three small edits wire it in.

---

## 1. The composite rescales — this is the only breaking change

Confluence Desk's composite was **insider 45 + dark 30 + 13F 25 = 100**.
Adding a fourth leg is only honest if the other three make room, so:

| leg | was | now |
|---|---|---|
| insider | 45 | **35** |
| dark pool | 30 | **25** |
| 13F | 25 | **20** |
| **flow** | — | **20** |
| | 100 | **100** |

`flow.CONFLUENCE_LEGS` carries those numbers and
`test_confluence_legs_still_sum_to_100_after_adding_flow` guards them.

In `score.py`, change the three leg constants to 35 / 25 / 20 and add:

```python
import flow
flow_points = flow.confluence_flow_leg(flow_card["score"] if flow_card else 0.0)
```

**Two consequences to expect, both correct and both worth saying out loud:**

1. **Every score on the existing board drops by ~22%.** A card that read 59.9
   yesterday reads ~46.7 today with no flow. That is not a regression, it is the
   scale making room — but the tape in `observations` now contains two
   incompatible scales, so anything fitted against it later must filter on the
   scan date. Record the cutover date in `meta`.
2. **The structural caps move.** No 13F coverage used to cap a card at exactly
   75; it now caps at 80. No flow caps at 80. Neither caps at 60. Those are
   consequences of the weights, not separate rules, which is the point of
   making the legs sum to exactly 100.

## 2. The scan loop

Flow is intraday and 13F is quarterly, so they do not share a cadence. Add a
second background thread rather than folding flow into the existing scan:

```python
from flowscan import FlowScan, market_open

def flow_loop(stop):
    scan = FlowScan(TOKEN)
    while not stop.is_set():
        if market_open():
            board = scan.run()
            store.save_flow(board)          # see 3
        stop.wait(300)                       # 5 minutes, the chosen cadence
```

Cost is ~3 calls per scan, ~234 per session. Compare with the Swing Desk's
~10,000/day. Flow costs almost nothing because both endpoints are market-wide.

**Do not make this pull-driven.** The Swing Desk's scans only ran while a
browser tab was open, so with no tab there were no scans, no Discord and an
empty 4pm wrap. The Insider Desk fixed that with an internal thread; do the
same here.

## 3. Storage

`observations` needs the flow components. `CREATE TABLE IF NOT EXISTS` is **not
a migration** — Confluence Desk nearly broke every insert on a live user database
by adding a column to an existing table. Add to `Store.MIGRATIONS`:

```python
("observations", "flow_score",      "REAL"),
("observations", "flow_ask_share",  "REAL"),
("observations", "flow_ask_premium","REAL"),
("observations", "flow_lane",       "TEXT"),
("observations", "flow_parts",      "TEXT"),   # json
```

and let the `PRAGMA table_info` check run on every open, as it already does.

Write **every** card's components on every scan, including the ones that did not
qualify — that tape is the only honest way to fit the weights later, and it has
to exist before the outcomes do.

## 4. The board

`flowboard.js` renders the four lanes and the share card. It reads
`/api/flow` and expects exactly the `{meta, cards}` shape `FlowScan.run()`
returns. Inject it the way `inject.py` already injects `share.js`.

**Ship a build stamp.** The Insider Desk's Share button was once reported
missing when the browser tab simply predated the deploy. The footer prints the served file's
mtime for exactly that reason.

---

## Verification before you trust a board

1. `python -m pytest tests/ -q` **on the PC**, not only in a container.
   Confluence Desk shipped a test that passed in the container and failed on the
   real machine because it exercised a fallback whose target only exists there.
2. After the first live board, query the tape for **per-component spread**:

   ```sql
   SELECT COUNT(*),
          SUM(json_extract(flow_parts,'$.sweep')    > 0),
          SUM(json_extract(flow_parts,'$.opening')  > 0),
          SUM(json_extract(flow_parts,'$.relative') > 0)
     FROM observations WHERE scan_date = date('now');
   ```

   Confluence Desk ran eight clean scans overnight with `sustain` scoring
   **exactly zero on all 111 cards** — 14 of 30 points dead — and nobody noticed
   until the tape was queried. If any component here is zero across the whole
   board, the threshold is wrong, not the market.
3. Check the **observed maximum** in `meta`. On the first calibration session
   it sat around 70 against a theoretical 100. If it sits below ~45 for a week the model is
   too hard to satisfy; if anything reaches 95 the gates are too loose.

## Known gaps, carried forward honestly

- **No fitted weights.** Same position as all five siblings. The six components
  are reasoned from one session's live distribution, not fitted to outcomes.
- **No outcome tracking.** The obvious next build: record the underlying price
  at first appearance and mark it up at +1/+5/+20 sessions. Flow is a
  days-to-weeks effect, so this says something in a month rather than a quarter
  — much faster than the insider leg, which needs a quarter.
- **The OI confirmation loop is designed but not built.** Every card is scored
  before its open interest confirms. Re-reading the same contracts the next
  morning and stamping each card confirmed / contradicted is the single highest
  value addition, because it converts the desk's main caveat into its main
  signal. `days_of_oi_increases` is a weak proxy for it today.
- **`ask_side_perc_7_day` is displayed but not scored.** It looked like the
  strongest corroborating field in the calibration sample (some names printed a
  full 1.0) and it
  is deliberately left out of the model until the tape can say whether it adds
  anything beyond today's ask share.
- **The spreads lane is unscored as direction.** A near-100%-ask multileg print
  is real institutional size and the desk can only say so, not say which way.
