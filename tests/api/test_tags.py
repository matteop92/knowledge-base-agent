"""Suggested tags: grouping, note cards and decisions, against a fake connection (no database needed)."""

from __future__ import annotations

import unittest
from contextlib import contextmanager

from app.api.conversations import ConversationError
from app.api.tags import TagStore


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(self, suggestions=(), tag_rows=(), known=True, seed_empty=False):
        self.suggestions, self.tag_rows, self.known = list(suggestions), list(tag_rows), known
        self.seed_empty, self.seeded = seed_empty, False
        self.approved, self.recent = [], []
        self.writes: list[tuple] = []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if sql.startswith("select p.proposed_tag"):
            return FakeResult(self.suggestions)
        if sql.startswith("select name from knowledge.tags where status='approved'"):
            return FakeResult([(name,) for name in self.approved])
        if sql.startswith("select d.relative_path"):
            return FakeResult(self.recent)
        if sql.startswith("select count"):
            return FakeResult([(0 if self.seed_empty and not self.seeded else 1,)])
        if sql.startswith("select name, status, rule"):
            return FakeResult([(name, status, None) for name, status in self.tag_rows])
        if sql.startswith("select pg_advisory"):
            return FakeResult([(None,)])
        if sql.startswith("select 1"):
            return FakeResult([(1,)] if self.known else [])
        if sql.startswith("insert into knowledge.tags") and "'approved', %s)" in sql and self.seed_empty and not self.seeded:
            self.seeded = True
            return FakeResult([])
        self.writes.append((sql, params))
        return FakeResult([])


def store(conn):
    @contextmanager
    def connect():
        yield conn
    return TagStore(connect, "New Vault")


def row(tag, document_id, path, metadata=None, properties=None):
    return (tag, document_id, path, metadata or {}, properties or {})


class TagStoreTests(unittest.TestCase):
    def test_approved_tags_come_back_as_names_in_list_order(self) -> None:
        conn = FakeConnection()
        conn.approved = ["leadership", "ai-pilots"]
        self.assertEqual(store(conn).approved(), ["leadership", "ai-pilots"])

    def test_starters_turn_this_weeks_notes_into_questions(self) -> None:
        conn = FakeConnection()
        conn.recent = [("Knowledge/Notes/Pilot.md", {"title": "Pilot lessons", "frontmatter": {"type": "transcript"}}, {}), ("Knowledge/Notes/Bare.md", {}, None)]
        result = store(conn).starters()
        self.assertEqual(result[0], {"title": "Pilot lessons", "question": "What do my notes say about Pilot lessons?",
                                     "source_type": "transcript"})
        self.assertEqual(result[1]["title"], "Bare")
        self.assertIsNone(result[1]["source_type"])
        self.assertEqual(store(FakeConnection()).starters(), [])

    def test_suggestions_group_by_tag_biggest_first_with_note_cards(self) -> None:
        conn = FakeConnection(suggestions=[
            row("ai-pilots", "d1", "Knowledge/Notes/Pilot.md", {"title": "Pilot lessons"}, {"description": "What we learned."}),
            row("decision-making", "d2", "Knowledge/Notes/Hiring.md", {"title": "Founder hiring talk", "authors": ["A. Founder"]},
                {"source_type": "transcript"}),
            row("decision-making", "d3", "Knowledge/Notes/Spoons.md", {"title": "Bending Spoons interview"}, {}),
        ])
        result = store(conn).suggestions()
        self.assertEqual([(item["name"], item["count"]) for item in result], [("decision-making", 2), ("ai-pilots", 1)])
        card = result[0]["notes"][0]
        self.assertEqual((card["title"], card["people"], card["source_type"]), ("Founder hiring talk", ["A. Founder"], "transcript"))
        self.assertIn("obsidian://open?vault=New%20Vault&file=", card["obsidian_url"])
        self.assertEqual(result[1]["notes"][0]["description"], "What we learned.")

    def test_a_note_with_no_details_still_gets_a_card(self) -> None:
        card = store(FakeConnection(suggestions=[row("x", "d1", "Knowledge/Notes/Bare.md")])).suggestions()[0]["notes"][0]
        self.assertEqual((card["title"], card["people"], card["source_type"], card["description"]), ("Bare", [], None, None))

    def test_a_note_own_type_and_description_win_over_generated(self) -> None:
        metadata = {"title": "T", "description": "Own.", "frontmatter": {"type": "book"}}
        card = store(FakeConnection(suggestions=[row("x", "d", "a.md", metadata, {"source_type": "note", "description": "Generated."})])
                     ).suggestions()[0]["notes"][0]
        self.assertEqual((card["source_type"], card["description"]), ("book", "Own."))

    def test_approve_and_block_write_one_row_and_repeat_safely(self) -> None:
        conn = FakeConnection()
        tags = store(conn)
        tags.decide("Decision-Making", "approved")
        tags.decide("decision-making", "blocked")
        self.assertEqual([write[1] for write in conn.writes], [("decision-making", "approved"), ("decision-making", "blocked")])

    def test_an_empty_tags_table_is_seeded_before_the_first_suggestions_or_decision(self) -> None:
        conn = FakeConnection(seed_empty=True)
        store(conn).suggestions()
        self.assertTrue(conn.seeded)
        conn = FakeConnection(seed_empty=True)
        store(conn).decide("decision-making", "approved")
        self.assertTrue(conn.seeded)

    def test_a_bad_name_or_unknown_tag_is_refused_and_writes_nothing(self) -> None:
        conn = FakeConnection(known=False)
        for name in ("", "-bad", "x" * 50, "semi;colon"):
            with self.assertRaises(ConversationError) as caught:
                store(conn).decide(name, "approved")
            self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(ConversationError) as caught:
            store(conn).decide("never-proposed", "approved")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(conn.writes, [])


if __name__ == "__main__":
    unittest.main()
