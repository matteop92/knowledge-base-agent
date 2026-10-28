"""The Sources routes: card rows, reading payload and the scoped-ask checks, against a fake connection."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest import mock

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

from app.api.conversations import ConversationError
from app.api.sources import SourceStore, reading_blocks
from tests.api.test_api import EMAIL, FakeAuth, FakeEngine, settings

ADDED = datetime(2026, 10, 20, 9, tzinfo=timezone.utc)
NAMES = ("book", "highlights", "post", "note", "transcript", "article", "quote")
IDS = {name: f"00000000-0000-0000-0000-00000000000{index}" for index, name in enumerate(NAMES, 1)}


def row(name, path, metadata, added=ADDED):
    return {"document_id": IDS[name], "run_id": f"run-{name}", "source_sha256": "a" * 64, "root_alias": "primary",
            "relative_path": path, "metadata": {"title": name.title() + " title", **metadata}, "chunks": 1,
            "first_chunk": None, "added_at": added, "updated_at": added}


ROWS = [
    row("book", "Knowledge/Books/B.md", {"authors": ["Ada Stone"], "published": "2015-01-01"}),
    row("highlights", "Knowledge/Book Highlights/H.md", {"authors": ["Ada Stone"], "tags": ["craft"]}),
    row("post", "Knowledge/Notes/P.md", {"frontmatter": {"type": "social_post", "platform": "LinkedIn"}}),
    row("note", "Knowledge/Notes/N.md", {"description": "A short note."}),
    row("transcript", "Knowledge/Transcripts/T.md", {"authors": ["Ben Hart"], "guests": ["Ada Stone"],
                                                      "frontmatter": {"show": "Show Alpha"}}),
    row("article", "Knowledge/Articles/A.md", {"source_url": "https://example.org/a"},
        added=datetime(2026, 10, 1, tzinfo=timezone.utc)),
    row("quote", "Knowledge/Notes/Q.md", {"frontmatter": {"type": "quote"}}),
]


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Conn:
    def __init__(self, detail=None, active=True):
        self.detail, self.active = detail, active
        self.counted: list = []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if "jsonb_array_elements" in sql:
            return Result([(IDS["quote"], "Be the change.")])
        if sql.startswith("select d.relative_path"):
            return Result([self.detail] if self.detail else [])
        if sql.startswith("select 1 from knowledge.active_runs"):
            return Result([(1,)] if self.active else [])
        raise AssertionError(sql)


def store(conn):
    @contextmanager
    def connect():
        yield conn
    return SourceStore(connect, "New Vault")


class ListTests(unittest.TestCase):
    def listed(self, conn=None):
        with mock.patch("app.api.sources.load_sources", return_value=ROWS):
            return {item["id"]: item for item in store(conn or Conn()).list()["sources"]}

    def test_one_row_per_active_source_with_the_card_facts(self) -> None:
        rows = self.listed()
        self.assertEqual({item["type"] for item in rows.values()}, set(NAMES))
        transcript = rows[IDS["transcript"]]
        self.assertEqual((transcript["authors"], transcript["guests"], transcript["platform"],
                          transcript["source_name"]), (["Ben Hart"], ["Ada Stone"], "podcast", "Show Alpha"))
        self.assertEqual(rows[IDS["post"]]["platform"], "linkedin")
        self.assertEqual(rows[IDS["note"]]["description"], "A short note.")
        self.assertEqual(rows[IDS["highlights"]]["tags"], ["craft"])
        self.assertEqual(rows[IDS["article"]]["source_name"], "example.org")

    def test_only_quotes_carry_a_quote_text(self) -> None:
        rows = self.listed()
        self.assertNotIn("highlight_count", rows[IDS["highlights"]])
        self.assertEqual(rows[IDS["quote"]]["quote_text"], "Be the change.")
        self.assertEqual(sum("quote_text" in item for item in rows.values()), 1)

    def test_a_book_opens_in_obsidian(self) -> None:
        book = self.listed()[IDS["book"]]
        self.assertTrue(book["opens_in_obsidian"])
        self.assertIn("vault=New%20Vault", book["obsidian_url"])
        self.assertIn("Knowledge%2FBooks%2FB.md", book["obsidian_url"])
        self.assertEqual(book["book_year"], "2015")
        self.assertFalse(self.listed()[IDS["note"]]["opens_in_obsidian"])

    def test_newest_added_comes_first(self) -> None:
        with mock.patch("app.api.sources.load_sources", return_value=ROWS):
            ids = [item["id"] for item in store(Conn()).list()["sources"]]
        self.assertEqual(ids[-1], IDS["article"])


def block(index, kind, text, **fields):
    return {"index": index, "kind": kind, "raw_text": text, "span": {"start_char": 0, "end_char": len(text)}, **fields}


class DetailTests(unittest.TestCase):
    def detail(self, name, metadata, blocks):
        found = ("Knowledge/Transcripts/T.md", {"title": "T title", **metadata}, blocks, "run-1", "a" * 64,
                 "primary", ADDED)
        return store(Conn(detail=found)).detail(IDS[name])

    def test_transcript_turns_keep_order_and_speakers(self) -> None:
        body = self.detail("transcript", {"guests": ["Ada Stone"]}, [
            block(0, "speaker_turn", "Ben: Hello.", speaker="Ben Hart",
                  content_span={"start_char": 5, "end_char": 11}),
            block(1, "speaker_turn", "Ada: Hi there.", speaker="Ada Stone")])["body"]
        self.assertEqual([(item["speaker"], item["text"]) for item in body],
                         [("Ben Hart", "Hello."), ("Ada Stone", "Ada: Hi there.")])

    def test_highlights_carry_locations_and_the_note_rides_on_its_highlight(self) -> None:
        body = self.detail("highlights", {}, [
            block(0, "highlight", "Keep it small.", page_reference="Page 12"),
            block(1, "paragraph", "Try this.", content_role="personal_note")])["body"]
        self.assertEqual(len(body), 1)
        self.assertEqual((body[0]["page_reference"], body[0]["personal_note"]), ("Page 12", "Try this."))

    def test_the_description_appears_once_and_frontmatter_never(self) -> None:
        payload = self.detail("note", {"description": "In short.", "frontmatter_raw": "---\nsecret: 1\n---",
                                       "frontmatter": {"secret": 1}}, [block(0, "paragraph", "Body.")])
        self.assertEqual(payload["description"], "In short.")
        self.assertNotIn("secret", str(payload))
        self.assertEqual([item["text"] for item in payload["body"]], ["Body."])

    def test_unknown_or_malformed_ids_are_not_found(self) -> None:
        for value in (IDS["note"], "not-an-id"):
            with self.assertRaises(ConversationError) as caught:
                store(Conn()).detail(value)
            self.assertEqual(caught.exception.status, 404)

    def test_reading_blocks_skip_separators_and_empty_text(self) -> None:
        self.assertEqual(reading_blocks([block(0, "separator", "---"), block(1, "paragraph", "  ")]), [])

    def test_reading_blocks_drop_markdown_markers_and_images_but_keep_links(self) -> None:
        blocks = [block(0, "heading", "## How it worked ##\n"), block(1, "list_item", "- [ ] First *step*"),
                  block(2, "block_quote", "> One\n> two"), block(3, "paragraph", "[![Ad](https://x.org/a.png)](https://x.org/)"),
                  block(4, "paragraph", "See ![logo](a.png) [Opus](https://e.org/o?(a)) now."),
                  block(5, "list_item", "1. Numbered stays")]
        self.assertEqual([item["text"] for item in reading_blocks(blocks)],
                         ["How it worked", "First *step*", "One\ntwo", "See  [Opus](https://e.org/o?(a)) now.",
                          "1. Numbered stays"])

    def test_obsidian_image_embeds_and_image_links_with_brackets_go_but_embedded_notes_stay(self) -> None:
        blocks = [block(0, "paragraph", "![[Shot 1.PNG|300]] caption"), block(1, "paragraph", "![[Other note]] here"),
                  block(2, "paragraph", '![a](https://w.org/F_(x).jpg "t") after'), block(3, "paragraph", "![[only.png]]")]
        self.assertEqual([item["text"] for item in reading_blocks(blocks)], ["caption", "[[Other note]] here", "after"])


@unittest.skipUnless(TestClient, "needs fastapi")
class RouteTests(unittest.TestCase):
    def client(self, conn):
        from app.api.main import create_app

        @contextmanager
        def connect():
            yield conn
        return TestClient(create_app(settings(), connect=connect, auth=FakeAuth(), engine=FakeEngine()))

    def test_the_routes_need_a_session(self) -> None:
        client = self.client(Conn())
        self.assertEqual(client.get("/api/sources").status_code, 401)
        self.assertEqual(client.get(f"/api/sources/{IDS['note']}").status_code, 401)

    def test_a_scoped_ask_for_a_source_that_is_not_active_is_refused_before_any_turn_starts(self) -> None:
        for source_id, active in (("not-an-id", True), (IDS["note"], False)):
            with self.subTest(source_id=source_id), \
                    mock.patch("app.api.auth.SessionStore.check", return_value=mock.MagicMock(renewed=False)):
                client = self.client(Conn(active=active))
                response = client.post("/api/conversations", json={"question": "q", "source_id": source_id})
                self.assertEqual(response.status_code, 400, response.text)

    def test_a_scoped_ask_for_an_active_source_reaches_the_jobs(self) -> None:
        with mock.patch("app.api.auth.SessionStore.check", return_value=mock.MagicMock(renewed=False)), \
                mock.patch("app.api.answers.AnswerJobs.start", return_value=("c", "t")) as start:
            client = self.client(Conn())
            response = client.post("/api/conversations", json={"question": "q", "source_id": IDS["note"]})
            client.post("/api/conversations", json={"question": "q"})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual([call.args for call in start.call_args_list],
                         [(None, "q", IDS["note"]), (None, "q", None)])


if __name__ == "__main__":
    unittest.main()
