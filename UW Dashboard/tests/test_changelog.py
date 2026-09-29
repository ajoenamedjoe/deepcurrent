"""CHANGELOG.md drives the What's new page: it must parse, and every release must have its entry."""
import unittest

from _path import ROOT  # noqa: F401
import changelog
import server


class Changelog(unittest.TestCase):
    def setUp(self):
        self.d = changelog.load()

    def test_the_running_release_has_the_newest_entry(self):
        self.assertNotIn("error", self.d)
        self.assertEqual(self.d["releases"][0]["id"], server.RELEASE,
                         "bump server.RELEASE and add its entry at the top of CHANGELOG.md")

    def test_every_release_is_complete(self):
        ids = [r["id"] for r in self.d["releases"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate release ids")
        self.assertGreaterEqual(len(ids), 10)
        for r in self.d["releases"]:
            self.assertTrue(r["title"] and r["date"], r)
            self.assertTrue(r["items"], r["id"])
            self.assertTrue(r["tags"], r["id"])
            self.assertEqual(set(r["tags"]) - set(changelog.TAGS), set(), r["id"])
            for it in r["items"]:
                self.assertTrue(it["headline"], r["id"])

    def test_parser(self):
        text = ("# x\n\n---\n\n## 2031-01-02 · r2 · Two\nTags: Flow, Dashboard\nDo: Restart it.\n"
                "- **Headline one.** — Details `code`\n  continued here.\n- **Bare headline**\n\n"
                "## 2031-01-01 · r1 · One\nTags: Swing\n- **Only** - hyphen details\n")
        rs = changelog.parse(text)
        self.assertEqual([r["id"] for r in rs], ["r2", "r1"])
        self.assertEqual(rs[0]["tags"], ["Flow", "Dashboard"])
        self.assertEqual(rs[0]["do"], "Restart it.")
        self.assertEqual(rs[0]["items"][0], {"headline": "Headline one", "details": "Details `code` continued here."})
        self.assertEqual(rs[0]["items"][1], {"headline": "Bare headline", "details": ""})
        self.assertEqual(rs[1]["items"][0]["details"], "hyphen details")

    def test_missing_file_is_reported_not_raised(self):
        d = changelog.load("/nonexistent/CHANGELOG.md")
        self.assertEqual(d["releases"], [])
        self.assertIn("not found", d["error"])


if __name__ == "__main__":
    unittest.main()
