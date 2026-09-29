"""
Interactive P&L: broker CSV -> fills -> FIFO round trips -> daily P&L.

Formats recognised (auto-detected from the header, never from the file name):
  tos         thinkorswim "Account Statement" export (Account Trade History,
              fees matched in from Cash Balance / Futures Statements)
  schwab      schwab.com  History > Transactions CSV
  fidelity    fidelity.com Activity & Orders > History CSV
  robinhood   Robinhood account activity report CSV
  webull      Webull Orders Records CSV
  ibkr        Interactive Brokers Activity Statement CSV (Trades section)
  ibkr_flex   Interactive Brokers Flex Query trades CSV
  tastytrade  tastytrade transaction history CSV
  tradovate   Tradovate futures exports: Orders (filled orders), Fills, or Performance (round trips)
  generic     anything with date / symbol / side / quantity / price columns,
              optionally with a column mapping chosen on the page

Honesty rules (from the desks, applied to money):
  * A close with no matching open inside the uploaded history (the position
    was opened before the statement starts) is UNMATCHED: it is listed, and
    it is NOT counted as profit or loss. Unknown cost basis is not zero.
  * An option still open after its expiry date is closed at 0 on that date
    and marked "expired (inferred)", so a worthless expiry the export left
    out is not an open position forever.
  * Realised P&L uses FIFO lots per account and contract. A broker using a
    different lot method (specific-lot, average cost) can report different
    per-trade numbers; the daily total over a full round trip is the same.
"""

import csv
import hashlib
import io
import re
from collections import defaultdict, deque
from datetime import date, datetime

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
FUT_MONTH = "FGHJKMNQUVXZ"

# Point value per futures root. Micro contracts listed explicitly.
FUT_MULT = {
    "ES": 50, "MES": 5, "NQ": 20, "MNQ": 2, "RTY": 50, "M2K": 5, "YM": 5, "MYM": 0.5,
    "CL": 1000, "MCL": 100, "QM": 500, "NG": 10000, "GC": 100, "MGC": 10, "SI": 5000, "SIL": 1000,
    "HG": 25000, "ZB": 1000, "UB": 1000, "ZN": 1000, "ZF": 1000, "ZT": 2000, "6E": 125000,
    "6J": 12500000, "6B": 62500, "BTC": 5, "MBT": 0.1, "ETH": 50, "MET": 0.1, "VX": 1000,
    "ZC": 50, "ZS": 50, "ZW": 50, "HE": 400, "LE": 400,
}


class ParseError(Exception):
    def __init__(self, msg, columns=None):
        super().__init__(msg)
        self.columns = columns or []


# ======================================================================= helpers
def money(v):
    """'$1,234.56' -> 1234.56 ; '($1.23)' -> -1.23 ; '-$5' -> -5 ; '' -> None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "").replace("@", "")
    if s in ("", "--", "-", "None", "N/A", "n/a", "~"):
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    s = s.rstrip("S").strip()           # Robinhood "1S" on expirations
    try:
        x = float(s)
    except ValueError:
        return None
    return -x if neg else x


def _yy(y):
    y = int(y)
    return y + 2000 if y < 100 else y


def parse_dt(s):
    """Many broker date formats -> 'YYYY-MM-DDTHH:MM:SS' (time 00:00:00 if absent)."""
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    t = re.split(r"\s+as of\s+", t, flags=re.I)[0].strip()
    t = re.sub(r"\s+(EDT|EST|ET|CDT|CST|UTC|GMT)$", "", t, flags=re.I)
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:[;, ]*(\d{2}):?(\d{2}):?(\d{2})?)?$", t)      # IBKR flex
    if m:
        y, mo, d, hh, mi, ss = m.groups()
        return "%04d-%02d-%02dT%02d:%02d:%02d" % (int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0))
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", t)
    if m:
        y, mo, d, hh, mi, ss = m.groups()
        return "%04d-%02d-%02dT%02d:%02d:%02d" % (int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})(?:[ ,]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AP]M)?)?", t, re.I)
    if m:
        mo, d, y, hh, mi, ss, ap = m.groups()
        hh = int(hh or 0)
        if ap and ap.upper() == "PM" and hh < 12:
            hh += 12
        if ap and ap.upper() == "AM" and hh == 12:
            hh = 0
        return "%04d-%02d-%02dT%02d:%02d:%02d" % (_yy(y), int(mo), int(d), hh, int(mi or 0), int(ss or 0))
    m = re.match(r"^(\d{1,2})[ -]([A-Za-z]{3})[ -](\d{2,4})", t)      # 27 SEP 26
    if m and m.group(2).upper() in MONTHS:
        return "%04d-%02d-%02dT00:00:00" % (_yy(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1)))
    return None


def exp_date(s):
    """Option expiry in any broker spelling -> 'YYYY-MM-DD' or None."""
    if not s:
        return None
    t = re.sub(r"\(.*?\)", "", str(s)).strip()
    m = re.match(r"^(\d{1,2})\s*([A-Za-z]{3})\s*(\d{2,4})$", t)          # 27 SEP 26 / 27SEP26
    if m and m.group(2).upper() in MONTHS:
        return "%04d-%02d-%02d" % (_yy(m.group(3)), MONTHS[m.group(2).upper()], int(m.group(1)))
    dt = parse_dt(t)
    return dt[:10] if dt else None


def fmt_strike(x):
    x = float(x)
    return ("%d" % x) if x == int(x) else ("%g" % x)


def opt_key(root, expiry, strike, right):
    return "%s %s %s %s" % (root.upper(), expiry or "?", fmt_strike(strike), right.upper()[0])


def fut_root(sym):
    """'/ESZ26' '/ESZ6' 'ESZ6' '/ES' -> 'ES'; 'MESU26' -> 'MES'."""
    s = sym.lstrip("/").split(":")[0].upper()
    m = re.match(r"^([A-Z0-9]{1,4}?)([FGHJKMNQUVXZ])(\d{1,2})$", s)
    return m.group(1) if m else s


def fut_mult(sym):
    return FUT_MULT.get(fut_root(sym))


OCC_RE = re.compile(r"^-?\.?([A-Z][A-Z0-9.]{0,5}?)\d?\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")
SHORT_OCC_RE = re.compile(r"^-?\.?([A-Z][A-Z.]{0,5})(\d{2})(\d{2})(\d{2})([CP])(\d+(?:\.\d+)?)$")


def parse_symbol(sym, desc=""):
    """Return an instrument dict: asset, underlying, key, expiry, strike, right, mult."""
    s = (sym or "").strip().upper()
    d = (desc or "").strip()
    compact = s.replace(" ", "")
    m = OCC_RE.match(compact)
    if m:
        root, yy, mm, dd, cp, k = m.groups()
        exp = "%04d-%s-%s" % (2000 + int(yy), mm, dd)
        return _opt(root, exp, int(k) / 1000.0, cp)
    m = SHORT_OCC_RE.match(compact)                          # Fidelity -SPY260927C570
    if m:
        root, yy, mm, dd, cp, k = m.groups()
        return _opt(root, "%04d-%s-%s" % (2000 + int(yy), mm, dd), float(k), cp)
    # Schwab: "SPY 09/27/2026 570.00 C"
    m = re.match(r"^([A-Z][A-Z0-9./]*)\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+([\d.]+)\s+([CP])", s)
    if m:
        return _opt(m.group(1), exp_date(m.group(2)), float(m.group(3)), m.group(4))
    # IBKR: "SPY 27SEP26 570 C"
    m = re.match(r"^([A-Z][A-Z0-9.]*)\s+(\d{1,2}[A-Z]{3}\d{2})\s+([\d.]+)\s+([CP])$", s)
    if m:
        return _opt(m.group(1), exp_date(m.group(2)), float(m.group(3)), m.group(4))
    # Robinhood description: "SPY 9/27/2026 Call $570.00"
    m = re.search(r"\b([A-Z][A-Z0-9.]*)\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+(CALL|PUT)\s+\$?([\d,.]+)", d.upper())
    if m:
        return _opt(m.group(1), exp_date(m.group(2)), float(m.group(4).replace(",", "")), m.group(3))
    if s.startswith("/"):
        return {"asset": "future", "underlying": fut_root(s), "key": s.split(":")[0],
                "expiry": None, "strike": None, "right": None, "mult": fut_mult(s)}
    s = s.split()[0] if s else ""
    return {"asset": "stock", "underlying": s, "key": s, "expiry": None, "strike": None,
            "right": None, "mult": 1.0}


def _opt(root, exp, strike, right):
    root = root.upper().lstrip("-.")
    mult = 100.0
    if root.startswith("/"):
        mult = fut_mult(root) or 100.0
    return {"asset": "option", "underlying": root, "key": opt_key(root, exp, strike, right),
            "expiry": exp, "strike": float(strike), "right": right.upper()[0], "mult": mult}


def side_of(text):
    """Direction (+1 buy / -1 sell) and position effect from any broker's action text."""
    t = " %s " % (text or "").upper().replace("_", " ")
    buy = re.search(r"\b(BUY|BOT|BOUGHT|BTO|BTC|REINVEST\w*|COVER)\b", t)
    sell = re.search(r"\b(SELL|SOLD|STO|STC|SHORT)\b", t)
    if "BUY TO COVER" in t:
        sell = None
    if buy and sell:
        sign = 1 if buy.start() < sell.start() else -1
    else:
        sign = 1 if buy else -1 if sell else None
    effect = None
    if re.search(r"TO OPEN|OPENING|\bBTO\b|\bSTO\b|SELL SHORT|SHORT SALE", t):
        effect = "open"
    if re.search(r"TO CLOSE|CLOSING|\bBTC\b|\bSTC\b|COVER", t):
        effect = "close"
    return sign, effect


