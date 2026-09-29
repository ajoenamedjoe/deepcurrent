"""
CHANGELOG.md -> the What's new page.

    ## <date> · <release id> · <title>
    Tags: A, B
    Do: optional instruction after updating
    - **Headline** — details

Plain text only: the page escapes everything and turns `code` into <code>, nothing else.
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "CHANGELOG.md")
TAGS = ("Dashboard", "Valuation", "Flow", "Confluence", "Institutional", "Swing", "Growth", "GEX", "Security", "GitHub")

HEAD_RE = re.compile(r"^##\s+(.+?)\s+·\s+([\w.-]+)\s+·\s+(.+?)\s*$")
ITEM_RE = re.compile(r"^-\s+\*\*(.+?)\*\*\s*(?:[—-]\s*(.*))?$")


def parse(text):
    releases, cur = [], None
    body = text.split("\n---\n", 1)[-1]          # skip the header and format notes
    for raw in body.splitlines():
        line = raw.rstrip()
        m = HEAD_RE.match(line)
        if m:
            cur = {"date": m.group(1), "id": m.group(2), "title": m.group(3), "tags": [], "do": None, "items": []}
            releases.append(cur)
            continue
        if cur is None or not line.strip():
            continue
        if line.startswith("Tags:"):
            cur["tags"] = [t.strip() for t in line[5:].split(",") if t.strip()]
        elif line.startswith("Do:"):
            cur["do"] = line[3:].strip()
        else:
            m = ITEM_RE.match(line)
            if m:
                cur["items"].append({"headline": m.group(1).rstrip("."), "details": (m.group(2) or "").strip()})
            elif cur["items"]:                     # a wrapped continuation line
                it = cur["items"][-1]
                it["details"] = (it["details"] + " " + line.strip()).strip()
    return releases


def load(path=PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return {"releases": parse(fh.read()), "tags": list(TAGS)}
    except OSError as exc:
        return {"releases": [], "tags": list(TAGS), "error": "CHANGELOG.md not found (%s)" % exc.__class__.__name__}
