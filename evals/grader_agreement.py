"""Compare model grades with the user's hand ratings on the frozen checklist.

Reports, for the development and held-out question halves separately: agreement per
dimension, false passes and false fails check by check, overall scores per version,
off-scope counts, rating changes across repeats, and ungraded replies. Planted-error
receipts are checked against their expected failures. No model call is made.
"""

from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

from evals.answer_review import _private_output
from evals.answer_rubric import Checklist, load_rubric, score_checklist_ratings

CHECKLIST = Path("evals/answer_questions_v4_restructured.yaml")
DEV = ("Q1", "Q3", "Q5", "Q7")
# Both ratings of Q6.C3 were made before it was reworded on 2026-10-15.
EXCLUDED = ("Q6.C3",)


def _checks(checklist: Checklist, question_id: str) -> list[dict]:
    question = next(item for item in checklist.questions if item.id == question_id)
    return [{"id": check.id, "dimension": check.dimension, "type": check.type,
             "parent_id": check.parent_id, "applicability": check.applicability} for check in question.checks]


def _percent(passed: int, scored: int) -> float | None:
    return round(100 * passed / scored, 1) if scored else None


def answer_score(checks: list[dict], ratings: dict[str, str], off_scope: int) -> tuple[int, int]:
    """Passed and scored checks for one answer; each off-scope point is one failed check."""
    scores = score_checklist_ratings(checks, ratings)
    passed = sum(item["pass"] for item in scores["dimensions"].values())
    scored = sum(item["scored"] for item in scores["dimensions"].values())
    return passed, scored + off_scope