def event_kind(text):
    t = (text or "").upper()
    if re.search(r"EXPIR|OEXP|REMOVAL OF OPTION", t):
        return "expire"
    if re.search(r"ASSIGN|OASGN", t):
        return "assign"
    if re.search(r"EXERC|OEXCS", t):
        return "exercise"
    return None


def read_rows(text):
    return list(csv.reader(io.StringIO(text)))


def norm_header(h):
    return re.sub(r"\s+", " ", (h or "").replace("﻿", "").strip().strip('"').lower())


def find_header(rows, required, scan=40):
    """Index of the first row containing every required header name."""
    for i, r in enumerate(rows[:scan]):
        names = {norm_header(c) for c in r}
        if all(any(req == n for n in names) for req in required):
            return i
    return None


def col(header, *names):
    hs = [norm_header(h) for h in header]
    for n in names:
        if n in hs:
            return hs.index(n)
    return None


def cell(row, i):
    return row[i].strip() if i is not None and i < len(row) and row[i] is not None else ""


def make_fill(broker, account, ts, inst, qty, price, fees=0.0, effect=None, kind="trade", desc="",
              amount=None, raw_side=""):
    return {"broker": broker, "account": account or "", "ts": ts, "date": ts[:10],
            "key": inst["key"], "underlying": inst["underlying"], "asset": inst["asset"],
            "expiry": inst["expiry"], "strike": inst["strike"], "right": inst["right"],
            "mult": inst["mult"], "qty": qty, "price": price, "fees": abs(fees or 0.0),
            "effect": effect, "kind": kind, "desc": desc, "amount": amount, "side_text": raw_side}


def fees_from_amount(qty, price, mult, amount):
    """Net cash vs gross notional -> fees, when the export gives no fee column."""
    if amount is None or qty is None or price is None or not mult:
        return 0.0
    gross = abs(qty) * price * mult
    diff = abs(gross - abs(amount))
    return diff if gross and diff < 0.05 * gross + 5 else 0.0


