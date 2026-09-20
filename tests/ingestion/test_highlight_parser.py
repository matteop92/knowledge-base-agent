import tempfile
import unittest
from pathlib import Path

from app.ingestion.highlight_parser import parse_highlights
from app.ingestion.source_reader import capture_source


class HighlightParserTests(unittest.TestCase):
    def test_page_and_personal_note_attribution(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "Knowledge"
        path = root / "Book Highlights" / "book.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(Path("sources/fixtures/sample_highlights.md").read_bytes())
        capture = capture_source(root, "Book Highlights/book.md", Path(temp.name) / "runs")
        parsed = parse_highlights(capture)
        highlights = [block for block in parsed.blocks if block.kind == "highlight"]
        notes = [block for block in parsed.blocks if block.content_role == "personal_note"]
        self.assertEqual([block.page_reference for block in highlights], ["12", "17"])
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].page_reference, "12")
        self.assertEqual(notes[0].group_id, highlights[0].group_id)
        self.assertNotEqual(notes[0].group_id, highlights[1].group_id)
        self.assertIn("I want to revisit", notes[0].raw_text)
        self.assertNotIn("I want to revisit", highlights[0].raw_text)
        self.assertEqual(parsed.metadata.source_origin, "Kindle")
        self.assertEqual(parsed.metadata.authors, ["Example Author"])


if __name__ == "__main__":
    unittest.main()
