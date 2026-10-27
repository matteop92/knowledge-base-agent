import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from app.agent.catalog import vocabulary
from app.agent.properties import to_source
from app.agent.router import SPEC, SPEC_VERSION, build_questions, interpret, route_question, shortlist
from app.models.decisions import Choice

CHUNK = {"chunk_id": "c1", "source_spans": []}
TODAY = date(2026, 10, 27)


def source(path, **metadata):
    when = datetime(2026, 10, 26, tzinfo=timezone.utc)
    return to_source("doc-" + path, "run", "a" * 64, "root", path, metadata, 2, CHUNK, added_at=when, updated_at=when)


SOURCES = [
    source("Knowledge/Notes/new.md", title="Newest Note", authors=["Ada Stone"], tags=["ai"]),
    source("Knowledge/Articles/plans.md", title="Plans that never ship", authors=["Tim Ferriss"], date="2025-06-18"),
    source("Knowledge/Book Highlights/anything.md", title="Anything You Want", authors=["Derek Sivers"]),
    source("Knowledge/Transcripts/talk.md", title="A Talk", guests=["Niels Hoven"], authors=["Tim Ferriss"],
           generated={"source_type": "transcript"}),
]
VOCAB = vocabulary(SOURCES)


def route(name, probability=0.95):
    others = {"plain": 0.0, "narrowed": 0.0, "catalog": 0.0}
    return Choice(name, {**others, name: probability})


def answers(route_name="plain", probability=0.95, **extra):
    base = {"route": route(route_name, probability), "by_type": 0.05, "by_date": 0.05, "by_folder": 0.05,
            "cat_by_type": 0.05, "cat_by_date": 0.05, "cat_by_folder": 0.05,
            "by_person": 0.05, "by_tag": 0.05, "by_title": 0.05, "single": 0.05, "by_latest": 0.05,
            "op": Choice("list", {"list": 0.9, "count": 0.1}), "sort": Choice("none", {"none": 1.0}),
            "type_value": Choice("none", {"none": 1.0}), "folder_value": Choice("none", {"none": 1.0}),
            "person_value": Choice("none", {"none": 1.0}), "person_role": Choice("any", {"any": 1.0}),
            "tag_value": Choice("none", {"none": 1.0}), "title_value": Choice("none", {"none": 1.0})}
    return {**base, **extra}


def choice(name):
    return Choice(name, {name: 1.0})


def decide(question, **kwargs):
    return interpret(answers(**kwargs), question, VOCAB, TODAY)


