"""Evidence-validated semantic judgments over retained answer receipts.

Model judgments are provisional. Invalid or incomplete output is ungraded.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from app.ingestion.contracts import StrictRecord
from app.models.client import ModelClient, ModelClientError, ModelSpec
from evals.answer_rubric import AnswerRubric, Checklist, rubric_fingerprint, score_checklist_ratings


PROMPT_ROOT = Path(__file__).resolve().parents[1] / "prompts"
PROMPT = (PROMPT_ROOT / "answer_grader_v1.md").read_text(encoding="utf-8")
# The three-dimension checklist is binary and has its own instructions; version 3 adds
# the relevance scope and version 4 the source titles and people. Earlier versions are kept
# for the record and are no longer sent; the response format is still version 3.
CHECKLIST_PROMPT = (PROMPT_ROOT / "answer_grader_v4.md").read_text(encoding="utf-8")
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
                     gold_sources: dict[str, bytes],
                     other_sources: dict[str, tuple[str, str]] | None = None) -> dict:
    """Keep answer, inspected evidence, and revision-checked gold alternatives distinct.

    `other_sources` maps a document outside the checklist to its (source SHA-256, text): an
    answer from a larger index may read notes the checklist does not pin, and their passages
    are checked against these texts instead.
    """
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
        if document_id not in revisions and document_id in (other_sources or {}):
            expected_sha, source_text = other_sources[document_id]
            if evidence.get("source_sha256") != expected_sha:
                raise ValueError("inspected passage has an unexpected source revision")
        elif evidence.get("source_sha256") != revisions.get(document_id):
            raise ValueError("inspected passage has an unexpected source revision")
        else:
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
    extra = {}
    if layout == CHECKLIST_LAYOUT:
        for item, check in zip(checks, question.checks):
            item.update(type=check.type, parent_id=check.parent_id)
        extra["scope"] = {"in_scope": question.in_scope, "out_of_scope": question.out_of_scope}
        extra["sources"] = _source_labels(result, inspected)
    return {"question_id": question.id, "question": question.question, "layout": layout,
            "checks": checks, **extra,
            "answer": answer, "inspected": inspected, "citations": citations,
            "gold_alternatives": gold}


def _source_labels(result: dict, inspected: dict) -> dict:
    """Title and people for each inspected document, as the answer agent saw them in
    search results; a document without a search result is named by its file."""
    seen = {}
    for hit in (result.get("host_search") or {}).get("results") or []:
        if isinstance(hit, dict) and hit.get("document_id") and hit["document_id"] not in seen:
            seen[hit["document_id"]] = {"title": hit.get("title"), "people": hit.get("people") or []}
    labels = {}
    for passage in inspected.values():
        document_id = passage.get("document_id")
        if document_id and document_id not in labels:
            fallback = (passage.get("relative_path") or "").rsplit("/", 1)[-1].removesuffix(".md")
            labels[document_id] = seen.get(document_id) or {"title": fallback or None, "people": []}
    return labels


def validate_judgments(payload: dict, response: GraderResponse) -> dict:
    checks = {check["id"]: check for check in payload["checks"]}
    if payload.get("layout") == CHECKLIST_LAYOUT:
        return _validate_checklist_judgments(payload, checks, response)
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


def _normalized(text: str) -> str:
    """Compatibility forms, one hyphen, straight quotes, single spaces, lower case."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join("-" if unicodedata.category(char) == "Pd" else "'" if char in "‘’‚‛"
                   else '"' if char in "“”„‟" else char for char in text)
    return re.sub(r"\s+", " ", text).strip().lower()


def quoted(excerpt: str | None, text: str) -> bool:
    """An excerpt matches when it appears in the text after normalizing punctuation and
    spacing; an ellipsis may stand for omitted words, with the parts in order."""
    if not excerpt or not excerpt.strip():
        return False
    if excerpt in text:
        return True
    haystack, position = _normalized(text), 0
    parts = [_normalized(part).strip(" .") for part in re.split(r"\.\.\.|…", excerpt)]
    parts = [part for part in parts if part]
    for part in parts:
        found = haystack.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    return bool(parts)


