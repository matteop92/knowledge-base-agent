import unittest

from app.storage.records import SearchHit
from evals.retrieval_trial import coverage


class RecallTest(unittest.TestCase):
    def test_wrong_revision_and_partial_overlap_do_not_cover_a_span(self):
        question = {"document_id": "d", "source_sha256": "a", "must_find": [
            {"start_char": 10, "end_char": 20}]}
        def hit(sha, start, end):
            return SearchHit(rank=1, score=1, document_id="d", run_id="r",
                             source_sha256=sha, root_alias="root", relative_path="note.md",
                             chunk_id="c", source_spans=[{"start_char": start, "end_char": end}],
                             raw_text="")
        self.assertEqual(coverage([hit("b", 0, 30), hit("a", 10, 19)], question, 5)["covered"], 0)
        self.assertEqual(coverage([hit("a", 0, 30)] * 2, question, 5)["covered"], 1)

    def test_misses_and_negative_questions_are_explicit(self):
        question = {"document_id": "d", "source_sha256": "a",
                    "must_find": [{"start_char": 10, "end_char": 20}]}
        self.assertEqual(coverage([], question, 5)["missing_span_indices"], [0])
        self.assertIsNone(coverage([], {**question, "must_find": []}, 5)["recall"])