class RouterTests(unittest.TestCase):
    def test_latest_note_uploaded_is_a_catalog_plan_sorted_by_added_date(self):
        decision = decide("What's the latest note I've uploaded", route_name="catalog", cat_by_type=0.9,
                          type_value=choice("note"), sort=choice("added_newest"), single=0.9)
        self.assertEqual(decision.route, "catalog")
        self.assertEqual((decision.plan.types, decision.plan.sort, decision.plan.limit),
                         (["note"], "added_newest", 1))

    def test_a_count_by_author_is_a_count_plan(self):
        decision = decide("how many sources with author = Tim Ferriss", route_name="catalog", op=choice("count"),
                          by_person=0.9, person_value=choice("Tim Ferriss"), person_role=choice("author"))
        self.assertEqual((decision.plan.op, decision.plan.person, decision.plan.role),
                         ("count", "Tim Ferriss", "author"))

    def test_plain_stays_plain_and_carries_no_plan(self):
        decision = decide("What would Tim Ferriss recommend", by_person=0.9, person_value=choice("Tim Ferriss"))
        self.assertEqual(decision.route, "plain")
        self.assertIsNone(decision.plan)
        self.assertIsNone(decision.narrowing)

    def test_routes_below_their_threshold_are_plain(self):
        self.assertEqual(decide("q", route_name="narrowed", probability=0.55, by_type=0.9,
                                type_value=choice("highlights")).route, "plain")
        self.assertEqual(decide("q", route_name="catalog", probability=0.55).route, "plain")
        self.assertEqual(decide("q", route_name="narrowed", probability=0.65, by_type=0.9,
                                type_value=choice("highlights")).route, "narrowed")

    def test_narrowed_by_type_folder_and_period(self):
        decision = decide("In my book highlights, what does anyone say about decisions?", route_name="narrowed",
                          by_type=0.9, type_value=choice("highlights"))
        self.assertEqual(decision.narrowing.types, ["highlights"])
        decision = decide("From my Notes folder, what did I think about X in October?", route_name="narrowed",
                          by_folder=0.8, folder_value=choice("Knowledge/Notes"), by_date=0.9)
        self.assertEqual(decision.narrowing.folders, ["Knowledge/Notes"])
        self.assertEqual(decision.narrowing.period.start, "2026-10-01")

    def test_summarise_my_latest_note_narrows_to_the_newest_one_unless_a_number_is_given(self):
        decision = decide("Summarise my latest note", route_name="narrowed", by_type=0.9, type_value=choice("note"),
                          by_latest=0.9)
        self.assertEqual((decision.route, decision.narrowing.latest, decision.narrowing.types), ("narrowed", 1, ["note"]))
        decision = decide("Summarise my three latest articles", route_name="narrowed", by_type=0.9,
                          type_value=choice("article"), by_latest=0.9)
        self.assertEqual(decision.narrowing.latest, 3)

    def test_narrowed_with_nothing_to_narrow_by_is_plain(self):
        self.assertEqual(decide("q", route_name="narrowed", by_type=0.2).route, "plain")
        self.assertEqual(decide("q", route_name="narrowed", by_type=0.9).route, "plain")  # type_value is none

    def test_a_date_yes_without_a_parsable_period_does_not_narrow(self):
        self.assertEqual(decide("articles I saved", route_name="narrowed", by_date=0.9).route, "plain")
        decision = decide("articles I saved", route_name="narrowed", by_date=0.9, by_type=0.9,
                          type_value=choice("article"))
        self.assertIsNone(decision.narrowing.period)

    def test_person_and_tag_are_ignored_when_narrowed(self):
        decision = decide("q", route_name="narrowed", by_person=0.9, person_value=choice("Tim Ferriss"), by_tag=0.9,
                          tag_value=choice("ai"), by_type=0.9, type_value=choice("transcript"))
        self.assertEqual(decision.narrowing.record(), {"types": ["transcript"]})

    def test_which_note_did_i_add_last_is_catalog_but_a_topic_latest_is_plain(self):
        decision = decide("Which note did I add last?", route_name="catalog", sort=choice("added_newest"),
                          single=0.9, cat_by_type=0.9, type_value=choice("note"))
        self.assertEqual((decision.route, decision.plan.limit), ("catalog", 1))
        self.assertEqual(decide("my latest thinking on AI agents", by_latest=0.2).route, "plain")
        self.assertEqual(decide("q", route_name="narrowed", by_latest=0.2).route, "plain")

    def test_a_plan_that_fails_validation_is_plain(self):
        decision = decide("q", route_name="catalog", by_tag=0.9, tag_value=choice("not-a-tag"))
        self.assertEqual(decision.route, "plain")

    def test_a_catalog_question_filters_by_kind_folder_and_period_without_saying_only(self):
        decision = decide("How many articles from 2025 are in my Notes folder?", route_name="catalog", op=choice("count"),
                          cat_by_type=0.8, type_value=choice("article"), cat_by_folder=0.7,
                          folder_value=choice("Knowledge/Notes"), cat_by_date=0.9)
        self.assertEqual((decision.plan.types, decision.plan.folders, decision.plan.period.start),
                         (["article"], ["Knowledge/Notes"], "2025-01-01"))
        # The narrowing questions do not feed a catalog plan.
        decision = decide("How many articles are there?", route_name="catalog", op=choice("count"), by_type=0.9,
                          type_value=choice("article"))
        self.assertEqual(decision.plan.types, [])

    def test_limits_are_read_by_code(self):
        decision = decide("show the three most recent notes", route_name="catalog", sort=choice("added_newest"))
        self.assertEqual(decision.plan.limit, 3)

    def test_the_record_names_the_spec_and_what_was_chosen(self):
        decision = decide("q", route_name="catalog", op=choice("count"))
        record = decision.record(["doc-a"])
        self.assertEqual((record["route"], record["spec"], record["matched"]), ("catalog", SPEC_VERSION, ["doc-a"]))
        self.assertEqual(len(record["spec_sha256"]), 64)
        self.assertEqual(record["plan"]["op"], "count")

    def test_questions_list_plain_first_and_cap_options(self):
        questions = build_questions(VOCAB, "q")
        self.assertEqual(list(questions["route"]["criteria"]), ["plain", "narrowed", "catalog"])
        shuffled = build_questions(VOCAB, "q", ("catalog", "narrowed", "plain"))
        self.assertEqual(list(shuffled["route"]["criteria"])[0], "catalog")
        many = {**VOCAB, "titles": [f"Title {index}" for index in range(600)]}
        self.assertLessEqual(len(build_questions(many, "q")["title_value"]["criteria"]), 255)
        self.assertEqual(len(shortlist(many["titles"], "Title 599 please")), 254)
        self.assertIn("Title 599", shortlist(many["titles"], "Title 599 please"))
        self.assertEqual(SPEC["thresholds"]["narrowed"], 0.6)

    def test_scope_lines_say_what_was_searched(self):
        from app.agent.periods import Period
        from app.agent.router import Narrowing

        september = Period("2026-09-01", "2026-09-30", "date", "from September 2026")
        self.assertEqual(Narrowing(types=["note"], period=september).scope_line(12, False),
                         "Searched 12 notes from September 2026.")
        self.assertEqual(Narrowing(types=["highlights"], folders=["Knowledge/Book Highlights"]).scope_line(3, False),
                         "Searched 3 book highlights in Knowledge/Book Highlights.")
        self.assertEqual(Narrowing(latest=1, types=["note"]).scope_line(1, False),
                         "Searched your newest note by date added.")
        self.assertIn("first bulk load", Narrowing(latest=1).scope_line(1, True))
        self.assertNotIn("first bulk load", Narrowing(types=["note"], period=september).scope_line(2, True))
        self.assertEqual(Narrowing(types=["article"], period=september).widened_line(),
                         "No articles from September 2026 matched; searched everything instead.")

    def test_a_vault_with_no_tags_leaves_the_tag_question_out(self):
        questions = build_questions({**VOCAB, "tags": []}, "q")
        self.assertNotIn("tag_value", questions)
        self.assertIn("by_tag", questions)

    def test_failures_are_plain(self):
        with patch("app.agent.router.decide", return_value=None):
            self.assertEqual(route_question("q", SOURCES, client=object()).route, "plain")
        with patch("app.agent.router.decide", side_effect=ValueError("no route")):
            decision = route_question("q", SOURCES, client=object())
        self.assertEqual((decision.route, decision.called), ("plain", False))

    def test_a_successful_call_is_recorded_as_called(self):
        with patch("app.agent.router.decide", return_value=answers("catalog", op=choice("count"))):
            decision = route_question("how many?", SOURCES, client=object(), today=TODAY)
        self.assertEqual((decision.route, decision.called, decision.plan.op), ("catalog", True, "count"))


if __name__ == "__main__":
    unittest.main()
