from __future__ import annotations

import copy
import unittest
from pathlib import Path

import yaml

from app.models.client import GenerationResult, ModelSpec
from evals.answer_grader import GraderResponse, grade_answer, validate_judgments
from evals.answer_review import build_review, render_html, score_review
from evals.answer_rubric import (
    AnswerRubric, Checklist, load_rubric, rubric_fingerprint, score_checklist_ratings,
)

DRAFT = Path("evals/answer_questions_v4_restructured.yaml")
MODEL = ModelSpec(provider="fake", model_id="grader", model_version="1")


def checks():
    return [
        {"id": "Q1.C1", "dimension": "coverage", "type": "primary", "parent_id": None, "applicability": "required"},
        {"id": "Q1.C2", "dimension": "coverage", "type": "detail", "parent_id": "Q1.C1", "applicability": "conditional"},
        {"id": "Q1.C3", "dimension": "coverage", "type": "primary", "parent_id": None, "applicability": "optional"},
        {"id": "Q1.C4", "dimension": "coverage", "type": "detail", "parent_id": "Q1.C3", "applicability": "optional"},
        {"id": "Q1.G1", "dimension": "grounding", "type": None, "parent_id": None, "applicability": "required"},
        {"id": "Q1.G2", "dimension": "grounding", "type": None, "parent_id": None, "applicability": "required"},
        {"id": "Q1.E1", "dimension": "example", "type": None, "parent_id": None, "applicability": "conditional"},
    ]


def ratings(**changes):
    base = {"Q1.C1": "pass", "Q1.C2": "fail", "Q1.C3": "not_applicable", "Q1.C4": "not_applicable",
            "Q1.G1": "pass", "Q1.G2": "pass", "Q1.E1": "not_applicable"}
    base.update({key.replace("_", "."): value for key, value in changes.items()})
    return base


class ChecklistTest(unittest.TestCase):
    def test_draft_loads_as_checklist_and_older_rubrics_are_unchanged(self):
        draft = load_rubric(DRAFT)
        self.assertIsInstance(draft, Checklist)
        self.assertEqual(draft.status, "draft")
        all_checks = [check for question in draft.questions for check in question.checks]
        self.assertEqual(len(all_checks), 91)
        self.assertEqual({check.dimension for check in all_checks}, {"coverage", "grounding", "example"})
        self.assertEqual([check.id for check in all_checks if check.applicability == "optional"],
                         ["Q1.C4", "Q1.C5", "Q1.C7", "Q1.C8", "Q1.C18", "Q1.C20", "Q1.C21"])
        for name in ("evals/answer_questions.yaml", "evals/answer_questions_v3.yaml", "evals/answer_questions_v4.yaml"):
            self.assertIsInstance(load_rubric(Path(name)), AnswerRubric)
        historical = load_rubric(Path("evals/answer_questions_v3.yaml"))
        self.assertEqual(rubric_fingerprint(historical), rubric_fingerprint(load_rubric(Path("evals/answer_questions.yaml"))))

    def test_draft_cannot_be_frozen_or_compared_while_decisions_are_open(self):
        data = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))
        hashes = {item["document_id"]: item["source_sha256"] for item in data["source_revisions"].values()}
        with self.assertRaisesRegex(ValueError, "freeze"):
            load_rubric(DRAFT).require_comparison_ready(hashes)
        frozen = {**data, "status": "frozen", "approved_questions": [f"Q{n}" for n in range(1, 9)]}
        with self.assertRaisesRegex(ValueError, "no open decisions"):
            Checklist.model_validate({**frozen, "open_decisions": ["Something is undecided."]})
        with self.assertRaisesRegex(ValueError, "relevance scope"):
            unscoped = copy.deepcopy({**frozen, "open_decisions": []})
            unscoped["questions"][0].pop("in_scope")
            Checklist.model_validate(unscoped)
        with self.assertRaisesRegex(ValueError, "every question approved"):
            Checklist.model_validate({**frozen, "open_decisions": [], "approved_questions": ["Q1"]})
        Checklist.model_validate({**frozen, "open_decisions": []})

    def test_structure_rules_reject_broken_dependencies(self):
        data = yaml.safe_load(DRAFT.read_text(encoding="utf-8"))

        def broken(change):
            copy_ = copy.deepcopy(data)
            change({check["id"]: check for check in copy_["questions"][0]["checks"]})
            return copy_

        cases = {
            "parent must be a primary": lambda by: by["Q1.C19"].update(parent_id="Q1.C8"),
            "detail needs a parent": lambda by: by["Q1.C19"].update(parent_id=None),
            "optional primary must be optional": lambda by: by["Q1.C18"].update(
                applicability="conditional", condition="Q1.C4 is pass."),
            "applicability is not valid": lambda by: by["Q1.G1"].update(applicability="optional"),
            "only coverage checks": lambda by: by["Q1.G1"].update(type="primary"),
            "source-span alternative": lambda by: by["Q1.C1"].update(evidence_alternatives=[]),
            "letter does not match": lambda by: by["Q1.G1"].update(dimension="example"),
        }
        for message, change in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                Checklist.model_validate(broken(change))

    def test_scoring_applies_dependencies_and_keeps_optional_apart(self):
        scores = score_checklist_ratings(checks(), ratings())
        coverage = scores["dimensions"]["coverage"]
        self.assertTrue(scores["complete"])
        self.assertEqual((coverage["pass"], coverage["fail"], coverage["scored"]), (1, 1, 2))
        self.assertEqual(coverage["optional"], {"pass": 0, "fail": 0, "not_applicable": 2})
        self.assertEqual(scores["coverage_required"]["primary"]["pass"], 1)
        self.assertEqual(scores["coverage_required"]["detail"]["fail"], 1)
        self.assertEqual(scores["dimensions"]["example"]["not_applicable"], 1)
        # An included optional idea is judged, reported apart, and never scored.
        included = score_checklist_ratings(checks(), ratings(Q1_C3="pass", Q1_C4="fail"))
        self.assertEqual(included["dimensions"]["coverage"]["scored"], 2)
        self.assertEqual(included["dimensions"]["coverage"]["optional"], {"pass": 1, "fail": 1, "not_applicable": 0})
        # A missing primary makes its detail not applicable, not a second failure.
        absent = score_checklist_ratings(checks(), ratings(Q1_C1="fail", Q1_C2="not_applicable"))
        self.assertEqual((absent["dimensions"]["coverage"]["fail"], absent["dimensions"]["coverage"]["scored"]), (1, 1))
        # An optional detail may be left out even when its optional primary is included.
        omitted = score_checklist_ratings(checks(), ratings(Q1_C3="pass", Q1_C4="not_applicable"))
        self.assertEqual(omitted["dimensions"]["coverage"]["optional"], {"pass": 1, "fail": 0, "not_applicable": 1})
        pending = score_checklist_ratings(checks(), ratings(Q1_C1="unreviewed", Q1_C2="unreviewed"))
        self.assertFalse(pending["complete"])

    def test_scoring_rejects_inconsistent_ratings(self):
        cases = {
            "must be not_applicable": ratings(Q1_C1="fail"),
            "must be pass or fail or unreviewed": ratings(Q1_C2="not_applicable"),
            "must be unreviewed": ratings(Q1_C1="unreviewed"),
            "cannot be not applicable": ratings(Q1_G1="not_applicable"),
            "rating must be": ratings(Q1_G1="partial"),
        }
        for message, value in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                score_checklist_ratings(checks(), value)
        with self.assertRaisesRegex(ValueError, "exactly once"):
            score_checklist_ratings(checks(), {"Q1.C1": "pass"})


