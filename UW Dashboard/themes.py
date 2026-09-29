"""
Built-in themes for the dashboard, every desk and the share cards.

A theme is data: colour tokens per mode, three font stacks, a shape, and a few named
effects. Nothing here is user-supplied, and every value is fixed in this file, so the CSS
built from it needs no escaping beyond what the tests check.

  Paper      the original look, light and dark. It emits NO CSS at all: the pages'
             own built-in palette is Paper, so choosing it changes nothing.
  Carbon, Colorblind-safe       light and dark
  Newsprint                     light only
  Terminal, Midnight, Market Terminal, Cyberpunk   dark only

Delivery
  Dashboard  /  (index.html) gets the theme CSS injected at serve time (no flash), and
             /api/theme returns it again when the theme changes in Settings.
  Desks      each desk page loads http://127.0.0.1:<dash port>/theme.js?desk=<id>.
             The script carries its own payload, applies it, and refetches
             /api/theme/desk?desk=<id> (CORS-open, colours only) when the dashboard
             posts {"type": "uw-theme"} to the desk's frame. If the dashboard isn't
             running, the script never loads and the desk keeps its built-in look.
  Cards      theme.js exposes window.UWTheme.card(dark) -> palette + fonts + glow, which
             the Valuation, Flow and Swing canvases use in place of their fixed palettes.
"""

import json

SYS = 'system-ui, -apple-system, "Segoe UI", sans-serif'
MONO = '"Cascadia Mono", Consolas, "Lucida Console", Menlo, ui-monospace, monospace'
SERIF = 'Georgia, "Times New Roman", Times, serif'
GROTESK = '"Helvetica Neue", Helvetica, Arial, "Segoe UI", sans-serif'
NEWS_NUM = '"Franklin Gothic Medium", "Arial Narrow", Arial, sans-serif'
ORBITRON = 'Orbitron, "Segoe UI", system-ui, sans-serif'
TECHMONO = '"Share Tech Mono", Consolas, ui-monospace, monospace'

TOKENS = ("page", "side", "surface-1", "surface-2", "ink-1", "ink-2", "ink-3", "grid", "axis",
          "border", "border-strong", "s1", "s2", "s3", "s4", "s5",
          "good", "warning", "serious", "critical", "gain", "loss", "shadow")

PAPER_LIGHT = {
    "page": "#f9f9f7", "side": "#f1f1ed", "surface-1": "#fcfcfb", "surface-2": "#f4f4f1",
    "ink-1": "#0b0b0b", "ink-2": "#52514e", "ink-3": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
    "border": "rgba(11,11,11,0.10)", "border-strong": "rgba(11,11,11,0.18)",
    "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#eda100", "s5": "#e87ba4",
    "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
    "gain": "#0b8a0b", "loss": "#c83232",
    "shadow": "0 1px 2px rgba(11,11,11,.06), 0 4px 12px rgba(11,11,11,.04)",
}
PAPER_DARK = {
    "page": "#0d0d0d", "side": "#141413", "surface-1": "#1a1a19", "surface-2": "#232322",
    "ink-1": "#ffffff", "ink-2": "#c3c2b7", "ink-3": "#898781", "grid": "#2c2c2a", "axis": "#383835",
    "border": "rgba(255,255,255,0.10)", "border-strong": "rgba(255,255,255,0.18)",
    "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#c98500", "s5": "#d55181",
    "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
    "gain": "#2fbf4a", "loss": "#f0625f",
    "shadow": "0 1px 2px rgba(0,0,0,.4), 0 4px 14px rgba(0,0,0,.3)",
}