# ======================================================================= parsers
def parse_tos(rows):
    account = ""
    if rows and rows[0]:
        m = re.search(r"Account Statement for\s+(\S+)", ",".join(rows[0]))
        if m:
            account = "TOS " + m.group(1)[-4:]
    sections = {}
    cur = None
    for r in rows:
        nonempty = [c for c in r if c.strip()]
        if len(nonempty) == 1 and len(r) <= 2 and r[0].strip() == nonempty[0].strip() \
                and not re.search(r"\d", nonempty[0][:3]):
            cur = nonempty[0].strip()
            sections[cur] = []
            continue
        if cur is not None:
            sections[cur].append(r)
    fees = defaultdict(float)
    for name in ("Cash Balance", "Futures Statements"):
        sec = [r for r in sections.get(name, []) if any(c.strip() for c in r)]
        if not sec:
            continue
        h = sec[0]
        di = col(h, "exec date", "date")
        ti = col(h, "exec time", "time")
        ty = col(h, "type")
        mi = col(h, "misc fees")
        ci = col(h, "commissions & fees", "commissions")
        for r in sec[1:]:
            if ty is not None and cell(r, ty).upper() not in ("TRD", "TRADE"):
                continue
            ts = parse_dt("%s %s" % (cell(r, di), cell(r, ti)))
            if ts:
                fees[ts] += abs(money(cell(r, mi)) or 0) + abs(money(cell(r, ci)) or 0)
    fills, notes = [], []
    th = [r for r in sections.get("Account Trade History", []) if any(c.strip() for c in r)]
    if th:
        h = th[0]
        ix = {k: col(h, k) for k in ("exec time", "spread", "side", "qty", "pos effect", "symbol",
                                      "exp", "strike", "type", "price", "net price")}
        last_ts = None
        for r in th[1:]:
            ts = parse_dt(cell(r, ix["exec time"])) or last_ts
            if not ts:
                continue
            last_ts = ts
            typ = cell(r, ix["type"]).upper()
            sym = cell(r, ix["symbol"])
            qty = money(cell(r, ix["qty"]))
            price = money(cell(r, ix["price"]))
            if price is None:
                price = money(cell(r, ix["net price"]))
            if qty is None or price is None or not sym:
                continue
            if typ in ("CALL", "PUT"):
                inst = _opt(sym, exp_date(cell(r, ix["exp"])), money(cell(r, ix["strike"])) or 0, typ)
            else:
                inst = parse_symbol(sym)
            if inst["mult"] is None:
                notes.append("Unknown futures multiplier for %s -- skipped." % sym)
                continue
            eff = cell(r, ix["pos effect"]).upper()
            effect = "open" if "OPEN" in eff else "close" if "CLOSE" in eff else None
            side = cell(r, ix["side"]).upper()
            if side == "SELL" and qty > 0:
                qty = -qty
            spread = cell(r, ix["spread"])
            desc = "%s %s" % (spread, inst["key"]) if spread and spread.upper() != "SINGLE" else inst["key"]
            fills.append(make_fill("tos", account, ts, inst, qty, price, effect=effect, desc=desc.strip()))
        # hand each Cash Balance fee row to the fills that share its timestamp
        by_ts = defaultdict(list)
        for f in fills:
            by_ts[f["ts"]].append(f)
        unmatched = 0.0
        for ts, fee in fees.items():
            group = by_ts.get(ts)
            if not group:
                unmatched += fee
                continue
            tot = sum(abs(f["qty"]) for f in group) or 1
            for f in group:
                f["fees"] += fee * abs(f["qty"]) / tot
        if unmatched > 0.005:
            notes.append("$%.2f of fees in Cash Balance had no trade with the same timestamp; "
                         "not included." % unmatched)
    else:
        # no Trade History section: read single-leg trades from Cash Balance descriptions
        sec = [r for r in sections.get("Cash Balance", []) if any(c.strip() for c in r)]
        if not sec:
            raise ParseError("This thinkorswim statement has no Account Trade History or Cash Balance "
                             "section. Export it with Account Statement > export to CSV.")
        h = sec[0]
        di, ti, ty, de = col(h, "date"), col(h, "time"), col(h, "type"), col(h, "description")
        mi, ci = col(h, "misc fees"), col(h, "commissions & fees")
        skipped = 0
        for r in sec[1:]:
            if cell(r, ty).upper() not in ("TRD", "RAD"):
                continue
            ts = parse_dt("%s %s" % (cell(r, di), cell(r, ti)))
            d = cell(r, de).upper()
            fee = abs(money(cell(r, mi)) or 0) + abs(money(cell(r, ci)) or 0)
            m = re.match(r"^(BOT|SOLD)\s+([+-]?[\d,]+)\s+(.*?)\s+@\s*([\d.,]+)", d)
            if not m or not ts:
                if "EXPIRATION" in d:
                    m2 = re.search(r"([+-]?\d+)\s+([A-Z./]+)\s+\d+\s+(?:\(.*?\)\s+)?(\d{1,2} [A-Z]{3} \d{2})\s+([\d.]+)\s+(CALL|PUT)", d)
                    if m2 and ts:
                        inst = _opt(m2.group(2), exp_date(m2.group(3)), float(m2.group(4)), m2.group(5))
                        fills.append(make_fill("tos", account, ts, inst, None, 0.0, kind="expire",
                                               desc="expired " + inst["key"]))
                        continue
                skipped += 1
                continue
            qty = money(m.group(2))
            body = m.group(3)
            price = money(m.group(4))
            mo = re.match(r"^([A-Z./]+)\s+\d+\s+(?:\(.*?\)\s+)?(\d{1,2} [A-Z]{3} \d{2})\s+([\d.]+)\s+(CALL|PUT)$", body)
            if mo:
                inst = _opt(mo.group(1), exp_date(mo.group(2)), float(mo.group(3)), mo.group(4))
            elif re.match(r"^/?[A-Z.]+(:\w+)?$", body):
                inst = parse_symbol(body)
            else:
                skipped += 1           # spreads cannot be split from a one-line description
                continue
            if inst["mult"] is None:
                skipped += 1
                continue
            if m.group(1) == "SOLD" and qty > 0:
                qty = -qty
            fills.append(make_fill("tos", account, ts, inst, qty, price, fees=fee, desc=inst["key"]))
        if skipped:
            notes.append("%d Cash Balance trade rows could not be read (multi-leg spreads are only "
                         "itemised in Account Trade History)." % skipped)
    return fills, account, notes


SCHWAB_ACTIONS = {
    "buy": (1, None), "sell": (-1, None), "buy to open": (1, "open"), "sell to close": (-1, "close"),
    "sell to open": (-1, "open"), "buy to close": (1, "close"), "sell short": (-1, "open"),
    "buy to cover": (1, "close"), "reinvest shares": (1, "open"),
}


