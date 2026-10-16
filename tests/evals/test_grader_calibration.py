from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals.answer_grader import CHECKLIST_PROMPT, GRADE_SCHEMA_V3, GraderResponse, _source_labels, validate_judgments
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
        # Points the answer never made, blanks, and repeats are dropped and recorded.
        result = validate_judgments(grader_payload(), with_points(["A point the answer never made", " ",
                                                                   "Its detail.", "Its detail. "]))
        self.assertEqual(result["out_of_scope_points"], ["Its detail."])
        self.assertEqual(len(result["dropped_out_of_scope_points"]), 3)

    def test_schema_and_prompt_require_the_scope(self):
        self.assertIn("out_of_scope_points", GRADE_SCHEMA_V3["required"])
        self.assertEqual(GRADE_SCHEMA_V3["properties"]["version"]["enum"], [3])
        self.assertIn("out_of_scope_points", CHECKLIST_PROMPT)
        self.assertIn("version 3", CHECKLIST_PROMPT)


class SourceLabelTest(unittest.TestCase):
    def test_inspected_documents_carry_the_title_and_people_the_agent_saw(self):
        result = {"host_search": {"results": [
            {"document_id": "d1", "title": "Decisions", "people": ["Tim Ferriss"]},
            {"document_id": "d1", "title": "Later duplicate", "people": []}]}}
        inspected = {"E1.0": {"document_id": "d1", "relative_path": "Notes/Decisions.md"},
                     "E2.0": {"document_id": "d2", "relative_path": "Notes/Anger.md"}}
        self.assertEqual(_source_labels(result, inspected), {
            "d1": {"title": "Decisions", "people": ["Tim Ferriss"]},
            "d2": {"title": "Anger", "people": []}})
        self.assertIn("`sources`", CHECKLIST_PROMPT)


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


class CompletionBoundTest(unittest.TestCase):
    def test_spending_bound_equals_what_the_adapter_sends(self):
        from unittest.mock import patch

        from app.models.client import ModelSpec
        from app.models.openrouter import OpenRouterClient
        client = OpenRouterClient()
        reply = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        for model_id, effort in (("openai/gpt-5-mini", None), ("anthropic/any", None), ("anthropic/any", "medium"),
                                 ("deepseek/deepseek-v4.1-flash", None), ("qwen/qwen3.5-27b", None)):
            with self.subTest(model=model_id, effort=effort), patch.object(client, "_post", return_value=reply) as post:
                client.generate_json(model=ModelSpec(provider="openrouter", model_id=model_id, model_version="1"),
                                     system_prompt="s", input_text="i", schema_name="answer_grader_v4",
                                     json_schema={"type": "object"}, max_output_tokens=100, reasoning_effort=effort)
                sent = post.call_args.args[1]
                self.assertEqual(sent.get("max_tokens", sent.get("max_completion_tokens")),
                                 completion_bound(model_id, 100, effort))


