import tempfile
import unittest
from pathlib import Path

from app.ingestion.source_reader import capture_source
from app.ingestion.transcript_parser import TranscriptParseError, parse_transcript


class TranscriptParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "vault"
        self.root.mkdir()
        self.runtime = self.base / "runs"
        self.source = self.root / "episode.md"

    def parse(self, text: str):
        self.source.write_bytes(text.encode("utf-8"))
        return parse_transcript(capture_source(self.root, "episode.md", self.runtime))

    def test_fixture_metadata_and_multiline_turns(self) -> None:
        fixture = Path("sources/fixtures/sample_transcript.md").read_text(encoding="utf-8")
        result = self.parse(fixture)
        self.assertEqual(result.metadata.title, "A small conversation")
        self.assertEqual(result.metadata.published.isoformat(), "2026-09-20")
        self.assertEqual(result.metadata.frontmatter["custom_field"], "keep me")
        self.assertEqual([turn.speaker for turn in result.turns], ["Host", "Guest"])
        self.assertIn("I wanted to learn", result.turns[0].raw_text)
        self.assertIn("**This is bold**", result.turns[1].raw_text)
        self.assertTrue(result.metadata.frontmatter_raw.startswith("---\n"))
        for turn in result.turns:
            self.assertEqual(
                result.source_text[turn.span.start_char : turn.span.end_char], turn.raw_text
            )
            self.assertIsNone(turn.timestamp_start)

    def test_crlf_unicode_and_lines(self) -> None:
        result = self.parse(
            "---\r\ntitle: CRLF episode\r\n---\r\n\r\n"
            "**Host:** Hi — there.\r\n\r\nMore.\r\n**Guest:** Yes.\r\n"
        )
        self.assertEqual(result.turns[0].span.start_line, 5)
        self.assertEqual(result.turns[1].span.start_line, 8)
        self.assertEqual(result.turns[1].span.start_char, result.source_text.index("**Guest:**"))
        self.assertIn("\r\n", result.turns[0].raw_text)
        self.assertEqual(result.metadata.frontmatter_raw, "---\r\ntitle: CRLF episode\r\n---\r\n")

    def test_missing_optional_metadata_uses_filename(self) -> None:
        result = self.parse("**Host:** Hello.\n")
        self.assertEqual(result.metadata.title, "episode")
        self.assertIsNone(result.metadata.source_url)
        self.assertEqual(result.metadata.authors, [])

    def test_malformed_or_unmatched_text_fails(self) -> None:
        for text, expected in (
            ("---\n title: [\n---\n**Host:** Hi.", "invalid YAML"),
            ("---\ntitle: X\n**Host:** Hi.", "closing"),
            ("An interview without labels", "no supported"),
            ("Preface.\n**Host:** Hi.", "unmatched"),
            ("**Host:**\n", "no content"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(TranscriptParseError, expected):
                self.parse(text)


if __name__ == "__main__":
    unittest.main()
