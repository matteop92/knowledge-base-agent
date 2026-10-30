import tempfile
import unittest
from pathlib import Path

from app.ingestion.markdown_parser import parse_markdown
from app.ingestion.source_reader import capture_source
from app.ingestion.transcript_parser import TranscriptParseError


class MarkdownParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Knowledge"
        self.root.mkdir()
        self.runtime = Path(self.temp.name) / "runs"

    def parse(self, relative: str, text: str, kind: str = "generic"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return parse_markdown(capture_source(self.root, relative, self.runtime), source_kind=kind)

    def test_note_without_heading_and_article_tips(self) -> None:
        note = self.parse("Notes/idea.md", "First idea.\n\nSecond idea.\n", "note")
        self.assertEqual([block.kind for block in note.blocks], ["paragraph", "paragraph"])
        self.assertTrue(all(block.heading_path is None for block in note.blocks))
        article = self.parse(
            "Articles/article.md",
            "### Say it plainly\n\nOpening.\n\n**Tip 1: Ask directly**\n\n"
            "Try a question.\n\n- One example\n- Another example\n",
            "article",
        )
        self.assertEqual(
            [block.kind for block in article.blocks],
            ["heading", "paragraph", "tip", "paragraph", "list_item", "list_item"],
        )
        self.assertEqual(article.blocks[3].heading_path, ["Say it plainly"])

    def test_book_furniture_date_and_missing_asset(self) -> None:
        text = Path("sources/fixtures/sample_book.md").read_text(encoding="utf-8")
        book = self.parse("Books/book.md", text, "book")
        self.assertEqual(book.metadata.date.isoformat(), "2024-01-02")
        self.assertIn("paragraph", [block.kind for block in book.blocks])
        self.assertNotIn("diagram, 1", " ".join(block.raw_text for block in book.blocks))
        self.assertGreater(
            sum(item.reason == "export_furniture" for item in book.excluded_spans), 0
        )
        self.assertEqual(book.unresolved_assets[0].target, "images/diagram.png")
        self.assertEqual(book.blocks[-1].kind, "asset_reference")

    def test_invalid_frontmatter_is_reported(self) -> None:
        with self.assertRaisesRegex(TranscriptParseError, "invalid YAML"):
            self.parse("Notes/bad.md", "---\ntitle: [\n---\nBody.\n", "note")

    def test_guest_is_kept_with_author_and_missing_title_uses_filename(self) -> None:
        parsed = self.parse("Notes/interview.md", "---\nauthor: Host\nguest: '[[Luca Ferrari]]'\n---\nBody.\n", "note")
        self.assertEqual(parsed.metadata.title, "interview")
        self.assertEqual(parsed.metadata.authors, ["Host"])
        self.assertEqual(parsed.metadata.guests, ["Luca Ferrari"])

    def test_fences_and_tables_are_blocks_of_their_own_and_keep_heading_levels(self) -> None:
        text = (
            "# Title\n\n## Setup\n\nIntro:\n```python\n# a comment\n- not a list\nprint(1)\n```\n"
            "Tail.\n\n### Deep\n\n~~~mermaid\nflowchart TB\n    A --> B\n~~~\n\n"
            "Lead in:\n| Name | Note |\n| --- | :-: |\n| a | b |\n\n### After\n\nEnd.\n"
        ).replace("\n", "\r\n")
        note = self.parse("Notes/dspy.md", text, "note")
        kinds = [(block.kind, block.language) for block in note.blocks]
        self.assertEqual(kinds, [
            ("heading", None), ("heading", None), ("paragraph", None), ("code_block", "python"), ("paragraph", None),
            ("heading", None), ("code_block", "mermaid"), ("paragraph", None), ("table", None), ("heading", None),
            ("paragraph", None)])
        self.assertEqual([block.heading_path for block in note.blocks if block.kind == "heading"],
                         [["Title"], ["Title", "Setup"], ["Title", "Setup", "Deep"], ["Title", "Setup", "After"]])
        code = next(block for block in note.blocks if block.language == "python")
        self.assertEqual(code.raw_text[code.content_span.start_char - code.span.start_char:
                                       code.content_span.end_char - code.span.start_char],
                         "# a comment\r\n- not a list\r\nprint(1)\r\n")
        self.assertEqual(note.blocks[-2].heading_path, ["Title", "Setup", "After"])

    def test_fence_variants_follow_the_commonmark_rules_for_closing_and_info_strings(self) -> None:
        longer = self.parse("Notes/a.md", "````md\n```\ninner\n```\n````\n\nafter\n", "note")
        self.assertEqual([block.kind for block in longer.blocks], ["code_block", "paragraph"])  # a shorter run does not close it
        trailing = self.parse("Notes/b.md", "```\ncode\n``` not a close\nmore\n```\n\nafter\n", "note")
        self.assertEqual([block.kind for block in trailing.blocks], ["code_block", "paragraph"])
        self.assertIn("more", trailing.blocks[0].raw_text)  # a closer with text after it stays inside the fence
        info = self.parse("Notes/c.md", "``` a`b\nnot code\n", "note")
        self.assertEqual([block.kind for block in info.blocks], ["paragraph"])  # a backtick fence's info has no backtick
        tilde = self.parse("Notes/d.md", "~~~\n```\nstill code\n~~~\n\nafter\n", "note")
        self.assertEqual([block.kind for block in tilde.blocks], ["code_block", "paragraph"])

    def test_an_empty_fence_has_no_content_span_and_a_header_only_table_is_a_table(self) -> None:
        empty = self.parse("Notes/e.md", "Intro.\n\n```\n```\n\nAfter.\n", "note")
        self.assertEqual([block.kind for block in empty.blocks], ["paragraph", "code_block", "paragraph"])
        self.assertIsNone(empty.blocks[1].content_span)
        table = self.parse("Notes/f.md", "| a | b |\n| --- | --- |\n\nafter\n", "note")
        self.assertEqual([block.kind for block in table.blocks], ["table", "paragraph"])
        plain = self.parse("Notes/g.md", "a | b\n--- | ---\n1 | 2\n", "note")
        self.assertEqual([block.kind for block in plain.blocks], ["paragraph"])  # a table needs its leading pipes

    def test_unclosed_fence_runs_to_the_end_and_inline_backticks_are_not_a_fence(self) -> None:
        note = self.parse("Notes/open.md", "## A\n\n```x``` inline\n\n```text\n# not a heading\n", "note")
        self.assertEqual([block.kind for block in note.blocks], ["heading", "paragraph", "code_block"])
        self.assertIsNone(self.parse("Notes/pipes.md", "| just | text |\n", "note").blocks[0].language)
        self.assertEqual(self.parse("Notes/pipes.md", "| just | text |\n", "note").blocks[0].kind, "paragraph")


if __name__ == "__main__":
    unittest.main()
