"""Evidence-validated semantic judgments over retained answer receipts.

Model judgments are provisional. Invalid or incomplete output is ungraded.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from app.ingestion.contracts import StrictRecord
from app.models.client import ModelClient, ModelClientError, ModelSpec
from evals.answer_rubric import AnswerRubric, Checklist, rubric_fingerprint, score_checklist_ratings


PROMPT_ROOT = Path(__file__).resolve().parents[1] / "prompts"
PROMPT = (PROMPT_ROOT / "answer_grader_v1.md").read_text(encoding="utf-8")
# The three-dimension checklist is binary and has its own instructions; version 3 adds
# the relevance scope. Version 2 is kept for the record and is no longer sent.
CHECKLIST_PROMPT = (PROMPT_ROOT / "answer_grader_v3.md").read_text(encoding="utf-8")
CHECKLIST_LAYOUT = "three-dimension"


class InspectedSupport(StrictRecord):
    id: str
    excerpt: str = Field(min_length=1)


class CheckJudgment(StrictRecord):
    id: str
    rating: Literal["pass", "partial", "fail", "not_applicable", "unreviewed"]
    answer_excerpt: str | None
    inspected_support: list[InspectedSupport]
    reason: str = Field(min_length=1)


class GraderResponse(StrictRecord):
    version: Literal[1, 2, 3]
    judgments: list[CheckJudgment]
    # Version 3: exact answer excerpts, one per main point outside the question's scope.
    out_of_scope_points: list[str] = Field(default_factory=list)


GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "version": {"type": "integer", "enum": [1]},
        "judgments": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"},
            "rating": {"type": "string", "enum": ["pass", "partial", "fail", "not_applicable", "unreviewed"]},
            "answer_excerpt": {"type": ["string", "null"]},
            "inspected_support": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "excerpt": {"type": "string"}},
                "required": ["id", "excerpt"], "additionalProperties": False}},
            "reason": {"type": "string"}},
            "required": ["id", "rating", "answer_excerpt", "inspected_support", "reason"],
            "additionalProperties": False}}},
    "required": ["version", "judgments"], "additionalProperties": False}


GRADE_SCHEMA_V2 = json.loads(json.dumps(GRADE_SCHEMA))
GRADE_SCHEMA_V2["properties"]["version"]["enum"] = [2]
GRADE_SCHEMA_V2["properties"]["judgments"]["items"]["properties"]["rating"]["enum"] = [
    "pass", "fail", "not_applicable", "unreviewed"]

GRADE_SCHEMA_V3 = json.loads(json.dumps(GRADE_SCHEMA_V2))
GRADE_SCHEMA_V3["properties"]["version"]["enum"] = [3]
GRADE_SCHEMA_V3["properties"]["out_of_scope_points"] = {"type": "array", "items": {"type": "string"}}
GRADE_SCHEMA_V3["required"] = ["version", "judgments", "out_of_scope_points"]

SOURCE_DIMENSIONS = {"coverage", "example", "grounding", "attribution"}


def _question(rubric: AnswerRubric | Checklist, question_id: str):
    return next((item for item in rubric.questions if item.id == question_id), None)


def prepare_evidence(rubric: AnswerRubric | Checklist, question_id: str, answer_receipt: dict,
                     gold_sources: dict[str, bytes]) -> dict:
    """Keep answer, inspected evidence, and revision-checked gold alternatives distinct."""
    if rubric.version != 4:
        raise ValueError("grader requires version-4 criteria")
    observed = answer_receipt.get("configuration", {}).get("source_revisions", {})
    rubric.require_comparison_ready(observed)
    question = _question(rubric, question_id)
    if question is None:
        raise ValueError("unknown frozen question")
    result = answer_receipt.get("result", {})
    answer = result.get("answer")
    inspected = result.get("inspected", {})
    citations = result.get("citations", {})
    if not isinstance(answer, str) or not isinstance(inspected, dict) or not isinstance(citations, dict):
        raise ValueError("answer receipt is incomplete")
    if not set(citations).issubset(inspected):
        raise ValueError("answer cites passages outside the inspected ledger")
    revisions = {item.document_id: item.source_sha256 for item in rubric.source_revisions.values()}
    checked_sources = {}
    for document_id, expected_sha in revisions.items():
        raw = gold_sources.get(document_id)
        if raw is not None:
            if hashlib.sha256(raw).hexdigest() != expected_sha:
                raise ValueError("gold source revision is unavailable or changed")
            checked_sources[document_id] = raw.decode("utf-8")
    for evidence in inspected.values():
        document_id = evidence.get("document_id")
        if evidence.get("source_sha256") != revisions.get(document_id):
            raise ValueError("inspected passage has an unexpected source revision")
        source_text = checked_sources.get(document_id)
        start, end = evidence.get("start_char"), evidence.get("end_char")
        if (source_text is None or not isinstance(start, int) or not isinstance(end, int)
                or not 0 <= start < end <= len(source_text)
                or source_text[start:end] != evidence.get("text")):
            raise ValueError("inspected passage does not match the checked original")
    gold = {}
    for check in question.checks:
        alternatives = []
        for alternative in check.evidence_alternatives:
            if alternative.start_char is None:
                continue
            source = rubric.source_revisions[alternative.source]
            text = checked_sources.get(source.document_id)
            if text is None:
                raise ValueError("gold source revision is unavailable or changed")
            if alternative.end_char > len(text):
                raise ValueError("gold evidence span exceeds its source")
            alternatives.append({"source": alternative.source,
                "document_id": source.document_id, "source_sha256": source.source_sha256,
                "start_char": alternative.start_char, "end_char": alternative.end_char,
                "text": text[alternative.start_char:alternative.end_char]})
        gold[check.id] = alternatives
    layout = getattr(rubric, "layout", "facet")
    checks = [{"id": check.id, "dimension": check.dimension,
               "criterion": check.criterion, "applicability": check.applicability,
               "condition": check.condition} for check in question.checks]
    scope = None
    if layout == CHECKLIST_LAYOUT:
        for item, check in zip(checks, question.checks):
            item.update(type=check.type, parent_id=check.parent_id)
        scope = {"in_scope": question.in_scope, "out_of_scope": question.out_of_scope}
    return {"question_id": question.id, "question": question.question, "layout": layout,
            "checks": checks, **({"scope": scope} if scope else {}),
            "answer": answer, "inspected": inspected, "citations": citations,
            "gold_alternatives": gold}


def validate_judgments(payload: dict, response: GraderResponse) -> dict:
    checks = {check["id"]: check for check in payload["checks"]}
    judgments = {item.id: item for item in response.judgments}
    if len(judgments) != len(response.judgments) or set(judgments) != set(checks):
        raise ValueError("grader omitted, duplicated, or invented check IDs")
    if payload.get("layout") == CHECKLIST_LAYOUT:
        return _validate_checklist_judgments(payload, checks, judgments, response.out_of_scope_points)
    counts = {dimension: {rating: 0 for rating in
              ("pass", "partial", "fail", "not_applicable", "unreviewed")}
              for dimension in {check["dimension"] for check in checks.values()}}
    for check_id, item in judgments.items():
        check = checks[check_id]
        if item.rating == "not_applicable" and check["applicability"] != "conditional":
            raise ValueError("required check cannot be not applicable")
        if item.rating in ("pass", "partial") and not item.answer_excerpt:
            raise ValueError("positive judgment needs an exact answer excerpt")
        if item.answer_excerpt is not None and item.answer_excerpt not in payload["answer"]:
            raise ValueError("grader answer excerpt is not exact")
        for support in item.inspected_support:
            passage = payload["inspected"].get(support.id)
            if passage is None or support.excerpt not in passage["text"]:
                raise ValueError("grader support is not an exact inspected excerpt")
            if check["dimension"] == "grounding" and support.id not in payload["citations"]:
                raise ValueError("grounding support was not cited by the answer")
            if (check["dimension"] == "grounding" and item.rating in ("pass", "partial")
                    and f"[{support.id}]" not in item.answer_excerpt):
                raise ValueError("grounding excerpt must include its supporting citation")
        if item.rating in ("pass", "partial") and check["dimension"] in SOURCE_DIMENSIONS and not item.inspected_support:
            raise ValueError("positive source-backed check has no inspected support")
        counts[check["dimension"]][item.rating] += 1
    denominators = {name: sum(values[rating] for rating in ("pass", "partial", "fail"))
                    for name, values in counts.items()}
    return {"status": "graded", "counts": counts, "denominators": denominators,
            "judgments": [item.model_dump(mode="json") for item in response.judgments]}


def _validate_checklist_judgments(payload: dict, checks: dict, judgments: dict,
                                  out_of_scope_points: list[str]) -> dict:
    """Binary ratings, primary/detail dependencies, one count per grounding error, and
    off-scope points that must be exact, distinct excerpts of the answer."""
    points = [point.strip() for point in out_of_scope_points]
    if any(not point or point not in payload["answer"] for point in points):
        raise ValueError("off-scope point is not an exact answer excerpt")
    if len(set(points)) != len(points):
        raise ValueError("off-scope point listed twice")
    grounding_failures: set[str] = set()
    for check_id, item in judgments.items():
        check = checks[check_id]
        if item.rating == "partial":
            raise ValueError("checklist ratings are binary")
        if item.answer_excerpt is not None and item.answer_excerpt not in payload["answer"]:
            raise ValueError("grader answer excerpt is not exact")
        for support in item.inspected_support:
            passage = payload["inspected"].get(support.id)
            if passage is None or support.excerpt not in passage["text"]:
                raise ValueError("grader support is not an exact inspected excerpt")
            if check["dimension"] == "grounding" and support.id not in payload["citations"]:
                raise ValueError("grounding support was not cited by the answer")
        if check["dimension"] == "grounding":
            # Many grounding checks are prohibitions, so a pass may have nothing to quote.
            # A failure must point at the offending claim, and one claim fails one check.
            if item.rating == "fail":
                if not item.answer_excerpt:
                    raise ValueError("grounding failure needs the exact offending excerpt")
                if item.answer_excerpt in grounding_failures:
                    raise ValueError("one grounding error is counted under one check only")
                grounding_failures.add(item.answer_excerpt)
        elif item.rating == "pass" and (not item.answer_excerpt or not item.inspected_support):
            raise ValueError("passing coverage or example check needs an answer excerpt and inspected support")
    scores = score_checklist_ratings(list(checks.values()),
                                     {check_id: item.rating for check_id, item in judgments.items()})
    return {"status": "graded", "scores": scores, "out_of_scope_points": points,
            "judgments": [item.model_dump(mode="json") for item in judgments.values()]}


def grade_answer(rubric: AnswerRubric | Checklist, question_id: str, answer_receipt: dict,
                 gold_sources: dict[str, bytes], *, client: ModelClient, model: ModelSpec,
                 max_output_tokens: int = 8000, reasoning_effort: str | None = None) -> dict:
    """One bounded grader call; all failures remain ungraded for review or retry."""
    payload = prepare_evidence(rubric, question_id, answer_receipt, gold_sources)
    checklist = payload.get("layout") == CHECKLIST_LAYOUT
    prompt, schema_name, schema, version = (
        (CHECKLIST_PROMPT, "answer_grader_v3", GRADE_SCHEMA_V3, 3) if checklist
        else (PROMPT, "answer_grader_v1", GRADE_SCHEMA, 1))
    identity = {"question_id": question_id, "rubric_sha256": rubric_fingerprint(rubric),
                "grader_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "model": model.model_id, "grader_prompt_version": schema_name,
                "reasoning_effort": reasoning_effort,
                "answer_sha256": hashlib.sha256(payload["answer"].encode("utf-8")).hexdigest()}
    try:
        result = client.generate_json(model=model, system_prompt=prompt,
            input_text=json.dumps(payload, ensure_ascii=False),
            schema_name=schema_name, json_schema=schema,
            max_output_tokens=max_output_tokens, reasoning_effort=reasoning_effort)
        if result.model != model:
            raise ValueError("grader model changed")
        response = GraderResponse.model_validate(result.data)
        if response.version != version:
            raise ValueError("grader answered with the wrong response version")
        return {**identity, **validate_judgments(payload, response)}
    except (ModelClientError, ValidationError, ValueError) as exc:
        return {**identity, "status": "ungraded", "reason": type(exc).__name__}
