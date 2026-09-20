import tempfile
import unittest
from pathlib import Path

from app.ingestion.chunking import structural_raw_chunks
from app.ingestion.embeddings import EmbeddingSpec, embed_chunks
from app.ingestion.enrichment import ChunkContext
from app.ingestion.router import route_source
from app.ingestion.run_manifest import RunManifest
from app.ingestion.source_reader import capture_source
from app.models.client import EmbeddingResult
from app.models.client import ModelSpec, ModelUsage


class FakeClient:
    def __init__(self, vector=None):
        self.calls = []
        self.vector = vector or [0.2, 0.3]

    def embed(self, **kwargs):
        self.calls.append(kwargs)
        return EmbeddingResult(
            model=kwargs["model"], vectors=[self.vector for _ in kwargs["texts"]]
        )


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "knowledge"
        root.mkdir()
        self.root = root
        (root / "note.md").write_text("A useful thought.\n", encoding="utf-8")
        self.capture = capture_source(root, "note.md", Path(self.temp.name) / "runs")
        self.document = route_source(self.capture)
        self.chunks = structural_raw_chunks(self.document)
        self.manifest = RunManifest.for_document(self.document)

    def spec(self, model="one"):
        return EmbeddingSpec(
            provider="test", model=model, model_version="1", dimensions=2,
            distance_metric="cosine", input_recipe_version="raw-v1",
            chunking_version=self.chunks.chunking_version,
        )

    def test_ready_resume_and_version_coexistence(self):
        client = FakeClient()
        first = embed_chunks(self.document, self.chunks, client, self.spec(), self.manifest, self.capture.run_dir)
        again = embed_chunks(self.document, self.chunks, client, self.spec(), self.manifest, self.capture.run_dir)
        second = embed_chunks(self.document, self.chunks, client, self.spec("two"), self.manifest, self.capture.run_dir)
        self.assertTrue(first.ready and again.ready and second.ready)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len({*first.artifact_ids, *second.artifact_ids}), 2)
        self.assertEqual(len([r for r in self.manifest.artifacts.values() if r.status == "ready"]), 2)

    def test_invalid_vector_is_failed_and_never_ready(self):
        for vector in ([0.1], [float("nan"), 0.1]):
            with self.subTest(vector=vector):
                client = FakeClient(vector)
                with self.assertRaisesRegex(ValueError, "dimension or finite"):
                    embed_chunks(self.document, self.chunks, client, self.spec(), self.manifest, self.capture.run_dir)
                self.assertFalse(any(r.status == "ready" for r in self.manifest.artifacts.values()))

    def test_zero_batch_budget_leaves_incomplete_version(self):
        progress = embed_chunks(
            self.document, self.chunks, FakeClient(), self.spec(),
            self.manifest, self.capture.run_dir, max_batches=0,
        )
        self.assertFalse(progress.ready)
        self.assertEqual(progress.completed_chunks, 0)

    def test_partial_batches_resume_without_reembedding_ready_chunks(self):
        (self.root / "long.md").write_text("word " * 1200, encoding="utf-8")
        capture = capture_source(self.root, "long.md", Path(self.temp.name) / "runs")
        document = route_source(capture)
        chunks = structural_raw_chunks(document)
        self.assertGreater(len(chunks.chunks), 1)
        manifest = RunManifest.for_document(document)
        spec = EmbeddingSpec(
            provider="test", model="one", model_version="1", dimensions=2,
            distance_metric="cosine", input_recipe_version="raw-v1",
            chunking_version=chunks.chunking_version,
        )
        client = FakeClient()
        partial = embed_chunks(
            document, chunks, client, spec, manifest, capture.run_dir,
            batch_size=1, max_batches=1,
        )
        self.assertFalse(partial.ready)
        self.assertEqual(partial.completed_chunks, 1)
        complete = embed_chunks(document, chunks, client, spec, manifest, capture.run_dir, batch_size=1)
        self.assertTrue(complete.ready)
        self.assertEqual(len(client.calls), len(chunks.chunks))

    def test_contextual_input_has_separate_version(self):
        chunk = self.chunks.chunks[0]
        context = ChunkContext(
            document_id=self.document.document_id, run_id=self.document.run_id,
            source_sha256=self.document.source_sha256, chunk_id=chunk.chunk_id,
            source_spans=chunk.source_spans, context="A context sentence.",
            model=ModelSpec(provider="test", model_id="context", model_version="1"),
            prompt_version="chunk_context_v1", input_recipe_version="chunk-neighbors-v1",
            usage=ModelUsage(),
        )
        spec = EmbeddingSpec(
            provider="test", model="one", model_version="1", dimensions=2,
            distance_metric="cosine", input_recipe_version="contextualized-v1",
            context_prompt_version="chunk_context_v1",
            chunking_version=self.chunks.chunking_version,
        )
        client = FakeClient()
        result = embed_chunks(
            self.document, self.chunks, client, spec, self.manifest,
            self.capture.run_dir, contexts={chunk.chunk_id: context},
        )
        self.assertTrue(result.ready)
        self.assertTrue(client.calls[0]["texts"][0].startswith("A context sentence.\n\n"))
        with self.assertRaisesRegex(ValueError, "raw embeddings cannot use contexts"):
            embed_chunks(
                self.document, self.chunks, client, self.spec(),
                self.manifest, self.capture.run_dir,
                contexts={chunk.chunk_id: context},
            )


if __name__ == "__main__":
    unittest.main()