class LiveGuardTest(unittest.TestCase):
    """The live run refuses to spend before any call when a guard fails."""

    def run_main(self, *args, pricing=("0.000001", "0.000002"), params=("response_format", "structured_outputs", "reasoning")):
        from unittest.mock import patch

        import evals.grade_answers as runner
        made = {}

        class Client:
            rate_limit_retries = 0
            billable_requests: list = []
            failed_outputs: list = []

            def __init__(self, **kwargs):
                made.update(kwargs)

            def require_zdr_route(self, model_id, tag, structured=False):
                return {"model_id": model_id, "tag": tag, "supported_parameters": list(params),
                        "pricing": {"prompt": pricing[0], "completion": pricing[1]}}

        graded = []
        with patch.object(runner, "OpenRouterClient", Client), \
                patch.object(runner, "load_receipts", return_value=[("Q5", "a", {})]), \
                patch.object(runner, "gold_sources", return_value={}), \
                patch.object(runner, "plan", return_value=[{"question_id": "Q5", "label": "a", "receipt": {}, "input_bytes": 1000}]), \
                patch.object(runner, "grade_answer", side_effect=lambda *a, **k: graded.append(1)), \
                patch("sys.stdout"), patch("sys.stderr"):
            try:
                runner.main(["Q5=a", "--model", "m/x", "--provider-tag", "t", *args])
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, graded, made

    def test_every_guard_stops_a_live_run_before_any_call(self):
        live = ["--live", "--output", ".local/never-written", "--max-usd-per-million", "10", "--max-usd", "100"]
        cases = {
            "missing caps": ["--live"],
            "price above ceiling": [*live[:4], "1", *live[5:]],
            "worst case above cap": [*live[:6], "0.0001"],
            "effort on a route without reasoning": [*live, "--reasoning-effort", "medium"],
        }
        for name, args in cases.items():
            params = ("response_format", "structured_outputs") if "reasoning" in name else \
                ("response_format", "structured_outputs", "reasoning")
            with self.subTest(name=name):
                code, graded, _ = self.run_main(*args, params=params)
                self.assertNotEqual(code, 0)
                self.assertEqual(graded, [])

    def test_dry_run_makes_no_call_and_the_route_is_private(self):
        code, graded, made = self.run_main("--dry-run")
        self.assertEqual((code, graded), (0, []))
        self.assertFalse(made["allow_live"])
        self.assertEqual({key: made["provider"][key] for key in ("zdr", "data_collection", "allow_fallbacks")},
                         {"zdr": True, "data_collection": "deny", "allow_fallbacks": False})


class GradeAnswerTest(unittest.TestCase):
    def call(self, data):
        from unittest.mock import patch

        import evals.answer_grader as grader
        from app.models.client import GenerationResult, ModelSpec
        model = ModelSpec(provider="openrouter", model_id="m/x", model_version="1")
        seen = {}

        class Client:
            def generate_json(self, **kwargs):
                seen.update(kwargs)
                return GenerationResult(data=data, model=model)

        with patch.object(grader, "prepare_evidence", return_value={**grader_payload(), "question_id": "Q1"}):
            return grader.grade_answer(CHECKLIST, "Q1", {}, {}, client=Client(), model=model,
                                       reasoning_effort="medium"), seen

    def test_identity_and_failure_detail_are_recorded(self):
        good = {**judgments().model_dump(mode="json"), "version": 3, "out_of_scope_points": []}
        result, seen = self.call(good)
        self.assertEqual((result["status"], result["grader_prompt_version"], result["reasoning_effort"]),
                         ("graded", "answer_grader_v4", "medium"))
        self.assertEqual(seen["reasoning_effort"], "medium")
        self.assertEqual(len(result["answer_sha256"]), 64)
        result, _ = self.call({**good, "version": 2})
        self.assertEqual((result["status"], result["detail"]),
                         ("ungraded", "grader answered with the wrong response version"))
        self.assertEqual(result["raw_output"]["version"], 2)


class UnreviewedAgreementTest(unittest.TestCase):
    def test_grader_unreviewed_checks_are_counted_apart_and_never_crash_the_score(self):
        checks = q5_checks()
        detail = next(check for check in checks if check.parent_id)
        mine = {check.id: ("not_applicable" if check.parent_id == detail.parent_id else "pass") for check in checks}
        mine[detail.parent_id] = "fail"
        theirs = {check.id: ("pass" if check.applicability != "optional" else "not_applicable") for check in checks}
        theirs[detail.id] = "unreviewed"
        result = compare(CHECKLIST, {"v13": human_export(mine)}, [grade(theirs)])
        coverage = result["agreement"]["development"]["coverage"]
        self.assertEqual(coverage["grader_unreviewed"], 1)
        self.assertEqual(coverage["false_pass"], 1)  # the primary the user failed
        self.assertIsNotNone(result["scores"]["v13"]["user"]["percent"])
