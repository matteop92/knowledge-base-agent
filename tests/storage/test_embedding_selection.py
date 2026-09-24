import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.ingestion.preview import preview_markdown
from app.ingestion.run_manifest import RunManifest
from app.models.openrouter import OpenRouterClient
from app.storage import StorageError
from evals.compare_embeddings import _coverage, _select_live_manifest


class EmbeddingSelectionMetricsTest(unittest.TestCase):
    def test_overlap_counts_one_required_span(self):
        labels = [{"overlap_a", "overlap_b"}, {"second_span"}]
        hits = [SimpleNamespace(chunk_id="overlap_a"), SimpleNamespace(chunk_id="overlap_b")]
        result = _coverage(hits, labels)
        self.assertEqual(result["5"]["covered"], 1)
        self.assertFalse(result["5"]["complete"])
        hits.append(SimpleNamespace(chunk_id="second_span"))
        self.assertEqual(_coverage(hits, labels)["5"]["covered"], 2)
        self.assertTrue(_coverage(hits, labels)["5"]["complete"])

    def test_live_selection_is_saved_before_embedding_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / "vault"
            source.mkdir()
            (source / "note.md").write_text("# Note\n\nA grounded claim.\n", encoding="utf-8")
            preview = preview_markdown(source, "note.md", base / "runs")
            run_dir = base / "runs" / preview["run_id"]
            client = OpenRouterClient(allow_live=True, request_budget=7,
                                      estimated_input_token_budget=900, api_key="unused-test-key")
            _select_live_manifest(run_dir, client)
            saved = RunManifest.load(run_dir)
            self.assertTrue(saved.live_call_selected)
            self.assertEqual(saved.request_budget, 7)
            self.assertEqual(saved.estimated_input_token_budget, 900)
            with self.assertRaises(StorageError):
                _select_live_manifest(run_dir, OpenRouterClient(api_key="unused-test-key"))
