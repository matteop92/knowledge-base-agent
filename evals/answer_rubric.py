"""Revision-bound human answer rubric; comparison requires an approved freeze.

Two layouts load here: the historical facet rubric (`AnswerRubric`, version 3 and
the first version-4 draft) and the three-dimension `Checklist`.
"""

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


CHECK_LETTERS = {"coverage": "C", "grounding": "G", "example": "E"}
RATINGS = ("pass", "fail", "not_applicable", "unreviewed")


class ChecklistCheck(StrictRecord):
    """One binary check in the three-dimension checklist."""
    id: str = Field(pattern=r"^Q[1-8]\.[CGE][1-9][0-9]*$")
    dimension: Literal["coverage", "grounding", "example"]
    criterion: str = Field(min_length=1)
    type: Literal["primary", "detail"] | None = None
    parent_id: str | None = None
    applicability: Literal["required", "conditional", "optional"] = "required"
    condition: str | None = None
    evidence_alternatives: list[EvidenceAlternative] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_shape(self) -> ChecklistCheck:
        if self.id.split(".")[1][0] != CHECK_LETTERS[self.dimension]:
            raise ValueError("check ID letter does not match its dimension")
        if self.dimension == "coverage":
            if self.type is None:
                raise ValueError("coverage check must be primary or detail")
            if (self.type == "detail") != (self.parent_id is not None):
                raise ValueError("a detail needs a parent and a primary has none")
            allowed = ("conditional", "optional") if self.type == "detail" else ("required", "optional")
        else:
            if self.type is not None or self.parent_id is not None:
                raise ValueError("only coverage checks have primary/detail structure")
            allowed = ("required", "conditional") if self.dimension == "example" else ("required",)
        if self.applicability not in allowed:
            raise ValueError("applicability is not valid for this kind of check")
        if self.applicability == "conditional" and not self.condition:
            raise ValueError("conditional check needs a condition")
        if self.dimension != "grounding" and not any(
                alt.start_char is not None for alt in self.evidence_alternatives):
            raise ValueError("coverage and example checks need a source-span alternative")
        return self


class ChecklistQuestion(StrictRecord):
    id: str = Field(pattern=r"^Q[1-8]$")
    question: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    answerability: str = Field(pattern=r"^(full|partial|unsupported)$")
    # Relevance scope: a main point of an answer outside it counts as one failed check.
    in_scope: str | None = None
    out_of_scope: str | None = None
    sources: list[str] = Field(min_length=1)
    checks: list[ChecklistCheck] = Field(min_length=1)

    @model_validator(mode="after")
    def check_dependencies(self) -> ChecklistQuestion:
        by_id = {check.id: check for check in self.checks}
        if len(by_id) != len(self.checks) or any(not key.startswith(self.id + ".") for key in by_id):
            raise ValueError("checklist needs unique question-scoped check IDs")
        for check in self.checks:
            if any(alt.source not in self.sources for alt in check.evidence_alternatives):
                raise ValueError("check evidence cites a source outside its question")
            if check.parent_id is None:
                continue
            parent = by_id.get(check.parent_id)
            if parent is None or parent.type != "primary":
                raise ValueError("a detail's parent must be a primary check of the same question")
            if parent.applicability == "optional" and check.applicability != "optional":
                raise ValueError("a detail of an optional primary must be optional")
        return self


