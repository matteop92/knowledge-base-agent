"""Compare IDs-only exact-vector outcomes across Stage 4 context trials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


K_VALUES = (5, 10, 20)


def _profile(report: dict, name: str) -> dict:
    matches = [item for item in report["profiles"]
               if item["name"] == name and item["status"] == "complete"]
    if len(matches) != 1:
        raise ValueError(f"expected one complete {name} profile")
    return matches[0]


def _questions(profile: dict) -> dict[tuple[str, str], dict]:
    rows = {(item["document_id"], item["question_id"]): item["coverage"]
            for item in profile["questions"]}
    if len(rows) != len(profile["questions"]):
        raise ValueError("duplicate question outcome")
    return rows


def compare(baseline: dict, candidates: list[dict]) -> dict:
    raw = _profile(baseline, "structural_raw")
    previous = _profile(baseline, "structural_contextual")
    raw_questions = _questions(raw)
    previous_questions = _questions(previous)
    if raw_questions.keys() != previous_questions.keys():
        raise ValueError("baseline question sets differ")
    output = {"baseline": {"raw": raw["metrics"], "previous_contextual": previous["metrics"]},
              "candidates": []}
    for report in candidates:
        candidate = _profile(report, "structural_contextual")
        questions = _questions(candidate)
        if questions.keys() != raw_questions.keys():
            raise ValueError("candidate question set differs from baseline")
        changes = []
        for document_id, question_id in sorted(questions):
            row = {"document_id": document_id, "question_id": question_id}
            for k in K_VALUES:
                key = str(k)
                current = questions[(document_id, question_id)][key]
                original = raw_questions[(document_id, question_id)][key]
                prior = previous_questions[(document_id, question_id)][key]
                row[key] = {"covered_delta_vs_raw": current["covered"] - original["covered"],
                            "covered_delta_vs_previous": current["covered"] - prior["covered"],
                            "complete_vs_raw": [bool(current["complete"]), bool(original["complete"])]}
            if any(row[str(k)]["covered_delta_vs_raw"] or row[str(k)]["covered_delta_vs_previous"]
                   or row[str(k)]["complete_vs_raw"][0] != row[str(k)]["complete_vs_raw"][1]
                   for k in K_VALUES):
                changes.append(row)
        output["candidates"].append({"model": report["context_model"],
                                     "metrics": candidate["metrics"],
                                     "estimated_cost_usd": report["cost"]["estimated_usd"],
                                     "changed_questions": changes})
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path,
                        default=Path(".local/chunking-trial/report.json"))
    parser.add_argument("--candidate", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path,
                        default=Path(".local/stage4-context-comparison.json"))
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidates = [json.loads(path.read_text(encoding="utf-8")) for path in args.candidate]
    report = compare(baseline, candidates)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(args.output),
                      "models": [item["model"] for item in report["candidates"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
