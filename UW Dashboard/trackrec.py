"""
Desk track record: did the names each desk flagged actually go the right way?

Recording
  While the market is open, every ~20 minutes, each RUNNING desk's current top
  names (deskview.picks) are recorded -- once. A name already recorded for that desk
  inside its window (30 days; 120 for the quarterly 13F desk) is not recorded again,
  otherwise one good name sitting on a board for a month would count 20 times.

Scoring
  Entry = the close on the day it was flagged (or the next session if flagged on a
  weekend), exit = the close 5 / 20 / 60 sessions later, both from UW daily candles.
  Close-to-close on both legs, so the entry never uses a price the desk couldn't see.
  Direction matters: a bearish flow pick "wins" when the stock falls.
  Excess = the same, measured against SPY over the same sessions.

Honesty
  Horizons not yet reached are PENDING, never zero. Every summary says how many picks
  and how many distinct days it rests on (a count of rows is not a count of evidence).
"""

import datetime as dt
import threading
import time

HORIZONS = (5, 20, 60)
WINDOW_DAYS = {"institutional": 120}
DEFAULT_WINDOW = 30
RECENT_MAX = 5000      # every pick for the sortable table; a cap only so the page stays light
DESK_NAMES = {"confluence": "Confluence", "flow": "Unusual Options Flow",
              "institutional": "Institutional", "valuation": "Valuation", "swing": "Swing",
              "growth": "Growth Leaders"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    desk        TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    direction   TEXT NOT NULL,
    score       REAL,
    why         TEXT,
    picked_day  TEXT NOT NULL,
    picked_at   REAL NOT NULL,
    entry_day   TEXT, entry REAL, spy_entry REAL,
    r5 REAL, r20 REAL, r60 REAL,
    x5 REAL, x20 REAL, x60 REAL,
    checked_at  REAL
);
CREATE INDEX IF NOT EXISTS picks_desk ON picks(desk, ticker, picked_day);
"""


# ---------------------------------------------------------------- Eastern time without tzdata
def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def et_now(utc=None):
    """US Eastern wall clock (DST: 2nd Sunday of March to 1st Sunday of November, 2am local)."""
    utc = utc or dt.datetime.utcnow()
    y = utc.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(7))    # 2am EST = 07:00 UTC
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(6))     # 2am EDT = 06:00 UTC
    return utc - dt.timedelta(hours=4 if start <= utc < end else 5)


def market_hours(et=None):
    et = et or et_now()
    mins = et.hour * 60 + et.minute
    return et.weekday() < 5 and 9 * 60 + 45 <= mins <= 16 * 60 + 30


# ---------------------------------------------------------------- the maths
def measure(bars, spy, picked_day, direction, horizons=HORIZONS):
    """bars/spy: [{"date","close"}] oldest first. Returns entry info + per-horizon results."""
    idx = next((i for i, b in enumerate(bars) if b["date"] >= picked_day), None)
    if idx is None:
        return None
    entry = bars[idx]
    sidx = next((i for i, b in enumerate(spy) if b["date"] == entry["date"]), None)
    sign = -1 if direction == "short" else 1
    out = {"entry_day": entry["date"], "entry": entry["close"],
           "spy_entry": spy[sidx]["close"] if sidx is not None else None}
    for n in horizons:
        if idx + n < len(bars):
            ex = bars[idx + n]
            raw = ex["close"] / entry["close"] - 1
            out["r%d" % n] = sign * raw
            sx = next((b for b in spy if b["date"] == ex["date"]), None)
            if sidx is not None and sx:
                out["x%d" % n] = sign * (raw - (sx["close"] / spy[sidx]["close"] - 1))
    return out


def summarize(rows):
    by = {}
    for r in rows:
        d = by.setdefault(r["desk"], {"desk": r["desk"], "name": DESK_NAMES.get(r["desk"], r["desk"]),
                                      "picks": 0, "days": set(), "h": {}})
        d["picks"] += 1
        d["days"].add(r["picked_day"])
        for n in HORIZONS:
            h = d["h"].setdefault(n, {"n": 0, "wins": 0, "ret": 0.0, "xn": 0, "xret": 0.0, "pending": 0})
            v = r.get("r%d" % n)
            if v is None:
                h["pending"] += 1
                continue
            h["n"] += 1
            h["wins"] += v > 0
            h["ret"] += v
            x = r.get("x%d" % n)
            if x is not None:
                h["xn"] += 1
                h["xret"] += x
    out = []
    for d in by.values():
        hs = {}
        for n, h in d["h"].items():
            hs[str(n)] = {"n": h["n"], "pending": h["pending"],
                          "hit": h["wins"] / h["n"] if h["n"] else None,
                          "avg": h["ret"] / h["n"] if h["n"] else None,
                          "excess": h["xret"] / h["xn"] if h["xn"] else None}
        out.append({"desk": d["desk"], "name": d["name"], "picks": d["picks"], "days": len(d["days"]),
                    "horizons": hs})
    order = list(DESK_NAMES)
    out.sort(key=lambda d: order.index(d["desk"]) if d["desk"] in order else 99)
    return out


class TrackRecord:
    def __init__(self, store, desks, market, log=print):
        self.store, self.desks, self.market, self.log = store, desks, market, log
        self.lock = threading.Lock()
        with store.lock:
            store.conn.executescript(SCHEMA)
            store.conn.commit()
        self.last_score = 0.0

    # ---- recording
    def record(self, picks=None, today=None, now=None):
        today = today or et_now().date().isoformat()
        now = now or time.time()
        picks = picks if picks is not None else self.desks.picks()
        added = 0
        with self.lock, self.store.lock:
            c = self.store.conn
            for desk, block in picks.items():
                win = WINDOW_DAYS.get(desk, DEFAULT_WINDOW)
                since = (dt.date.fromisoformat(today) - dt.timedelta(days=win)).isoformat()
                for it in block.get("items") or []:
                    t = str(it.get("ticker") or "").upper()
                    if not t:
                        continue
                    hit = c.execute("SELECT 1 FROM picks WHERE desk=? AND ticker=? AND picked_day>?",
                                    (desk, t, since)).fetchone()
                    if hit:
                        continue
                    c.execute("INSERT INTO picks(desk,ticker,direction,score,why,picked_day,picked_at) "
                              "VALUES(?,?,?,?,?,?,?)",
                              (desk, t, it.get("direction") or "long", it.get("score"), it.get("why"), today, now))
                    added += 1
            c.commit()
        if added:
            self.log("track record: %d new pick(s) recorded" % added)
        return added

    # ---- scoring
    def score(self, today=None, force=False):
        """Fill in whatever horizons have been reached. At most once an hour unless forced."""
        if not force and time.time() - self.last_score < 3600:
            return 0
        self.last_score = time.time()
        today = today or et_now().date()
        with self.store.lock:
            rows = [dict(r) for r in self.store.conn.execute(
                "SELECT * FROM picks WHERE r%d IS NULL ORDER BY picked_day" % HORIZONS[-1]).fetchall()]
        if not rows:
            return 0
        try:
            spy = self.market.daily("SPY", "1Y")
        except Exception as exc:      # noqa: BLE001
            self.log("track record: SPY closes unavailable (%s)" % exc)
            return 0
        done = 0
        for r in rows:
            try:
                bars = self.market.daily(r["ticker"], "1Y")
            except Exception:         # noqa: BLE001 -- try again next hour
                continue
            m = measure(bars, spy, r["picked_day"], r["direction"])
            if not m:
                continue
            sets = {k: v for k, v in m.items() if v is not None}
            sets["checked_at"] = time.time()
            with self.store.lock:
                self.store.conn.execute("UPDATE picks SET %s WHERE id=?" % ",".join("%s=?" % k for k in sets),
                                        list(sets.values()) + [r["id"]])
                self.store.conn.commit()
            done += 1
        return done

    def report(self):
        with self.store.lock:
            rows = [dict(r) for r in self.store.conn.execute(
                "SELECT * FROM picks ORDER BY picked_day DESC, desk, score DESC").fetchall()]
        return {"summary": summarize(rows), "recent": rows[:RECENT_MAX], "total": len(rows),
                "horizons": list(HORIZONS), "first_day": rows[-1]["picked_day"] if rows else None}

    def run_forever(self, interval=1200):
        def loop():
            time.sleep(60)
            while True:
                try:
                    if market_hours():
                        self.record()
                    et = et_now()
                    if et.hour >= 17 or et.weekday() >= 5:
                        self.score()
                except Exception as exc:   # noqa: BLE001
                    self.log("track record error: %s" % exc)
                time.sleep(interval)
        t = threading.Thread(target=loop, daemon=True, name="trackrec")
        t.start()
        return t