def parse_schwab(rows):
    hi = find_header(rows, ["date", "action", "symbol", "quantity", "price"])
    h = rows[hi]
    ix = {k: col(h, k) for k in ("date", "action", "symbol", "description", "quantity", "price",
                                  "fees & comm", "amount")}
    fills, notes = [], []
    for r in rows[hi + 1:]:
        act = cell(r, ix["action"]).lower()
        ts = parse_dt(cell(r, ix["date"]))
        if not ts or not act:
            continue
        inst = parse_symbol(cell(r, ix["symbol"]), cell(r, ix["description"]))
        if not inst["key"]:
            continue
        qty = money(cell(r, ix["quantity"]))
        kind = event_kind(act)
        if kind:
            fills.append(make_fill("schwab", "", ts, inst, None, 0.0, kind=kind,
                                   desc="%s %s" % (kind, inst["key"])))
            continue
        if act not in SCHWAB_ACTIONS or qty is None:
            continue
        sign, effect = SCHWAB_ACTIONS[act]
        price = money(cell(r, ix["price"]))
        if price is None:
            continue
        fee = money(cell(r, ix["fees & comm"]))
        amt = money(cell(r, ix["amount"]))
        if fee is None:
            fee = fees_from_amount(qty, price, inst["mult"], amt)
        fills.append(make_fill("schwab", "", ts, inst, sign * abs(qty), price, fees=fee, effect=effect,
                               desc=cell(r, ix["description"]) or inst["key"], amount=amt, raw_side=act))
    return fills, "", notes


def parse_fidelity(rows):
    hi = find_header(rows, ["run date", "action", "symbol"])
    h = rows[hi]
    ix = {"date": col(h, "run date"), "action": col(h, "action"), "symbol": col(h, "symbol"),
          "desc": col(h, "description"), "qty": col(h, "quantity"), "price": col(h, "price ($)", "price"),
          "comm": col(h, "commission ($)", "commission"), "fees": col(h, "fees ($)", "fees"),
          "amount": col(h, "amount ($)", "amount"), "account": col(h, "account")}
    fills = []
    for r in rows[hi + 1:]:
        ts = parse_dt(cell(r, ix["date"]))
        act = cell(r, ix["action"])
        if not ts or not act:
            continue
        inst = parse_symbol(cell(r, ix["symbol"]), cell(r, ix["desc"]))
        if not inst["key"]:
            continue
        acct = cell(r, ix["account"])
        kind = event_kind(act)
        if kind:
            fills.append(make_fill("fidelity", acct, ts, inst, None, 0.0, kind=kind,
                                   desc="%s %s" % (kind, inst["key"])))
            continue
        sign, effect = side_of(act)
        if not re.search(r"YOU BOUGHT|YOU SOLD|OPENING TRANSACTION|CLOSING TRANSACTION|REINVESTMENT|SHORT SALE",
                         act.upper()):
            continue
        qty = money(cell(r, ix["qty"]))
        price = money(cell(r, ix["price"]))
        if qty is None or price is None or qty == 0:
            continue
        sign = -1 if qty < 0 else 1 if sign is None else sign
        fee = abs(money(cell(r, ix["comm"])) or 0) + abs(money(cell(r, ix["fees"])) or 0)
        fills.append(make_fill("fidelity", acct, ts, inst, sign * abs(qty), price, fees=fee, effect=effect,
                               desc=cell(r, ix["desc"]) or inst["key"], amount=money(cell(r, ix["amount"])),
                               raw_side=act))
    return fills, "", []


ROBINHOOD_CODES = {"BUY": (1, None), "SELL": (-1, None), "BTO": (1, "open"), "STC": (-1, "close"),
                   "STO": (-1, "open"), "BTC": (1, "close")}


def parse_robinhood(rows):
    hi = find_header(rows, ["activity date", "trans code"])
    h = rows[hi]
    ix = {"date": col(h, "activity date"), "inst": col(h, "instrument"), "desc": col(h, "description"),
          "code": col(h, "trans code"), "qty": col(h, "quantity"), "price": col(h, "price"),
          "amount": col(h, "amount")}
    fills = []
    for r in rows[hi + 1:]:
        ts = parse_dt(cell(r, ix["date"]))
        code = cell(r, ix["code"]).upper()
        if not ts or not code:
            continue
        desc = cell(r, ix["desc"])
        inst = parse_symbol(cell(r, ix["inst"]), desc)
        if not inst["key"]:
            continue
        kind = event_kind(code)
        if kind:
            fills.append(make_fill("robinhood", "", ts, inst, None, 0.0, kind=kind,
                                   desc="%s %s" % (kind, inst["key"])))
            continue
        if code not in ROBINHOOD_CODES:
            continue
        sign, effect = ROBINHOOD_CODES[code]
        qty, price = money(cell(r, ix["qty"])), money(cell(r, ix["price"]))
        if qty is None or price is None:
            continue
        amt = money(cell(r, ix["amount"]))
        fills.append(make_fill("robinhood", "", ts, inst, sign * abs(qty), price,
                               fees=fees_from_amount(qty, price, inst["mult"], amt), effect=effect,
                               desc=desc.splitlines()[0] if desc else inst["key"], amount=amt, raw_side=code))
    return fills, "", []


def parse_webull(rows):
    hi = find_header(rows, ["symbol", "side", "status", "filled"])
    h = rows[hi]
    ix = {"name": col(h, "name"), "sym": col(h, "symbol"), "side": col(h, "side"), "status": col(h, "status"),
          "filled": col(h, "filled"), "avg": col(h, "avg price"), "price": col(h, "price"),
          "time": col(h, "filled time", "filled time ", "create time", "placed time")}
    fills = []
    for r in rows[hi + 1:]:
        if "FILLED" not in cell(r, ix["status"]).upper():
            continue
        ts = parse_dt(cell(r, ix["time"]))
        qty = money(cell(r, ix["filled"]))
        price = money(cell(r, ix["avg"]))
        if price is None:
            price = money(cell(r, ix["price"]))
        if not ts or not qty or price is None:
            continue
        inst = parse_symbol(cell(r, ix["sym"]), cell(r, ix["name"]))
        sign, effect = side_of(cell(r, ix["side"]))
        if sign is None:
            continue
        fills.append(make_fill("webull", "", ts, inst, sign * abs(qty), price, effect=effect,
                               desc=cell(r, ix["name"]) or inst["key"], raw_side=cell(r, ix["side"])))
    return fills, "", ["Webull order exports carry no fees; P&L is before commissions."]


