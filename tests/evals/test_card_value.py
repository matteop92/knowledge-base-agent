from __future__ import annotations

import unittest
from pathlib import Path

from evals.answer_rubric import load_rubric
from evals.card_value import evidence_hits


class CardValueTest(unittest.TestCase):
    def test_evidence_counts_only_overlapping_passages_of_the_same_revision(self):
        rubric = load_rubric(Path("evals/answer_questions_v4.yaml"))
        question = rubric.questions[0]
        check = next(item for item in question.checks
                     if any(alt.start_char is not None for alt in item.evidence_alternatives))
        alternative = next(alt for alt in check.evidence_alternatives if alt.start_char is not None)
        source = rubric.source_revisions[alternative.source]
        passage = {"document_id": source.document_id, "source_sha256": source.source_sha256,
                   "start_char": alternative.start_char + 1, "end_char": alternative.start_char + 5}
        found, bound = evidence_hits(rubric, question.id, [passage])
        self.assertGreaterEqual(found, 1)
        self.assertGreater(bound, found)
        self.assertEqual(evidence_hits(rubric, question.id, [{**passage, "source_sha256": "0" * 64}])[0], 0)
        self.assertEqual(evidence_hits(rubric, question.id, [{**passage, "start_char": 0, "end_char": 1}])[0], 0)


if __name__ == "__main__":
    unittest.main()
