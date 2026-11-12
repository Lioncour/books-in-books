"""
Offline tests for the extraction pipeline, corrections and the write guard.

Run from the project folder:

    .venv\\Scripts\\python -m unittest discover -s tests -v

No network, no spaCy model and no Claude credentials are needed: Open Library
and the Judge are replaced with fakes, and each test works in a temp folder.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

os.environ["BOOKS_NO_ENV_FILE"] = "1"  # never let a local .env steer the tests

import judge  # noqa: E402
import process_books as pb  # noqa: E402

# A tiny catalogue standing in for Open Library: (title, author, first year)
CATALOGUE = [
    ("Lean In", "Sheryl Sandberg", 2013),
    ("Atlas Shrugged", "Ayn Rand", 1957),
    ("Atlas Shrugged", "Anne Williams", 2009),
    ("Pop Goes the Weasel", "James Patterson", 1999),
    ("Beautiful Child", "Torey L. Hayden", 2002),
    ("A Christmas Carol", "Charles Dickens", 1843),
    ("Great Expectations", "Charles Dickens", 1861),
]


def fake_lookup(title, author=None, max_year=None, strict_title=False):
    """Mimics lookup_open_library's contract against CATALOGUE."""
    for cat_title, cat_author, year in CATALOGUE:
        if pb.title_similarity(title, cat_title) < (0.95 if strict_title and not author else 0.72):
            continue
        if author and not pb.author_matches(author, cat_author):
            continue
        if max_year and year > max_year + 1:
            continue
        return {
            "title": cat_title,
            "author": cat_author,
            "open_library_id": f"/works/{pb.normalize_title(cat_title)}-{pb.normalize_title(cat_author)}",
            "match_score": 1.0,
            "cover_id": None,
            "first_publish_year": year,
        }
    return None


def fake_judge(verdicts_by_title, source_year=None):
    """Build a judge callable answering from a {raw title: verdict} table."""

    def run(source_title, source_author, mentions, progress_callback=None):
        out = {}
        for i, mention in enumerate(mentions):
            verdict = verdicts_by_title.get(mention["title"])
            if verdict is not None:
                out[i] = {"index": i, "reason": "test", **verdict}
        return {"source_year": source_year, "verdicts": out}

    return run


def book(title, author):
    return {"is_reference": True, "kind": "book", "canonical_title": title, "author": author}


NOT_A_WORK = {"is_reference": False, "kind": "other", "canonical_title": "", "author": ""}