def parse_ibkr(rows):
    account = ""
    mults = {}
    headers = {}
    fills = []
    for r in rows:
        if len(r) < 3:
            continue
        sec, kind = r[0].strip(), r[1].strip()
        if kind == "Header":
            headers[sec] = r
            continue
        if kind != "Data" or sec not in headers:
            continue
        h = headers[sec]
        if sec == "Account Information" and len(r) > 3 and r[2].strip() == "Account":
            account = "IBKR " + r[3].strip()[-4:]
        if sec == "Financial Instrument Information":
            si, mi = col(h, "symbol"), col(h, "multiplier")
            if si is not None and mi is not None and money(cell(r, mi)):
                mults[cell(r, si).upper()] = money(cell(r, mi))
    for r in rows:
        if len(r) < 3 or r[0].strip() != "Trades" or r[1].strip() != "Data":
            continue
        h = headers.get("Trades")
        disc = cell(r, col(h, "datadiscriminator"))
        if disc and disc not in ("Order", "Trade", "ExecutionTrade"):
            continue
        cat = cell(r, col(h, "asset category"))
        if "forex" in cat.lower():
            continue
        sym = cell(r, col(h, "symbol"))
        ts = parse_dt(cell(r, col(h, "date/time")))
        qty = money(cell(r, col(h, "quantity")))
        price = money(cell(r, col(h, "t. price")))
        fee = money(cell(r, col(h, "comm/fee", "comm in usd")))
        code = cell(r, col(h, "code"))
        if not ts or qty is None or price is None or not sym:
            continue
        inst = parse_symbol(sym)
        if "future" in cat.lower() and "option" not in cat.lower():
            m = mults.get(sym.upper()) or fut_mult(sym)
            inst = {"asset": "future", "underlying": fut_root(sym), "key": "/" + sym.upper(),
                    "expiry": None, "strike": None, "right": None, "mult": m}
        elif inst["asset"] == "option" and sym.upper() in mults:
            inst["mult"] = mults[sym.upper()]
        if not inst["mult"]:
            continue
        codes = set(re.split(r"[;,\s]+", code.upper()))
        effect = "open" if "O" in codes else "close" if "C" in codes else None
        k = "expire" if "EP" in codes else "assign" if "A" in codes else "exercise" if "EX" in codes else "trade"
        fills.append(make_fill("ibkr", account, ts, inst, qty, price, fees=fee, effect=effect, kind=k,
                               desc=inst["key"]))
    if not fills and "Trades" not in headers:
        raise ParseError("This Interactive Brokers file has no Trades section.")
    return fills, account, []


def parse_ibkr_flex(rows):
    hi = find_header(rows, ["symbol", "quantity", "tradeprice"])
    h = rows[hi]
    g = lambda r, *n: cell(r, col(h, *n))   # noqa: E731
    fills = []
    for r in rows[hi + 1:]:
        if norm_header(cell(r, 0)) == norm_header(h[0]):
            continue                         # repeated header per account
        ts = parse_dt(g(r, "datetime", "date/time") or g(r, "tradedate"))
        qty, price = money(g(r, "quantity")), money(g(r, "tradeprice"))
        if not ts or qty is None or price is None:
            continue
        cls = g(r, "assetclass").upper()
        mult = money(g(r, "multiplier")) or 1.0
        if cls in ("OPT", "FOP") and g(r, "put/call"):
            inst = _opt(g(r, "underlyingsymbol") or g(r, "symbol").split()[0], exp_date(g(r, "expiry")),
                        money(g(r, "strike")) or 0, g(r, "put/call"))
            inst["mult"] = mult
        elif cls == "FUT":
            sym = g(r, "symbol")
            inst = {"asset": "future", "underlying": fut_root(sym), "key": "/" + sym.upper(),
                    "expiry": None, "strike": None, "right": None, "mult": mult}
        elif cls == "CASH":
            continue
        else:
            inst = parse_symbol(g(r, "symbol"))
        oc = g(r, "open/closeindicator", "open/close").upper()
        effect = "open" if oc.startswith("O") else "close" if oc.startswith("C") else None
        acct = g(r, "clientaccountid", "accountid")
        fills.append(make_fill("ibkr", "IBKR " + acct[-4:] if acct else "", ts, inst, qty, price,
                               fees=money(g(r, "ibcommission", "commission")), effect=effect, desc=inst["key"]))
    return fills, "", []


def parse_tasty(rows):
    hi = find_header(rows, ["date", "type", "action", "symbol", "instrument type"])
    h = rows[hi]
    g = lambda r, *n: cell(r, col(h, *n))   # noqa: E731
    fills = []
    for r in rows[hi + 1:]:
        ts = parse_dt(g(r, "date"))
        typ = g(r, "type").upper()
        if not ts or typ not in ("TRADE", "RECEIVE DELIVER"):
            continue
        itype = g(r, "instrument type").lower()
        sym = g(r, "symbol")
        mult = money(g(r, "multiplier")) or (100.0 if "option" in itype else 1.0)
        if "option" in itype and g(r, "call or put"):
            inst = _opt(g(r, "underlying symbol", "root symbol") or sym.split()[0], exp_date(g(r, "expiration date")),
                        money(g(r, "strike price")) or 0, g(r, "call or put"))
            inst["mult"] = mult
        elif "future" in itype:
            inst = {"asset": "future", "underlying": fut_root(sym), "key": sym.split()[0],
                    "expiry": None, "strike": None, "right": None, "mult": fut_mult(sym) or mult}
        else:
            inst = parse_symbol(sym)
        sub = g(r, "sub type")
        kind = event_kind(sub)
        if typ == "RECEIVE DELIVER" and kind and inst["asset"] == "option":
            fills.append(make_fill("tastytrade", "", ts, inst, None, 0.0, kind=kind,
                                   desc="%s %s" % (kind, inst["key"])))
            continue
        sign, effect = side_of(g(r, "action"))
        qty, price = money(g(r, "quantity")), money(g(r, "average price"))
        if sign is None or not qty or price is None:
            continue
        price = abs(price)
        if price and inst["mult"] and abs(money(g(r, "value")) or 0) and \
                abs(abs(money(g(r, "value"))) - qty * price) < 0.02 * qty * price and inst["mult"] != 1:
            price = price / inst["mult"]      # some exports give average price per contract
        fee = abs(money(g(r, "commissions")) or 0) + abs(money(g(r, "fees")) or 0)
        fills.append(make_fill("tastytrade", "", ts, inst, sign * abs(qty), price, fees=fee, effect=effect,
                               desc=g(r, "description") or inst["key"], amount=money(g(r, "value")),
                               raw_side=g(r, "action")))
    return fills, "", []


