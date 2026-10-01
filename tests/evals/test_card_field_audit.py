from __future__ import annotations

import unittest

from evals.card_field_audit import check_label, field_recall, numeric_screen, occupancy


class CardFieldAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.group = {"document_id": "doc", "run_id": "run", "source_sha256": "a" * 64,
                      "chunk_id": "chunk", "start_char": 10, "end_char": 30,
                      "group_sha256": "b" * 64}
        self.label = {**self.group, "fields": {
            "situation": {"status": "absent"},
            "action": {"status": "supported", "reason": "The source states the step."},
            "result": {"status": "supported", "reason": "The source reports the outcome."},
            "principle": {"status": "ambiguous", "reason": "Several interpretations fit."},
        }}
        self.card = {"document_id": "doc", "chunk_id": "chunk",
                     "evidence_span": {"start_char": 10, "end_char": 30},
                     "card_type": "example", "action": None, "result": None}

    def test_supported_empty_fields_are_missed_and_absent_is_not(self) -> None:
        report = field_recall([self.group], [self.label], [self.card])
        self.assertEqual(report["action"], {"missed": 1, "supported": 1})
        self.assertEqual(report["result"], {"missed": 1, "supported": 1})
        self.assertEqual(report["situation"], {"absent": 1})
        self.assertEqual(report["principle"], {"ambiguous": 1})

    def test_changed_source_or_span_rejects_label(self) -> None:
        for change in ({"source_sha256": "c" * 64}, {"start_char": 11},
                       {"group_sha256": "d" * 64}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "stale"):
                check_label({**self.label, **change}, self.group)

    def test_no_card_misses_supported_field(self) -> None:
        report = field_recall([self.group], [self.label], [])
        self.assertEqual(report["action"]["missed"], 1)

    def test_candidate_with_wrong_source_hash_cannot_earn_coverage(self) -> None:
        stale = {**self.card, "action": "stated step", "source_sha256": "c" * 64}
        with self.assertRaisesRegex(ValueError, "stale source"):
            field_recall([self.group], [self.label], [stale])

    def test_groups_from_one_chunk_cannot_cross_splits(self) -> None:
        other_group = {**self.group, "start_char": 40, "end_char": 60}
        other_label = {**self.label, "start_char": 40, "end_char": 60,
                       "split": "validation"}
        with self.assertRaisesRegex(ValueError, "cannot cross"):
            field_recall([self.group, other_group],
                         [{**self.label, "split": "development"}, other_label], [])

    def test_occupancy_is_separate_from_applicability(self) -> None:
        report = occupancy([{"card_type": "example", "action": None},
                            {"card_type": "tactic", "action": "Try a customer call"}])
        self.assertEqual(report["cards"], 2)
        self.assertEqual(report["all_null"], 1)
        self.assertEqual(report["fields"]["action"], 1)

    def test_numeric_screen_needs_supported_fields_across_core_dimensions(self) -> None:
        old = {field: {"supported": 10, "populated": 0} for field in
               ("situation", "action", "result", "principle")}
        candidate = {field: {"supported": 10, "populated": 6} for field in old}
        self.assertEqual(numeric_screen(old, candidate)["numeric_gate"], "provisional_pass")
        candidate["result"]["populated"] = 2
        self.assertEqual(numeric_screen(old, candidate)["numeric_gate"], "fail")


if __name__ == "__main__":
    unittest.main()
