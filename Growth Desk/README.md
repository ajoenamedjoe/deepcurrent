# Growth Leaders (O'Neil-style)

A desk that looks for growth leaders the way William O'Neil described them: strong and accelerating earnings,
a price near its highs, buying on volume, relative strength, funds adding, and a market in an uptrend. It is
inspired by his published method; it is **not affiliated with or endorsed by Investor's Business Daily**, and the
ratings here (relative strength, group rank) are our own calculations from Unusual Whales data.

## What it checks

| | Check | Passes when | Points |
|---|---|---|---|
| C | Current earnings | latest quarter's EPS up 25%+ on the same quarter a year ago (speeding up earns more) | 25 |
| A | Annual earnings | trailing-12-month EPS up 25%+ a year over 3 years, no down year | 15 |
| N | New highs | within 15% of the 52-week high | 10 |
| S | Supply and demand | up-day volume ≥ down-day volume over 50 sessions; share count up ≤ 5% a year | 10 |
| L | Leader | relative strength 80+ of 99 (plus the industry group's rank) | 25 |
| I | Institutions | latest 13F quarter: at least as many funds adding as cutting, net shares up | 15 |
| M | Market direction | SPY and QQQ in a confirmed uptrend (not the score: the 7th check) | – |

Parts with no data drop out and the rest re-scale; a stock with fewer than 60 measurable points gets no score.
**Leader** = all seven pass.

## Base, buy point and sell rules

The desk finds the latest base (a 3-35% pullback from a high at least 3 weeks old, after a 20%+ advance) and its
pivot. A close, or in market hours a price, above the pivot on volume 40%+ above average is a **breakout**; the
buy range runs to 5% above the pivot. O'Neil's sell rules are shown on every card: cut losses at 7-8% below the
buy point, take most gains at 20-25%.

## When it runs

* Every trading day after the close (16:20 ET), and at start-up if the last session was never scanned:
  ~3,000 stocks ranked from the screener, then every candidate (RS 70+, $10+, within 25% of the high)
  checked in full. The first run makes ~1,500-2,000 API calls; earnings and 13F data are cached, so later
  runs make far fewer.
* Every 15 minutes in market hours: one call for the names near a pivot, to catch breakouts.
* Discord (optional): only when a Leader breaks out in a confirmed uptrend. Your own private channel only: posting Unusual Whales data to others may need a redistribution licence from Unusual Whales.

## Files

`server.py` (port 8760) · `scan.py` · `model.py` (all scoring) · `store.py` (SQLite `growth.db`) ·
`notify.py` · `uw.py` · `diag.py` / `DIAG.bat` · `tests/` (`RUN_TESTS.bat`).

Data: Unusual Whales. Educational use only, not investment advice.