GENERIC = {
    "date": ["date/time", "datetime", "exec time", "execution time", "filled time", "trade date", "date",
             "activity date", "run date", "time", "timestamp"],
    "symbol": ["symbol", "ticker", "instrument", "security", "contract", "description"],
    "side": ["side", "action", "buy/sell", "trans code", "transaction type", "type"],
    "qty": ["quantity", "qty", "filled", "shares", "contracts", "size", "amount"],
    "price": ["price", "fill price", "avg price", "average price", "t. price", "trade price", "execution price"],
    "fees": ["fees", "fee", "commission", "commissions", "fees & comm", "comm/fee"],
    "mult": ["multiplier", "mult"],
    "effect": ["pos effect", "position effect", "open/close"],
}


def guess_mapping(header):
    hs = [norm_header(h) for h in header]
    used, out = set(), {}
    for field in ("date", "symbol", "side", "price", "qty", "fees", "mult", "effect"):
        for name in GENERIC[field]:
            if name in hs and hs.index(name) not in used:
                out[field] = header[hs.index(name)]
                used.add(hs.index(name))
                break
    return out


def parse_generic(rows, mapping=None):
    hi = None
    for i, r in enumerate(rows[:40]):
        if len([c for c in r if c.strip()]) >= 4:
            hi = i
            break
    if hi is None:
        raise ParseError("Could not find a header row in this file.")
    h = rows[hi]
    mp = mapping or guess_mapping(h)
    missing = [k for k in ("date", "symbol", "qty", "price") if not mp.get(k)]
    if missing:
        raise ParseError("I don't recognise this file's format. Match its columns below.",
                         columns=[c.strip() for c in h if c.strip()])
    ix = {k: col(h, norm_header(v)) for k, v in mp.items() if v}
    fills = []
    for r in rows[hi + 1:]:
        ts = parse_dt(cell(r, ix.get("date")))
        qty, price = money(cell(r, ix.get("qty"))), money(cell(r, ix.get("price")))
        if not ts or qty is None or price is None:
            continue
        sym = cell(r, ix.get("symbol"))
        inst = parse_symbol(sym, sym)
        if not inst["key"]:
            continue
        stext = cell(r, ix.get("side"))
        kind = event_kind(stext)
        if kind:
            fills.append(make_fill("generic", "", ts, inst, None, 0.0, kind=kind, desc=kind + " " + inst["key"]))
            continue
        sign, effect = side_of(stext + " " + cell(r, ix.get("effect")))
        if sign is None:
            sign = -1 if qty < 0 else 1
        m = money(cell(r, ix.get("mult")))
        if m:
            inst["mult"] = m
        if inst["mult"] is None:
            continue
        fills.append(make_fill("generic", "", ts, inst, sign * abs(qty), abs(price),
                               fees=money(cell(r, ix.get("fees"))) or 0, effect=effect, desc=inst["key"],
                               raw_side=stext))
    return fills, "", []


# ======================================================================= Tradovate
def _tv_contract(contract, product, qty=None, price=None, notional=None):
    """A futures instrument. Point value from the product root, else from Notional Value / (qty x price)
    (Tradovate's own number), else unknown -- never guessed."""
    c = (contract or "").strip().upper()
    root = (product or "").strip().upper() or fut_root(c)
    mult = FUT_MULT.get(root)
    if mult is None and notional and qty and price:
        mult = round(abs(notional) / (abs(qty) * abs(price)), 6) or None
    return {"asset": "future", "underlying": root, "key": "/" + c, "expiry": None, "strike": None, "right": None,
            "mult": mult}


def parse_tradovate(rows):
    """Tradovate has three trade exports; all are read:
      Orders       one row per order: Status, B/S, Contract, Product, filledQty / avgPrice, Fill Time
      Fills        one row per execution: B/S, Quantity, Price, Contract, Product, Timestamp, commission
      Performance  one row per closed round trip: buyPrice, sellPrice, qty, boughtTimestamp, soldTimestamp, pnl
    Upload ONE of Orders or Fills for a period, not both (they describe the same executions)."""
    hi = find_header(rows, ["b/s", "contract"])
    if hi is None:
        hi = find_header(rows, ["buyprice", "sellprice"])
    if hi is None:
        raise ParseError("Recognised a Tradovate file but found no B/S / Contract columns.")
    h = rows[hi]
    names = {norm_header(c) for c in h}
    fills, notes, acct = [], [], ""
    c_acct = col(h, "account")
    if "buyprice" in names and "sellprice" in names:                      # Performance: round trips
        ix = {k: col(h, k) for k in ("symbol", "qty", "buyprice", "sellprice", "pnl", "boughttimestamp",
                                      "soldtimestamp", "buyfillid", "sellfillid")}
        for r in rows[hi + 1:]:
            q = money(cell(r, ix["qty"]))
            bp, sp = money(cell(r, ix["buyprice"])), money(cell(r, ix["sellprice"]))
            bt, st = parse_dt(cell(r, ix["boughttimestamp"])), parse_dt(cell(r, ix["soldtimestamp"]))
            if not q or bp is None or sp is None or not bt or not st:
                continue
            sym = cell(r, ix["symbol"])
            inst = _tv_contract(sym, "", q, None)
            pnl = money(cell(r, ix["pnl"]))
            if inst["mult"] is None and pnl is not None and sp != bp:
                inst["mult"] = round(abs(pnl / ((sp - bp) * q)), 6)
            if inst["mult"] is None:
                notes.append("%s: unknown point value; its rows were left out." % sym)
                continue
            # a long opens with the buy; a short (sold before it was bought) opens with the sell
            first, second = ((bt, 1, bp), (st, -1, sp)) if bt <= st else ((st, -1, sp), (bt, 1, bp))
            for ts, sign, px in (first, second):
                fills.append(make_fill("tradovate", acct, ts, inst, sign * abs(q), px,
                                       effect="open" if (ts, sign, px) == first else "close",
                                       desc=inst["key"], raw_side="Buy" if sign > 0 else "Sell"))
        notes.append("Tradovate Performance rows are round trips; commissions are not in this export.")
        return fills, acct, notes
    fill_rows = "fill id" in names or ("quantity" in names and "price" in names and "status" not in names)
    ix = {"side": col(h, "b/s"), "contract": col(h, "contract"), "product": col(h, "product"),
          "desc": col(h, "product description"), "status": col(h, "status"),
          "qty": col(h, "filledqty", "filled qty") if not fill_rows else col(h, "quantity", "qty"),
          "price": col(h, "avgprice", "avg fill price") if not fill_rows else col(h, "price"),
          "ts": col(h, "fill time", "timestamp") if not fill_rows else col(h, "timestamp", "fill time", "date"),
          "notional": col(h, "notional value"), "fees": col(h, "commission", "commissions", "fees")}
    unknown = set()
    for r in rows[hi + 1:]:
        if not fill_rows and "FILLED" not in cell(r, ix["status"]).upper():
            continue                                                       # rejected / canceled / working
        q, px = money(cell(r, ix["qty"])), money(cell(r, ix["price"]))
        ts = parse_dt(cell(r, ix["ts"]))
        side = cell(r, ix["side"])
        sign, _ = side_of(side)
        if not q or px is None or not ts or sign is None:
            continue
        inst = _tv_contract(cell(r, ix["contract"]), cell(r, ix["product"]), q, px, money(cell(r, ix["notional"])))
        if inst["mult"] is None:
            unknown.add(inst["underlying"])
            continue
        acct = acct or cell(r, c_acct)
        fills.append(make_fill("tradovate", cell(r, c_acct), ts, inst, sign * abs(q), px,
                               fees=money(cell(r, ix["fees"])) or 0.0,
                               desc=cell(r, ix["desc"]) or inst["key"], raw_side=side))
    if unknown:
        notes.append("Unknown point value for %s: those rows were left out." % ", ".join(sorted(unknown)))
    if ix["fees"] is None:
        notes.append("Tradovate %s exports carry no commissions; P&L is before fees." % ("Fills" if fill_rows else "Orders"))
    notes.append("Upload either the Orders or the Fills export for a period, not both: they list the same trades.")
    return fills, acct, notes


