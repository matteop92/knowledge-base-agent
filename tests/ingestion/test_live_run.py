import math
import tempfile
import unittest
from pathlib import Path

from app.ingestion.chunking import structural_raw_chunks
from app.ingestion.live_run import estimate
from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source
from evals.compare_chunking import Prepared


class LiveRunEstimateTests(unittest.TestCase):
    def test_raw_budget_excludes_context_and_summaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "vault"
            root.mkdir()
            (root / "note.md").write_text("# Note\n\nA useful grounded fact.\n", encoding="utf-8")
            capture = capture_source(root, "note.md", Path(temporary) / "runs")
            document = route_source(capture)
            chunks = structural_raw_chunks(document)
            prepared = [Prepared(capture.run_dir, document, {"structural_raw": chunks}, [], {})]

            raw = estimate(prepared, None, None, False)
            with_summaries = estimate(
                prepared, None, "openai/gpt-5-mini", False,
            )
            self.assertEqual(raw["request_bound"],
                             1 + math.ceil(len(chunks.chunks) / 16))
            self.assertLess(raw["conservative_usd"], with_summaries["conservative_usd"])
            with self.assertRaisesRegex(ValueError, "context model is required"):
                estimate(prepared, None, None, True)