class TempProject(unittest.TestCase):
    """Each test gets an empty database in its own folder."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        pb.init_database(quiet=True)
        self.conn = sqlite3.connect("database.db")
        self.cur = self.conn.cursor()
        patches = [
            mock.patch.object(pb, "lookup_open_library", side_effect=fake_lookup),
            mock.patch.object(pb, "ensure_cover_for_book", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.cur.execute(
            "INSERT INTO books (title, author, open_library_id, verified) VALUES (?, ?, ?, 1)",
            ("Careless People", "Sarah Wynn-Williams", "/works/source"),
        )
        self.source_id = self.cur.lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def store(self, mentions, judge_fn):
        return pb._store_references(
            mentions, self.source_id, self.cur, self.conn, quiet=True, judge=judge_fn
        )

    def links(self):
        self.cur.execute(
            """
            SELECT b.title, b.author, b.kind, IFNULL(r.confirmed, 0)
            FROM book_references r JOIN books b ON b.id = r.referenced_book_id
            WHERE r.source_book_id = ? ORDER BY b.title
            """,
            (self.source_id,),
        )
        return self.cur.fetchall()

    def skipped(self):
        self.cur.execute(
            "SELECT raw_title, reason FROM skipped_mentions WHERE source_book_id = ? ORDER BY raw_title",
            (self.source_id,),
        )
        return dict(self.cur.fetchall())


class HelperTests(unittest.TestCase):
    def test_author_matches(self):
        self.assertTrue(pb.author_matches("Sun Tzu", ["孙武 (Sun Tzu)"]))
        self.assertTrue(pb.author_matches("Charles Dickens", "Dickens, Charles"))
        self.assertTrue(pb.author_matches("Dickens", ["Charles Dickens"]))
        self.assertTrue(pb.author_matches("Edward Hyde Clarendon", ["Edward Hyde, 1st Earl of Clarendon"]))
        self.assertFalse(pb.author_matches("Ayn Rand", ["Anne Williams"]))
        self.assertFalse(pb.author_matches("Lady Gaga", ["Judi James"]))
        self.assertFalse(pb.author_matches("", ["Anyone"]))
        self.assertFalse(pb.author_matches("Ayn Rand", [None, ""]))

    def test_title_containment_is_by_whole_words(self):
        self.assertLess(pb.title_similarity("If--", "Tom Swift"), 0.5)
        self.assertLess(pb.title_similarity("Emma", "Dilemmas"), 0.5)
        self.assertGreater(pb.title_similarity("Show Business", "Underfoot in Show Business"), 0.72)
        self.assertGreater(pb.title_similarity("The Rover Boys", "The Rover Boys in Southern Waters"), 0.72)

    def test_clean_context_decodes_entities(self):
        self.assertEqual(
            pb.clean_context("rhymes.&#13; Her favorite &amp;amp; best\r\n one"),
            "rhymes. Her favorite & best one",
        )

    def test_file_metadata_cleanup(self):
        self.assertEqual(pb.normalize_author_name("Wynn-Williams, Sarah"), "Sarah Wynn-Williams")
        self.assertEqual(pb.normalize_author_name("Dennis E. Taylor"), "Dennis E. Taylor")
        self.assertEqual(
            pb.clean_metadata_title("We Are Legion (We Are Bob) (Bobiverse Book 1)"),
            "We Are Legion (We Are Bob)",
        )
        self.assertEqual(
            pb.clean_metadata_title("SpecOps (Expeditionary Force Book 2)"), "SpecOps"
        )


class JudgedExtractionTests(TempProject):
    def test_junk_is_dropped_with_the_judges_reason(self):
        self.store(
            [{"title": "Introduction", "context": "Introduction This book began"}],
            fake_judge({"Introduction": NOT_A_WORK}),
        )
        self.assertEqual(self.links(), [])
        self.assertTrue(self.skipped()["Introduction"].startswith("Judge:"))

    def test_judged_book_bypasses_the_plausibility_filter(self):
        # "Lean In" used to be rejected as "Not a plausible book title"
        self.store(
            [{"title": "Lean In", "context": "after Sheryl published Lean In"}],
            fake_judge({"Lean In": book("Lean In", "Sheryl Sandberg")}),
        )
        self.assertEqual(self.links(), [("Lean In", "Sheryl Sandberg", "book", 0)])

    def test_author_picks_the_right_work_among_same_titles(self):
        self.store(
            [{"title": "Atlas Shrugged", "context": "quoting Atlas Shrugged at him"}],
            fake_judge({"Atlas Shrugged": book("Atlas Shrugged", "Ayn Rand")}),
        )
        self.assertEqual(self.links(), [("Atlas Shrugged", "Ayn Rand", "book", 0)])

    def test_existing_wrong_author_row_is_not_reused_or_overwritten(self):
        self.cur.execute(
            "INSERT INTO books (title, author, open_library_id, verified) VALUES (?, ?, ?, 1)",
            ("Atlas Shrugged", "Anne Williams", "/works/wrong"),
        )
        self.conn.commit()
        self.store(
            [{"title": "Atlas Shrugged", "context": "quoting Atlas Shrugged at him"}],
            fake_judge({"Atlas Shrugged": book("Atlas Shrugged", "Ayn Rand")}),
        )
        self.assertEqual(self.links(), [("Atlas Shrugged", "Ayn Rand", "book", 0)])
        self.cur.execute("SELECT author FROM books WHERE open_library_id = '/works/wrong'")
        self.assertEqual(self.cur.fetchone()[0], "Anne Williams")

    def test_title_only_match_newer_than_the_source_is_not_used(self):
        # To Kill a Mockingbird (1960) cannot mention Beautiful Child (2002).
        # With no author to pin the work down, the 2002 record must not be linked.
        self.store(
            [{"title": "Beautiful Child", "context": "a beautiful child she was"}],
            fake_judge({"Beautiful Child": book("Beautiful Child", "")}, source_year=1960),
        )
        self.assertNotIn("Torey L. Hayden", [l[1] for l in self.links()])

    def test_anonymous_work_missing_from_the_catalogue_is_filed_on_the_judges_word(self):
        self.store(
            [{"title": "Proclamation of the Irish Republic", "context": "the epigraph, from the Proclamation"}],
            fake_judge({"Proclamation of the Irish Republic": book("Proclamation of the Irish Republic", "")}),
        )
        self.assertEqual(self.links(), [("Proclamation of the Irish Republic", "Anonymous", "book", 0)])

    def test_later_work_named_with_its_author_is_kept(self):
        # An afterword in a later edition lists the author's later books
        self.store(
            [{"title": "Lean In", "context": "Her other books include Lean In (2013)"}],
            fake_judge({"Lean In": book("Lean In", "Sheryl Sandberg")}, source_year=1970),
        )
        self.assertEqual([l[0] for l in self.links()], ["Lean In"])

    def test_work_missing_from_the_catalogue_is_filed_on_the_judges_word(self):
        self.store(
            [{"title": "Aesop and Rhodope", "context": "the dialogue Aesop and Rhodope"}],
            fake_judge({"Aesop and Rhodope": book("Aesop and Rhodope", "Walter Savage Landor")}),
        )
        self.assertEqual(self.links(), [("Aesop and Rhodope", "Walter Savage Landor", "book", 0)])
        self.cur.execute("SELECT open_library_id FROM books WHERE title = 'Aesop and Rhodope'")
        self.assertTrue(self.cur.fetchone()[0].startswith("judge_book_"))

    def test_without_an_author_only_an_identical_title_is_accepted(self):
        # "The Complete Poetry & Selected Prose" must not become Vallejo's "The Complete Poetry"
        self.cur.execute(
            "INSERT INTO books (title, author, open_library_id, verified) VALUES (?, ?, ?, 1)",
            ("The Complete Poetry", "César Vallejo", "/works/vallejo"),
        )
        self.conn.commit()
        self.store(
            [{"title": "The Complete Poetry & Selected Prose", "context": "The Complete Poetry & Selected Prose of"}],
            fake_judge({"The Complete Poetry & Selected Prose": book("The Complete Poetry and Selected Prose", "")}),
        )
        # filed as its own anonymous work, never as Vallejo's book
        self.assertEqual(
            self.links(), [("The Complete Poetry and Selected Prose", "Anonymous", "book", 0)]
        )

    def test_music_keeps_the_composer_the_judge_named(self):
        with mock.patch("mention_kinds.lookup_by_kind", side_effect=AssertionError("performer lookup")):
            self.store(
                [{"title": "Messiah", "context": "unable to get the Messiah in stiff covers"}],
                fake_judge({"Messiah": {"is_reference": True, "kind": "music",
                                        "canonical_title": "Messiah", "author": "George Frideric Handel"}}),
            )
        self.assertEqual(self.links(), [("Messiah", "George Frideric Handel", "music", 0)])

    def test_tune_is_not_filed_as_a_novel_or_given_a_random_performer(self):
        with mock.patch("mention_kinds.lookup_by_kind", side_effect=AssertionError("performer lookup")):
            self.store(
                [{"title": "Pop Goes the Weasel", "context": "the music changed to Pop Goes the Weasel"}],
                fake_judge(
                    {
                        "Pop Goes the Weasel": {
                            "is_reference": True,
                            "kind": "music",
                            "canonical_title": "Pop Goes the Weasel",
                            "author": "",
                        }
                    }
                ),
            )
        self.assertEqual(self.links(), [("Pop Goes the Weasel", "Traditional", "music", 0)])

    def test_without_the_judge_the_old_matching_still_works(self):
        result = self.store(
            [{"title": "A Christmas Carol", "context": "read A Christmas Carol aloud"}],
            lambda *args, **kwargs: None,
        )
        self.assertFalse(result["judged"])
        self.assertEqual(self.links(), [("A Christmas Carol", "Charles Dickens", "book", 0)])

    def test_quotes_are_stored_clean(self):
        self.store(
            [{"title": "Lean In", "context": "published&#13; Lean In &amp; more"}],
            fake_judge({"Lean In": book("Lean In", "Sheryl Sandberg")}),
        )
        self.cur.execute("SELECT context, raw_title FROM book_references")
        self.assertEqual(self.cur.fetchone(), ("published Lean In & more", "Lean In"))


class CorrectionTests(TempProject):
    MENTION = [{"title": "Lean In", "context": "after Sheryl published Lean In"}]
    JUDGE = staticmethod(fake_judge({"Lean In": book("Lean In", "Sheryl Sandberg")}))

    def override(self, raw_key, action, target_id=None):
        self.cur.execute(
            "INSERT INTO reference_overrides (source_book_id, raw_key, action, target_book_id) VALUES (?, ?, ?, ?)",
            (self.source_id, raw_key, action, target_id),
        )
        self.conn.commit()

    def test_rejection_survives_re_extraction(self):
        self.override(pb.override_key("Lean In"), "reject")
        self.store(self.MENTION, self.JUDGE)
        self.assertEqual(self.links(), [])
        self.assertEqual(self.skipped()["Lean In"], "Rejected by you")

    def test_rejecting_a_target_blocks_it_whatever_the_wording(self):
        self.store(self.MENTION, self.JUDGE)
        self.cur.execute("SELECT referenced_book_id FROM book_references")
        target_id = self.cur.fetchone()[0]
        self.cur.execute("DELETE FROM book_references")
        self.override(f"#{target_id}", "reject")
        self.store([{"title": "Lean In!", "context": "her book, Lean In!"}],
                   fake_judge({"Lean In!": book("Lean In", "Sheryl Sandberg")}))
        self.assertEqual(self.links(), [])

    def test_manual_link_is_replayed_and_stays_confirmed(self):
        self.cur.execute(
            "INSERT INTO books (title, author, open_library_id, verified) VALUES (?, ?, ?, 1)",
            ("Great Expectations", "Charles Dickens", "/works/ge"),
        )
        target_id = self.cur.lastrowid
        self.override(pb.override_key("Expectations"), "link", target_id)
        # The Judge is never consulted for a mention a person already decided
        boom = mock.Mock(side_effect=AssertionError("judge should not be called"))
        self.store([{"title": "Expectations", "context": "like Pip in Expectations"}], boom)
        self.assertEqual(self.links(), [("Great Expectations", "Charles Dickens", "book", 1)])


class CandidateTests(unittest.TestCase):
    MARKUP = (
        "<p>She was reading <i>Middlemarch</i> again, and <em>never</em> finished "
        "<i>The Mill on the Floss</i>. <cite>Diary of a Provincial Lady</cite> came next. "
        "<i>Really, though.</i> <span class=\"italic\">Daniel Deronda</span></p>"
    )

    def test_italic_titles_are_found_and_emphasis_is_not(self):
        text = pb._strip_tags(self.MARKUP)
        titles = [m["title"] for m in pb.extract_markup_mentions(self.MARKUP, text)]
        self.assertEqual(
            titles,
            ["Middlemarch", "The Mill on the Floss", "Diary of a Provincial Lady", "Daniel Deronda"],
        )

    def test_markup_mentions_carry_their_passage(self):
        text = pb._strip_tags(self.MARKUP)
        mention = pb.extract_markup_mentions(self.MARKUP, text)[0]
        self.assertEqual(mention["source"], "markup")
        self.assertIn("She was reading Middlemarch again", mention["context"])

    def test_italics_set_by_a_stylesheet_class_are_found(self):
        css = (
            ".class_s2US { font-style: italic; }\n"
            "p.note, span.em-x { margin: 0; font-style:oblique }\n"
            ".plain { font-weight: bold; }\n"
            ".chapter .inner { font-style: italic; }"
        )
        self.assertEqual(pb.italic_classes_from_css(css), ["class_s2US", "note", "em-x", "inner"])
        markup = (
            '<p>We watched <span class="class_s2US">Finding Nemo</span> twice, '
            'then <span class="plain">Not Italic Here</span> and '
            '<span class="x class_s2US y">The Lion King</span>.</p>'
        )
        text = pb._strip_tags(markup)
        titles = [m["title"] for m in pb.extract_markup_mentions(markup, text, ["class_s2US"])]
        self.assertEqual(titles, ["Finding Nemo", "The Lion King"])

    def test_quoted_titles_but_not_dialogue(self):
        text = (
            'He sang “I Want It That Way” badly. “Come here,” she said. '
            '“What do you want?” Then the essay “On Going a Journey” was read. “Yes”'
        )
        titles = [m["title"] for m in pb.extract_quoted_mentions(text)]
        self.assertEqual(titles, ["I Want It That Way", "On Going a Journey"])

    def test_title_shape(self):
        self.assertTrue(pb.looks_like_a_title("The History of the Rebellion"))
        self.assertTrue(pb.looks_like_a_title("Emma"))
        self.assertFalse(pb.looks_like_a_title("never"))
        self.assertFalse(pb.looks_like_a_title("It was a long time ago."))
        self.assertFalse(pb.looks_like_a_title("She went out and did not come back that night at all"))


class PipelineHousekeepingTests(TempProject):
    def test_italic_candidates_are_ignored_without_the_judge(self):
        mentions = [
            {"title": "A Christmas Carol", "context": "read A Christmas Carol aloud"},
            {"title": "Great Expectations", "context": "Great Expectations", "source": "markup"},
        ]
        self.store(mentions, lambda *args, **kwargs: None)
        self.assertEqual([l[0] for l in self.links()], ["A Christmas Carol"])
        self.assertEqual(self.skipped(), {})

    def test_italic_candidates_are_used_with_the_judge(self):
        self.store(
            [{"title": "Great Expectations", "context": "Pip in Great Expectations", "source": "markup"}],
            fake_judge({"Great Expectations": book("Great Expectations", "Charles Dickens")}),
        )
        self.assertEqual([l[0] for l in self.links()], ["Great Expectations"])

    def test_candidates_are_cached_per_file_and_version(self):
        candidates = [{"title": "Lean In", "context": "c"}]
        self.assertIsNone(pb.cached_candidates("abc", self.cur))
        pb.store_candidates("abc", candidates, self.cur, self.conn)
        self.assertEqual(pb.cached_candidates("abc", self.cur), candidates)
        with mock.patch.object(pb, "EXTRACTOR_VERSION", pb.EXTRACTOR_VERSION + 1):
            self.assertIsNone(pb.cached_candidates("abc", self.cur))

    def test_cached_candidates_skip_the_slow_reading_pass(self):
        pb.store_candidates(
            f"{pb.spacy_model_name()}:abc", [{"title": "Lean In", "context": "c"}], self.cur, self.conn
        )
        with mock.patch.object(pb, "find_candidates", side_effect=AssertionError("re-read")):
            with mock.patch("judge.judge_mentions", return_value=None):
                pb.process_book_file(
                    Path("whatever.epub"), self.source_id, self.cur, self.conn,
                    quiet=True, file_hash="abc",
                )

    def test_orphan_works_are_pruned_but_library_books_stay(self):
        self.cur.execute(
            "INSERT INTO books (title, author, open_library_id, verified) VALUES ('Orphan', 'X', 'o1', 1)"
        )
        self.cur.execute(
            "INSERT INTO processed_files (filename, file_hash, book_id) VALUES ('f.epub', 'h', ?)",
            (self.source_id,),
        )
        self.assertEqual(pb.prune_orphan_works(self.cur), 1)
        self.cur.execute("SELECT title FROM books")
        self.assertEqual([r[0] for r in self.cur.fetchall()], ["Careless People"])

    def test_library_book_is_refiled_under_its_real_author(self):
        # "Montaigne" matched Montaigne's Essais; the file says Stefan Zweig wrote it
        self.cur.execute(
            "INSERT INTO books (title, author, open_library_id, verified, cover_id) VALUES (?, ?, ?, 1, '111')",
            ("Montaigne", "Stefan Zweig", "/works/essais"),
        )
        book_id = self.cur.lastrowid
        right = {
            "title": "Montaigne", "author": "Stefan Zweig", "open_library_id": "/works/zweig",
            "match_score": 1.0, "cover_id": "222", "first_publish_year": 1960,
        }
        with mock.patch.object(pb, "lookup_open_library", return_value=right):
            self.assertTrue(pb.refresh_owned_record(book_id, "Montaigne", "Stefan Zweig", self.cur, self.conn))
            self.assertFalse(pb.refresh_owned_record(book_id, "Montaigne", "Stefan Zweig", self.cur, self.conn))
        self.cur.execute("SELECT open_library_id, cover_id FROM books WHERE id = ?", (book_id,))
        self.assertEqual(self.cur.fetchone(), ("/works/zweig", "222"))


class MannerAndCacheTests(TempProject):
    def test_manner_from_the_judge_is_stored_on_the_link(self):
        verdict = dict(book("Lean In", "Sheryl Sandberg"), manner="recommended")
        self.store([{"title": "Lean In", "context": "you must read Lean In"}],
                   fake_judge({"Lean In": verdict}))
        self.cur.execute("SELECT manner FROM book_references")
        self.assertEqual(self.cur.fetchone()[0], "recommended")

    def test_catalogue_answers_are_cached(self):
        mock.patch.stopall()  # use the real _ol_search path, with requests faked
        reply = mock.Mock()
        reply.json.return_value = {"docs": [{"key": "/works/1", "title": "Emma", "author_name": ["Jane Austen"],
                                             "junk_field": "dropped"}]}
        with mock.patch.object(pb.requests, "get", return_value=reply) as get:
            first = pb._ol_search({"title": "Emma", "author": "Jane Austen"})
            second = pb._ol_search({"author": "Jane Austen", "title": "Emma"})
            pb._ol_search({"title": "Persuasion"})
        self.assertEqual(first, second)
        self.assertEqual(first, [{"key": "/works/1", "title": "Emma", "author_name": ["Jane Austen"]}])
        self.assertEqual(get.call_count, 2)  # Emma once, Persuasion once

    def test_stale_cache_entries_are_refetched(self):
        mock.patch.stopall()
        reply = mock.Mock()
        reply.json.return_value = {"docs": []}
        with mock.patch.object(pb.requests, "get", return_value=reply) as get:
            pb._ol_search({"title": "Emma"})
            with mock.patch.object(pb, "LOOKUP_CACHE_DAYS", 0):
                pb._ol_search({"title": "Emma"})
        self.assertEqual(get.call_count, 2)

    def test_hand_edited_library_book_is_not_overwritten_by_file_metadata(self):
        Path("books").mkdir()
        Path("books/Wrong Name - Someone Else.txt").write_text("text", encoding="utf-8")
        self.cur.execute(
            "INSERT INTO processed_files (filename, file_hash, book_id) VALUES (?, 'h', ?)",
            ("Wrong Name - Someone Else.txt", self.source_id),
        )
        self.cur.execute("UPDATE books SET user_edited = 1 WHERE id = ?", (self.source_id,))
        self.conn.commit()
        pb.repair_owned_books(self.cur, self.conn)
        self.cur.execute("SELECT title, author FROM books WHERE id = ?", (self.source_id,))
        self.assertEqual(self.cur.fetchone(), ("Careless People", "Sarah Wynn-Williams"))


class EvalScriptTests(TempProject):
    def test_precision_and_recall_against_a_gold_set(self):
        import eval_extraction

        for title, author, olid in [("Emma", "Jane Austen", "e"), ("Selected Essays", "Someone Wrong", "s"),
                                    ("Bon voyage!", "A. Phrasebook", "b")]:
            self.cur.execute(
                "INSERT INTO books (title, author, open_library_id, verified) VALUES (?, ?, ?, 1)",
                (title, author, olid),
            )
            self.cur.execute(
                "INSERT INTO book_references (source_book_id, referenced_book_id) VALUES (?, ?)",
                (self.source_id, self.cur.lastrowid),
            )
        self.conn.commit()
        gold = {
            "source": {"title": "Careless People", "author": "Sarah Wynn-Williams"},
            "works": [
                {"title": "Emma", "authors": ["Jane Austen"]},
                {"title": "Selected Essays", "authors": ["William Hazlitt"]},
                {"title": "Persuasion", "authors": ["Jane Austen"]},
            ],
            "not_works": ["Bon voyage"],
        }
        result = eval_extraction.evaluate(gold, self.cur)
        self.assertEqual(result["correct"], 1)
        self.assertEqual(len(result["wrong_work"]), 1)
        self.assertEqual(len(result["not_a_work"]), 1)
        self.assertEqual(result["precision"], round(1 / 3, 3))
        self.assertEqual(result["recall"], round(1 / 3, 3))
        self.assertEqual(sorted(result["missed"]), ["Persuasion", "Selected Essays"])

    def test_a_placeholder_creator_does_not_make_a_right_work_wrong(self):
        import eval_extraction

        for title, author, kind, olid in [("The Croppy Boy", "Traditional", "music", "c"),
                                          ("All Creatures Great and Small", "British television series", "movie", "a"),
                                          ("Jingle Bells", "A Random Band", "music", "j")]:
            self.cur.execute(
                "INSERT INTO books (title, author, open_library_id, verified, kind) VALUES (?, ?, ?, 1, ?)",
                (title, author, olid, kind),
            )
            self.cur.execute(
                "INSERT INTO book_references (source_book_id, referenced_book_id) VALUES (?, ?)",
                (self.source_id, self.cur.lastrowid),
            )
        self.conn.commit()
        gold = {
            "source": {"title": "Careless People", "author": "Sarah Wynn-Williams"},
            "works": [
                {"title": "The Croppy Boy", "authors": ["William B. McBurney"], "kind": "music"},
                {"title": "All Creatures Great and Small", "authors": ["James Herriot"], "kind": "movie"},
                {"title": "Jingle Bells", "authors": ["James Lord Pierpont"], "kind": "music"},
            ],
        }
        result = eval_extraction.evaluate(gold, self.cur)
        self.assertEqual(result["correct"], 2)
        self.assertEqual(len(result["wrong_work"]), 1)  # a named, wrong performer still counts as wrong


class ReaderTests(TempProject):
    """reader.read_for_mentions against a fake streaming client."""

    TEXT = (
        "She was reading Middlemarch again that winter, slowly. "
        "Her brother hummed the old carol and poured a drink. "
        "Nobody mentioned the war."
    )

    def make_client(self, works):
        calls = []

        class Stream:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def get_final_message(self_inner):
                return SimpleNamespace(
                    stop_reason="end_turn",
                    content=[SimpleNamespace(type="text", text=json.dumps({"works": works}))],
                )

        def stream(**kwargs):
            calls.append(kwargs)
            return Stream()

        return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream))), calls

    def work(self, title, quote, author="George Eliot", kind="book", manner="read"):
        return {"title": title, "author": author, "kind": kind, "manner": manner, "quote": quote}

    def test_finds_carry_a_verdict_and_their_passage(self):
        import reader

        client, calls = self.make_client(
            [self.work("Middlemarch", "She was reading Middlemarch again that winter, slowly.")]
        )
        found = reader.read_for_mentions("A Novel", "Someone", self.TEXT, client=client)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], "reader")
        self.assertEqual(found[0]["verdict"]["author"], "George Eliot")
        self.assertEqual(found[0]["verdict"]["manner"], "read")
        self.assertIn("Book: A Novel by Someone", calls[0]["messages"][0]["content"])

    def test_a_work_without_a_real_quote_is_discarded(self):
        import reader

        client, _ = self.make_client(
            [
                self.work("Middlemarch", "She was reading Middlemarch again that winter, slowly."),
                self.work("War and Peace", "He had always loved War and Peace above all others.", "Leo Tolstoy"),
            ]
        )
        found = reader.read_for_mentions("A Novel", "Someone", self.TEXT, client=client)
        self.assertEqual([m["title"] for m in found], ["Middlemarch"])

    def test_long_text_is_read_in_parts(self):
        import reader

        client, calls = self.make_client([])
        text = "A paragraph of plain prose.\n" * 4000
        reader.read_for_mentions("A Novel", "Someone", text, client=client)
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(len(c["messages"][0]["content"]) < reader.CHUNK_CHARS + 200 for c in calls))

    def test_reader_finds_skip_the_judge_and_become_links(self):
        mention = {
            "title": "Great Expectations",
            "context": "Pip, as in Great Expectations",
            "source": "reader",
            "verdict": dict(book("Great Expectations", "Charles Dickens"), manner="compared"),
        }
        boom = mock.Mock(side_effect=AssertionError("judge should not be called"))
        result = self.store([mention], boom)
        self.assertTrue(result["judged"])
        self.assertEqual(self.links(), [("Great Expectations", "Charles Dickens", "book", 0)])
        self.cur.execute("SELECT manner FROM book_references")
        self.assertEqual(self.cur.fetchone()[0], "compared")

    def test_reader_is_off_unless_switched_on(self):
        import reader

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOOKS_READER", None)
            self.assertFalse(reader.reader_enabled())
            with mock.patch.object(reader, "read_for_mentions", side_effect=AssertionError("called")):
                out = pb.add_reader_mentions(
                    [{"title": "X"}], Path("b.txt"), self.source_id, "h", self.cur, self.conn
                )
            self.assertEqual(out, [{"title": "X"}])
        with mock.patch.dict(os.environ, {"BOOKS_READER": "on"}):
            self.assertTrue(reader.reader_enabled())

    def test_reader_results_are_cached_per_file_and_model(self):
        import reader

        Path("b.txt").write_text(self.TEXT, encoding="utf-8")
        finds = [{"title": "Middlemarch", "context": "q", "source": "reader", "verdict": book("Middlemarch", "George Eliot")}]
        with mock.patch.dict(os.environ, {"BOOKS_READER": "on"}):
            with mock.patch.object(reader, "read_for_mentions", return_value=finds) as read:
                first = pb.add_reader_mentions([], Path("b.txt"), self.source_id, "h", self.cur, self.conn)
                second = pb.add_reader_mentions([], Path("b.txt"), self.source_id, "h", self.cur, self.conn)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(first, second)


class SpacyModelTests(unittest.TestCase):
    def test_small_model_steps_in_when_the_configured_one_is_missing(self):
        fake_spacy = SimpleNamespace(
            load=mock.Mock(side_effect=lambda name: (_ for _ in ()).throw(OSError())
                           if name != pb.SMALL_SPACY_MODEL else "small-model")
        )
        with mock.patch.dict(sys.modules, {"spacy": fake_spacy}),                 mock.patch.object(pb, "nlp", None), mock.patch.object(pb, "_nlp_model_name", None):
            self.assertEqual(pb.get_nlp(), "small-model")
            self.assertEqual(pb.spacy_model_name(), pb.SMALL_SPACY_MODEL)


class AuditorTests(TempProject):
    """The Auditor checks finished links and acts on what it finds."""

    def add_link(self, title, author, olid, raw_title=None, kind="book", confirmed=0):
        self.cur.execute(
            "INSERT INTO books (title, author, open_library_id, verified, kind) VALUES (?, ?, ?, 1, ?)",
            (title, author, olid, kind),
        )
        target = self.cur.lastrowid
        self.cur.execute(
            """
            INSERT INTO book_references (source_book_id, referenced_book_id, context, raw_title, confirmed, manner)
            VALUES (?, ?, 'a passage', ?, ?, 'read')
            """,
            (self.source_id, target, raw_title or title, confirmed),
        )
        self.conn.commit()
        return target

    def audit(self, verdicts_by_title):
        def fake(source_title, source_author, links, progress_callback=None):
            out = {}
            for i, link in enumerate(links):
                v = verdicts_by_title.get(link["title"])
                if v:
                    out[i] = {"index": i, "correct_title": "", "correct_author": "",
                              "correct_kind": "book", "reason": "test", **v}
            return out

        return pb.audit_book(self.source_id, self.cur, self.conn, auditor=fake)

    def audits(self):
        self.cur.execute(
            """
            SELECT b.title, b.author, r.audit FROM book_references r
            JOIN books b ON b.id = r.referenced_book_id WHERE r.source_book_id = ? ORDER BY b.title
            """,
            (self.source_id,),
        )
        return self.cur.fetchall()

    def test_right_links_are_marked_checked(self):
        self.add_link("Lean In", "Sheryl Sandberg", "/works/lean")
        summary = self.audit({"Lean In": {"verdict": "right"}})
        self.assertEqual(self.audits(), [("Lean In", "Sheryl Sandberg", "ok")])
        self.assertEqual(summary["ok"], 1)

    def test_wrong_work_is_refiled_and_the_fix_is_remembered(self):
        wrong = self.add_link("Atlas Shrugged", "Anne Williams", "/works/wrong", raw_title="Atlas Shrugged")
        summary = self.audit({"Atlas Shrugged": {"verdict": "wrong_work",
                                                  "correct_title": "Atlas Shrugged",
                                                  "correct_author": "Ayn Rand"}})
        self.assertEqual(self.audits(), [("Atlas Shrugged", "Ayn Rand", "fixed")])
        self.assertEqual(summary["fixed"], 1)
        # the manner of the mention moves with the link
        self.cur.execute("SELECT manner FROM book_references")
        self.assertEqual(self.cur.fetchone()[0], "read")
        # a re-read replays the fix without asking the Judge or the Auditor again
        self.cur.execute("DELETE FROM book_references")
        boom = mock.Mock(side_effect=AssertionError("judge should not be called"))
        self.store([{"title": "Atlas Shrugged", "context": "quoting Atlas Shrugged"}], boom)
        self.assertEqual(self.audits(), [("Atlas Shrugged", "Ayn Rand", "fixed")])
        self.cur.execute("SELECT confirmed FROM book_references")
        self.assertEqual(self.cur.fetchone()[0], 0)  # not passed off as a person's confirmation
        self.cur.execute("SELECT 1 FROM books WHERE id = ?", (wrong,))
        self.assertIsNone(self.cur.fetchone())  # the wrong record is pruned

    def test_non_reference_is_removed_and_stays_out(self):
        self.add_link("Bon voyage!", "Conrad J. Schmitt", "/works/bv", raw_title="Bon Voyage")
        summary = self.audit({"Bon voyage!": {"verdict": "not_a_reference", "reason": "a farewell"}})
        self.assertEqual(self.audits(), [])
        self.assertEqual(summary["removed"], 1)
        self.assertEqual(self.skipped()["Bon Voyage"], "Rejected by the Auditor: a farewell")
        self.store([{"title": "Bon Voyage", "context": "to scrawl you a Bon Voyage"}],
                   fake_judge({"Bon Voyage": book("Bon voyage!", "Conrad J. Schmitt")}))
        self.assertEqual(self.audits(), [])
        self.assertEqual(self.skipped()["Bon Voyage"], "Rejected by the Auditor")

    def test_unsure_links_are_flagged_not_changed(self):
        self.add_link("The Vulgate", "George Henslow", "/works/v")
        self.audit({"The Vulgate": {"verdict": "unsure", "reason": "study or the text?"}})
        self.assertEqual(self.audits(), [("The Vulgate", "George Henslow", "unsure")])

    def test_wrong_work_without_a_usable_correction_is_removed(self):
        self.add_link("Jubilee", "Ruby Braff", "mb_1", kind="music")
        self.audit({"Jubilee": {"verdict": "wrong_work", "correct_title": ""}})
        self.assertEqual(self.audits(), [])

    def test_when_the_catalogue_only_has_the_wrong_record_the_auditor_files_its_own(self):
        # A folk tale filed under one reteller: the lookup can only return that row again
        self.add_link("The Gingerbread Boy", "Paul Galdone", "/works/galdone")
        summary = self.audit({"The Gingerbread Boy": {"verdict": "wrong_work",
                                                       "correct_title": "The Gingerbread Boy",
                                                       "correct_author": ""}})
        self.assertEqual(self.audits(), [("The Gingerbread Boy", "Anonymous", "fixed")])
        self.assertEqual(summary["fixed"], 1)

    def test_judged_films_are_not_matched_to_remakes_or_games(self):
        with mock.patch("mention_kinds.lookup_by_kind", side_effect=AssertionError("film search")):
            self.store(
                [{"title": "The Wizard of Oz", "context": "like Dorothy in The Wizard of Oz"}],
                fake_judge({"The Wizard of Oz": {"is_reference": True, "kind": "movie",
                                                 "canonical_title": "The Wizard of Oz",
                                                 "author": "Victor Fleming"}}),
            )
        self.assertEqual(self.links(), [("The Wizard of Oz", "Victor Fleming", "movie", 0)])

    def test_anonymous_work_is_not_refiled_under_a_commentary_on_it(self):
        self.add_link("The Proverbs", "Derek Kidner", "/works/kidner", raw_title="Proverbs")
        commentary = {"title": "The Book of Proverbs", "author": "Bruce K. Waltke",
                      "open_library_id": "/works/waltke", "match_score": 1.0, "cover_id": None}
        with mock.patch.object(pb, "lookup_open_library", return_value=commentary):
            self.audit({"The Proverbs": {"verdict": "wrong_work",
                                         "correct_title": "Book of Proverbs", "correct_author": ""}})
        self.assertEqual(self.audits(), [("Book of Proverbs", "Anonymous", "fixed")])

    def test_links_a_person_confirmed_are_never_audited(self):
        self.add_link("Lean In", "Sheryl Sandberg", "/works/lean", confirmed=1)
        seen = []

        def fake(source_title, source_author, links, progress_callback=None):
            seen.extend(links)
            return {}

        pb.audit_book(self.source_id, self.cur, self.conn, auditor=fake)
        self.assertEqual(seen, [])

    def test_a_persons_decision_outranks_the_auditor(self):
        pb._save_override(self.cur, self.source_id, "lean in", "link", 7, made_by="you")
        pb._save_override(self.cur, self.source_id, "lean in", "reject", made_by="auditor")
        self.cur.execute("SELECT action, made_by FROM reference_overrides")
        self.assertEqual(self.cur.fetchall(), [("link", "you")])

    def test_no_credentials_means_no_audit(self):
        self.add_link("Lean In", "Sheryl Sandberg", "/works/lean")
        with mock.patch.object(judge, "_get_client", return_value=None):
            summary = pb.audit_book(self.source_id, self.cur, self.conn)
        self.assertFalse(summary["ran"])
        self.assertEqual(self.audits(), [("Lean In", "Sheryl Sandberg", None)])


class RescueTests(TempProject):
    def test_works_dropped_for_lack_of_a_catalogue_record_are_linked(self):
        pb._record_skip(self.cur, self.source_id, "the Proclamation",
                        "Judge says it is “Proclamation of the Irish Republic”, but no catalogue match",
                        "from the Proclamation", suggested_title="Proclamation of the Irish Republic")
        pb._record_skip(self.cur, self.source_id, "Introduction", "Judge: a heading", "ctx")
        self.conn.commit()
        self.assertEqual(pb.rescue_dropped_works(quiet=True), 1)
        self.assertEqual(self.links(), [("Proclamation of the Irish Republic", "Anonymous", "book", 0)])
        self.assertEqual(list(self.skipped()), ["Introduction"])
        self.cur.execute("SELECT audit FROM book_references")
        self.assertIsNone(self.cur.fetchone()[0])  # left for the Auditor

    def test_a_rejected_mention_is_not_rescued(self):
        pb._record_skip(self.cur, self.source_id, "The Bible",
                        "Judge says it is “The Bible”, but no catalogue match", "ctx",
                        suggested_title="The Bible")
        pb._save_override(self.cur, self.source_id, pb.override_key("The Bible"), "reject")
        self.conn.commit()
        self.assertEqual(pb.rescue_dropped_works(quiet=True), 0)
        self.assertEqual(self.links(), [])


class AuditorClientTests(unittest.TestCase):
    def test_request_shows_the_passage_the_wording_and_the_record(self):
        import auditor

        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=json.dumps({"verdicts": [
                    {"index": 0, "verdict": "right", "correct_title": "", "correct_author": "",
                     "correct_kind": "book", "reason": "ok"},
                    {"index": 99, "verdict": "right", "correct_title": "", "correct_author": "",
                     "correct_kind": "book", "reason": "stray"},
                ]}))],
            )

        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
        links = [{"title": "The Vulgate", "author": "George Henslow", "kind": "book",
                  "raw_title": "Vulgate", "context": "will try to find a Vulgate for you",
                  "judge_filed": True}]
        result = auditor.audit_links("84, Charing Cross Road", "Helene Hanff", links, client=client)
        self.assertEqual(list(result), [0])
        prompt = calls[0]["messages"][0]["content"]
        for expected in ("will try to find a Vulgate for you", 'wording found: "Vulgate"',
                         '"The Vulgate" by George Henslow', "filed on the Judge's word",
                         "84, Charing Cross Road by Helene Hanff"):
            self.assertIn(expected, prompt)


class JudgeClientTests(unittest.TestCase):
    """judge.judge_mentions against a fake Anthropic client."""

    def make_client(self, responder):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return responder(kwargs, len(calls))

        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
        return client, calls

    @staticmethod
    def reply(payload, stop_reason="end_turn"):
        return SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="text", text=json.dumps(payload))],
        )

    def test_batches_and_keeps_only_valid_verdicts(self):
        mentions = [{"title": f"Title {i}", "context": "ctx"} for i in range(judge.BATCH_SIZE + 5)]

        def responder(kwargs, call_number):
            start = 0 if call_number == 1 else judge.BATCH_SIZE
            count = judge.BATCH_SIZE if call_number == 1 else 5
            verdicts = [
                {"index": start + i, "is_reference": True, "kind": "book",
                 "canonical_title": f"Title {start + i}", "author": "", "reason": "ok"}
                for i in range(count)
            ]
            # an index from outside this batch must be ignored
            verdicts.append({"index": 9999, "is_reference": True, "kind": "book",
                             "canonical_title": "x", "author": "", "reason": "stray"})
            return self.reply({"source_year": 2025, "verdicts": verdicts})

        client, calls = self.make_client(responder)
        result = judge.judge_mentions("Careless People", "Sarah Wynn-Williams", mentions, client=client)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sorted(result["verdicts"]), list(range(judge.BATCH_SIZE + 5)))
        self.assertEqual(result["source_year"], 2025)
        self.assertIn("Careless People by Sarah Wynn-Williams", calls[0]["messages"][0]["content"])
        self.assertEqual(calls[0]["output_config"]["format"]["type"], "json_schema")

    def test_a_refused_first_batch_means_fall_back(self):
        client, _calls = self.make_client(lambda kwargs, n: self.reply({}, stop_reason="refusal"))
        self.assertIsNone(
            judge.judge_mentions("Book", "Author", [{"title": "X", "context": "c"}], client=client)
        )

    def test_no_credentials_means_fall_back(self):
        with mock.patch.object(judge, "_get_client", return_value=None):
            self.assertIsNone(judge.judge_mentions("Book", "Author", [{"title": "X"}]))


class WriteGuardTests(unittest.TestCase):
    """The server only accepts changes from its own pages on this machine."""

    @classmethod
    def setUpClass(cls):
        cls._cwd = os.getcwd()
        cls._tmp = tempfile.TemporaryDirectory()
        os.chdir(cls._tmp.name)
        os.environ.pop("ADMIN_TOKEN", None)
        sys.modules.pop("app", None)
        import app as app_module

        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._cwd)
        sys.modules.pop("app", None)
        try:
            cls._tmp.cleanup()
        except OSError:
            pass

    def post(self, remote="127.0.0.1", origin=None):
        headers = {"Origin": origin} if origin else {}
        return self.client.post(
            "/api/review/link",
            json={"source_id": 9001, "target_id": 9002, "action": "confirm"},
            headers=headers,
            environ_overrides={"REMOTE_ADDR": remote},
        )

    def test_local_write_passes_the_guard(self):
        self.assertEqual(self.post().status_code, 404)  # guard passed; link does not exist

    def test_same_origin_write_passes_the_guard(self):
        self.assertEqual(self.post(origin="http://localhost").status_code, 404)

    def test_other_website_cannot_write(self):
        self.assertEqual(self.post(origin="https://evil.example").status_code, 403)

    def test_other_machine_cannot_write_without_a_token(self):
        self.assertEqual(self.post(remote="192.168.1.20").status_code, 401)

    def test_other_machine_can_still_read(self):
        response = self.client.get("/api/health", environ_overrides={"REMOTE_ADDR": "192.168.1.20"})
        self.assertEqual(response.status_code, 200)

    def test_no_default_admin_token(self):
        self.assertIsNone(self.app_module.ADMIN_TOKEN)
        response = self.client.post(
            "/api/admin/login",
            json={"token": "books-admin"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.20"},
        )
        self.assertEqual(response.status_code, 401)

    def test_identical_upload_is_refused(self):
        import io

        def upload(name):
            return self.client.post(
                "/api/upload",
                data={"files": (io.BytesIO(b"the same book text"), name)},
                content_type="multipart/form-data",
            )

        self.assertEqual(upload("First Copy.txt").status_code, 200)
        second = upload("Second Copy.txt")
        self.assertEqual(second.status_code, 400)
        self.assertIn("Already in your library", second.get_json()["error"])
        self.assertEqual(sorted(p.name for p in Path("books").glob("*.txt")), ["First Copy.txt"])

    def test_old_verify_page_is_gone(self):
        self.assertEqual(self.client.get("/verify").status_code, 404)

    def test_library_book_can_be_edited_by_hand(self):
        conn = sqlite3.connect("database.db")
        cur = conn.cursor()
        cur.execute("INSERT INTO books (title, author, open_library_id, verified) VALUES ('Montaine', 'Unknown', 'm1', 0)")
        book_id = cur.lastrowid
        cur.execute("INSERT INTO books (title, author, open_library_id, verified) VALUES ('Not Mine', 'X', 'n1', 1)")
        other_id = cur.lastrowid
        Path("books").mkdir(exist_ok=True)
        Path("books/montaigne.txt").write_text("essays", encoding="utf-8")
        cur.execute("INSERT INTO processed_files (filename, file_hash, book_id) VALUES ('montaigne.txt', 'h', ?)", (book_id,))
        conn.commit()

        with mock.patch("process_books.refresh_owned_record", return_value=True) as refile:
            ok = self.client.patch(f"/api/library/books/{book_id}",
                                   json={"title": "Montaigne", "author": "Stefan Zweig"})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.get_json()["refiled"])
        refile.assert_called_once()
        self.assertEqual(
            cur.execute("SELECT title, author, user_edited FROM books WHERE id = ?", (book_id,)).fetchone(),
            ("Montaigne", "Stefan Zweig", 1),
        )
        self.assertEqual(self.client.patch(f"/api/library/books/{other_id}", json={"title": "X"}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/library/books/{book_id}", json={"title": " "}).status_code, 400)
        conn.close()

    def test_review_flow_confirm_reject_restore(self):
        conn = sqlite3.connect("database.db")
        cur = conn.cursor()
        cur.execute("INSERT INTO books (title, author, open_library_id, verified) VALUES ('Src', 'A', 's1', 1)")
        source_id = cur.lastrowid
        cur.execute("INSERT INTO books (title, author, open_library_id, verified) VALUES ('Tgt', 'B', 't1', 1)")
        target_id = cur.lastrowid
        cur.execute(
            "INSERT INTO book_references (source_book_id, referenced_book_id, context, raw_title) VALUES (?, ?, 'q', 'Tgt')",
            (source_id, target_id),
        )
        conn.commit()

        def act(action):
            return self.client.post(
                "/api/review/link",
                json={"source_id": source_id, "target_id": target_id, "action": action,
                      "raw_title": "Tgt", "context": "q"},
            )

        queue = self.client.get("/api/review/queue").get_json()
        self.assertEqual([l["title"] for l in queue["links"]], ["Tgt"])

        self.assertEqual(act("reject").status_code, 200)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM book_references").fetchone()[0], 0)
        self.assertEqual(
            sorted(r[0] for r in cur.execute("SELECT raw_key FROM reference_overrides")),
            sorted([f"#{target_id}", "tgt"]),
        )

        self.assertEqual(act("restore").status_code, 200)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM book_references").fetchone()[0], 1)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM reference_overrides").fetchone()[0], 0)

        self.assertEqual(act("confirm").status_code, 200)
        self.assertEqual(cur.execute("SELECT confirmed FROM book_references").fetchone()[0], 1)
        self.assertEqual(self.client.get("/api/review/queue").get_json()["links"], [])
        conn.close()


if __name__ == "__main__":
    unittest.main()