THEMES = [
    {"id": "paper", "name": "Paper", "modes": ("light", "dark"),
     "desc": "The original: warm paper greys, soft cards, blue accent. Light and dark.",
     "light": PAPER_LIGHT, "dark": PAPER_DARK, "fonts": None, "square": False, "effects": ()},

    {"id": "carbon", "name": "Carbon", "modes": ("light", "dark"),
     "desc": "Stark black and white, square edges, flat cards, one electric-blue accent.",
     "light": {"page": "#ffffff", "side": "#f4f4f4", "surface-1": "#ffffff", "surface-2": "#f2f2f2",
               "ink-1": "#000000", "ink-2": "#393939", "ink-3": "#6f6f6f", "grid": "#e0e0e0", "axis": "#c6c6c6",
               "border": "rgba(0,0,0,0.14)", "border-strong": "rgba(0,0,0,0.40)",
               "s1": "#0f62fe", "s2": "#8a3ffc", "s3": "#007d79", "s4": "#b28600", "s5": "#d02670",
               "good": "#198038", "warning": "#b28600", "serious": "#ba4e00", "critical": "#da1e28",
               "gain": "#198038", "loss": "#da1e28", "shadow": "none"},
     "dark": {"page": "#000000", "side": "#0b0b0b", "surface-1": "#121212", "surface-2": "#1f1f1f",
              "ink-1": "#ffffff", "ink-2": "#c6c6c6", "ink-3": "#8d8d8d", "grid": "#262626", "axis": "#393939",
              "border": "rgba(255,255,255,0.16)", "border-strong": "rgba(255,255,255,0.42)",
              "s1": "#4589ff", "s2": "#a56eff", "s3": "#08bdba", "s4": "#f1c21b", "s5": "#ff7eb6",
              "good": "#42be65", "warning": "#f1c21b", "serious": "#ff832b", "critical": "#fa4d56",
              "gain": "#42be65", "loss": "#fa4d56", "shadow": "none"},
     "fonts": {"body": GROTESK, "head": GROTESK, "num": GROTESK}, "square": True, "effects": ("caps",)},

    {"id": "colorblind", "name": "Colorblind-safe", "modes": ("light", "dark"),
     "desc": "Blue for gains and vermilion for losses, with a chart palette that stays distinct for every common colour-vision type.",
     "light": dict(PAPER_LIGHT, **{"s1": "#0072b2", "s2": "#e69f00", "s3": "#009e73", "s4": "#cc79a7", "s5": "#56b4e9",
                                   "good": "#0072b2", "warning": "#e69f00", "serious": "#cc79a7", "critical": "#d55e00",
                                   "gain": "#0068a3", "loss": "#b84f00"}),
     "dark": dict(PAPER_DARK, **{"s1": "#56b4e9", "s2": "#e69f00", "s3": "#009e73", "s4": "#cc79a7", "s5": "#f0e442",
                                 "good": "#56b4e9", "warning": "#e69f00", "serious": "#cc79a7", "critical": "#ff7a2f",
                                 "gain": "#56b4e9", "loss": "#ff7a2f"}),
     "fonts": None, "square": False, "effects": ()},

    {"id": "newsprint", "name": "Newsprint", "modes": ("light",),
     "desc": "Cream paper, serif headlines, ink rules under every section, blue and red instead of green and red.",
     "light": {"page": "#f4efe3", "side": "#ebe4d3", "surface-1": "#fbf8f0", "surface-2": "#efe8d8",
               "ink-1": "#1a1714", "ink-2": "#4a443b", "ink-3": "#6f675a", "grid": "#ddd4c0", "axis": "#b9ae96",
               "border": "rgba(26,23,20,0.18)", "border-strong": "rgba(26,23,20,0.45)",
               "s1": "#1f4e8c", "s2": "#b3261e", "s3": "#2f6b3a", "s4": "#9a6400", "s5": "#6b3e8c",
               "good": "#2f6b3a", "warning": "#9a6400", "serious": "#c0501f", "critical": "#b3261e",
               "gain": "#1f4e8c", "loss": "#b3261e", "shadow": "none"},
     "fonts": {"body": SERIF, "head": SERIF, "num": NEWS_NUM}, "square": True, "effects": ("rules",)},

    {"id": "terminal", "name": "Terminal", "modes": ("dark",),
     "desc": "Green-phosphor CRT: everything monospace, faint scanlines, a soft glow on headings.",
     "dark": {"page": "#020a04", "side": "#03100a", "surface-1": "#061409", "surface-2": "#0b2012",
              "ink-1": "#a8ffbc", "ink-2": "#62d87f", "ink-3": "#3f9a56", "grid": "#113a1f", "axis": "#1d5a31",
              "border": "rgba(80,255,120,0.20)", "border-strong": "rgba(80,255,120,0.42)",
              "s1": "#33ff66", "s2": "#ffb000", "s3": "#00e5ff", "s4": "#d7ff5e", "s5": "#ff6e9c",
              "good": "#33ff66", "warning": "#ffd23f", "serious": "#ff9f1c", "critical": "#ff5555",
              "gain": "#33ff66", "loss": "#ff5555", "shadow": "none"},
     "fonts": {"body": MONO, "head": MONO, "num": MONO}, "square": True,
     "effects": ("glow", "scanlines", "caps", "prompt"), "glow": "#33ff66"},

    {"id": "midnight", "name": "Midnight", "modes": ("dark",),
     "desc": "Deep navy with neon accents and a soft glow on the numbers that matter.",
     "dark": {"page": "#070b1a", "side": "#0a1024", "surface-1": "#0f1630", "surface-2": "#17213f",
              "ink-1": "#eef2ff", "ink-2": "#aeb9d8", "ink-3": "#7784aa", "grid": "#1e2a4d", "axis": "#2b3a66",
              "border": "rgba(140,170,255,0.15)", "border-strong": "rgba(140,170,255,0.32)",
              "s1": "#5aa6ff", "s2": "#ff6ad5", "s3": "#2ef2c4", "s4": "#ffc857", "s5": "#b28dff",
              "good": "#2ef2a0", "warning": "#ffc857", "serious": "#ff9a5c", "critical": "#ff5c7a",
              "gain": "#2ef2a0", "loss": "#ff5c7a",
              "shadow": "0 1px 2px rgba(0,0,0,.5), 0 6px 22px rgba(20,40,120,.35)"},
     "fonts": None, "square": False, "effects": ("glow",), "glow": "#5aa6ff"},

    {"id": "market", "name": "Market Terminal", "modes": ("dark",),
     "desc": "The classic trading-terminal look: black screen, amber data, blue header bars, yellow highlights, dense monospace tables.",
     "dark": {"page": "#000000", "side": "#000000", "surface-1": "#070707", "surface-2": "#141414",
              "ink-1": "#ffab40", "ink-2": "#e8e8e8", "ink-3": "#a0a0a0", "grid": "#262626", "axis": "#3a3a3a",
              "border": "rgba(255,255,255,0.16)", "border-strong": "rgba(255,171,64,0.55)",
              "s1": "#ffab40", "s2": "#4aa3ff", "s3": "#3ddc84", "s4": "#f2f2f2", "s5": "#ff5fa2",
              "good": "#3ddc84", "warning": "#ffe14d", "serious": "#ff8a3d", "critical": "#ff4545",
              "gain": "#3ddc84", "loss": "#ff4545", "shadow": "none"},
     "fonts": {"body": MONO, "head": MONO, "num": MONO}, "square": True,
     "effects": ("bars", "dense", "caps"), "bar": "#0b2f8a", "bar_ink": "#ffffff", "highlight": "#ffe14d"},

    {"id": "cyberpunk", "name": "Cyberpunk", "modes": ("dark",), "motion": True,
     "desc": "80s retro neon: magenta and cyan on deep violet, glowing headings, scanlines and a sunset grid. Optional slow motion.",
     "dark": {"page": "#0d0221", "side": "#110330", "surface-1": "#180740", "surface-2": "#231055",
              "ink-1": "#f7f0ff", "ink-2": "#cdbaf3", "ink-3": "#9780c4", "grid": "#2e1466", "axis": "#43208a",
              "border": "rgba(255,46,207,0.28)", "border-strong": "rgba(0,240,255,0.50)",
              "s1": "#00f0ff", "s2": "#ff2ecf", "s3": "#7cff6b", "s4": "#ffd319", "s5": "#b967ff",
              "good": "#39ff9f", "warning": "#ffd319", "serious": "#ff9f45", "critical": "#ff3864",
              "gain": "#39ff9f", "loss": "#ff3864",
              "shadow": "0 0 0 1px rgba(255,46,207,.18), 0 0 18px rgba(255,46,207,.14)"},
     "fonts": {"body": SYS, "head": ORBITRON, "num": TECHMONO}, "square": True,
     "fontfiles": (("Orbitron", 500, "orbitron-latin-500-normal.woff2"), ("Orbitron", 700, "orbitron-latin-700-normal.woff2"),
                   ("Orbitron", 900, "orbitron-latin-900-normal.woff2"),
                   ("Share Tech Mono", 400, "share-tech-mono-latin-400-normal.woff2")),
     "effects": ("glow", "scanlines", "grid", "neon", "caps"), "glow": "#ff2ecf", "glow2": "#00f0ff"},
]
BY_ID = {t["id"]: t for t in THEMES}
DEFAULT = "paper"


