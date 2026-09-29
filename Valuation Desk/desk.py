"""
One valuation, end to end:

    fetch (4-6 API calls) -> analyse -> write .py -> EXECUTE it -> verify the
    printed verdict matches -> write .docx -> record the run

The PNG card is drawn by the browser and POSTed to /api/card/<id>, because a
stdlib-only desk has no rasteriser; `save_card` writes it next to the others.
Every file for a run lives in reports/<TICKER>/<YYYY-MM-DD_HHMM>/.
"""

import base64
import json
import os
import re
import threading
from datetime import datetime

import generate as G
import valuation as V

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_locks = {}
_locks_guard = threading.Lock()


def clean_ticker(raw):
    t = (raw or "").strip().upper().lstrip("$")
    if not TICKER_RE.match(t):
        raise ValueError("'%s' is not a ticker symbol." % (raw or ""))
    return t


def _lock_for(t):
    with _locks_guard:
        return _locks.setdefault(t, threading.Lock())


def run_ticker(client, store, ticker, overrides=None, fx=None, log=print, fetch=None):
    """
    -> dict(run_id, out_dir, files, card, result). Raises ValueError with a
    sentence for anything the user can fix (bad ticker, no FX rate, ...).
    `fetch` is injectable for tests; default is uw.fetch_all.
    """
    t = clean_ticker(ticker)
    with _lock_for(t):
        if fetch is None:
            import uw
            fetch = uw.fetch_all
        calls0 = getattr(client, "calls", 0)
        payloads, meta = fetch(client, t)
        res = V.run(payloads, fx=fx, overrides=overrides)
        for miss in meta.get("missing") or []:
            res["fin"]["notes"].append("Not fetched: %s -- that quality component is unmeasured, not zero." % miss)
        if meta.get("price_source"):
            res["fin"]["notes"].append("Price from %s." % meta["price_source"])
        stamp = datetime.now()
        base_dir = os.path.join(REPORTS, t, stamp.strftime("%Y-%m-%d_%H%M%S"))
        out_dir, k = base_dir, 1
        while os.path.exists(out_dir):          # two runs in one second must not share a folder
            k += 1
            out_dir = "%s_%d" % (base_dir, k)
        os.makedirs(out_dir)
        n = G.names(t)
        files = {}

        with open(os.path.join(out_dir, n["data"]), "w", encoding="utf-8") as fh:
            json.dump({"ticker": t, "fetched_at": stamp.isoformat(timespec="seconds"),
                       "meta": meta, "payloads": payloads}, fh)
        files["data"] = n["data"]

        script = os.path.join(out_dir, n["py"])
        with open(script, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(G.analysis_script(res, payloads, fx=fx))
        files["py"] = n["py"]
        console = G.execute_script(script)
        G.verify_console(res, console)
        with open(os.path.join(out_dir, n["console"]), "w", encoding="utf-8") as fh:
            fh.write(console)
        files["console"] = n["console"]

        G.report(res, os.path.join(out_dir, n["docx"]), stamp=stamp.strftime("%B %d, %Y"))
        files["docx"] = n["docx"]

        run_id = store.add(res, out_dir, meta.get("price_source"))
        used = getattr(client, "calls", 0) - calls0
        log("%s: %s/10 %s, base %s vs %s, %d API calls -> %s" % (
            t, res["score"]["badge"], res["score"]["verdict"],
            V.px(res["valuation"]["scenarios"]["base"]["per_share"]), V.px(res["fin"]["price"]),
            used, out_dir))
        return {"run_id": run_id, "out_dir": out_dir, "files": files, "card": G.card(res),
                "result": slim(res), "console": console, "api_calls": used}


def slim(res):
    """The result minus the bulky year-by-year rows the UI does not need twice."""
    out = json.loads(json.dumps(res, default=str))
    return out


PNG_HEAD = b"\x89PNG\r\n\x1a\n"


def short_card_name(ticker):
    """The summary card sits beside the full one. Named here, not in generate.names(), so a
    stored run's file list picks it up without changing the report generator."""
    return G.names(ticker)["png"].replace("_Summary_Card.png", "_Short_Card.png")


def save_card(store, run_id, data_url, kind="full"):
    row = store.get(run_id)
    if not row:
        raise ValueError("No run %s." % run_id)
    if not data_url.startswith("data:image/png;base64,"):
        raise ValueError("Card must be a PNG data URL.")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    if not raw.startswith(PNG_HEAD) or len(raw) > 8_000_000:
        raise ValueError("Not a PNG, or too large.")
    name = short_card_name(row["ticker"]) if kind == "short" else G.names(row["ticker"])["png"]
    path = os.path.join(row["out_dir"], name)
    with open(path, "wb") as fh:
        fh.write(raw)
    if kind != "short":
        store.mark_card(run_id)
    return path


def run_files(row):
    """Which deliverables exist on disk for a stored run."""
    n = G.names(row["ticker"])
    out = {}
    for k, name in n.items():
        if os.path.isfile(os.path.join(row["out_dir"], name)):
            out[k] = name
    short = short_card_name(row["ticker"])
    if os.path.isfile(os.path.join(row["out_dir"], short)):
        out["short"] = short
    return out
