from __future__ import annotations

import unittest
from pathlib import Path

from evals.answer_rubric import AnswerRubric, evidence_hits, load_rubric, rubric_fingerprint


class AnswerRubricTest(unittest.TestCase):
    def test_versioned_draft_keeps_legacy_fingerprint_and_cannot_compare(self):
        historical = load_rubric(Path('evals/answer_questions_v3.yaml'))
        self.assertEqual(rubric_fingerprint(historical), rubric_fingerprint(self.rubric))
        revised = load_rubric(Path('evals/answer_questions_v4.yaml'))
        self.assertEqual(revised.version, 4)
        self.assertEqual(revised.status, 'draft')
        self.assertGreater(sum(len(q.checks) for q in revised.questions), 70)
        q3 = revised.questions[2]
        wording = [check for check in q3.checks if check.id == 'Q3.C5']
        self.assertEqual(len(wording), 1)
        self.assertIn('website tests are acceptable', wording[0].criterion)
        self.assertEqual(wording[0].applicability, 'required')
        self.assertTrue(all(any(alt.start_char is not None for alt in check.evidence_alternatives)
                            for question in revised.questions for check in question.checks
                            if check.dimension in ('coverage', 'example', 'attribution')))
        with self.assertRaisesRegex(ValueError, 'freeze'):
            revised.require_comparison_ready({source.document_id: source.source_sha256
                                              for source in revised.source_revisions.values()})

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


    def setUp(self):
        self.rubric = load_rubric(Path("evals/answer_questions.yaml"))

    def test_frozen_has_eight_revision_bound_questions(self):
        self.assertEqual([item.id for item in self.rubric.questions], [f"Q{x}" for x in range(1, 9)])
        self.assertEqual(self.rubric.questions[6].answerability, "partial")
        self.assertEqual(self.rubric.questions[7].answerability, "partial")

    def test_draft_and_changed_revision_block_comparison(self):
        hashes = {item.document_id: item.source_sha256
                  for item in self.rubric.source_revisions.values()}
        self.rubric.require_comparison_ready(hashes)
        data = self.rubric.model_dump(mode="json")
        data["status"] = "draft"
        data["questions"][0]["review_status"] = "pending"
        draft = AnswerRubric.model_validate(data)
        with self.assertRaisesRegex(ValueError, "freeze"):
            draft.require_comparison_ready(hashes)
        data["status"] = "frozen"
        with self.assertRaisesRegex(ValueError, "approval"):
            AnswerRubric.model_validate(data)
        for question in data["questions"]:
            question["review_status"] = "approved"
        frozen = AnswerRubric.model_validate(data)
        frozen.require_comparison_ready(hashes)
        hashes[next(iter(hashes))] = "0" * 64
        with self.assertRaisesRegex(ValueError, "revision changed"):
            frozen.require_comparison_ready(hashes)

    def test_duplicate_id_or_missing_answerability_is_invalid(self):
        data = self.rubric.model_dump(mode="json")
        data["questions"][1]["id"] = "Q1"
        with self.assertRaises(ValueError):
            AnswerRubric.model_validate(data)
        data["questions"][1]["id"] = "Q2"
        del data["questions"][1]["answerability"]
        with self.assertRaises(ValueError):
            AnswerRubric.model_validate(data)

    def test_all_eight_user_approved_questions_are_frozen(self):
        self.assertEqual({question.id for question in self.rubric.questions
                          if question.review_status == "approved"}, {f"Q{x}" for x in range(1, 9)})
        self.assertEqual(self.rubric.status, "frozen")
        self.assertEqual(self.rubric.version, 3)

    def test_examples_are_required_but_suggestions_are_not_prescribed(self):
        data = self.rubric.model_dump(mode="json")
        data["questions"][0]["suggested_examples"] = []
        self.assertTrue(AnswerRubric.model_validate(data).examples_required)
        data["examples_required"] = False
        with self.assertRaises(ValueError):
            AnswerRubric.model_validate(data)
