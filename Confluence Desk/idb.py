"""
Confluence Desk -- the institutional (13F) leg.

READ-ONLY reader over the Institutional Desk's own SQLite file. This desk
never re-runs the 13F scan: that scan is ~3,000 API calls and 15-25 minutes,
it has already been run against the 2026-06-30 quarter, and its output is
exactly the small-fund universe this desk is meant to inherit --
  $50M-$2.5B AUM, hedge-fund tagged, <= 75 positions, and companies that pass
  the profitability + size gate.

Opening it `mode=ro` means the two desks can be running at the same time and
this one cannot corrupt the other's database.

STALENESS IS THE WHOLE POINT OF THIS MODULE'S CAREFULNESS. The newest 13F
quarter is 2026-06-30, filed around 2026-08-14; Q3 does not land until
~2026-11-14. Every card therefore carries the report date and the filing
date, and `coverage()` says out loud how old the data is. The design is
"backdrop, no decay", so age carries no points -- but it must never be
readable as live.
"""

import json
import os
import sqlite3

DEFAULT_PATHS = (
    os.path.join("..", "Institutional Desk", "institutional.db"),
    os.path.join("..", "UW Institutional Desk", "institutional.db"),
    os.path.join("..", "Institutional Desk Web", "institutional.db"),
)

BULLISH_KINDS = ("NEW", "ADD")


def locate(explicit=None):
    """
    Find institutional.db, or return None. Never raises.

    AN EXPLICIT PATH IS STRICT. If you name a database and it is not there,
    this returns None rather than quietly searching the default locations:
    silently reading a DIFFERENT quarter's data than the one you asked for is
    strictly worse than failing to find anything.

    This was a real bug, and it is worth recording HOW it surfaced. The test
    for it passed in the build container -- where no sibling database exists,
    so the fallback found nothing and the explicit path appeared to be
    honoured -- and failed the moment the same tests ran on the machine the
    desk actually runs on, where the fallback target is sitting right next
    door. A fallback can only be tested somewhere the fallback target exists.
    """
    if explicit:
        return os.path.abspath(explicit) if os.path.isfile(explicit) else None
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in DEFAULT_PATHS:
        path = os.path.join(here, rel)
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