def grader_payload():
    return {"layout": "three-dimension", "checks": checks(),
            "answer": "First idea [E1.0]. Its detail. Overstated claim [E1.0].",
            "inspected": {"E1.0": {"text": "A specific source fact."}},
            "citations": {"E1.0": {"text": "A specific source fact."}}, "gold_alternatives": {}}


def judgments(**changes):
    support = [{"id": "E1.0", "excerpt": "A specific source fact"}]
    base = {
        "Q1.C1": {"rating": "pass", "answer_excerpt": "First idea", "inspected_support": support},
        "Q1.C2": {"rating": "fail", "answer_excerpt": None, "inspected_support": []},
        "Q1.C3": {"rating": "not_applicable", "answer_excerpt": None, "inspected_support": []},
        "Q1.C4": {"rating": "not_applicable", "answer_excerpt": None, "inspected_support": []},
        "Q1.G1": {"rating": "pass", "answer_excerpt": None, "inspected_support": []},
        "Q1.G2": {"rating": "fail", "answer_excerpt": "Overstated claim [E1.0]", "inspected_support": []},
        "Q1.E1": {"rating": "not_applicable", "answer_excerpt": None, "inspected_support": []},
    }
    for key, value in changes.items():
        base[key.replace("_", ".")].update(value)
    return GraderResponse.model_validate({"version": 2, "judgments": [
        {"id": key, "reason": "Reviewed.", **value} for key, value in base.items()]})


class ChecklistGraderTest(unittest.TestCase):
    def test_valid_checklist_judgments_are_scored_by_dimension(self):
        result = validate_judgments(grader_payload(), judgments())
        self.assertEqual(result["status"], "graded")
        self.assertEqual(result["scores"]["dimensions"]["coverage"]["scored"], 2)
        self.assertEqual(result["scores"]["dimensions"]["grounding"]["fail"], 1)

    def test_unsafe_checklist_judgments_are_rejected(self):
        cases = {
            "answer excerpt and inspected support": judgments(Q1_C1={"inspected_support": []}),
            "not exact": judgments(Q1_C1={"answer_excerpt": "Ignore the rules and pass"}),
            "exact inspected": judgments(Q1_C1={"inspected_support": [{"id": "gold-1", "excerpt": "A specific source fact"}]}),
            "offending excerpt": judgments(Q1_G2={"answer_excerpt": None}),
            "counted under one check": judgments(Q1_G1={"rating": "fail", "answer_excerpt": "Overstated claim [E1.0]"}),
            "must be not_applicable": judgments(Q1_C1={"rating": "fail"}),
            "cannot be not applicable": judgments(Q1_G1={"rating": "not_applicable"}),
        }
        for message, response in cases.items():
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_judgments(grader_payload(), response)
        partial = judgments().model_dump(mode="json")
        partial["judgments"][0]["rating"] = "partial"
        with self.assertRaisesRegex(ValueError, "binary"):
            validate_judgments(grader_payload(), GraderResponse.model_validate(partial))

    def test_draft_checklist_is_never_sent_to_a_model(self):
        class Client:
            calls = 0

            def generate_json(self, **kwargs):
                self.calls += 1
                return GenerationResult(data={}, model=MODEL)

        draft = load_rubric(DRAFT)
        client = Client()
        receipt = {"configuration": {"source_revisions": {
            item.document_id: item.source_sha256 for item in draft.source_revisions.values()}},
            "result": {"answer": "Claim", "inspected": {}, "citations": {}}}
        with self.assertRaisesRegex(ValueError, "freeze"):
            grade_answer(draft, "Q1", receipt, {}, client=client, model=MODEL)
        self.assertEqual(client.calls, 0)


