"""Local human review of saved answer receipts against the three-dimension checklist.

`build` writes one self-contained private HTML page; `score` validates the judgments
that page exports and tallies them. Neither makes a model call.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path

from evals.answer_rubric import Checklist, load_rubric, rubric_fingerprint, score_checklist_ratings

TEMPLATE_PATH = Path(__file__).with_name("answer_review_template.html")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _question(checklist: Checklist, question_id: str):
    question = next((item for item in checklist.questions if item.id == question_id), None)
    if question is None:
        raise ValueError(f"unknown question {question_id}")
    return question


def review_entry(checklist: Checklist, question_id: str, receipt: dict, label: str) -> dict:
    """One answer to review; refuses receipts from other source revisions."""
    question = _question(checklist, question_id)
    result = receipt.get("result") or {}
    answer = result.get("answer")
    citations = result.get("citations")
    if receipt.get("status") != "completed" or not isinstance(answer, str) or not isinstance(citations, dict):
        raise ValueError(f"{label} is not a completed answer receipt")
    configuration = receipt.get("configuration") or {}
    observed = configuration.get("source_revisions") or {}
    for name in question.sources:
        source = checklist.source_revisions[name]
        if observed.get(source.document_id) != source.source_sha256:
            raise ValueError(f"{label} was produced from a different revision of {name}")
    return {"key": f"{question_id}:{_sha(answer)[:12]}", "label": label, "question_id": question_id,
            "answer": answer, "answer_sha256": _sha(answer),
            "prompt_version": configuration.get("prompt_version"), "model": configuration.get("model"),
            "citations": [{"id": key, "source": value.get("relative_path", "").rsplit("/", 1)[-1],
                           "text": value.get("text", "")} for key, value in citations.items()]}


def build_review(checklist: Checklist, receipts: list[tuple[str, str, dict]],
                 earlier: dict | None = None) -> dict:
    """`earlier` is a previous export; its ratings prefill answers and checks that still exist."""
    entries = [review_entry(checklist, question_id, receipt, label)
               for question_id, label, receipt in receipts]
    if len({entry["key"] for entry in entries}) != len(entries):
        raise ValueError("the same answer was supplied twice")
    known = {check.id for question in checklist.questions for check in question.checks}
    prefill = {item["key"]: {"ratings": {key: value for key, value in (item.get("ratings") or {}).items()
                                         if key in known},
                             "notes": item.get("notes") or {},
                             "out_of_scope_points": item.get("out_of_scope_points") or [],
                             "scope_reviewed": bool(item.get("scope_reviewed"))}
               for item in (earlier or {}).get("reviews", [])}
    return {"checklist_sha256": rubric_fingerprint(checklist), "checklist_status": checklist.status,
            "open_decisions": checklist.open_decisions, "prefill": prefill,
            "questions": {question.id: {"question": question.question,
                                        "in_scope": question.in_scope, "out_of_scope": question.out_of_scope,
                                        "checks": [check.model_dump(mode="json", exclude={"evidence_alternatives"})
                                                   for check in question.checks]}
                          for question in checklist.questions},
            "entries": entries}


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (TEMPLATE_PATH.read_text(encoding="utf-8")
            .replace("__STATUS__", html.escape(data["checklist_status"]))
            .replace("__DATA__", payload))


def score_review(checklist: Checklist, review: dict) -> dict:
    """Validate exported judgments against the current checklist and total them.

    Totals come first because that is what prompt and model changes are compared on:
    one total for all questions, one per dimension, then one per answer. Each main point
    of an answer outside the question's scope counts as one failed relevance check.
    """
    if review.get("checklist_sha256") != rubric_fingerprint(checklist):
        raise ValueError("review was made against a different checklist")
    dimensions = {name: {"pass": 0, "scored": 0} for name in ("coverage", "grounding", "example", "relevance")}
    answers = []
    for item in review.get("reviews", []):
        question = _question(checklist, item.get("question_id"))
        scores = score_checklist_ratings([check.model_dump() for check in question.checks],
                                         item.get("ratings") or {})
        outside = [point for point in item.get("out_of_scope_points") or [] if str(point).strip()]
        scope_reviewed = bool(item.get("scope_reviewed"))
        passed = scored = 0
        for name, tally in scores["dimensions"].items():
            dimensions[name]["pass"] += tally["pass"]
            dimensions[name]["scored"] += tally["scored"]
            passed += tally["pass"]
            scored += tally["scored"]
        dimensions["relevance"]["scored"] += len(outside)
        scored += len(outside)
        answers.append({"key": item.get("key"), "label": item.get("label"), "question_id": question.id,
                        "answer_sha256": item.get("answer_sha256"),
                        "pass": passed, "scored": scored, "percent": _percent(passed, scored),
                        "grounding_failures": scores["dimensions"]["grounding"]["fail"],
                        "out_of_scope_points": len(outside), "scope_reviewed": scope_reviewed,
                        "complete": scores["complete"] and scope_reviewed, "detail": scores})
    if not answers:
        raise ValueError("review contains no answers")
    for tally in dimensions.values():
        tally["percent"] = _percent(tally["pass"], tally["scored"])
    passed = sum(answer["pass"] for answer in answers)
    scored = sum(answer["scored"] for answer in answers)
    # A draft checklist can be used to try the rules out; its scores are not a baseline.
    return {"checklist_sha256": review["checklist_sha256"], "checklist_status": checklist.status,
            "provisional": checklist.status != "frozen",
            "complete": all(answer["complete"] for answer in answers),
            "total": {"pass": passed, "scored": scored, "percent": _percent(passed, scored),
                      "grounding_failures": sum(answer["grounding_failures"] for answer in answers),
                      "out_of_scope_points": sum(answer["out_of_scope_points"] for answer in answers)},
            "dimensions": dimensions, "answers": answers}


def _percent(passed: int, scored: int) -> float | None:
    return round(100 * passed / scored, 1) if scored else None


def _private_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to((Path.cwd() / ".local").resolve()) or resolved.exists():
        raise ValueError("choose a new output path under .local/")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def _receipts(arguments: list[str]) -> list[tuple[str, str, dict]]:
    """Accept `Q8=path/to/receipt.json` or a pilot manifest listing its completed attempts."""
    found = []
    for argument in arguments:
        if "=" in argument:
            question_id, path = argument.split("=", 1)
            found.append((question_id, path, json.loads(Path(path).read_text(encoding="utf-8"))))
            continue
        manifest = json.loads(Path(argument).read_text(encoding="utf-8"))
        for question_id, attempts in manifest.get("questions", {}).items():
            for attempt in attempts:
                if attempt.get("status") == "completed":
                    report = Path(attempt["report"])
                    label = "/".join(report.parts[-3:])
                    found.append((question_id, label, json.loads(report.read_text(encoding="utf-8"))))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "score"))
    parser.add_argument("inputs", nargs="+",
                        help="build: QID=receipt.json or pilot.json paths; score: the exported judgments file")
    parser.add_argument("--checklist", type=Path, default=Path("evals/answer_questions_v4_restructured.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prefill", type=Path, help="build: an earlier export whose ratings to carry over")
    args = parser.parse_args(argv)
    checklist = load_rubric(args.checklist)
    if not isinstance(checklist, Checklist):
        parser.error("this command needs the three-dimension checklist")
    target = _private_output(args.output)
    if args.action == "build":
        earlier = json.loads(args.prefill.read_text(encoding="utf-8")) if args.prefill else None
        data = build_review(checklist, _receipts(args.inputs), earlier)
        target.write_text(render_html(data), encoding="utf-8")
        print(json.dumps({"page": str(args.output), "answers": len(data["entries"]),
                          "checklist_status": checklist.status}))
        return 0
    review = json.loads(Path(args.inputs[0]).read_text(encoding="utf-8"))
    result = score_review(checklist, review)
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("provisional", "complete", "total", "dimensions")}))
    for answer in result["answers"]:
        print(answer["question_id"], f"{answer['pass']}/{answer['scored']}", answer["percent"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
