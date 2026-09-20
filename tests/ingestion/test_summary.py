import tempfile
import unittest
from pathlib import Path

from app.ingestion.chunking import structural_raw_chunks
from app.ingestion.router import route_source
from app.ingestion.run_manifest import RunManifest
from app.ingestion.source_reader import capture_source
from app.ingestion.summary import summarize_document
from app.models.client import GenerationResult, ModelSpec


class FakeClient:
    def __init__(self):
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        return GenerationResult(data={"summary": "A grounded summary."}, model=kwargs["model"])


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "knowledge"
        self.root.mkdir()
        self.runtime = Path(self.temp.name) / "runs"
        self.model = ModelSpec(provider="test", model_id="summary", model_version="1")

    def source(self, text):
        (self.root / "note.md").write_text(text, encoding="utf-8")
        capture = capture_source(self.root, "note.md", self.runtime)
        document = route_source(capture)
        chunks = structural_raw_chunks(document)
        return capture, document, chunks, RunManifest.for_document(document)

    def test_short_document_and_resume(self):
        capture, document, chunks, manifest = self.source("A short note.\n")
        client = FakeClient()
        first = summarize_document(document, chunks, client, self.model, manifest, capture.run_dir)
        second = summarize_document(document, chunks, client, self.model, manifest, capture.run_dir)
        self.assertEqual(first, second)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(first.document_summary.chunk_ids, [chunks.chunks[0].chunk_id])

    def test_partial_sections_do_not_promote_document_summary(self):
        capture, document, chunks, manifest = self.source("\n\n".join(
            f"## Section {index}\n\n" + (f"Passage {index} has details. " * 170)
            for index in range(3)
        ))
        self.assertGreater(len(chunks.chunks), 1)
        client = FakeClient()
        partial = summarize_document(document, chunks, client, self.model, manifest, capture.run_dir, max_sections=1)
        self.assertEqual(partial.completed_sections, 1)
        self.assertIsNone(partial.document_summary)
        complete = summarize_document(document, chunks, client, self.model, manifest, capture.run_dir)
        self.assertEqual(complete.completed_sections, len(chunks.chunks))
        self.assertEqual(complete.document_summary.level, "document")
        self.assertEqual(complete.document_summary.chunk_ids, [chunk.chunk_id for chunk in chunks.chunks])
        self.assertEqual(sum(call["schema_name"] == "summary" for call in client.calls), len(chunks.chunks) + 2)


if __name__ == "__main__":
    unittest.main()