def detect(text):
    head = text[:6000].lower()
    rows = read_rows(text[:20000])
    flat = [{norm_header(c) for c in r} for r in rows[:40]]

    def has(*names):
        return any(all(n in s for n in names) for s in flat)
    if "account trade history" in head or ("account statement for" in head and "cash balance" in head):
        return "tos"
    if any(r and r[0].strip() == "Trades" and len(r) > 1 and r[1].strip() == "Header" for r in rows[:400]) \
            or "statement,header" in head:
        return "ibkr"
    if has("tradeprice", "quantity"):
        return "ibkr_flex"
    if has("run date", "action", "symbol"):
        return "fidelity"
    if has("activity date", "trans code"):
        return "robinhood"
    if has("instrument type", "action", "symbol") and has("average price"):
        return "tastytrade"
    if has("date", "action", "symbol", "quantity", "price") and (has("fees & comm") or has("amount")):
        return "schwab"
    if has("symbol", "side", "status", "filled"):
        return "webull"
    if has("b/s", "contract") or has("buyprice", "sellprice", "boughttimestamp"):
        return "tradovate"
    return "generic"


PARSERS = {"tos": parse_tos, "schwab": parse_schwab, "fidelity": parse_fidelity,
           "robinhood": parse_robinhood, "webull": parse_webull, "ibkr": parse_ibkr,
           "ibkr_flex": parse_ibkr_flex, "tastytrade": parse_tasty, "tradovate": parse_tradovate}

BROKER_NAMES = {"tos": "thinkorswim", "schwab": "Schwab", "fidelity": "Fidelity", "robinhood": "Robinhood",
                "webull": "Webull", "ibkr": "Interactive Brokers", "ibkr_flex": "Interactive Brokers (Flex)",
                "tastytrade": "tastytrade", "tradovate": "Tradovate", "generic": "Generic CSV"}


