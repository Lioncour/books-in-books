"""
Live check of the Judge against the real Claude API.

Skipped unless you ask for it, because it needs credentials and costs a little:

    $env:BOOKS_LIVE_TESTS = "1"
    $env:ANTHROPIC_API_KEY = "<your key>"
    .venv\\Scripts\\python -m unittest tests.test_live_judge -v

The passages are the real mistakes that title-only matching made in this
library. If the prompt or the model changes and one of these flips, the test
says so before the whole library is re-read.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import judge  # noqa: E402

LIVE = os.environ.get("BOOKS_LIVE_TESTS") == "1" and judge.judge_status()["available"]

MENTIONS = [
    {
        "title": "Pop Goes the Weasel",
        "context": "buttons, and the music changed to ‘Camptown Races’, then ‘Pop Goes the "
        "Weasel’ then a couple video game theme songs I couldn’t name,",
    },
    {
        "title": "Introduction",
        "context": "Contents Introduction 1. The Beginning 2. The Offer 3. The Flight",
    },
    {
        "title": "Lean In",
        "context": "By the time Sheryl published Lean In, the office had already decided "
        "what it thought of the book and of her.",
    },
    {
        "title": "Bon Voyage",
        "context": "sitting on the rug surrounded by books in every direction to scrawl you a "
        "Bon Voyage. I hope you and Brian have a ball in London.",
    },
    {
        "title": "Clarendon’s Rebellion",
        "context": "please don’t worry about us using old books such as Clarendon’s Rebellion "
        "for wrapping. In this particular case they were just two odd volumes",
    },
]


@unittest.skipUnless(LIVE, "set BOOKS_LIVE_TESTS=1 and Claude credentials to run")
class LiveJudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = judge.judge_mentions("A test library book", "Unknown", MENTIONS)
        assert result is not None, "the Judge returned nothing"
        cls.verdicts = result["verdicts"]

    def verdict(self, index):
        self.assertIn(index, self.verdicts, f"no verdict for {MENTIONS[index]['title']}")
        return self.verdicts[index]

    def test_a_tune_is_music_not_a_novel(self):
        v = self.verdict(0)
        self.assertTrue(v["is_reference"])
        self.assertEqual(v["kind"], "music")
        self.assertNotIn("patterson", v["author"].lower())

    def test_a_contents_heading_is_not_a_work(self):
        self.assertFalse(self.verdict(1)["is_reference"])

    def test_a_real_book_is_recognised_with_its_author(self):
        v = self.verdict(2)
        self.assertTrue(v["is_reference"])
        self.assertEqual(v["kind"], "book")
        self.assertIn("sandberg", v["author"].lower())

    def test_a_farewell_is_not_a_work(self):
        self.assertFalse(self.verdict(3)["is_reference"])

    def test_an_abbreviated_title_gets_its_catalogue_title_and_author(self):
        v = self.verdict(4)
        self.assertTrue(v["is_reference"])
        self.assertIn("rebellion", v["canonical_title"].lower())
        self.assertTrue("clarendon" in v["author"].lower() or "hyde" in v["author"].lower())

    def test_every_verdict_names_a_manner(self):
        for v in self.verdicts.values():
            self.assertIn(v["manner"], judge.MANNERS)


if __name__ == "__main__":
    unittest.main()
