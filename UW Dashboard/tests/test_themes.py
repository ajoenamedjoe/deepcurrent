"""Built-in themes: complete, legible, Paper unchanged, and the desks are wired to follow."""
import itertools
import os
import re
import unittest

from _path import ROOT
import themes

PARENT = os.path.dirname(ROOT)


def rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def lum(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(rgb(a), rgb(b))) ** .5


def each_mode():
    for t in themes.THEMES:
        for m in t["modes"]:
            yield t, m, t[m]


class Complete(unittest.TestCase):
    def test_the_built_in_set(self):
        ids = [t["id"] for t in themes.THEMES]
        self.assertEqual(ids, ["paper", "carbon", "colorblind", "newsprint", "terminal", "midnight", "market", "cyberpunk"])
        self.assertEqual(themes.get("paper")["modes"], ("light", "dark"))      # Paper keeps both
        self.assertEqual(themes.get("market")["modes"], ("dark",))
        self.assertTrue(themes.get("cyberpunk").get("motion"))
        self.assertEqual(themes.get("nonsense")["id"], "paper")

    def test_every_mode_defines_every_token(self):
        for t, m, tok in each_mode():
            self.assertEqual(set(themes.TOKENS) - set(tok), set(), (t["id"], m))
            for k, v in tok.items():
                self.assertRegex(v, r"^(#[0-9a-f]{6}|rgba\([\d.,\s]+\)|none|[\d\s.pxrgba(),]+)$", (t["id"], m, k))

    def test_bundled_fonts_exist(self):
        for f in themes.FONT_FILES:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "fonts", f)), f)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "fonts", "OFL-Orbitron.txt")))


class Legible(unittest.TestCase):
    """WCAG contrast on the surfaces text actually sits on."""

    def test_text_contrast(self):
        for t, m, k in each_mode():
            for bg in ("page", "surface-1", "surface-2"):
                self.assertGreaterEqual(contrast(k["ink-1"], k[bg]), 7.0, (t["id"], m, "ink-1", bg))
                self.assertGreaterEqual(contrast(k["ink-2"], k[bg]), 4.5, (t["id"], m, "ink-2", bg))
            self.assertGreaterEqual(contrast(k["ink-3"], k["surface-1"]), 3.0, (t["id"], m, "ink-3"))

    def test_signal_colours_read_on_cards(self):
        for t, m, k in each_mode():
            if t["id"] == "paper":
                continue        # Paper is the shipped palette, unchanged by design
            for c in ("gain", "loss", "critical", "s1"):
                self.assertGreaterEqual(contrast(k[c], k["surface-1"]), 3.0, (t["id"], m, c))

    def test_gain_and_loss_are_never_confusable(self):
        for t, m, k in each_mode():
            self.assertGreater(dist(k["gain"], k["loss"]), 150, (t["id"], m))

    def test_chart_series_are_distinct(self):
        for t, m, k in each_mode():
            for a, b in itertools.combinations(("s1", "s2", "s3", "s4", "s5"), 2):
                self.assertGreater(dist(k[a], k[b]), 60, (t["id"], m, a, b))


class PaperUnchanged(unittest.TestCase):
    def test_paper_emits_nothing(self):
        self.assertEqual(themes.dashboard_css("paper", "dark"), "")
        self.assertEqual(themes.desk_payload("paper", "swing")["css"], "")

    def test_paper_tokens_are_the_page_palette(self):
        """Paper's gallery preview and share-card colours must be the colours the page ships with."""
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as fh:
            page = fh.read()
        light = page[page.index(":root {"):page.index("}", page.index(":root {"))]
        dark = page[page.index(':root[data-theme="dark"]'):]
        dark = dark[:dark.index("}")]
        for block, tok in ((light, themes.PAPER_LIGHT), (dark, themes.PAPER_DARK)):
            found = dict(re.findall(r"--([\w-]+):\s*([^;]+);", block))
            for k, v in found.items():
                if k in tok:
                    self.assertEqual(tok[k].replace(" ", ""), v.strip().replace(" ", ""), k)