# Fonts bundled in fonts/ (SIL Open Font License, texts alongside), served by the dashboard at /fonts/<file>
# with CORS so the desks can use them. Nothing is fetched from the internet.
FONT_FILES = {f for t in THEMES for _fam, _w, f in t.get("fontfiles", ())}


def fontface_css(theme, base=""):
    return "".join("@font-face { font-family: \"%s\"; font-weight: %d; font-style: normal; font-display: swap;"
                   " src: url(\"%s/fonts/%s\") format(\"woff2\"); }\n" % (fam, w, base, f)
                   for fam, w, f in theme.get("fontfiles", ()))


def get(tid):
    return BY_ID.get(tid) or BY_ID[DEFAULT]


def resolve_mode(theme, pref):
    """'light' / 'dark' when the theme or the setting fixes it, None = follow the system."""
    if len(theme["modes"]) == 1:
        return theme["modes"][0]
    return pref if pref in ("light", "dark") else None


def public_list():
    """What the Settings gallery needs to draw a preview of each theme."""
    out = []
    for t in THEMES:
        out.append({"id": t["id"], "name": t["name"], "desc": t["desc"], "modes": list(t["modes"]),
                    "motion": bool(t.get("motion")), "square": t["square"],
                    "fonts": t["fonts"] or {"body": SYS, "head": SYS, "num": SYS},
                    "fontface": fontface_css(t),
                    "tokens": {m: t[m] for m in t["modes"]}, "effects": list(t["effects"]),
                    "bar": t.get("bar"), "glow": t.get("glow")})
    return out