def compare(checklist: Checklist, human: dict[str, dict], grades: list[dict],
            planted: list[dict] | None = None, dev: tuple[str, ...] = DEV) -> dict:
    """`human` maps a version name to a review export; `grades` are grade receipts."""
    known = {check.id for question in checklist.questions for check in question.checks}
    reviews = {}
    for version, export in human.items():
        for item in export["reviews"]:
            ratings = {key: value for key, value in item["ratings"].items() if key in known}
            reviews[item["answer_sha256"]] = {**item, "ratings": ratings, "version": version}
    planted_labels = {case["label"] for case in planted or []}
    half = lambda question_id: "development" if question_id in dev else "held_out"  # noqa: E731
    agreement = defaultdict(lambda: defaultdict(lambda: {"agree": 0, "total": 0, "false_pass": 0, "false_fail": 0,
                                                         "grader_unreviewed": 0}))
    disagreements, ungraded, unmatched = [], [], []
    scores = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # version -> source -> [passed, scored]
    off_scope = []
    by_check = defaultdict(set)  # (answer, check) -> ratings across repeats
    for grade in grades:
        if grade.get("status") != "graded":
            ungraded.append({"label": grade.get("label"), "repeat": grade.get("repeat"), "reason": grade.get("reason")})
            continue
        review = reviews.get(grade.get("answer_sha256"))
        given = {item["id"]: item for item in grade["judgments"]}
        for check_id, item in given.items():
            by_check[(grade["label"], check_id)].add(item["rating"])
        if review is None:
            if grade.get("label") not in planted_labels:
                unmatched.append(grade.get("label"))
            continue
        question_id, checks = review["question_id"], _checks(checklist, review["question_id"])
        for check in checks:
            if check["applicability"] == "optional" or check["id"] in EXCLUDED:
                continue
            mine, theirs = review["ratings"][check["id"]], given[check["id"]]["rating"]
            cell = agreement[half(question_id)][check["dimension"]]
            if theirs == "unreviewed":
                cell["grader_unreviewed"] += 1  # reported apart, never counted as agreement
                continue
            cell["total"] += 1
            if mine == theirs:
                cell["agree"] += 1
                continue
            kind = "false_pass" if theirs == "pass" and mine == "fail" else (
                "false_fail" if theirs == "fail" and mine == "pass" else "other")
            if kind != "other":
                cell[kind] += 1
            disagreements.append({"half": half(question_id), "label": review["label"], "repeat": grade.get("repeat"),
                                  "check": check["id"], "dimension": check["dimension"], "kind": kind,
                                  "user": mine, "grader": theirs, "reason": given[check["id"]].get("reason")})
        theirs = {key: value["rating"] for key, value in given.items()}
        # Checks the grader could not judge are left out of both scores.
        mine_scored = {key: ("unreviewed" if theirs.get(key) == "unreviewed" else value)
                       for key, value in review["ratings"].items()}
        points = grade.get("out_of_scope_points", [])
        for source, ratings, count in (("user", mine_scored, len(review.get("out_of_scope_points") or [])),
                                       ("grader", theirs, len(points))):
            passed, scored = answer_score(checks, ratings, count)
            # The user's score is counted once per grade so both sides cover the same repeats.
            scores[review["version"]][source][0] += passed
            scores[review["version"]][source][1] += scored
        off_scope.append({"label": review["label"], "repeat": grade.get("repeat"),
                          "user": len(review.get("out_of_scope_points") or []), "grader": len(points),
                          "grader_points": points})
    for cells in agreement.values():
        for cell in cells.values():
            cell["percent"] = _percent(cell["agree"], cell["total"])
    unstable = sorted(f"{label} {check}" for (label, check), seen in by_check.items() if len(seen) > 1)
    planted_results = []
    for case in planted or []:
        for grade in (g for g in grades if g.get("label") == case["label"]):
            if grade.get("status") != "graded":
                planted_results.append({"label": case["label"], "fault": case.get("fault"), "repeat": grade.get("repeat"),
                                        "caught": False, "missed": ["ungraded"]})
                continue
            given = {item["id"]: item["rating"] for item in grade["judgments"]}
            missed = [f"{check} expected {rating}, got {given.get(check)}"
                      for check, rating in case.get("expect", {}).items() if given.get(check) != rating]
            any_of = case.get("expect_any_fail", [])
            if any_of and not any(given.get(check) == "fail" for check in any_of):
                missed.append(f"none of {', '.join(any_of)} failed")
            if len(grade.get("out_of_scope_points", [])) < case.get("out_of_scope_at_least", 0):
                missed.append("off-scope point not found")
            planted_results.append({"label": case["label"], "fault": case.get("fault"),
                                    "repeat": grade.get("repeat"), "caught": not missed, "missed": missed})
    return {
        "agreement": {name: dict(cells) for name, cells in agreement.items()},
        "scores": {version: {source: {"pass": value[0], "scored": value[1], "percent": _percent(*value)}
                             for source, value in sources.items()} for version, sources in scores.items()},
        "off_scope": off_scope,
        "unstable_checks": unstable,
        "unstable_share": _percent(len(unstable), len(by_check)),
        "ungraded": ungraded, "unmatched": unmatched,
        "planted": planted_results,
        "disagreements": disagreements,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("grades", nargs="+", help="grade receipt directories")
    parser.add_argument("--human", action="append", required=True, help="NAME=review-export.json")
    parser.add_argument("--planted", type=Path, help="planted-error spec (a JSON list)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    checklist = load_rubric(CHECKLIST)
    human = {}
    for item in args.human:
        name, path = item.split("=", 1)
        human[name] = json.loads(Path(path).read_text(encoding="utf-8"))
    grades = [json.loads(Path(path).read_text(encoding="utf-8"))
              for directory in args.grades for path in sorted(glob.glob(str(Path(directory) / "*.repeat-*.json")))]
    planted = json.loads(args.planted.read_text(encoding="utf-8")) if args.planted else None
    result = compare(checklist, human, grades, planted)
    _private_output(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("agreement", "scores", "unstable_share")}, indent=1))
    print("ungraded", len(result["ungraded"]), "disagreements", len(result["disagreements"]),
          "planted caught", sum(item["caught"] for item in result["planted"]), "of", len(result["planted"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
