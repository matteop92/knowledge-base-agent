"""Revision-bound human answer rubric; comparison requires an approved freeze."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
from typing import Literal

import yaml
from pydantic import Field, model_validator

from app.ingestion.contracts import StrictRecord


class SourceRevision(StrictRecord):
    document_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvidenceAlternative(StrictRecord):
    source: str = Field(min_length=1)
    description: str = Field(min_length=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def paired_span(self) -> EvidenceAlternative:
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("evidence alternative needs both span offsets")
        if self.start_char is not None and self.end_char <= self.start_char:
            raise ValueError("evidence alternative has an invalid span")
        return self


class AnswerCheck(StrictRecord):
    id: str = Field(pattern=r"^Q[1-8]\.C[1-9][0-9]*$")
    dimension: Literal["coverage", "example", "grounding", "qualification", "gap", "usefulness", "attribution"]
    criterion: str = Field(min_length=1)
    applicability: Literal["required", "conditional"] = "required"
    condition: str | None = None
    evidence_alternatives: list[EvidenceAlternative] = Field(default_factory=list)
    legacy_facet_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def conditional_reason(self) -> AnswerCheck:
        if self.applicability == "conditional" and not self.condition:
            raise ValueError("conditional check needs a condition")
        return self


class AnswerQuestion(StrictRecord):
    id: str = Field(pattern=r"^Q[1-8]$")
    review_status: str = Field(pattern=r"^(pending|approved)$")
    question: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    answerability: str = Field(pattern=r"^(full|partial|unsupported)$")
    sources: list[str]
    essential_facets: list[str]
    suggested_examples: list[str]
    alternative_evidence: str = Field(min_length=1)
    qualifications: str = Field(min_length=1)
    prohibited_inferences: list[str]
    useful_answer: str = Field(min_length=1)
    checks: list[AnswerCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_facets(self) -> AnswerQuestion:
        if self.answerability != "unsupported" and not self.essential_facets:
            raise ValueError("answerable question needs essential facets")
        if not self.sources or not self.prohibited_inferences:
            raise ValueError("source and unsupported-inference checks are required")
        return self


class AnswerRubric(StrictRecord):
    version: int = Field(ge=1)
    status: str = Field(pattern=r"^(draft|frozen)$")
    answer_expectations: list[str] = Field(min_length=1)
    examples_required: Literal[True]
    source_revisions: dict[str, SourceRevision]
    questions: list[AnswerQuestion]
    criteria_changes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_questions(self) -> AnswerRubric:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)) or set(ids) != {f"Q{n}" for n in range(1, 9)}:
            raise ValueError("rubric must have exactly Q1 through Q8")
        if any(source not in self.source_revisions
               for question in self.questions for source in question.sources):
            raise ValueError("question cites an unknown source revision")
        if self.status == "frozen" and any(question.review_status != "approved" for question in self.questions):
            raise ValueError("frozen rubric requires approval of every question")
        if self.version == 4:
            for question in self.questions:
                check_ids = [check.id for check in question.checks]
                if not check_ids or len(check_ids) != len(set(check_ids)) or any(
                    not check_id.startswith(question.id + ".") for check_id in check_ids
                ):
                    raise ValueError("version 4 needs unique question-scoped check IDs")
                if any(alt.source not in question.sources for check in question.checks
                       for alt in check.evidence_alternatives):
                    raise ValueError("check evidence cites a source outside its question")
                if self.status == "frozen" and any(check.dimension in ("coverage", "example", "attribution")
                   and not any(alt.start_char is not None for alt in check.evidence_alternatives)
                   for check in question.checks):
                    raise ValueError("frozen version 4 needs source-span evidence alternatives")
        return self

    def require_comparison_ready(self, observed_hashes: dict[str, str]) -> None:
        if self.status != "frozen" or any(question.review_status != "approved" for question in self.questions):
            raise ValueError("answer rubric needs user-reviewed freeze")
        for name, source in self.source_revisions.items():
            if observed_hashes.get(source.document_id) != source.source_sha256:
                raise ValueError(f"source revision changed for {name}")


def load_rubric(path: Path) -> AnswerRubric:
    return AnswerRubric.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def rubric_fingerprint(rubric: AnswerRubric) -> str:
    data = rubric.model_dump(mode="json")
    if rubric.version == 3:
        data.pop("criteria_changes", None)
        for question in data["questions"]:
            question.pop("checks", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


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
