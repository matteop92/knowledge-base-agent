import tempfile
import unittest
from pathlib import Path

from app.ingestion.chunking import fixed_raw_chunks, structural_raw_chunks
from app.ingestion.contracts import ChunkSet
from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source


class ChunkingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Knowledge"
        self.root.mkdir()
        self.runtime = Path(self.temp.name) / "runs"

    def parse(self, relative: str, text: str):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return route_source(capture_source(self.root, relative, self.runtime))

    def test_variants_cover_the_same_eligible_body(self) -> None:
        text = "## Section\n\n" + ("One long thought with several words. " * 350)
        parsed = self.parse("Books/book.md", text)
        fixed = fixed_raw_chunks(parsed)
        structural = structural_raw_chunks(parsed)
        self.assertEqual(fixed.variant, "fixed_raw")
        self.assertEqual(structural.variant, "structural_raw")
        self.assertEqual(fixed.source_sha256, structural.source_sha256)
        self.assertEqual(fixed.tokenizer_version, structural.tokenizer_version)
        self.assertEqual(
            [chunk.model_dump() for chunk in fixed.chunks],
            [chunk.model_dump() for chunk in fixed_raw_chunks(parsed).chunks],
        )
        self.assertEqual(fixed.chunks[1].overlap_tokens, 80)
        self.assertTrue(all(chunk.token_count <= 1000 for chunk in structural.chunks))
        fixed.validate_against(parsed)
        structural.validate_against(parsed)

    def test_code_blocks_and_tables_stay_in_the_chunks(self) -> None:
        text = ("## Setup\n\nIntro.\n\n```mermaid\nflowchart TB\n    A --> B\n```\n\n"
                "| Name | Note |\n| --- | --- |\n| a | b |\n")
        parsed = self.parse("Notes/n.md", text)
        self.assertEqual([block.kind for block in parsed.blocks], ["heading", "paragraph", "code_block", "table"])
        for chunks in (fixed_raw_chunks(parsed), structural_raw_chunks(parsed)):
            joined = "".join(chunk.raw_text for chunk in chunks.chunks)
            self.assertIn("A --> B", joined)
            self.assertIn("| a | b |", joined)

    def test_short_note_and_highlight_note_relationship(self) -> None:
        note = self.parse("Notes/note.md", "## Thoughts\n\n" + "word " * 200)
        self.assertEqual(len(structural_raw_chunks(note).chunks), 1)
        highlights = self.parse(
            "Book Highlights/book.md",
            Path("sources/fixtures/sample_highlights.md").read_text(encoding="utf-8"),
        )
        chunks = structural_raw_chunks(highlights).chunks
        note_block = next(
            block for block in highlights.blocks if block.content_role == "personal_note"
        )
        self.assertTrue(
            any(
                note_block.index in chunk.block_indices
                and any(
                    block.kind == "highlight" and block.group_id == note_block.group_id
                    and block.index in chunk.block_indices
                    for block in highlights.blocks
                )
                for chunk in chunks
            )
        )

    def test_oversized_turn_splits_with_exact_source_spans(self) -> None:
        parsed = self.parse(
            "Transcripts/long.md",
            "**Host:** " + ("A complete sentence ends here. " * 600),
        )
        structural = structural_raw_chunks(parsed)
        self.assertGreater(len(structural.chunks), 1)
        self.assertTrue(all(chunk.token_count <= 1000 for chunk in structural.chunks))
        self.assertEqual(structural.chunks[0].boundary_reason, "oversized_block")
        structural.validate_against(parsed)

    def test_invalid_chunk_source_text_and_coverage_are_rejected(self) -> None:
        parsed = self.parse("Notes/note.md", "First paragraph.\n\nSecond paragraph.\n")
        valid = structural_raw_chunks(parsed)
        mismatched = valid.model_dump()
        mismatched["chunks"][0]["raw_text"] = "invented"
        with self.assertRaisesRegex(ValueError, "raw_text does not match"):
            ChunkSet.model_validate(mismatched).validate_against(parsed)
        missing = valid.model_dump()
        missing["chunks"][0]["source_spans"] = missing["chunks"][0]["source_spans"][:1]
        missing["chunks"][0]["raw_text"] = parsed.source_text[
            missing["chunks"][0]["source_spans"][0]["start_char"]:
            missing["chunks"][0]["source_spans"][0]["end_char"]
        ]
        with self.assertRaisesRegex(ValueError, "coverage does not match"):
            ChunkSet.model_validate(missing).validate_against(parsed)


if __name__ == "__main__":
    unittest.main()
