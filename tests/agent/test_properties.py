import unittest
from unittest.mock import MagicMock

from app.agent.properties import PropertyQuery, answer_properties, matches, route_model, source_type, to_source
from app.models.client import GenerationResult, ModelSpec
from app.storage.records import SourcePassage

CHUNK = {"chunk_id": "c1", "source_spans": [{"start_char": 0, "end_char": 40}]}


def source(path, **metadata):
    return to_source("doc-" + path, "run", "a" * 64, "root", path, metadata, 2, CHUNK)


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
        self.assertEqual(source_type({"source_type": "Book"}, "Knowledge/Notes/a.md"), "book")

    def test_author_is_not_any_mention(self):
        self.assertEqual(len(matches(SOURCES, PropertyQuery(authors=["ada stone"]))), 2)
        self.assertEqual(len(matches(SOURCES, PropertyQuery(people=["Ada Stone"]))), 3)

    def test_filters_combine_and_title_picks_the_best_match(self):
        self.assertEqual([s.title for s in matches(SOURCES, PropertyQuery(types=["article"], date="2025"))],
                         ["Plans that never ship"])
        self.assertEqual([s.title for s in matches(SOURCES, PropertyQuery(title="never ship"))],
                         ["Plans that never ship"])

    def test_model_route_keeps_only_known_values(self):
        client = MagicMock()
        client.generate_json.return_value = GenerationResult(
            model=ModelSpec(provider="openrouter", model_id="m", model_version="1"),
            data={"route": "properties", "count": True, "authors": ["ADA STONE", "Nobody"], "guests": [],
                  "people": [], "tags": ["made-up"], "types": [], "date": "October", "title": None})
        query = route_model("how many by ada?", SOURCES, client=client, model=MagicMock())
        self.assertEqual((query.authors, query.tags, query.date), (["Ada Stone"], [], None))
        client.generate_json.return_value = client.generate_json.return_value.model_copy(
            update={"data": {**client.generate_json.return_value.data, "route": "search"}})
        self.assertIsNone(route_model("what does ada say?", SOURCES, client=client, model=MagicMock()))

    def test_answer_counts_matches_and_cites_each_first_passage(self):
        retriever = MagicMock()
        retriever.read.side_effect = lambda hit, span: SourcePassage(
            document_id=hit.hit.document_id, run_id="run", source_sha256="a" * 64, root_alias="root",
            relative_path=hit.hit.relative_path, chunk_id="c1", start_char=0, end_char=5, text="intro")
        result = answer_properties("how many?", PropertyQuery(count=True, authors=["Ada Stone"]), SOURCES,
                                   retriever=retriever, started=0.0, model_calls=0, route={"mode": "rules"})
        self.assertTrue(result.answer.startswith("You have 2 sources with author Ada Stone"))
        self.assertEqual(set(result.citations), {"E1.0", "E2.0"})
        self.assertIn("[E1.0]", result.answer)
        self.assertEqual(result.actions[0]["matched"], ["doc-Knowledge/Transcripts/pricing.md",
                                                        "doc-Knowledge/Notes/idea.md"])
        empty = answer_properties("?", PropertyQuery(tags=["none"]), SOURCES, retriever=retriever, started=0.0,
                                  model_calls=0, route={})
        self.assertTrue(empty.answer.startswith("None of your 5 sources is tagged none."))


if __name__ == "__main__":
    unittest.main()
