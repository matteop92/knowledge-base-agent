"""The Sources routes: card rows, reading payload and the scoped-ask checks, against a fake connection."""

from __future__ import annotations

import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

from app.api.conversations import ConversationError
from app.api.sources import SourceStore, reading_blocks
from app.ingestion.markdown_parser import _is_table_rule, parse_markdown
from app.ingestion.source_reader import capture_source
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
    def __init__(self, detail=None, active=True, image=None):
        self.detail, self.active, self.image = detail, active, image
        self.counted: list = []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if sql.startswith("select r.run_id, r.parsed"):
            return Result([self.image] if self.image else [])
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

    def test_reading_blocks_leave_out_the_front_matter_heading_but_not_other_headings(self) -> None:
        blocks = [block(0, "heading", "## Front Matter"), block(1, "heading", "## Chapter 1"), block(2, "paragraph", "Front Matter")]
        self.assertEqual([item["text"] for item in reading_blocks(blocks)], ["Chapter 1", "Front Matter"])

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

    def test_a_standalone_https_image_becomes_an_image_block_without_its_url(self) -> None:
        blocks = [block(0, "paragraph", "Before."),
                  block(1, "asset_reference", "![A chart](https://x.test/a.png)\n", asset_target="https://x.test/a.png"),
                  block(2, "asset_reference", '![](https://x.test/b.png "Title")', asset_target='https://x.test/b.png "Title"'),
                  block(3, "asset_reference", "![c](<https://x.test/c.png>)", asset_target="<https://x.test/c.png>"),
                  block(4, "asset_reference", "![d](http://x.test/d.png)", asset_target="http://x.test/d.png"),
                  block(5, "asset_reference", "![e](e.png)", asset_target="e.png")]
        body = reading_blocks(blocks)
        self.assertEqual([item for item in body if item["kind"] == "image"],
                         [{"kind": "image", "index": 1, "alt": "A chart"}, {"kind": "image", "index": 2, "alt": ""},
                          {"kind": "image", "index": 3, "alt": "c"}])
        self.assertEqual([item["kind"] for item in body], ["paragraph", "image", "image", "image"])
        self.assertNotIn("x.test", str(body))

    def test_the_detail_carries_the_active_run(self) -> None:
        self.assertEqual(self.detail("note", {}, [block(0, "paragraph", "Body.")])["run_id"], "run-1")


