import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ingestion.chunking import structural_raw_chunks
from app.ingestion.router import route_source
from app.ingestion.run_manifest import RunManifest
from app.ingestion.source_reader import capture_source
from app.ingestion.summary import summarize_document
from app.models.client import GenerationResult, ModelClientError, ModelSpec
from app.models.openrouter import OpenRouterClient


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

    def test_v2_prompt_is_versioned_and_resumable(self):
        capture, document, chunks, manifest = self.source("A short note.\n")
        client = FakeClient()
        first = summarize_document(document, chunks, client, self.model, manifest,
                                   capture.run_dir, prompt_family="v2")
        second = summarize_document(document, chunks, client, self.model, manifest,
                                    capture.run_dir, prompt_family="v2")
        self.assertEqual(first, second)
        self.assertEqual(first.document_summary.prompt_version, "document_summary_v2")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["max_output_tokens"], 700)

    def test_v3_document_reads_all_section_summaries_directly(self):
        capture, document, chunks, manifest = self.source("\n\n".join(
            f"## Section {index}\n\n" + (f"Passage {index} has details. " * 170)
            for index in range(3)
        ))
        client = FakeClient()
        progress = summarize_document(document, chunks, client, self.model,
                                      manifest, capture.run_dir,
                                      prompt_family="v3")
        self.assertIsNotNone(progress.document_summary)
        self.assertEqual(progress.document_summary.prompt_version, "document_summary_v3")
        self.assertEqual(len(client.calls), len(chunks.chunks) + 1)
        self.assertIn("Section 1:", client.calls[-1]["input_text"])

    def test_v6_carries_source_author_into_sections_and_document(self):
        capture, document, chunks, manifest = self.source("\n\n".join(
            f"## Section {index}\n\n" + (f"Passage {index} has details. " * 170)
            for index in range(3)
        ))
        metadata = document.metadata.model_copy(update={"authors": ["Derek Sivers"]})
        document = document.model_copy(update={"metadata": metadata})
        client = FakeClient()
        progress = summarize_document(document, chunks, client, self.model,
                                      manifest, capture.run_dir, prompt_family="v6")
        self.assertIsNotNone(progress.document_summary)
        self.assertEqual(len(client.calls), len(chunks.chunks) + 1)
        self.assertTrue(all("Source author metadata: Derek Sivers" in call["input_text"]
                            for call in client.calls))
        self.assertEqual(client.calls[-1]["max_output_tokens"], 1200)

    def test_v9_reuses_v8_sections_and_only_resynthesizes_document(self):
        capture, document, chunks, manifest = self.source("\n\n".join(
            f"## Section {index}\n\n" + (f"Passage {index} has details. " * 170)
            for index in range(3)
        ))
        client = FakeClient()
        summarize_document(document, chunks, client, self.model, manifest,
                           capture.run_dir, prompt_family="v8")
        initial_calls = len(client.calls)
        progress = summarize_document(document, chunks, client, self.model, manifest,
                                      capture.run_dir, prompt_family="v9")
        self.assertEqual(len(client.calls), initial_calls + 1)
        self.assertEqual(progress.document_summary.prompt_version, "document_summary_v9")
        self.assertEqual(client.calls[-1]["max_output_tokens"], 900)

    def test_v15_reuses_v14_sections_and_only_resynthesizes_document(self):
        capture, document, chunks, manifest = self.source("\n\n".join(
            f"## Section {index}\n\n" + (f"Passage {index} has details. " * 170)
            for index in range(3)
        ))
        client = FakeClient()
        summarize_document(document, chunks, client, self.model, manifest,
                           capture.run_dir, prompt_family="v14")
        initial_calls = len(client.calls)
        progress = summarize_document(document, chunks, client, self.model, manifest,
                                      capture.run_dir, prompt_family="v15")
        self.assertEqual(len(client.calls), initial_calls + 1)
        self.assertEqual(progress.document_summary.prompt_version, "document_summary_v15")
        self.assertEqual(client.calls[-1]["max_output_tokens"], 900)

    def test_section_rejects_unstated_trial_claim(self):
        capture, document, chunks, manifest = self.source("\n\n".join(
            f"## Section {index}\n\n" + (f"Passage {index} has details. " * 170)
            for index in range(3)
        ))
        client = FakeClient()
        with patch.object(client, "generate_json", return_value=GenerationResult(
            data={"summary": "These anecdotes were not randomized trials."},
            model=self.model,
        )):
            with self.assertRaisesRegex(ValueError, "unsupported evidence-method"):
                summarize_document(document, chunks, client, self.model, manifest,
                                   capture.run_dir, prompt_family="v14")
        self.assertTrue(any(record.error_code == "ValueError"
                            for record in manifest.artifacts.values()))

    def test_live_client_with_default_manifest_cannot_summarize(self):
        capture, document, chunks, manifest = self.source("A short note.\n")
        client = OpenRouterClient(
            allow_live=True, api_key="test-key", request_budget=1,
            estimated_input_token_budget=100,
        )
        with patch.object(client, "generate_json") as generate:
            with self.assertRaisesRegex(ModelClientError, "do not match"):
                summarize_document(document, chunks, client, self.model, manifest, capture.run_dir)
            generate.assert_not_called()

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
