import unittest
from datetime import datetime, timezone

from app.agent.catalog import (CatalogPlan, PlanError, apply_plan, build_plan, filter_sources, resolve_scope,
                               sort_sources, vocabulary)
from app.agent.periods import Period
from app.agent.properties import BULK_LOAD_CUTOFF, added_date, to_source

CHUNK = {"chunk_id": "c1", "source_spans": []}


def at(day, hour=12):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def source(path, added=None, updated=None, **metadata):
    return to_source("doc-" + path, "run", "a" * 64, "root", path, metadata, 2, CHUNK,
                     added_at=added or at(26), updated_at=updated or added or at(26))


SOURCES = [
    source("Knowledge/Notes/old.md", at(26, 9), title="Older Note", authors=["Ada Stone"]),
    source("Knowledge/Notes/new.md", at(26, 17), title="Newest Note", authors=["Ada Stone"], tags=["ai"]),
    source("Knowledge/Notes/same-a.md", at(26, 17), title="Same Time A"),
    source("Knowledge/Articles/plans.md", at(26, 10), title="Plans that never ship", authors=["Dee Marsh"],
           date="2025-06-18", tags=["marketing"]),
    source("Knowledge/Articles/rest.md", at(26, 11), title="Rest Days", authors=["Eli Park"], date="2024-04-07",
           frontmatter={"guest": "Fay Lund"}),
    source("Knowledge/Transcripts/talk.md", at(26, 8), title="A Talk", guests=["Ben Ford"], authors=["Tim Ferriss"],
           generated={"source_type": "transcript"}),
]
VOCAB = vocabulary(SOURCES)


def titles(found):
    return [item.title for item in found]


class CatalogTests(unittest.TestCase):
    def test_newest_added_with_limit_one_is_the_last_added(self):
        plan = build_plan({"types": ["note"], "sort": "added_newest", "limit": 1}, VOCAB)
        self.assertEqual(titles(apply_plan(SOURCES, plan)), ["Newest Note"])

    def test_same_time_sources_sort_the_same_every_time(self):
        first = titles(sort_sources(SOURCES, "added_newest"))
        self.assertEqual(first, titles(sort_sources(list(reversed(SOURCES)), "added_newest")))
        self.assertEqual(first[:2], ["Newest Note", "Same Time A"])

    def test_edit_moves_updated_at_not_added_at(self):
        edited = source("Knowledge/Notes/old.md", at(26, 9), at(27), title="Older Note")
        self.assertEqual((edited.added_at, edited.updated_at), (at(26, 9), at(27)))

    def test_undated_sorts_last_and_is_still_listed(self):
        found = sort_sources(SOURCES, "dated_newest")
        self.assertEqual(titles(found)[:2], ["Plans that never ship", "Rest Days"])
        self.assertEqual(len(found), len(SOURCES))
        self.assertEqual(titles(sort_sources(SOURCES, "dated_oldest"))[:2], ["Rest Days", "Plans that never ship"])

    def test_count_list_tag_person_roles_and_title(self):
        self.assertEqual(len(apply_plan(SOURCES, build_plan({"op": "count", "types": ["article"]}, VOCAB))), 2)
        self.assertEqual(titles(apply_plan(SOURCES, build_plan({"tag": "AI"}, VOCAB))), ["Newest Note"])
        self.assertEqual(len(apply_plan(SOURCES, build_plan({"person": "ada stone", "role": "author"}, VOCAB))), 2)
        self.assertEqual(titles(apply_plan(SOURCES, build_plan({"person": "Ben Ford", "role": "guest"}, VOCAB))),
                         ["A Talk"])
        self.assertEqual(titles(apply_plan(SOURCES, build_plan({"person": "Ben Ford", "role": "author"}, VOCAB))), [])
        self.assertEqual(titles(apply_plan(SOURCES, build_plan({"person": "Fay Lund"}, VOCAB))), ["Rest Days"])
        self.assertEqual(titles(apply_plan(SOURCES, build_plan({"title": "Plans that never ship"}, VOCAB))),
                         ["Plans that never ship"])

    def test_count_ignores_the_limit(self):
        plan = build_plan({"op": "count", "types": ["note"], "limit": 1}, VOCAB)
        self.assertEqual(len(apply_plan(SOURCES, plan)), 3)

    def test_invalid_plans_are_rejected_before_they_run(self):
        for bad in ({"colour": "red"}, {"tag": "not-a-tag"}, {"types": ["poem"]}, {"limit": 0}, {"limit": 101},
                    {"limit": "3"}, {"sort": "random"}, {"op": "delete"}, {"person": "Nobody"}, {"folders": "x"},
                    {"period": "2025"}):
            with self.subTest(bad), self.assertRaises(PlanError):
                build_plan(bad, VOCAB)

    def test_a_title_with_quotes_and_sql_matches_literally(self):
        plan = CatalogPlan(title="x'; drop table knowledge.runs; --")
        self.assertEqual(filter_sources(SOURCES, plan), [])

    def test_resolve_scope_by_folder_type_period_and_both(self):
        self.assertEqual(len(resolve_scope(SOURCES, folders=["Knowledge/Notes"]).document_ids), 3)
        self.assertEqual(len(resolve_scope(SOURCES, types=["article"]).document_ids), 2)
        in_2025 = Period("2025-01-01", "2025-12-31", "date", "from 2025")
        self.assertEqual(resolve_scope(SOURCES, period=in_2025).document_ids, ["doc-Knowledge/Articles/plans.md"])
        self.assertEqual(resolve_scope(SOURCES, types=["note"], period=in_2025).document_ids, [])

    def test_resolve_scope_latest_keeps_the_newest_by_added_date(self):
        self.assertEqual(resolve_scope(SOURCES, types=["note"], latest=1).document_ids, ["doc-Knowledge/Notes/new.md"])
        self.assertEqual(len(resolve_scope(SOURCES, latest=3).document_ids), 3)

    def test_added_period_filters_on_added_at(self):
        period = Period("2026-10-26", "2026-10-26", "added", "from last week")
        self.assertEqual(len(filter_sources(SOURCES, CatalogPlan(period=period))), len(SOURCES))

    def test_bulk_load_notes_take_their_own_date(self):
        early = datetime(2026, 10, 18, 10, 16, tzinfo=timezone.utc)
        late = datetime(2026, 10, 19, 9, 10, tzinfo=timezone.utc)
        self.assertEqual(added_date(early, {"created": "2025-03-04"}, "Knowledge/Notes/a.md"),
                         (datetime(2025, 3, 4, tzinfo=timezone.utc), True))
        self.assertEqual(added_date(early, {}, "Knowledge/Notes/2024-11-02 idea.md"),
                         (datetime(2024, 11, 2, tzinfo=timezone.utc), True))
        self.assertEqual(added_date(early, {}, "Knowledge/Notes/idea.md"), (early, False))
        self.assertEqual(added_date(late, {"created": "2025-03-04"}, "Knowledge/Notes/a.md"), (late, False))
        self.assertLessEqual(early, BULK_LOAD_CUTOFF)

    def test_scope_reports_when_a_date_stood_in_for_the_added_date(self):
        early = datetime(2026, 10, 18, 10, 16, tzinfo=timezone.utc)
        old = to_source("d", "r", "a" * 64, "root", "Knowledge/Notes/n.md", {"created": "2025-01-01"}, 1, CHUNK,
                        added_at=early, updated_at=early)
        self.assertTrue(resolve_scope([old], types=["note"]).added_from_date)
        self.assertFalse(resolve_scope(SOURCES, types=["note"]).added_from_date)


if __name__ == "__main__":
    unittest.main()