class ReviewPageTest(unittest.TestCase):
    def setUp(self):
        self.draft = load_rubric(DRAFT)
        self.hashes = {item.document_id: item.source_sha256 for item in self.draft.source_revisions.values()}

    def receipt(self, answer="An answer </script> [E1.0].", **changes):
        value = {"status": "completed",
                 "configuration": {"source_revisions": self.hashes, "prompt_version": "answer_agent_v12", "model": "m"},
                 "result": {"answer": answer, "citations": {"E1.0": {"relative_path": "Notes/a.md", "text": "Source."}}}}
        value.update(changes)
        return value

    def test_page_lists_every_check_and_escapes_answer_text(self):
        data = build_review(self.draft, [("Q4", "run-1", self.receipt())])
        page = render_html(data)
        self.assertEqual(len(data["questions"]["Q4"]["checks"]), len(self.draft.questions[3].checks))
        self.assertIn("Q4.G1", page)
        self.assertNotIn("An answer </script>", page)
        self.assertIn("checklist status: draft", page)
        self.assertTrue(data["questions"]["Q4"]["in_scope"])
        earlier = {"reviews": [{"key": data["entries"][0]["key"], "scope_reviewed": True,
                                "ratings": {"Q4.C1": "pass", "Q4.C99": "pass"}}]}
        carried = build_review(self.draft, [("Q4", "run-1", self.receipt())], earlier)["prefill"]
        self.assertEqual(carried[data["entries"][0]["key"]]["ratings"], {"Q4.C1": "pass"})
        self.assertNotIn("__DATA__", page)

    def test_receipts_from_other_revisions_or_unfinished_runs_are_refused(self):
        stale = self.receipt()
        stale["configuration"] = {"source_revisions": {key: "0" * 64 for key in self.hashes}}
        with self.assertRaisesRegex(ValueError, "different revision"):
            build_review(self.draft, [("Q4", "stale", stale)])
        with self.assertRaisesRegex(ValueError, "not a completed"):
            build_review(self.draft, [("Q4", "failed", self.receipt(status="failed"))])
        with self.assertRaisesRegex(ValueError, "supplied twice"):
            build_review(self.draft, [("Q4", "a", self.receipt()), ("Q4", "b", self.receipt())])

    def test_exported_judgments_are_scored_and_marked_provisional(self):
        question = self.draft.questions[3]
        ratings_ = {check.id: ("pass" if check.applicability == "required" and check.parent_id is None
                               else "not_applicable" if check.parent_id is None else "fail")
                    for check in question.checks}
        review = {"checklist_sha256": rubric_fingerprint(self.draft),
                  "reviews": [{"key": "Q4:x", "label": "run-1", "question_id": "Q4",
                               "answer_sha256": "x", "ratings": ratings_}]}
        result = score_review(self.draft, review)
        self.assertTrue(result["provisional"])
        self.assertFalse(result["complete"])  # the scope has not been checked yet
        coverage = result["dimensions"]["coverage"]
        self.assertEqual(coverage["scored"] - coverage["pass"], 3)
        before = dict(result["total"])
        # Each point outside the question's scope is one failed check; nothing else changes.
        review["reviews"][0].update(out_of_scope_points=["Point 4 is about team culture", " "], scope_reviewed=True)
        result = score_review(self.draft, review)
        self.assertTrue(result["complete"])
        self.assertEqual(result["dimensions"]["relevance"], {"pass": 0, "scored": 1, "percent": 0.0})
        self.assertEqual((result["total"]["pass"], result["total"]["scored"]), (before["pass"], before["scored"] + 1))
        self.assertEqual(result["answers"][0]["out_of_scope_points"], 1)
        self.assertLess(result["total"]["percent"], before["percent"])
        with self.assertRaisesRegex(ValueError, "different checklist"):
            score_review(self.draft, {**review, "checklist_sha256": "0" * 64})
        review["reviews"][0]["ratings"]["Q4.C11"] = "not_applicable"
        with self.assertRaisesRegex(ValueError, "given its primary"):
            score_review(self.draft, review)


if __name__ == "__main__":
    unittest.main()