class Checklist(StrictRecord):
    """Three-dimension checklist: coverage, grounding, example; binary ratings."""
    version: int = Field(ge=4)
    status: str = Field(pattern=r"^(draft|frozen)$")
    source_revisions: dict[str, SourceRevision]
    questions: list[ChecklistQuestion]
    criteria_changes: list[str] = Field(default_factory=list)
    approved_questions: list[str] = Field(default_factory=list)
    open_decisions: list[str] = Field(default_factory=list)

    @property
    def layout(self) -> str:
        return "three-dimension"

    @model_validator(mode="after")
    def check_questions(self) -> Checklist:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)) or set(ids) != {f"Q{n}" for n in range(1, 9)}:
            raise ValueError("checklist must have exactly Q1 through Q8")
        if any(source not in self.source_revisions
               for question in self.questions for source in question.sources):
            raise ValueError("question cites an unknown source revision")
        if not set(self.approved_questions).issubset(ids):
            raise ValueError("approval names an unknown question")
        if self.status == "frozen" and (self.open_decisions or set(self.approved_questions) != set(ids)):
            raise ValueError("frozen checklist needs every question approved and no open decisions")
        if self.status == "frozen" and any(not question.in_scope or not question.out_of_scope
                                           for question in self.questions):
            raise ValueError("frozen checklist needs a relevance scope for every question")
        return self

    def require_source_revisions(self, observed_hashes: dict[str, str]) -> None:
        for name, source in self.source_revisions.items():
            if observed_hashes.get(source.document_id) != source.source_sha256:
                raise ValueError(f"source revision changed for {name}")

    def require_comparison_ready(self, observed_hashes: dict[str, str]) -> None:
        if self.status != "frozen":
            raise ValueError("answer rubric needs user-reviewed freeze")
        self.require_source_revisions(observed_hashes)


def score_checklist_ratings(checks: list[dict], ratings: dict[str, str]) -> dict:
    """Validate one answer's ratings against the dependency rules and tally them.

    A detail is rated only when its primary passes; otherwise it is not applicable.
    Optional checks are reported apart and never enter the scored totals.
    """
    by_id = {check["id"]: check for check in checks}
    if set(ratings) != set(by_id):
        raise ValueError("ratings must cover each check exactly once")
    if any(value not in RATINGS for value in ratings.values()):
        raise ValueError("rating must be pass, fail, not_applicable, or unreviewed")
    dimensions = {name: {**dict.fromkeys(RATINGS, 0), "optional": dict.fromkeys(RATINGS[:3], 0)}
                  for name in CHECK_LETTERS}
    coverage = {"primary": dict.fromkeys(RATINGS, 0), "detail": dict.fromkeys(RATINGS, 0)}
    for check in checks:
        rating = ratings[check["id"]]
        parent = check.get("parent_id")
        if parent is not None:
            expected = {"pass": ("pass", "fail", "unreviewed"), "unreviewed": ("unreviewed",)}.get(
                ratings[parent], ("not_applicable",))
            if ratings[parent] == "pass" and check["applicability"] == "optional":
                expected += ("not_applicable",)  # optional content that the answer leaves out
            if rating not in expected:
                raise ValueError(f"{check['id']} must be {' or '.join(expected)} given its primary")
        elif rating == "not_applicable" and check["applicability"] == "required":
            raise ValueError(f"required check {check['id']} cannot be not applicable")
        tally = dimensions[check["dimension"]]
        if rating == "unreviewed":
            tally["unreviewed"] += 1
        elif check["applicability"] == "optional":
            tally["optional"][rating] += 1
        else:
            tally[rating] += 1
        if check["dimension"] == "coverage" and check["applicability"] != "optional":
            coverage[check["type"]][rating] += 1
    for tally in dimensions.values():
        tally["scored"] = tally["pass"] + tally["fail"]
    return {"complete": all(tally["unreviewed"] == 0 for tally in dimensions.values()),
            "dimensions": dimensions, "coverage_required": coverage}


def load_rubric(path: Path) -> AnswerRubric | Checklist:
    """Load either layout; the facet rubric is the one that declares answer expectations."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "answer_expectations" not in data:
        return Checklist.model_validate(data)
    return AnswerRubric.model_validate(data)


def rubric_fingerprint(rubric: AnswerRubric | Checklist) -> str:
    data = rubric.model_dump(mode="json")
    if rubric.version == 3:
        data.pop("criteria_changes", None)
        for question in data["questions"]:
            question.pop("checks", None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def evidence_hits(rubric: AnswerRubric | Checklist, question_id: str, passages: list[dict]) -> tuple[int, int]:
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
