"""Revision-bound human answer rubric; comparison requires an approved freeze."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from app.ingestion.contracts import StrictRecord


class SourceRevision(StrictRecord):
    document_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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
        return self

    def require_comparison_ready(self, observed_hashes: dict[str, str]) -> None:
        if self.status != "frozen" or any(question.review_status != "approved" for question in self.questions):
            raise ValueError("answer rubric needs user-reviewed freeze")
        for name, source in self.source_revisions.items():
            if observed_hashes.get(source.document_id) != source.source_sha256:
                raise ValueError(f"source revision changed for {name}")


def load_rubric(path: Path) -> AnswerRubric:
    return AnswerRubric.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