class Css(unittest.TestCase):
    def test_css_is_well_formed(self):
        for t in themes.THEMES:
            for motion in (False, True):
                for css in (themes.dashboard_css(t["id"], "system", motion),
                            themes.desk_payload(t["id"], "flow", "system", motion)["css"],
                            themes.desk_payload(t["id"], "valuation", "light", motion)["css"]):
                    self.assertEqual(css.count("{"), css.count("}"), t["id"])
                    self.assertNotIn("</", css)
                    for bad in ("%s", "%d", "%(", "%%", "None"):
                        self.assertNotIn(bad, css, t["id"])

    def test_single_mode_themes_win_over_any_data_theme(self):
        css = themes.dashboard_css("terminal")
        self.assertIn(":root:root[data-theme]", css)
        css = themes.dashboard_css("carbon", "light")
        self.assertIn(':root:root[data-theme="dark"]', css)

    def test_motion_only_when_asked_and_only_for_cyberpunk(self):
        self.assertNotIn("@keyframes uwgrid", themes.dashboard_css("cyberpunk", motion=False))
        on = themes.dashboard_css("cyberpunk", motion=True)
        self.assertIn("@keyframes uwgrid", on)
        self.assertIn("prefers-reduced-motion: no-preference", on)
        self.assertNotIn("@keyframes", themes.dashboard_css("terminal", motion=True))

    def test_flow_desk_gets_its_own_variable_names(self):
        css = themes.desk_payload("market", "flow")["css"]
        for v in ("--ground:", "--panel:", "--ink:", "--bull:", "--bear:", "--accent:", "--mono:", "--r: 0px"):
            self.assertIn(v, css)
        css = themes.desk_payload("market", "swing")["css"]
        self.assertIn("--surface-1:", css)
        self.assertIn("#themeBtn { display: none", css)           # the desk's own toggle yields
        self.assertIn("#optDark, #optLight { display: none", css)  # single-mode: no card light/dark

    def test_fonts_are_bundled_not_fetched(self):
        for t in themes.THEMES:
            for css in (themes.dashboard_css(t["id"]), themes.desk_payload(t["id"], "swing")["css"]):
                self.assertNotIn("googleapis", css)
        self.assertIn("http://127.0.0.1:8700/fonts/orbitron", themes.desk_payload("cyberpunk", "swing")["css"])

    def test_card_palette_covers_every_mode(self):
        for t in themes.THEMES:
            cp = themes.card_palette(t)
            self.assertEqual(sorted(cp["modes"]), sorted(t["modes"]))
            for m in cp["modes"].values():
                self.assertEqual({"bg", "panel", "ink1", "ink2", "ink3", "good", "bad", "s1"} - set(m), set())

    def test_theme_js_is_self_contained(self):
        js = themes.theme_js("valuation", themes.desk_payload("cyberpunk", "valuation"), "http://127.0.0.1:8700")
        self.assertIn('"http://127.0.0.1:8700"', js)
        self.assertIn('"uw-theme"', js)
        self.assertNotIn("%(", js)
        self.assertIn("i % 5", js)


class DesksWired(unittest.TestCase):
    """When the desk folders sit beside the dashboard, each desk page loads theme.js and its
    share card goes through uwPal."""
    FILES = {"Valuation Desk/index.html": "valuation", "Swing Desk/index.html": "swing",
             "Confluence Desk/index.html": "confluence", "Institutional Desk/index.html": "institutional",
             "UW Flow Desk/flowboard.html": "flow", "Growth Desk/index.html": "growth"}

    def test_desks_load_the_theme_hook(self):
        seen = 0
        for rel, desk in self.FILES.items():
            p = os.path.join(PARENT, rel)
            if not os.path.exists(p):
                continue
            seen += 1
            with open(p, encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn('src="http://127.0.0.1:8700/theme.js?desk=%s"' % desk, page, rel)
            self.assertIn("uwPal(", page, rel)
            self.assertLess(page.index("theme.js?desk="), page.index("</head>"), rel)
        if not seen:
            self.skipTest("desk folders not next to the dashboard")


if __name__ == "__main__":
    unittest.main()
