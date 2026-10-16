from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals.answer_grader import CHECKLIST_PROMPT, GRADE_SCHEMA_V3, GraderResponse, validate_judgments
from evals.answer_rubric import load_rubric
from evals.grade_answers import completion_bound, gold_sources, receipts_from_judgments
from evals.grader_agreement import _checks, answer_score, compare
from tests.evals.test_answer_checklist import grader_payload, judgments

CHECKLIST = load_rubric(Path("evals/answer_questions_v4_restructured.yaml"))


def with_points(points):
    return GraderResponse.model_validate({**judgments().model_dump(mode="json"), "version": 3,
                                          "out_of_scope_points": points})


class RelevanceTest(unittest.TestCase):
    def test_off_scope_points_are_exact_distinct_answer_excerpts(self):
        result = validate_judgments(grader_payload(), with_points(["Its detail."]))
        self.assertEqual(result["out_of_scope_points"], ["Its detail."])
        self.assertEqual(validate_judgments(grader_payload(), with_points([]))["out_of_scope_points"], [])
        for points, message in ((["A point the answer never made"], "exact answer excerpt"),
                                ([" "], "exact answer excerpt"),
                                (["Its detail.", "Its detail. "], "listed twice")):
            with self.subTest(points=points), self.assertRaisesRegex(ValueError, message):
                validate_judgments(grader_payload(), with_points(points))

    def test_schema_and_prompt_require_the_scope(self):
        self.assertIn("out_of_scope_points", GRADE_SCHEMA_V3["required"])
        self.assertEqual(GRADE_SCHEMA_V3["properties"]["version"]["enum"], [3])
        self.assertIn("out_of_scope_points", CHECKLIST_PROMPT)
        self.assertIn("version 3", CHECKLIST_PROMPT)


class RunnerTest(unittest.TestCase):
    def test_completion_bound_matches_the_adapter(self):
        self.assertEqual(completion_bound("openai/gpt-5-mini", 100, None), 400)
        self.assertEqual(completion_bound("anthropic/any", 100, None), 200)
        self.assertEqual(completion_bound("openai/other", 100, "low"), 400)

    def test_receipts_come_from_a_review_export_and_snapshots_must_match(self):
        with tempfile.TemporaryDirectory() as folder:
            export = Path(folder, "review.json")
            export.write_text(json.dumps({"reviews": [{"question_id": "Q5", "label": "run/Q5/attempt-1.json"}]}))
            self.assertEqual(receipts_from_judgments(str(export)), ["Q5=.local/run/Q5/attempt-1.json"])
            Path(folder, "source.md").write_text("not a pinned revision")
            with self.assertRaisesRegex(ValueError, "snapshot is missing"):
                gold_sources(CHECKLIST, str(Path(folder, "*.md")))


def q5_checks():
    return next(question for question in CHECKLIST.questions if question.id == "Q5").checks


def human_export(ratings, points=()):
    return {"reviews": [{"question_id": "Q5", "label": "run/Q5.json", "answer_sha256": "a" * 64,
                         "ratings": ratings, "out_of_scope_points": list(points)}]}


def grade(ratings, *, repeat=1, points=(), label=".local/run/Q5.json", sha="a" * 64):
    return {"status": "graded", "label": label, "repeat": repeat, "answer_sha256": sha,
            "out_of_scope_points": list(points),
            "judgments": [{"id": key, "rating": value, "reason": "r"} for key, value in ratings.items()]}


class AgreementTest(unittest.TestCase):
    def setUp(self):
        self.mine = {check.id: ("not_applicable" if check.applicability == "conditional" and check.parent_id
                                else "pass") for check in q5_checks()}
        self.mine["Q5.E1"] = "pass"

    def test_agreement_false_passes_scores_and_repeats(self):
        mine = {**self.mine, "Q5.G1": "fail"}
        details = [check.id for check in q5_checks() if check.parent_id]
        mine.update({key: "pass" for key in details})
        theirs = {**mine, "Q5.G1": "pass", "Q5.C1": "fail"}
        theirs.update({key: "not_applicable" for key in details if
                       next(c for c in q5_checks() if c.id == key).parent_id == "Q5.C1"})
        result = compare(CHECKLIST, {"v13": human_export(mine, ["off"])},
                         [grade(theirs), grade(mine, repeat=2, points=["x"]),
                          {"status": "ungraded", "label": "z", "repeat": 1, "reason": "ValueError"}])
        grounding = result["agreement"]["development"]["grounding"]
        self.assertEqual((grounding["false_pass"], grounding["agree"], grounding["total"]), (1, 5, 6))
        self.assertEqual(result["agreement"]["development"]["coverage"]["false_fail"], 1)
        self.assertIn("run/Q5.json Q5.G1", " ".join(f"{d['label']} {d['check']}" for d in result["disagreements"]))
        self.assertIn(".local/run/Q5.json Q5.G1", result["unstable_checks"])
        self.assertEqual(len(result["ungraded"]), 1)
        self.assertEqual([(row["user"], row["grader"]) for row in result["off_scope"]], [(1, 0), (1, 1)])
        checks = _checks(CHECKLIST, "Q5")
        # The user's answer is counted once per graded repeat, with its one off-scope point.
        self.assertEqual(result["scores"]["v13"]["user"]["scored"], 2 * answer_score(checks, mine, 1)[1])
        self.assertEqual(result["scores"]["v13"]["grader"]["scored"],
                         answer_score(checks, theirs, 0)[1] + answer_score(checks, mine, 1)[1])

    def test_planted_faults_must_be_caught(self):
        planted = [{"label": "p", "expect_any_fail": ["Q5.G1", "Q5.G2"], "out_of_scope_at_least": 1}]
        caught = compare(CHECKLIST, {}, [grade({**self.mine, "Q5.G2": "fail"}, label="p", sha="b" * 64,
                                               points=["x"])], planted)
        missed = compare(CHECKLIST, {}, [grade(self.mine, label="p", sha="b" * 64)], planted)
        self.assertTrue(caught["planted"][0]["caught"])
        self.assertEqual(caught["unmatched"], [])
        self.assertEqual(len(missed["planted"][0]["missed"]), 2)


if __name__ == "__main__":
    unittest.main()
