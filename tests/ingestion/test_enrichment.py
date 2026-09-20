import tempfile
import unittest
from pathlib import Path

from app.ingestion.chunking import structural_raw_chunks
from app.ingestion.enrichment import (
    extract_evidence_cards,
    generate_chunk_context,
)
from app.ingestion.router import route_source
from app.ingestion.run_manifest import RunManifest
from app.ingestion.source_reader import capture_source
from app.models.client import GenerationResult, ModelClientError, ModelSpec, ModelUsage


class FakeClient:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return GenerationResult(
            data=self.data,
            model=kwargs["model"],
            usage=ModelUsage(input_tokens=40, output_tokens=12, total_tokens=52),
        )

    def embed(self, **kwargs):
        raise AssertionError("unexpected embedding call")


class EnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Knowledge"
        self.root.mkdir()
        self.runtime = Path(self.temp.name) / "runs"
        self.model = ModelSpec(
            provider="openrouter", model_id="example/open-model", model_version="v1"
        )

    def setup_source(self, relative: str, text: str):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        capture = capture_source(self.root, relative, self.runtime)
        document = route_source(capture)
        chunks = structural_raw_chunks(document)
        return capture, document, chunks, RunManifest.for_document(document)

    def test_context_uses_bounded_source_and_is_versioned(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/idea.md", "## Thoughts\n\nA source passage.\n"
        )
        client = FakeClient({"context": "This note records a short thought."})
        first = generate_chunk_context(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir, max_input_tokens=200,
        )
        second = generate_chunk_context(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir, max_input_tokens=200,
        )
        self.assertEqual(first, second)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("A source passage.", client.calls[0]["input_text"])
        self.assertLess(len(client.calls[0]["input_text"]), 500)
        self.assertEqual(first.source_spans, chunks.chunks[0].source_spans)
        self.assertEqual(next(iter(manifest.artifacts.values())).status, "ready")
        self.assertEqual(RunManifest.load(capture.run_dir), manifest)

    def test_exact_card_and_nullable_fields(self) -> None:
        text = "**Host:** We called ten customers first.\n"
        capture, document, chunks, manifest = self.setup_source("Transcripts/episode.md", text)
        quote = "called ten customers"
        start = document.source_text.index(quote)
        client = FakeClient({"cards": [{
            "card_type": "tactic", "statement": "Call customers early.",
            "situation": None, "action": None, "result": None,
            "principle": "Seek firsthand feedback.",
            "evidence_start_char": start,
            "evidence_end_char": start + len(quote), "evidence_text": quote,
        }]})
        artifact = extract_evidence_cards(
            document, chunks, chunks.chunks[0], client, self.model,
            manifest, capture.run_dir,
        )
        card = artifact.cards[0]
        self.assertIsNone(card.situation)
        self.assertIsNone(card.action)
        self.assertIsNone(card.result)
        self.assertTrue(card.principle_is_derived)
        self.assertEqual(card.attribution, "Host")
        card.validate_against(document, chunks.chunks[0])

    def test_personal_annotation_is_not_attributed_to_book_author(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Book Highlights/book.md",
            Path("sources/fixtures/sample_highlights.md").read_text(encoding="utf-8"),
        )
        quote = "I want to revisit this."
        start = document.source_text.index(quote)
        target = next(
            chunk for chunk in chunks.chunks
            if any(span.start_char <= start < span.end_char for span in chunk.source_spans)
        )
        client = FakeClient({"cards": [{
            "card_type": "lesson", "statement": "Revisit the passage.",
            "evidence_start_char": start,
            "evidence_end_char": start + len(quote), "evidence_text": quote,
        }]})
        artifact = extract_evidence_cards(
            document, chunks, target, client, self.model, manifest, capture.run_dir,
        )
        self.assertEqual(artifact.cards[0].content_role, "personal_note")
        self.assertEqual(artifact.cards[0].attribution, "personal_note")

    def test_invented_evidence_and_model_failure_leave_no_ready_artifact(self) -> None:
        capture, document, chunks, manifest = self.setup_source(
            "Notes/idea.md", "A real sentence.\n"
        )
        invented = FakeClient({"cards": [{
            "card_type": "claim", "statement": "Unsupported.",
            "evidence_start_char": 0, "evidence_end_char": 6,
            "evidence_text": "made up",
        }]})
        with self.assertRaisesRegex(ValueError, "exact source text"):
            extract_evidence_cards(
                document, chunks, chunks.chunks[0], invented, self.model,
                manifest, capture.run_dir,
            )
        self.assertTrue(all(record.status == "failed" for record in manifest.artifacts.values()))
        self.assertEqual(list((capture.run_dir / "artifacts").rglob("*.json")) if (capture.run_dir / "artifacts").exists() else [], [])
        failing = FakeClient(error=ModelClientError("timeout"))
        with self.assertRaises(ModelClientError):
            generate_chunk_context(
                document, chunks, chunks.chunks[0], failing, self.model,
                manifest, capture.run_dir,
            )
        self.assertTrue(all(record.status == "failed" for record in manifest.artifacts.values()))


if __name__ == "__main__":
    unittest.main()