def parse(text, account_label="", mapping=None, force=None):
    """Return (broker, fills, rows_read, notes). Fills carry stable ids for de-duplication."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    rows = read_rows(text)
    broker = force or ("generic" if mapping else detect(text))
    if broker == "generic":
        fills, acct, notes = parse_generic(rows, mapping)
    else:
        try:
            fills, acct, notes = PARSERS[broker](rows)
        except ParseError:
            raise
        except Exception as exc:          # noqa: BLE001 -- a surprising layout: say so, offer mapping
            raise ParseError("Recognised a %s file but could not read it (%s). You can match its "
                             "columns by hand below." % (BROKER_NAMES[broker], exc),
                             columns=[c for c in (rows[0] if rows else []) if c.strip()])
    label = (account_label or "").strip()
    for f in fills:
        f["account"] = label or f["account"] or acct or BROKER_NAMES[broker]
    order_rows_chronologically(fills)
    seen = defaultdict(int)
    for f in fills:
        base = "|".join(str(f.get(k)) for k in ("broker", "account", "ts", "key", "qty", "price", "kind"))
        seen[base] += 1
        f["id"] = hashlib.sha1(("%s|%d" % (base, seen[base])).encode()).hexdigest()[:20]
    return broker, fills, len(rows), notes


def order_rows_chronologically(fills):
    """Give every fill a seq so same-timestamp rows keep their real order.

    Most exports list newest first. Date-only exports (Schwab, Fidelity,
    Robinhood) put an open and its same-day close on one timestamp, so the
    file order is the only order there is -- and it must be reversed.
    """
    if not fills:
        return
    newest_first = fills[0]["ts"] > fills[-1]["ts"]
    n = len(fills)
    for i, f in enumerate(fills):
        f["seq"] = (n - i) if newest_first else i
    # within one timestamp, an explicit open goes before a close of the same contract
    fills.sort(key=lambda f: (f["ts"], 0 if f["effect"] == "open" else 1 if f["effect"] is None else 2, f["seq"]))
    for i, f in enumerate(fills):
        f["seq"] = i


# ======================================================================= FIFO
def compute(fills, today=None, account=None):
    """FIFO round trips. Returns closed trades, unmatched closes, open lots, daily totals."""
    today = today or date.today().isoformat()
    fl = [f for f in fills if not account or f["account"] == account]
    fl.sort(key=lambda f: (f["ts"], f.get("seq", 0)))
    lots = defaultdict(deque)            # (account, key) -> deque of lot dicts
    info = {}
    closed, unmatched = [], []

    def close_against(f, q_close, price, kind):
        """Close up to |q_close| (signed like the closing fill) against the open lots."""
        k = (f["account"], f["key"])
        pieces, remaining = [], q_close
        fee_unit = (f["fees"] / abs(f["qty"])) if f.get("qty") else 0.0
        while remaining and lots[k] and (lots[k][0]["q"] > 0) != (remaining > 0):
            lot = lots[k][0]
            m = min(abs(remaining), abs(lot["q"]))
            sgn = 1 if lot["q"] > 0 else -1
            gross = (price - lot["price"]) * m * f["mult"] * sgn
            fees = lot["fee_unit"] * m + fee_unit * m
            pieces.append({"qty": m, "open_price": lot["price"], "open_ts": lot["ts"], "gross": gross,
                           "fees": fees, "dir": "long" if sgn > 0 else "short"})
            lot["q"] -= sgn * m
            remaining += sgn * m          # moves toward zero
            if abs(lot["q"]) < 1e-9:
                lots[k].popleft()
        if pieces:
            q = sum(p["qty"] for p in pieces)
            gross = sum(p["gross"] for p in pieces)
            fees = sum(p["fees"] for p in pieces)
            closed.append({
                "date": f["ts"][:10], "ts": f["ts"], "account": f["account"], "key": f["key"],
                "underlying": f["underlying"], "asset": f["asset"], "right": f.get("right"),
                "dir": pieces[0]["dir"], "qty": q,
                "open_price": sum(p["open_price"] * p["qty"] for p in pieces) / q,
                "close_price": price, "open_ts": min(p["open_ts"] for p in pieces),
                "gross": gross, "fees": fees, "net": gross - fees, "kind": kind,
                "mult": f["mult"], "desc": f.get("desc") or f["key"], "fill_id": f.get("id")})
        return remaining

    for f in fl:
        k = (f["account"], f["key"])
        info[k] = f
        if f["qty"] is None:             # expiry / assignment / exercise: flatten at 0
            net = sum(l["q"] for l in lots[k])
            if net:
                ff = dict(f, qty=-net, fees=f.get("fees", 0.0))
                close_against(ff, -net, f.get("price") or 0.0, f["kind"])
            continue
        q = f["qty"]
        if lots[k] and (lots[k][0]["q"] > 0) != (q > 0):
            q = close_against(f, q, f["price"], f["kind"])
        if abs(q) < 1e-9:
            continue
        # q is what is left once every opposite lot is gone
        closing = f["effect"] == "close" or (
            f["effect"] is None and q < 0 and not lots[k] and f["asset"] == "stock"
            and "SHORT" not in (f.get("side_text") or "").upper())
        if closing and not lots[k]:
            unmatched.append({"date": f["ts"][:10], "ts": f["ts"], "account": f["account"], "key": f["key"],
                              "qty": abs(q), "price": f["price"], "side": "sell" if q < 0 else "buy",
                              "desc": f.get("desc") or f["key"],
                              "why": "Closes a position opened before this upload's history starts -- its cost "
                                     "is unknown, so it is not counted. Upload an older statement to include it."})
            continue
        lots[k].append({"q": q, "price": f["price"], "ts": f["ts"],
                        "fee_unit": (f["fees"] / abs(f["qty"])) if f["qty"] else 0.0})

    # options still open after expiry expired worthless (the export left the event out)
    for k, dq in list(lots.items()):
        f = info.get(k)
        if not dq or not f or f["asset"] != "option" or not f.get("expiry") or f["expiry"] >= today:
            continue
        net = sum(l["q"] for l in dq)
        ts = f["expiry"] + "T16:00:00"
        ff = dict(f, ts=ts, qty=-net, fees=0.0, desc="expired (inferred) " + f["key"])
        close_against(ff, -net, 0.0, "expired_inferred")

    open_pos = []
    for (acct, key), dq in lots.items():
        net = sum(l["q"] for l in dq)
        if abs(net) > 1e-9:
            f = info[(acct, key)]
            cost = sum(l["q"] * l["price"] for l in dq)
            open_pos.append({"account": acct, "key": key, "qty": net, "avg_price": cost / net,
                             "since": min(l["ts"] for l in dq), "asset": f["asset"], "mult": f["mult"]})
    closed.sort(key=lambda t: t["ts"])
    return {"closed": closed, "unmatched": unmatched, "open": open_pos}


def daily(closed, unmatched=()):
    days = {}
    for t in closed:
        d = days.setdefault(t["date"], {"date": t["date"], "net": 0.0, "gross": 0.0, "fees": 0.0,
                                        "trades": 0, "wins": 0, "losses": 0, "unmatched": 0})
        d["net"] += t["net"]
        d["gross"] += t["gross"]
        d["fees"] += t["fees"]
        d["trades"] += 1
        d["wins"] += t["net"] > 0
        d["losses"] += t["net"] < 0
    for u in unmatched:
        d = days.setdefault(u["date"], {"date": u["date"], "net": 0.0, "gross": 0.0, "fees": 0.0,
                                        "trades": 0, "wins": 0, "losses": 0, "unmatched": 0})
        d["unmatched"] += 1
    return [days[k] for k in sorted(days)]


def stats(closed, days):
    wins = [t["net"] for t in closed if t["net"] > 0]
    losses = [t["net"] for t in closed if t["net"] < 0]
    traded = [d for d in days if d["trades"]]
    gw, gl = sum(wins), -sum(losses)
    return {
        "net": sum(t["net"] for t in closed), "gross": sum(t["gross"] for t in closed),
        "fees": sum(t["fees"] for t in closed), "trades": len(closed),
        "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / (len(wins) + len(losses))) if (wins or losses) else None,
        "profit_factor": (gw / gl) if gl else None,
        "avg_win": (gw / len(wins)) if wins else None, "avg_loss": (-gl / len(losses)) if losses else None,
        "largest_win": max(wins) if wins else None, "largest_loss": min(losses) if losses else None,
        "green_days": sum(1 for d in traded if d["net"] > 0), "red_days": sum(1 for d in traded if d["net"] < 0),
        "best_day": max(traded, key=lambda d: d["net"]) if traded else None,
        "worst_day": min(traded, key=lambda d: d["net"]) if traded else None,
    }
