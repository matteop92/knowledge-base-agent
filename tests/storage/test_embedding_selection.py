import unittest
from types import SimpleNamespace

from evals.compare_embeddings import _coverage


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
