"""Evidence-validated semantic judgments over retained answer receipts.

Model judgments are provisional. Invalid or incomplete output is ungraded.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from app.ingestion.contracts import StrictRecord
from app.models.client import ModelClient, ModelClientError, ModelSpec
from evals.answer_rubric import AnswerRubric, rubric_fingerprint


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "answer_grader_v1.md"
PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


class InspectedSupport(StrictRecord):
    id: str
    excerpt: str = Field(min_length=1)


class CheckJudgment(StrictRecord):
    id: str
    rating: Literal["pass", "partial", "fail", "not_applicable", "unreviewed"]
    answer_excerpt: str | None
    inspected_support: list[InspectedSupport]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_excerpts(self) -> CheckJudgment:
        if self.rating in ("pass", "partial") and not self.answer_excerpt:
            raise ValueError("positive judgment needs an exact answer excerpt")
        return self


class GraderResponse(StrictRecord):
    version: Literal[1]
    judgments: list[CheckJudgment]


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


SOURCE_DIMENSIONS = {"coverage", "example", "grounding", "attribution"}


def _question(rubric: AnswerRubric, question_id: str):
    return next((item for item in rubric.questions if item.id == question_id), None)


def prepare_evidence(rubric: AnswerRubric, question_id: str, answer_receipt: dict,
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
    for evidence in inspected.values():
        if evidence.get("source_sha256") != revisions.get(evidence.get("document_id")):
            raise ValueError("inspected passage has an unexpected source revision")
        if len(evidence.get("text", "")) != evidence.get("end_char", 0) - evidence.get("start_char", 0):
            raise ValueError("inspected passage offsets do not match its text")
    gold = {}
    for check in question.checks:
        alternatives = []
        for alternative in check.evidence_alternatives:
            if alternative.start_char is None:
                continue
            source = rubric.source_revisions[alternative.source]
            raw = gold_sources.get(source.document_id)
            if raw is None or hashlib.sha256(raw).hexdigest() != source.source_sha256:
                raise ValueError("gold source revision is unavailable or changed")
            text = raw.decode("utf-8")
            if alternative.end_char > len(text):
                raise ValueError("gold evidence span exceeds its source")
            alternatives.append({"source": alternative.source,
                "document_id": source.document_id, "source_sha256": source.source_sha256,
                "start_char": alternative.start_char, "end_char": alternative.end_char,
                "text": text[alternative.start_char:alternative.end_char]})
        gold[check.id] = alternatives
    return {"question_id": question.id, "question": question.question,
            "checks": [{"id": check.id, "dimension": check.dimension,
                        "criterion": check.criterion, "applicability": check.applicability,
                        "condition": check.condition} for check in question.checks],
            "answer": answer, "inspected": inspected, "citations": citations,
            "gold_alternatives": gold}


def validate_judgments(payload: dict, response: GraderResponse) -> dict:
    checks = {check["id"]: check for check in payload["checks"]}
    judgments = {item.id: item for item in response.judgments}
    if len(judgments) != len(response.judgments) or set(judgments) != set(checks):
        raise ValueError("grader omitted, duplicated, or invented check IDs")
    counts = {dimension: {rating: 0 for rating in
              ("pass", "partial", "fail", "not_applicable", "unreviewed")}
              for dimension in {check["dimension"] for check in checks.values()}}
    for check_id, item in judgments.items():
        check = checks[check_id]
        if item.rating == "not_applicable" and check["applicability"] != "conditional":
            raise ValueError("required check cannot be not applicable")
        if item.answer_excerpt is not None and item.answer_excerpt not in payload["answer"]:
            raise ValueError("grader answer excerpt is not exact")
        for support in item.inspected_support:
            passage = payload["inspected"].get(support.id)
            if passage is None or support.excerpt not in passage["text"]:
                raise ValueError("grader support is not an exact inspected excerpt")
            if check["dimension"] == "grounding" and support.id not in payload["citations"]:
                raise ValueError("grounding support was not cited by the answer")
        if item.rating in ("pass", "partial") and check["dimension"] in SOURCE_DIMENSIONS and not item.inspected_support:
            raise ValueError("positive source-backed check has no inspected support")
        counts[check["dimension"]][item.rating] += 1
    denominators = {name: sum(values[rating] for rating in ("pass", "partial", "fail"))
                    for name, values in counts.items()}
    return {"status": "graded", "counts": counts, "denominators": denominators,
            "judgments": [item.model_dump(mode="json") for item in response.judgments]}


def grade_answer(rubric: AnswerRubric, question_id: str, answer_receipt: dict,
                 gold_sources: dict[str, bytes], *, client: ModelClient, model: ModelSpec,
                 max_output_tokens: int = 2800) -> dict:
    """One bounded grader call; all failures remain ungraded for review or retry."""
    payload = prepare_evidence(rubric, question_id, answer_receipt, gold_sources)
    identity = {"question_id": question_id, "rubric_sha256": rubric_fingerprint(rubric),
                "grader_prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
                "model": model.model_id}
    try:
        result = client.generate_json(model=model, system_prompt=PROMPT,
            input_text=json.dumps(payload, ensure_ascii=False),
            schema_name="answer_grader_v1", json_schema=GRADE_SCHEMA,
            max_output_tokens=max_output_tokens)
        if result.model != model:
            raise ValueError("grader model changed")
        response = GraderResponse.model_validate(result.data)
        return {**identity, **validate_judgments(payload, response)}
    except (ModelClientError, ValidationError, ValueError) as exc:
        return {**identity, "status": "ungraded", "reason": type(exc).__name__}
