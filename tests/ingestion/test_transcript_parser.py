import tempfile
import unittest
from pathlib import Path

from app.ingestion.source_reader import capture_source
from app.ingestion.transcript_parser import (
    TranscriptParseError,
    parse_bracket_transcript,
    parse_transcript,
)


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
        self.assertEqual([block.speaker for block in result.blocks], ["Host", "Guest"])
        self.assertIn("I wanted to learn", result.blocks[0].raw_text)
        self.assertIn("**This is bold**", result.blocks[1].raw_text)
        self.assertTrue(result.metadata.frontmatter_raw.startswith("---\n"))
        for turn in result.blocks:
            self.assertEqual(
                result.source_text[turn.span.start_char : turn.span.end_char], turn.raw_text
            )
            self.assertIsNone(turn.timestamp_start)

    def test_crlf_unicode_and_lines(self) -> None:
        result = self.parse(
            "---\r\ntitle: CRLF episode\r\n---\r\n\r\n"
            "**Host:** Hi — there.\r\n\r\nMore.\r\n**Guest:** Yes.\r\n"
        )
        self.assertEqual(result.blocks[0].span.start_line, 5)
        self.assertEqual(result.blocks[1].span.start_line, 8)
        self.assertEqual(result.blocks[1].span.start_char, result.source_text.index("**Guest:**"))
        self.assertIn("\r\n", result.blocks[0].raw_text)
        self.assertEqual(result.metadata.frontmatter_raw, "---\r\ntitle: CRLF episode\r\n---\r\n")

    def test_missing_optional_metadata_uses_filename(self) -> None:
        result = self.parse("**Host:** Hello.\n")
        self.assertEqual(result.metadata.title, "episode")
        self.assertIsNone(result.metadata.source_url)
        self.assertEqual(result.metadata.authors, [])

    def test_missing_opening_bold_for_known_speaker_preserves_turn_boundary(self) -> None:
        result = self.parse("**Host:** Hello.\n**Guest:** Reply.\nHost:** Follow-up.\n")
        self.assertEqual([block.speaker for block in result.blocks], ["Host", "Guest", "Host"])
        self.assertEqual(result.blocks[2].raw_text, "Host:** Follow-up.\n")
        self.assertEqual(
            result.source_text[result.blocks[2].span.start_char : result.blocks[2].span.end_char],
            result.blocks[2].raw_text,
        )

    def test_malformed_or_unmatched_text_fails(self) -> None:
        for text, expected in (
            ("---\n title: [\n---\n**Host:** Hi.", "invalid YAML"),
            ("---\ntitle: X\n**Host:** Hi.", "closing"),
            ("An interview without labels", "no supported"),
            ("**Host:**\n", "no content"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(TranscriptParseError, expected):
                self.parse(text)

    def test_unmatched_preface_is_reported(self) -> None:
        result = self.parse("Preface.\n**Host:** Hi.")
        self.assertEqual(result.excluded_spans[0].reason, "unclassified")
        self.assertEqual(result.excluded_spans[0].raw_text, "Preface.\n")

    def test_unknown_malformed_speaker_is_not_attributed_to_previous_turn(self) -> None:
        result = self.parse(
            "**Host:** Hello.\nUnknown:** Other person's words.\n**Guest:** Reply.\n"
        )
        self.assertEqual([block.speaker for block in result.blocks], ["Host", "Guest"])
        self.assertNotIn("Other person's words", result.blocks[0].raw_text)
        self.assertEqual(result.excluded_spans[0].reason, "unclassified")
        self.assertIn("Other person's words", result.excluded_spans[0].raw_text)

    def test_origin_url_date_and_personal_note_metadata(self) -> None:
        result = self.parse(
            "---\nsource: Kindle\nurl: https://example.com/book\ndate: 2024-01-02\n"
            "note: My note\n---\n**Host:** Hi.\n"
        )
        self.assertEqual(result.metadata.source_origin, "Kindle")
        self.assertEqual(result.metadata.source_url, "https://example.com/book")
        self.assertEqual(result.metadata.date.isoformat(), "2024-01-02")
        self.assertEqual(result.metadata.frontmatter["note"], "My note")
        from_source = self.parse("---\nsource: https://example.com/episode\n---\n**Host:** Hi.\n")
        self.assertEqual(from_source.metadata.source_url, "https://example.com/episode")
        self.assertIsNone(from_source.metadata.source_origin)

    def test_bracket_transcript_preface_and_summary_are_unattributed(self) -> None:
        fixture = Path("sources/fixtures/sample_bracket_transcript.md").read_text(encoding="utf-8")
        self.source.write_text(fixture, encoding="utf-8")
        parsed = parse_bracket_transcript(
            capture_source(self.root, "episode.md", self.runtime)
        )
        self.assertEqual([block.speaker for block in parsed.blocks if block.kind == "speaker_turn"], ["Host", "Guest"])
        summary = next(block for block in parsed.blocks if "guests discuss" in block.raw_text)
        self.assertEqual(summary.kind, "paragraph")
        self.assertIsNone(summary.speaker)
        self.assertEqual(parsed.blocks[-1].heading_path, ["Episode transcript"])

    def test_malformed_bracket_label_is_unclassified(self) -> None:
        self.source.write_text(
            "\\[Host\\]\nHello.\n[Unknown]\nDo not attribute me.\n\\[Guest\\]\nReply.\n",
            encoding="utf-8",
        )
        parsed = parse_bracket_transcript(
            capture_source(self.root, "episode.md", self.runtime)
        )
        self.assertEqual(parsed.diagnostics[0].code, "malformed_speaker_label")
        self.assertNotIn("Do not attribute me", parsed.blocks[0].raw_text)
        self.assertEqual(parsed.excluded_spans[0].reason, "unclassified")


if __name__ == "__main__":
    unittest.main()