def _validate_checklist_judgments(payload: dict, checks: dict, response: GraderResponse) -> dict:
    """Judge each check on its own: a judgment that breaks a rule becomes `unreviewed`
    (never a pass) and the reason is recorded, so one bad quote does not discard the
    answer. Only the excerpts a rule relies on are checked: the answer excerpt and
    inspected support of a passing coverage or example check, and the offending excerpt
    of a grounding failure. One grounding error is counted under one check only."""
    invalid: dict[str, str] = {}
    given: dict[str, CheckJudgment] = {}
    for item in response.judgments:
        if item.id not in checks:
            continue
        if item.id in given:
            invalid[item.id] = "judged twice"
        given[item.id] = item
    ratings = {}
    grounding_failures: set[str] = set()
    for check_id, check in checks.items():
        item = given.get(check_id)
        if item is None:
            invalid.setdefault(check_id, "not judged")
            ratings[check_id] = "unreviewed"
            continue
        rating, problem = item.rating, None
        if rating == "partial":
            problem = "checklist ratings are binary"
        elif check["dimension"] == "grounding" and rating == "fail":
            excerpt = _normalized(item.answer_excerpt or "")
            if not quoted(item.answer_excerpt, payload["answer"]):
                problem = "grounding failure needs an exact offending excerpt"
            elif excerpt in grounding_failures:
                problem = "one grounding error is counted under one check only"
            grounding_failures.add(excerpt)
        elif check["dimension"] != "grounding" and rating == "pass":
            if not quoted(item.answer_excerpt, payload["answer"]):
                problem = "passing check needs an exact answer excerpt"
            elif not item.inspected_support or any(
                    support.id not in payload["inspected"]
                    or not quoted(support.excerpt, payload["inspected"][support.id]["text"])
                    for support in item.inspected_support):
                problem = "passing check needs exact support from an inspected passage"
        if rating == "not_applicable" and check["applicability"] == "required" and not check.get("parent_id"):
            problem = "a required check cannot be not applicable"
        if problem or check_id in invalid:
            invalid.setdefault(check_id, problem)
            rating = "unreviewed"
        ratings[check_id] = rating
    # Dependencies: a detail follows its primary unless the primary passed.
    for check_id, check in checks.items():
        parent = check.get("parent_id")
        if parent is None:
            continue
        if ratings[parent] == "unreviewed":
            ratings[check_id] = "unreviewed"
        elif ratings[parent] != "pass":
            ratings[check_id] = "not_applicable"
        elif ratings[check_id] == "not_applicable" and check["applicability"] != "optional":
            invalid.setdefault(check_id, "a detail of a passing primary needs a rating")
            ratings[check_id] = "unreviewed"
    points = [point.strip() for point in response.out_of_scope_points]
    keep = [quoted(point, payload["answer"]) and _normalized(point) not in map(_normalized, points[:index])
            for index, point in enumerate(points)]
    kept = [point for point, ok in zip(points, keep) if ok]
    scores = score_checklist_ratings(list(checks.values()), ratings)
    judgments = []
    for check_id in checks:
        item = given.get(check_id)
        record = item.model_dump(mode="json") if item else {"id": check_id, "reason": "not judged"}
        record["rating"] = ratings[check_id]
        if check_id in invalid:
            record["validation"] = invalid[check_id]
        judgments.append(record)
    return {"status": "graded", "scores": scores, "out_of_scope_points": kept,
            "dropped_out_of_scope_points": [point for point, ok in zip(points, keep) if not ok],
            "unreviewed_by_validation": invalid, "judgments": judgments}


def grade_answer(rubric: AnswerRubric | Checklist, question_id: str, answer_receipt: dict,
                 gold_sources: dict[str, bytes], *, client: ModelClient, model: ModelSpec,
                 max_output_tokens: int = 8000, reasoning_effort: str | None = None,
                 other_sources: dict[str, tuple[str, str]] | None = None) -> dict:
    """One bounded grader call; all failures remain ungraded for review or retry."""
    payload = prepare_evidence(rubric, question_id, answer_receipt, gold_sources, other_sources)
    checklist = payload.get("layout") == CHECKLIST_LAYOUT
    prompt, schema_name, schema, version = (
        (CHECKLIST_PROMPT, "answer_grader_v4", GRADE_SCHEMA_V3, 3) if checklist
        else (PROMPT, "answer_grader_v1", GRADE_SCHEMA, 1))
    identity = {"question_id": question_id, "rubric_sha256": rubric_fingerprint(rubric),
                "grader_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "model": model.model_id, "grader_prompt_version": schema_name,
                "reasoning_effort": reasoning_effort,
                "answer_sha256": hashlib.sha256(payload["answer"].encode("utf-8")).hexdigest()}
    result = None
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
        # The detail and the raw reply stay in the private grade receipt for diagnosis.
        detail = (str(exc).splitlines() or [""])[0][:300]
        return {**identity, "status": "ungraded", "reason": type(exc).__name__, "detail": detail,
                "raw_output": result.data if result is not None else None}