class Institutional:
    """
    Loaded once per scan and held in memory.

    The whole bullish event set is a few hundred rows -- a few hundred
    kilobytes. Loading it once beats opening a cursor per ticker,
    and it means a mid-scan file replacement (the other desk finishing its own
    scan) cannot hand this one half of one quarter and half of another.
    """

    def __init__(self, path=None):
        self.path = locate(path)
        self.available = False
        self.report_date = None
        self.filed_around = None
        self.error = None
        self.by_ticker = {}
        self.fund_count = 0
        self._load()

    # ------------------------------------------------------------ loading

    def _load(self):
        if not self.path:
            self.error = (
                "institutional.db not found. Expected it next door in "
                "'Institutional Desk'. The board still works -- cards just "
                "cap at 75 without the 13F leg."
            )
            return
        try:
            conn = sqlite3.connect("file:%s?mode=ro" % self.path.replace("?", "%3f"),
                                   uri=True, timeout=5)
        except sqlite3.Error as exc:
            self.error = "could not open institutional.db: %s" % exc
            return
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_report_date'"
            ).fetchone()
            self.report_date = row["value"] if row else None
            if not self.report_date:
                row = conn.execute(
                    "SELECT report_date FROM events ORDER BY report_date DESC LIMIT 1"
                ).fetchone()
                self.report_date = row["report_date"] if row else None
            if not self.report_date:
                self.error = "institutional.db has no scanned quarter yet"
                return

            # 13F is due 45 days after quarter end; most filers use the last
            # week. Shown as "filed around" because the per-filer filing_date
            # varies and the board is quarter-level, not filer-level.
            self.filed_around = _plus_days(self.report_date, 45)

            names = {
                r["cik"]: (r["short_name"] or _titlecase(r["name"] or ""))
                for r in conn.execute(
                    "SELECT cik, name, short_name FROM institutions"
                )
            }

            placeholders = ",".join("?" for _ in BULLISH_KINDS)
            cursor = conn.execute(
                "SELECT ticker, cik, kind, units, units_change, change_perc, "
                "       value, weight, trajectory, score "
                "  FROM events "
                " WHERE report_date = ? AND kind IN (%s)" % placeholders,
                (self.report_date,) + BULLISH_KINDS,
            )
            for row in cursor:
                ticker = (row["ticker"] or "").upper()
                if not ticker:
                    continue
                bucket = self.by_ticker.setdefault(ticker, [])
                bucket.append({
                    "cik": row["cik"],
                    "fund": names.get(row["cik"], "Unknown fund"),
                    "kind": row["kind"],
                    "units": row["units"],
                    "units_change": row["units_change"],
                    "change_perc": row["change_perc"],
                    "value": row["value"],
                    "weight": row["weight"] or 0.0,
                    "trajectory": row["trajectory"],
                    "fund_score": row["score"],
                })

            # Funds actually IN the backdrop, not every filer in the table.
            # `institutions` holds all ~8,900 13F filers the Institutional Desk
            # ever paged; reporting that as "~9,000 small funds" on the board
            # implies the whole 13F universe was screened in, when the point of
            # inheriting that desk's screen is that it is NARROW.
            self.fund_count = len({
                e["cik"] for events in self.by_ticker.values() for e in events
            })
            self.available = True
        except sqlite3.Error as exc:
            self.error = "institutional.db read failed: %s" % exc
        finally:
            conn.close()

    # ------------------------------------------------------------- lookup

    def get(self, ticker):
        """
        The per-company 13F aggregate, or None when this ticker is outside the
        small-fund universe.

        None and "zero funds" are different facts and the card says so: one
        means the funds this desk tracks are not in the name, the other means
        the desk has no 13F data at all.
        """
        if not self.available:
            return None
        events = self.by_ticker.get((ticker or "").upper())
        if not events:
            return None
        events = sorted(events, key=lambda e: -(e["value"] or 0.0))
        return {
            "ticker": ticker.upper(),
            "distinct_funds": len({e["cik"] for e in events}),
            "new_count": sum(1 for e in events if e["kind"] == "NEW"),
            "add_count": sum(1 for e in events if e["kind"] == "ADD"),
            "total_value": sum(e["value"] or 0.0 for e in events),
            "best_position_weight": max(e["weight"] or 0.0 for e in events),
            "trajectories": [e["trajectory"] for e in events if e["trajectory"]],
            "funds": events,
            "report_date": self.report_date,
            "filed_around": self.filed_around,
        }

    def coverage(self):
        return {
            "available": self.available,
            "path": self.path,
            "error": self.error,
            "report_date": self.report_date,
            "filed_around": self.filed_around,
            "tickers": len(self.by_ticker),
            "funds": self.fund_count,
            "age_days": _days_since(self.filed_around),
        }


# ---------------------------------------------------------------- helpers

def _plus_days(iso_date, days):
    import datetime as dt
    try:
        return (dt.date.fromisoformat(str(iso_date)[:10])
                + dt.timedelta(days=days)).isoformat()
    except (TypeError, ValueError):
        return None


def _days_since(iso_date):
    import datetime as dt
    try:
        return (dt.date.today() - dt.date.fromisoformat(str(iso_date)[:10])).days
    except (TypeError, ValueError):
        return None


def _titlecase(name):
    """
    The API only supplies short_name for some filers, so the fallback is the
    legal name -- which arrives SHOUTING. Title-casing it keeps fund names
    from being half title-case and half caps on the same card (Institutional
    Desk bug #4).
    """
    if not name:
        return ""
    if name.isupper():
        out = name.title()
        for fixup in ("Llc", "Lp", "L.P.", "Llp", "Ltd", "Inc", "Lc", "Plc"):
            out = out.replace(fixup, fixup.upper())
        return out
    return name
