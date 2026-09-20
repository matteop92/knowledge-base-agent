import unittest

from pydantic import ValidationError

from app.ingestion.contracts import (
    DocumentBlock,
    ExcludedSpan,
    ParsedDocument,
    SourceLocator,
    SourceMetadata,
    SourceSpan,
)


def span(text: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(
        start_char=start,
        end_char=end,
        start_line=text.count("\n", 0, start) + 1,
        end_line=text.count("\n", 0, end - 1) + 1,
    )


def make_record() -> ParsedDocument:
    text = "---\nsource: Kindle\n---\n# Chapter\nA paragraph.\n> A highlight.\nMy note.\n"
    front_end = text.index("#")
    heading_end = text.index("A paragraph")
    paragraph_end = text.index("> A highlight")
    highlight_end = text.index("My note")
    boundaries = [
        (front_end, heading_end, "heading", "source"),
        (heading_end, paragraph_end, "paragraph", "source"),
        (paragraph_end, highlight_end, "highlight", "source"),
        (highlight_end, len(text), "paragraph", "personal_note"),
    ]
    blocks = [
        DocumentBlock(
            index=index,
            kind=kind,
            span=span(text, start, end),
            raw_text=text[start:end],
            heading_path=["Chapter"] if kind != "heading" else None,
            page_reference="42" if kind == "highlight" else None,
            content_role=role,
        )
        for index, (start, end, kind, role) in enumerate(boundaries)
    ]
    return ParsedDocument(
        document_id="document-1",
        run_id="run-1",
        source_sha256="a" * 64,
        locator=SourceLocator(root_alias="primary", relative_path="Books/example.md"),
        metadata=SourceMetadata(
            title="Example",
            source_origin="Kindle",
            date=None,
            frontmatter_raw=text[:front_end],
            frontmatter={"source": "Kindle"},
        ),
        source_text=text,
        blocks=blocks,
        excluded_spans=[
            ExcludedSpan(
                span=span(text, 0, front_end),
                raw_text=text[:front_end],
                reason="frontmatter",
            )
        ],
    )


class ContractTests(unittest.TestCase):
    def test_round_trip_mixed_blocks_and_attribution(self) -> None:
        record = make_record()
        restored = ParsedDocument.model_validate_json(record.model_dump_json())
        self.assertEqual(restored, record)
        self.assertEqual(restored.schema_version, "2")
        self.assertIsNone(restored.metadata.published)
        self.assertEqual(restored.blocks[-1].heading_path, ["Chapter"])
        self.assertEqual(restored.blocks[2].page_reference, "42")
        self.assertEqual(restored.blocks[-1].content_role, "personal_note")
        self.assertEqual(restored.metadata.source_origin, "Kindle")
        self.assertIsNone(restored.metadata.source_url)

    def test_speaker_turn_and_note_without_heading(self) -> None:
        text = "**Host:** Hello.\n"
        record = ParsedDocument(
            document_id="d",
            run_id="r",
            source_sha256="a" * 64,
            locator=SourceLocator(root_alias="primary", relative_path="Transcripts/episode.md"),
            metadata=SourceMetadata(title="Episode"),
            source_text=text,
            blocks=[
                DocumentBlock(
                    index=0, kind="speaker_turn", speaker="Host",
                    span=span(text, 0, len(text)),
                    content_span=span(text, len("**Host:** "), len(text)),
                    raw_text=text,
                )
            ],
        )
        self.assertEqual(ParsedDocument.model_validate_json(record.model_dump_json()), record)
        note_text = "My note.\n"
        note = ParsedDocument(
            document_id="note",
            run_id="run",
            source_sha256="b" * 64,
            locator=SourceLocator(root_alias="primary", relative_path="Notes/idea.md"),
            metadata=SourceMetadata(title="Idea"),
            source_text=note_text,
            blocks=[
                DocumentBlock(
                    index=0,
                    kind="paragraph",
                    span=span(note_text, 0, len(note_text)),
                    raw_text=note_text,
                    content_role="personal_note",
                )
            ],
        )
        self.assertIsNone(note.blocks[0].heading_path)
        self.assertIsNone(note.metadata.date)

    def test_mismatched_text_and_out_of_bounds_are_rejected(self) -> None:
        record = make_record().model_dump()
        record["blocks"][0]["raw_text"] = "invented"
        with self.assertRaisesRegex(ValidationError, "raw_text does not match"):
            ParsedDocument.model_validate(record)
        record = make_record().model_dump()
        record["blocks"][-1]["span"]["end_char"] += 10
        with self.assertRaisesRegex(ValidationError, "extends beyond"):
            ParsedDocument.model_validate(record)

    def test_overlap_and_unreported_gap_are_rejected(self) -> None:
        record = make_record().model_dump()
        record["blocks"][1]["span"]["start_char"] -= 1
        record["blocks"][1]["raw_text"] = record["source_text"][
            record["blocks"][1]["span"]["start_char"]:record["blocks"][1]["span"]["end_char"]
        ]
        record["blocks"][1]["span"]["start_line"] = record["source_text"].count(
            "\n", 0, record["blocks"][1]["span"]["start_char"]
        ) + 1
        with self.assertRaisesRegex(ValidationError, "ordered and nonoverlapping"):
            ParsedDocument.model_validate(record)
        record = make_record().model_dump()
        record["excluded_spans"] = []
        with self.assertRaisesRegex(ValidationError, "overlap or unreported gap"):
            ParsedDocument.model_validate(record)

    def test_block_order_is_source_order(self) -> None:
        record = make_record().model_dump()
        record["blocks"][1], record["blocks"][2] = record["blocks"][2], record["blocks"][1]
        record["blocks"][1]["index"] = 1
        record["blocks"][2]["index"] = 2
        with self.assertRaisesRegex(ValidationError, "ordered and nonoverlapping"):
            ParsedDocument.model_validate(record)

    def test_invalid_url_and_locator_are_rejected(self) -> None:
        for url in ("Kindle", "https://a b", "https://example.com:bad"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                SourceMetadata(title="Bad", source_url=url)
        for relative_path in ("../episode.md", "/episode.md", "C:/episode.md", "foo\\episode.md"):
            with self.subTest(relative_path=relative_path), self.assertRaises(ValidationError):
                SourceLocator(root_alias="primary", relative_path=relative_path)


if __name__ == "__main__":
    unittest.main()
