"""Compare answer receipts with cards available and withheld (plan unit U9)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.answer_rubric import AnswerRubric, load_rubric

MATCHED_KEYS = ("model", "reasoning_effort", "prompt_sha256", "action_schema_sha256", "limits",
                "embedding_spec_id", "source_revisions", "budget_profile")


def evidence_hits(rubric: AnswerRubric, question_id: str, passages: list[dict]) -> tuple[int, int]:
    """Count source-bound checks whose evidence span overlaps a passage of the same revision."""
    question = next(item for item in rubric.questions if item.id == question_id)
    bound = found = 0
    for check in question.checks:
        spans = [(rubric.source_revisions[alt.source], alt.start_char, alt.end_char)
                 for alt in check.evidence_alternatives if alt.start_char is not None]
        if not spans:
            continue
        bound += 1
        found += any(passage["document_id"] == source.document_id
                     and passage["source_sha256"] == source.source_sha256
                     and passage["start_char"] < end and start < passage["end_char"]
                     for source, start, end in spans for passage in passages)
    return found, bound


def summarize(rubric: AnswerRubric, report: dict) -> dict:
    result = report["result"]
    question_id = report["question_id"]
    inspected, bound = evidence_hits(rubric, question_id, list(result["inspected"].values()))
    cited, _ = evidence_hits(rubric, question_id, list(result["citations"].values()))
    return {"termination": result["termination"], "completeness": result["completeness"],
            "checks_with_source_spans": bound, "evidence_inspected": inspected, "evidence_cited": cited,
            "chunks_read": len({key.split(".")[0] for key in result["inspected"]}),
            "model_calls": result["model_calls"], "estimated_usd": report.get("estimated_usd")}


def compare(rubric: AnswerRubric, with_cards: Path, without_cards: Path) -> dict:
    arms: dict[str, dict] = {}
    configurations = {}
    for name, directory, expected in (("with_cards", with_cards, True), ("without_cards", without_cards, False)):
        manifest = json.loads((directory / "pilot.json").read_text(encoding="utf-8"))
        arms[name] = {}
        for question_id, attempts in manifest["questions"].items():
            report = json.loads(Path(attempts[-1]["report"]).read_text(encoding="utf-8"))
            if report.get("status") != "completed":
                arms[name][question_id] = {"termination": "failed"}
                continue
            configuration = report["configuration"]
            if configuration.get("cards", True) is not expected:
                raise ValueError(f"{name} receipt has the wrong card setting")
            configurations.setdefault(name, {key: configuration.get(key) for key in MATCHED_KEYS})
            if configurations[name] != {key: configuration.get(key) for key in MATCHED_KEYS}:
                raise ValueError(f"{name} receipts do not share one configuration")
            arms[name][question_id] = summarize(rubric, {**report, "question_id": question_id})
    if configurations.get("with_cards") != configurations.get("without_cards"):
        raise ValueError("the two arms differ in more than the card setting")
    totals = {name: {key: sum(item.get(key, 0) for item in runs.values())
                     for key in ("checks_with_source_spans", "evidence_inspected", "evidence_cited",
                                 "chunks_read", "model_calls")}
              for name, runs in arms.items()}
    return {"rubric_version": rubric.version, "rubric_status": rubric.status, "arms": arms, "totals": totals}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("with_cards", type=Path)
    parser.add_argument("without_cards", type=Path)
    parser.add_argument("--rubric", type=Path, default=Path("evals/answer_questions_v4.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    target = args.output.resolve()
    if not target.is_relative_to((Path.cwd() / ".local").resolve()) or target.exists():
        parser.error("choose a new private output under .local")
    report = compare(load_rubric(args.rubric), args.with_cards, args.without_cards)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
