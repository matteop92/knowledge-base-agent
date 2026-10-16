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
        path.write_text(text, encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