class CodeAndTableTests(unittest.TestCase):
    """Fenced code and tables, read from the real parser's blocks."""

    def reading(self, text):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Knowledge"
            root.mkdir()
            (root / "n.md").write_text(text, encoding="utf-8")
            document = parse_markdown(capture_source(root, "n.md", Path(temp) / "runs"), source_kind="note")
        return reading_blocks([item.model_dump(mode="json") for item in document.blocks])

    def test_a_fence_becomes_one_code_block_and_its_lines_are_not_read_as_markdown(self) -> None:
        body = self.reading("Intro.\n\n```mermaid\nflowchart TB\n\n    A --> B\n```\n\n```python\n# not a heading\n- not a list\n```\n\nEnd.\n")
        self.assertEqual([(item["kind"], item.get("language")) for item in body],
                         [("paragraph", None), ("code_block", "mermaid"), ("code_block", "python"), ("paragraph", None)])
        self.assertEqual(body[1]["text"], "flowchart TB\n\n    A --> B")
        self.assertEqual(body[2]["text"], "# not a heading\n- not a list")

    def test_text_touching_a_fence_stays_beside_it(self) -> None:
        body = self.reading("Before:\n```text\ncode\n```\nAfter.\n")
        self.assertEqual([(item["kind"], item["text"]) for item in body],
                         [("paragraph", "Before:"), ("code_block", "code"), ("paragraph", "After.")])

    def test_an_unclosed_fence_runs_to_the_end_of_the_note(self) -> None:
        body = self.reading("Intro.\n\n```mermaid\nflowchart TB\n    A --> B\n")
        self.assertEqual([item["kind"] for item in body], ["paragraph", "code_block"])
        self.assertIn("A --> B", body[1]["text"])

    def test_a_table_becomes_rows_and_unescapes_pipes(self) -> None:
        body = self.reading("Lead in:\n| Name | Note |\n| --- | :-: |\n| a | b \\| c |\n| d | e |\n\nAfter.\n")
        self.assertEqual([item["kind"] for item in body], ["paragraph", "table", "paragraph"])
        self.assertEqual(body[1]["rows"], [["Name", "Note"], ["a", "b | c"], ["d", "e"]])

    def test_a_fence_with_no_body_draws_nothing(self) -> None:
        for text in ("Intro.\n\n```\n```\n\nAfter.\n", "Intro.\n\n```python\n"):
            with self.subTest(text=text):
                self.assertFalse([item for item in self.reading(text) if item["kind"] == "code_block"])

    def test_inline_triple_backticks_do_not_open_a_fence(self) -> None:
        body = self.reading("Intro.\n\n```x``` is inline code\n\n## Real heading\n\nBody.\n")
        self.assertEqual([item["kind"] for item in body], ["paragraph", "paragraph", "heading", "paragraph"])

    def test_the_table_rule_check_is_linear_on_a_hostile_line(self) -> None:
        started = time.monotonic()
        self.assertFalse(_is_table_rule("-" + " " * 200_000 + "x"))
        self.assertTrue(_is_table_rule(" | --- | :-: | -- | "))
        self.assertLess(time.monotonic() - started, 1)

    def test_detail_returns_code_blocks(self) -> None:
        text = "```mermaid\nflowchart TB\n```\n"
        found = ("Knowledge/Notes/N.md", {"title": "N title"}, [{**block(0, "code_block", text), "language": "Mermaid",
                 "content_span": {"start_char": 11, "end_char": 24}}], "run-1", "a" * 64, "primary", ADDED)
        body = store(Conn(detail=found)).detail(IDS["note"])["body"]
        self.assertEqual([(item["kind"], item["language"], item["text"]) for item in body],
                         [("code_block", "mermaid", "flowchart TB")])

    def test_a_line_with_pipes_but_no_rule_row_stays_text(self) -> None:
        body = self.reading("| just | text |\n")
        self.assertEqual([item["kind"] for item in body], ["paragraph"])


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
        self.assertEqual(client.get(f"/api/sources/{IDS['note']}/images/1?r=run-1").status_code, 401)

    def image(self, path, conn, fetched=("image/png", b"\x89PNG\r\n\x1a\nbytes"), renewed=False):
        fetch = mock.Mock(side_effect=fetched) if isinstance(fetched, Exception) else mock.Mock(return_value=fetched)
        with mock.patch("app.api.auth.SessionStore.check", return_value=mock.MagicMock(renewed=renewed)), \
                mock.patch("app.api.images.fetch", fetch):
            return self.client(conn).get(path), fetch

    def test_an_image_block_is_fetched_by_its_cleaned_url_and_cached_by_the_browser_only(self) -> None:
        for target in ('https://x.test/a.png "Title"', "<https://x.test/a.png>"):
            conn = Conn(image=("run-1", {"index": 1, "kind": "asset_reference", "asset_target": target}))
            response, fetch = self.image(f"/api/sources/{IDS['note']}/images/1?r=run-1", conn)
            self.assertEqual(response.status_code, 200, response.text)
            fetch.assert_called_once_with("https://x.test/a.png")
            self.assertEqual(response.headers["content-type"], "image/png")
            self.assertEqual(response.headers["cache-control"], "private, max-age=86400")
            self.assertIn("img-src 'self' data:;", response.headers["content-security-policy"])
            self.assertNotIn("set-cookie", response.headers)

    def test_anything_but_an_https_image_block_of_the_active_run_is_not_found_and_never_fetched(self) -> None:
        image = {"index": 1, "kind": "asset_reference", "asset_target": "https://x.test/a.png"}
        cases = [(f"/api/sources/not-an-id/images/1?r=run-1", Conn(image=("run-1", image))),
                 (f"/api/sources/{IDS['note']}/images/-1?r=run-1", Conn(image=("run-1", image))),
                 (f"/api/sources/{IDS['note']}/images/1?r=run-0", Conn(image=("run-1", image))),
                 (f"/api/sources/{IDS['note']}/images/1", Conn(image=("run-1", image))),
                 (f"/api/sources/{IDS['note']}/images/1?r=run-1", Conn(image=("run-1", None))),
                 (f"/api/sources/{IDS['note']}/images/1?r=run-1", Conn()),
                 (f"/api/sources/{IDS['note']}/images/1?r=run-1",
                  Conn(image=("run-1", {**image, "kind": "paragraph"}))),
                 (f"/api/sources/{IDS['note']}/images/1?r=run-1",
                  Conn(image=("run-1", {**image, "asset_target": "http://x.test/a.png"})))]
        for path, conn in cases:
            with self.subTest(path=path, image=conn.image):
                response, fetch = self.image(path, conn)
                self.assertEqual(response.status_code, 404, response.text)
                fetch.assert_not_called()

    def test_a_renewed_session_reaches_the_browser_on_an_image_response(self) -> None:
        conn = Conn(image=("run-1", {"index": 1, "kind": "asset_reference", "asset_target": "https://x.test/a.png"}))
        response, _ = self.image(f"/api/sources/{IDS['note']}/images/1?r=run-1", conn, renewed=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("set-cookie", response.headers)

    def test_a_failed_fetch_is_a_502_that_never_shows_the_url(self) -> None:
        from app.api.images import ImageError
        conn = Conn(image=("run-1", {"index": 1, "kind": "asset_reference",
                                     "asset_target": "https://x.test/secret/a.png?token=abc"}))
        with self.assertLogs("knowledge.api", level="INFO") as logs:
            response, _ = self.image(f"/api/sources/{IDS['note']}/images/1?r=run-1", conn,
                                     fetched=ImageError("x.test answered 404"))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"error": "the image could not be loaded"})
        self.assertNotIn("secret", " ".join(logs.output) + response.text)

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
