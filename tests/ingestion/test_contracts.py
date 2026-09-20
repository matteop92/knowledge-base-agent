import unittest

from pydantic import ValidationError

from app.ingestion.contracts import (
    ParsedTranscript,
    SourceLocator,
    SourceMetadata,
    SourceSpan,
    TranscriptTurn,
)


def make_record() -> ParsedTranscript:
    text = "**Host:** Hello.\n\n**Guest:** Hi — there.\n"
    first = text.index("**Guest:**")
    turns = [
        TranscriptTurn(
            index=0,
            speaker="Host",
            span=SourceSpan(start_char=0, end_char=first, start_line=1, end_line=2),
            content_span=SourceSpan(start_char=10, end_char=first, start_line=1, end_line=2),
            raw_text=text[:first],
        ),
        TranscriptTurn(
            index=1,
            speaker="Guest",
            span=SourceSpan(start_char=first, end_char=len(text), start_line=3, end_line=3),
            content_span=SourceSpan(start_char=first + 11, end_char=len(text), start_line=3, end_line=3),
            raw_text=text[first:],
        ),
    ]
    return ParsedTranscript(
        document_id="document-1",
        run_id="run-1",
        source_sha256="a" * 64,
        locator=SourceLocator(root_alias="primary", relative_path="episode.md"),
        metadata=SourceMetadata(title="Episode", frontmatter={"extra": "value"}),
        source_text=text,
        turns=turns,
    )


class ContractTests(unittest.TestCase):
    def test_round_trip_and_optional_metadata(self) -> None:
        record = make_record()
        restored = ParsedTranscript.model_validate_json(record.model_dump_json())
        self.assertEqual(restored, record)
        self.assertIsNone(restored.metadata.published)
        self.assertIsNone(restored.turns[0].timestamp_start)
        self.assertEqual(restored.metadata.frontmatter["extra"], "value")

    def test_mismatched_text_is_rejected(self) -> None:
        record = make_record().model_dump()
        record["turns"][0]["raw_text"] = "invented"
        with self.assertRaisesRegex(ValidationError, "raw_text does not match"):
            ParsedTranscript.model_validate(record)

    def test_out_of_bounds_span_is_rejected(self) -> None:
        record = make_record().model_dump()
        record["turns"][1]["span"]["end_char"] += 10
        record["turns"][1]["content_span"]["end_char"] += 10
        with self.assertRaisesRegex(ValidationError, "extends beyond"):
            ParsedTranscript.model_validate(record)

    def test_absolute_or_parent_locator_is_rejected(self) -> None:
        for relative_path in ("../episode.md", "/episode.md", "C:/episode.md", "foo\\episode.md"):
            with self.subTest(relative_path=relative_path), self.assertRaises(ValidationError):
                SourceLocator(root_alias="primary", relative_path=relative_path)


if __name__ == "__main__":
    unittest.main()
