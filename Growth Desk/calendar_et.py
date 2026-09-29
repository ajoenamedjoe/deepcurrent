"""US Eastern time and the NYSE session calendar, stdlib only (no tzdata on Windows Python)."""

import datetime as dt

NYSE_HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}
OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60
DAILY_SCAN_MIN = 16 * 60 + 20        # the after-close scan waits for the closing prints


def _nth_sunday(y, month, n):
    d = dt.date(y, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def offset_hours(utc):
    start = dt.datetime.combine(_nth_sunday(utc.year, 3, 2), dt.time(7))
    end = dt.datetime.combine(_nth_sunday(utc.year, 11, 1), dt.time(6))
    return 4 if start <= utc < end else 5


def et_now(utc=None):
    utc = utc or dt.datetime.utcnow()
    return utc - dt.timedelta(hours=offset_hours(utc))


def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in NYSE_HOLIDAYS


def last_session(et=None):
    """The most recent trading day whose close (plus 20 minutes) has passed."""
    et = et or et_now()
    d = et.date()
    if not (is_trading_day(d) and et.hour * 60 + et.minute >= DAILY_SCAN_MIN):
        d -= dt.timedelta(days=1)
        while not is_trading_day(d):
            d -= dt.timedelta(days=1)
    return d


def scan_due_after(session):
    """UTC epoch seconds of the session's after-close scan time."""
    local = dt.datetime.combine(session, dt.time(DAILY_SCAN_MIN // 60, DAILY_SCAN_MIN % 60))
    guess = local + dt.timedelta(hours=5)
    utc = local + dt.timedelta(hours=offset_hours(guess))
    return (utc - dt.datetime(1970, 1, 1)).total_seconds()


def market_open(et=None, start_min=OPEN_MIN + 20):
    """True during the regular session, from 9:50 (the first 20 minutes' volume pace is noise)."""
    et = et or et_now()
    m = et.hour * 60 + et.minute
    return is_trading_day(et.date()) and start_min <= m < CLOSE_MIN


def session_fraction(et=None):
    et = et or et_now()
    m = et.hour * 60 + et.minute + et.second / 60.0
    return max(0.0, min(1.0, (m - OPEN_MIN) / float(CLOSE_MIN - OPEN_MIN)))
