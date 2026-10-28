import unittest
from unittest.mock import MagicMock

from datetime import datetime, timezone

from app.agent.catalog import build_plan, vocabulary
from app.agent.properties import answer_catalog, source_type, to_source
from app.storage.records import SourcePassage

CHUNK = {"chunk_id": "c1", "source_spans": [{"start_char": 0, "end_char": 40}]}


def at(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def source(path, added_at=None, **metadata):
    return to_source("doc-" + path, "run", "a" * 64, "root", path, metadata, 2, CHUNK, added_at=added_at,
                     updated_at=added_at)


SOURCES = [
    source("Knowledge/Transcripts/pricing.md", title="Notes on Pricing", authors=["[[Ada Stone]]"],
           tags=["marketing"]),
    source("Knowledge/Notes/idea.md", title="A Plain Idea", authors=["Ada Stone"],
           guests=["Ben Ford"], generated={"source_type": "transcript", "tags": ["positioning"]}),
    source("Knowledge/Notes/garden.md", title="garden",
           generated={"people": ["Ada Stone", "Cal Reyes"], "source_type": "transcript"}),
    source("Knowledge/Articles/plans.md", title="Plans that never ship", authors=["Dee Marsh"],
           date="2025-06-18", tags=["article"]),
    source("Knowledge/Notes/rest.md", title="Rest Days", authors=["Eli Park"],
           frontmatter={"guest": "[[Fay Lund]]"}, tags=["productivity"], date="2025-04-07"),
]


class PropertyTests(unittest.TestCase):
    def test_frontmatter_wins_and_generated_properties_fill_gaps(self):
        premise, garden = SOURCES[1], SOURCES[2]
        self.assertEqual(premise.people, ["Ada Stone", "Ben Ford"])
        self.assertEqual(garden.authors, [])
        self.assertEqual(garden.people, ["Ada Stone", "Cal Reyes"])
        self.assertEqual(SOURCES[4].guests, ["Fay Lund"])
        self.assertEqual(source_type({}, "Knowledge/Articles/a.md"), "article")
        self.assertEqual(source_type({"source_type": "Book"}, "Knowledge/Notes/a.md"), "note")
        self.assertEqual(source_type({"source_type": "Book"}, "Inbox/a.md"), "book")
        self.assertIsNone(source_type({}, "Inbox/a.md"))

    def test_type_comes_from_the_note_then_its_folder_then_the_classifier(self):
        own = {"source_type": "social_post"}
        self.assertEqual(source_type({"source_type": "transcript"}, "Knowledge/Notes/a.md", own), "social_post")
        self.assertEqual(source_type({"source_type": "transcript"}, "Knowledge/Notes/a.md", {"type": "Book"}), "book")
        self.assertEqual(source_type({"source_type": "transcript"}, "Knowledge/Notes/a.md", {}), "note")

    def test_frontmatter_type_wins_and_a_generated_date_fills_a_missing_one(self):
        typed = source("Knowledge/Notes/typed.md", title="Typed", frontmatter={"type": "Article"},
                       generated={"source_type": "note", "date": "2024-03-01"})
        self.assertEqual((typed.type, typed.date), ("article", "2024-03-01"))
        dated = source("Knowledge/Notes/dated.md", title="Dated", published="2023-01-02",
                       generated={"date": "2024-03-01"})
        self.assertEqual(dated.date, "2023-01-02")

    def test_answer_counts_matches_leads_with_the_reading_and_cites_each_first_passage(self):
        retriever = MagicMock()
        retriever.read.side_effect = lambda hit, span: SourcePassage(
            document_id=hit.hit.document_id, run_id="run", source_sha256="a" * 64, root_alias="root",
            relative_path=hit.hit.relative_path, chunk_id="c1", start_char=0, end_char=5, text="intro")
        vocab = vocabulary(SOURCES)
        plan = build_plan({"op": "count", "person": "Ada Stone", "role": "author"}, vocab)
        result = answer_catalog(plan, SOURCES, retriever=retriever, started=0.0, model_calls=1, route={"spec": "s"})
        self.assertTrue(result.answer.startswith("Counted sources with author Ada Stone."))
        self.assertIn("You have 2 sources with author Ada Stone", result.answer)
        self.assertEqual(set(result.citations), {"E1.0", "E2.0"})
        self.assertIn("[E1.0]", result.answer)
        # Each source is a bold title with its details on an indented line beneath.
        self.assertRegex(result.answer, r"- \*\*[^*\n]+\*\*\n  .+ \[E1\.0\]")
        self.assertEqual(result.actions[0]["matched"], ["doc-Knowledge/Notes/idea.md",
                                                        "doc-Knowledge/Transcripts/pricing.md"])
        empty = answer_catalog(build_plan({"tag": "marketing", "types": ["note"]}, vocab), SOURCES,
                               retriever=retriever, started=0.0, model_calls=0, route={})
        self.assertIn("None of your 5 sources is of type note and tagged marketing.", empty.answer)

    def test_the_reading_sentence_says_how_a_latest_question_was_read(self):
        retriever = MagicMock()
        retriever.read.side_effect = lambda hit, span: SourcePassage(
            document_id=hit.hit.document_id, run_id="run", source_sha256="a" * 64, root_alias="root",
            relative_path=hit.hit.relative_path, chunk_id="c1", start_char=0, end_char=5, text="intro")
        newest = source("Knowledge/Notes/new.md", title="Newest", added_at=at(26, 17))
        older = source("Knowledge/Notes/old.md", title="Older", added_at=at(26, 9))
        plan = build_plan({"types": ["note"], "sort": "added_newest", "limit": 1}, vocabulary([newest, older]))
        result = answer_catalog(plan, [older, newest], retriever=retriever, started=0.0, model_calls=1, route={})
        self.assertIn("Sorted by when it was added, newest first; showing 1.", result.answer)
        self.assertIn("**Newest**", result.answer)
        self.assertNotIn("**Older**", result.answer)


if __name__ == "__main__":
    unittest.main()