# ---------------------------------------------------------------- CSS
def _hexa(c, a):
    """#rrggbb + alpha -> rgba()."""
    c = c.lstrip("#")
    return "rgba(%d,%d,%d,%.2f)" % (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


def _vars(tokens, names=None, extra=None):
    names = names or {k: k for k in TOKENS}
    out = []
    for src, dst in names.items():
        v = tokens.get(src) if not callable(src) else None
        if v is not None:
            out.append("--%s: %s;" % (dst, v))
    for k, v in (extra or {}).items():
        out.append("--%s: %s;" % (k, v))
    return " ".join(out)


def _mode_blocks(theme, pref, var_fn, prefix=":root:root"):
    """CSS that sets the tokens. Single-mode themes win over any data-theme the page sets;
    two-mode themes key on data-theme, which the page (or theme.js) sets from the setting."""
    modes = theme["modes"]
    if len(modes) == 1:
        m = modes[0]
        return "%s, %s[data-theme] { color-scheme: %s; %s }\n" % (prefix, prefix, m, var_fn(theme[m], m))
    css = "%s, %s[data-theme=\"light\"] { color-scheme: light; %s }\n" % (prefix, prefix, var_fn(theme["light"], "light"))
    css += "%s[data-theme=\"dark\"] { color-scheme: dark; %s }\n" % (prefix, var_fn(theme["dark"], "dark"))
    return css


SEL = {
    # where each effect lands, per page family
    "dash": {"glow": ".top h1, .clock .t, .panel > h2, nav a.item.active span, .gd-head h3, .center-msg h2, .kpi .v",
             "head": "h1, h2, h3, .clock .t, nav .grp, .panel > h2, .kpi .l",
             "num": ".n, .mono, td.n, .kpi .v, .clock .t, .gd-w",
             "panel": ".panel, .kpi, .gd-card",
             "hdr": ".top", "hdr_one": ".top", "title": ".top h1",
             "h2": ".panel > h2"},
    "desk": {"glow": "h1, .brand h1, .tkr, .panel > h2, .section-title h2, .score-ring .n",
             "head": "h1, h2, h3, .brand h1, .tkr, .lbl",
             "num": ".n, .mono, .num, td.n, .v, .p, .px .p, .stat .v",
             "panel": ".panel, .card, .stat, .sheet",
             "hdr": "header", "hdr_one": "header", "title": "header h1, .brand h1",
             "h2": ".panel > h2, .section-title h2, .panel-hd h2"},
}


def effects_css(theme, family, motion=False):
    s = SEL[family]
    fx = set(theme["effects"])
    f = theme["fonts"]
    tok = theme[theme["modes"][-1]]
    css = []
    if f:
        css.append("body, button, input, select, textarea { font-family: %s; }" % f["body"])
        css.append("%s { font-family: %s; }" % (s["head"], f["head"]))
        css.append("%s { font-family: %s; }" % (s["num"], f["num"]))
    if theme["square"]:
        css.append("*:not(.dot):not(.spinner):not(.score-ring):not([class*=\"dot\"]) { border-radius: 0 !important; }")
    if "caps" in fx:
        css.append("%s { text-transform: uppercase; letter-spacing: .06em; }" % s["h2"])
    if "glow" in fx:
        g = theme.get("glow", tok["s1"])
        css.append("%s { text-shadow: 0 0 6px %s, 0 0 16px %s; }" % (s["glow"], _hexa(g, .55), _hexa(g, .28)))
    if "neon" in fx:
        g2 = theme.get("glow2", tok["s1"])
        css.append("%s { border-color: %s !important; box-shadow: 0 0 0 1px %s, 0 0 16px %s, inset 0 0 22px %s; }"
                   % (s["panel"], _hexa(theme["glow"], .45), _hexa(theme["glow"], .18), _hexa(theme["glow"], .16),
                      _hexa(g2, .05)))
    if "scanlines" in fx:
        css.append("body::after { content: \"\"; position: fixed; inset: 0; pointer-events: none; z-index: 2147483000;"
                   " background: repeating-linear-gradient(to bottom, rgba(0,0,0,0) 0, rgba(0,0,0,0) 2px,"
                   " rgba(0,0,0,.16) 2px, rgba(0,0,0,.16) 3px); }")
    if "rules" in fx:
        css.append("%s { color: var(--ink-1); border-bottom: 3px double var(--ink-1); padding-bottom: 5px; }" % s["h2"])
        css.append("%s { box-shadow: none; border-color: var(--border-strong); }" % s["panel"])
    if "prompt" in fx and family == "dash":
        css.append(".top h1::before { content: \"> \"; color: var(--s1); }")
        css.append(".top h1::after { content: \"_\"; color: var(--s1); margin-left: 2px; }")
    if "bars" in fx:
        bar, ink = theme["bar"], theme["bar_ink"]
        css.append("%s { background: %s !important; border-bottom: 2px solid var(--s1); }" % (s["hdr"], bar))
        css.append("%s h1, %s .sub, %s .brand .sub { color: %s; }" % (s["hdr"], s["hdr"], s["hdr"], ink))
        if family == "dash":
            css.append(".panel > h2 { background: %s; color: %s; margin: -10px -12px 10px; padding: 5px 12px; }" % (bar, ink))
            css.append("nav a.item.active { box-shadow: inset 3px 0 0 var(--s1); background: var(--surface-2); }")
            css.append("nav a.item.active span { color: %s; }" % theme["highlight"])
        else:
            css.append(".panel > h2 { background: %s; color: %s; padding: 4px 10px; }" % (bar, ink))
        css.append("::selection { background: %s; color: #000; }" % theme["highlight"])
    if "dense" in fx:
        css.append("body { font-size: 13px; }")
        css.append("td, th { padding-top: 3px !important; padding-bottom: 3px !important; }")
        if family == "dash":
            css.append(".panel { padding: 10px 12px; margin-bottom: 10px; } .pad { padding: 12px 14px 50px; }"
                       " .kpi { padding: 8px 10px; }")
    if "grid" in fx:
        g1, g2 = theme["glow"], theme["glow2"]
        hdr = s["hdr_one"]
        # a sunset band in the page header ...
        css.append("%s { position: relative; isolation: isolate; overflow: hidden; border-bottom: 1px solid %s !important;"
                   " background: linear-gradient(90deg, %s 0%%, #2a0a4e 55%%, #45104f 100%%) !important;"
                   " box-shadow: 0 1px 14px %s; }" % (hdr, _hexa(g1, .7), tok["page"], _hexa(g1, .35)))
        if family == "dash":   # desk headers hold opaque panels that would hide the sun
          css.append("%s::after { content: \"\"; position: absolute; right: 34%%; bottom: -34px; width: 96px; height: 96px;"
                   " z-index: -1; pointer-events: none; border-radius: 50%% !important;"
                   " background: repeating-linear-gradient(180deg, transparent 0 5px, #2a0a4e 5px 8px) bottom / 100%% 62%% no-repeat,"
                   " linear-gradient(180deg, #ffd319 10%%, #ff2ecf 75%%); opacity: .85; filter: drop-shadow(0 0 10px %s); }"
                   % (hdr, _hexa(g1, .6)))
        # ... and a neon floor grid fading up from the bottom of the window, behind everything
        css.append("body::before { content: \"\"; position: fixed; left: -20vw; right: -20vw; bottom: -2vh; height: 46vh;"
                   " z-index: -1; pointer-events: none; transform: perspective(300px) rotateX(62deg); transform-origin: 50%% 100%%;"
                   " background: repeating-linear-gradient(90deg, %s 0 2px, transparent 2px 64px),"
                   " repeating-linear-gradient(0deg, %s 0 2px, transparent 2px 40px);"
                   " -webkit-mask: linear-gradient(to top, #000 10%%, transparent 95%%); mask: linear-gradient(to top, #000 10%%, transparent 95%%);"
                   " opacity: .55; }" % (_hexa(g2, .55), _hexa(g1, .55)))
        css.append("%s { color: %s; }" % (s["title"], g1))
        if motion and theme.get("motion"):
            css.append("@media (prefers-reduced-motion: no-preference) {"
                       " body::before { animation: uwgrid 2.4s linear infinite; }"
                       " @keyframes uwgrid { from { background-position: 0 0, 0 0; } to { background-position: 0 0, 0 40px; } }"
                       " %s { animation: uwglow 4s ease-in-out infinite; }"
                       " @keyframes uwglow { 50%% { text-shadow: 0 0 10px %s, 0 0 28px %s; } }"
                       " %s::after { animation: uwsun 6s ease-in-out infinite; }"
                       " @keyframes uwsun { 50%% { filter: drop-shadow(0 0 22px %s); } } }"
                       % (s["glow"], _hexa(g1, .85), _hexa(g2, .5), hdr if family == "dash" else "html:not(html)", _hexa(g1, .9)))
    return "\n".join(css) + "\n"


def dashboard_css(tid, pref="system", motion=False):
    """The <style> the dashboard page carries. Paper = empty: the page's own palette is Paper."""
    t = get(tid)
    if t["id"] == DEFAULT:
        return ""
    css = fontface_css(t) + _mode_blocks(t, pref, lambda tok, m: _vars(tok))
    return css + effects_css(t, "dash", motion)


# ---------------------------------------------------------------- desks
PAPER_DESKS = ("valuation", "confluence", "institutional", "swing")
FLOW_MAP = {"page": "ground", "surface-1": "panel", "surface-2": "panel-2", "border-strong": "line",
            "grid": "line-soft", "ink-1": "ink", "ink-2": "ink-2", "ink-3": "ink-3", "s1": "accent",
            "gain": "bull", "loss": "bear", "warning": "pend", "s5": "violet", "axis": "meter-track"}
# the desk's own light/dark button (hidden while a theme is in charge), and its share card's
# Dark/Light choice (hidden when the theme has only one mode)
TOGGLES = {"valuation": "#themeBtn", "swing": "#themeBtn", "confluence": "#themeBtn", "institutional": "#btn-theme",
           "flow": None, "growth": "#themeBtn"}
CARD_MODE = {"valuation": "#cardTheme", "swing": "#optDark, #optLight", "confluence": "#shareThemeSeg",
             "institutional": "#share-theme", "flow": "#optDark, #optLight", "growth": "#cardTheme"}


def _desk_vars(desk, theme, tok, mode):
    if desk == "flow":
        extra = {"accent-soft": _hexa(tok["s1"], .14)}
        if theme["fonts"]:
            extra.update({"sans": theme["fonts"]["body"], "cond": theme["fonts"]["head"], "mono": theme["fonts"]["num"]})
        if theme["square"]:
            extra["r"] = "0px"
        return _vars(tok, FLOW_MAP, extra)
    return _vars(tok)


def card_palette(theme):
    """Colours and fonts for the canvas share cards, per available mode."""
    out = {}
    for m in theme["modes"]:
        t = theme[m]
        out[m] = {"bg": t["surface-1"], "panel": t["surface-2"], "ink1": t["ink-1"], "ink2": t["ink-2"], "ink3": t["ink-3"],
                  "grid": t["grid"], "line": t["border-strong"], "good": t["gain"], "bad": t["loss"], "warn": t["warning"],
                  "s1": t["s1"], "s2": t["s2"], "s3": t["s3"], "s4": t["s4"], "s5": t["s5"]}
    f = theme["fonts"] or {"body": SYS, "head": SYS, "num": SYS}
    return {"modes": out, "fonts": f, "glow": theme.get("glow") if "glow" in theme["effects"] else None,
            "bar": theme.get("bar"), "bar_ink": theme.get("bar_ink")}


def desk_payload(tid, desk, pref="system", motion=False, origin="http://127.0.0.1:8700"):
    t = get(tid)
    if t["id"] == DEFAULT:
        return {"id": DEFAULT, "name": t["name"], "css": "", "mode": None}
    css = fontface_css(t, origin) + _mode_blocks(t, pref, lambda tok, m: _desk_vars(desk, t, tok, m), prefix=":root:root")
    tog = TOGGLES.get(desk)
    if tog:
        css += "%s { display: none !important; }\n" % tog
    if len(t["modes"]) == 1 and CARD_MODE.get(desk):
        css += "%s { display: none !important; }\n" % CARD_MODE[desk]
    css += effects_css(t, "desk", motion)
    return {"id": t["id"], "name": t["name"], "css": css,
            "mode": resolve_mode(t, pref), "modes": list(t["modes"]), "card": card_palette(t)}


THEME_JS = r"""/* Dashboard theme hook for the %(desk)s desk. Served by the dashboard; if the dashboard
   is not running this file never loads and the desk keeps its own look. */
(function () {
  var ORIGIN = %(origin)s, DESK = %(desk_js)s;
  var cur = %(payload)s;
  function mode(p) {
    if (p.mode) return p.mode;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  /* The name the dashboard gives this desk ("Deep Current · Valuation"), on the page and its share cards.
     Elements marked data-uw-name show it; window.UWBrand.label is what the cards draw. */
  function names(b) {
    if (!b || !b.label) return;
    window.UWBrand = { brand: b.brand, desk: b.desk, label: b.label };
    document.title = b.label;
    var els = document.querySelectorAll ? document.querySelectorAll("[data-uw-name]") : [];
    for (var i = 0; i < els.length; i++) els[i].textContent = b.label;
  }
  function apply(p) {
    cur = p;
    if (p) names(p.brand);
    var st = document.getElementById("uw-theme-css");
    if (!p || p.id === "paper") {
      if (st) st.remove();
      window.UWTheme = null;
      if (window.__uwThemeSaved) document.documentElement.setAttribute("data-theme", window.__uwThemeSaved);
    } else {
      if (!st) { st = document.createElement("style"); st.id = "uw-theme-css"; }
      st.textContent = p.css;
      (document.head || document.documentElement).appendChild(st);   // always last, so it wins
      if (window.__uwThemeSaved === undefined) window.__uwThemeSaved = document.documentElement.getAttribute("data-theme") || "";
      document.documentElement.setAttribute("data-theme", mode(p));
      window.UWTheme = {
        id: p.id, name: p.name, modes: p.modes,
        single: p.modes.length === 1,
        fonts: p.card.fonts, glow: p.card.glow,
        /* The desk's own card palette with every key it knows replaced by the theme's colour.
           Keys differ between desks (ink / ink1, bull / good, accent / s1 ...), so map them all. */
        pal: function (base, dark) {
          var m = p.card.modes[dark ? "dark" : "light"] || p.card.modes[p.modes[0]];
          var alias = { bg: m.bg, panel: m.panel, ink: m.ink1, ink1: m.ink1, ink2: m.ink2, ink3: m.ink3, grid: m.grid,
                        line: m.line, good: m.good, bull: m.good, up: m.good, bad: m.bad, bear: m.bad, down: m.bad,
                        warn: m.warn, s1: m.s1, accent: m.s1, s2: m.s2, s3: m.s3, s4: m.s4, s5: m.s5, track: m.panel };
          var o = {}, k;
          for (k in base) o[k] = alias.hasOwnProperty(k) ? alias[k] : base[k];
          if (base.s && typeof base.s === "object") {
            var ks = Object.keys(base.s), ser = [m.s1, m.s2, m.s3, m.s4, m.s5];
            o.s = {}; ks.forEach(function (x, i) { o.s[x] = ser[i %% 5]; });
          }
          return o;
        },
        font: function (kind, fallback) { return (p.card.fonts && p.card.fonts[kind]) || fallback; }
      };
    }
    try { window.dispatchEvent(new CustomEvent("uw-theme", { detail: window.UWTheme })); } catch (e) {}
  }
  apply(cur);
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", function () { apply(cur); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { if (cur && !cur.mode) apply(cur); });
  window.addEventListener("message", function (e) {
    var ok = [ORIGIN, ORIGIN.replace("127.0.0.1", "localhost")];
    if (ok.indexOf(e.origin) < 0 || !e.data || e.data.type !== "uw-theme") return;
    fetch(ORIGIN + "/api/theme/desk?desk=" + encodeURIComponent(DESK), { cache: "no-store" })
      .then(function (r) { return r.json(); }).then(apply).catch(function () {});
  });
})();
"""


def theme_js(desk, payload, origin):
    return THEME_JS % {"desk": desk if desk in CARD_MODE else "unknown", "origin": json.dumps(origin),
                       "desk_js": json.dumps(desk), "payload": json.dumps(payload)}
