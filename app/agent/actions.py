"""Strict versioned JSON actions independent of provider-native tools."""

from __future__ import annotations

import copy
from typing import Literal

from pydantic import Field, model_validator

from app.ingestion.contracts import StrictRecord


class NeedUpdate(StrictRecord):
    id: str = Field(pattern=r"^N[1-9][0-9]*$")
    description: str = Field(min_length=3, max_length=500)
    status: Literal["open", "resolved", "excluded"]
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str | None = None

    @model_validator(mode="after")
    def check_support(self) -> NeedUpdate:
        if self.status == "resolved" and not self.evidence_ids:
            raise ValueError("resolved need requires inspected evidence IDs")
        if self.status == "excluded" and not self.rationale:
            raise ValueError("excluded need requires a scope reason")
        return self


class AgentAction(StrictRecord):
    version: Literal[1, 2]
    action: Literal["search", "search_cards", "find", "read", "finish"]
    query: str | None
    reference_id: str | None
    span_index: int | None
    answer: str | None
    citation_ids: list[str]
    completeness: Literal["complete", "partial", "unsupported"] | None
    gaps: list[str]
    sufficient: bool | None
    offset: int | None = None
    need_updates: list[NeedUpdate] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_action(self) -> AgentAction:
        if self.version == 1 and (self.action == "find" or self.offset is not None or self.need_updates):
            raise ValueError("version 1 cannot use find, offsets, or investigation needs")
        if self.action != "finish" and (self.answer is not None or self.citation_ids or self.completeness is not None
                                        or self.gaps or self.sufficient is not None):
            raise ValueError("only finish may provide answer, citations, completeness, gaps, or sufficiency")
        if self.action in ("search", "search_cards") and (not self.query or not self.query.strip()):
            raise ValueError("search action needs a query")
        if self.action in ("search", "search_cards") and (self.reference_id is not None or self.span_index is not None):
            raise ValueError("search actions cannot select a reference or span")
        if self.action == "find" and (not self.reference_id or not self.query or not self.query.strip()):
            raise ValueError("find needs a reference ID and a query")
        if self.action == "find" and self.span_index is not None:
            raise ValueError("find does not select a span")
        if self.action == "read" and not self.reference_id:
            raise ValueError("read action needs a reference ID")
        if self.action == "read" and (self.span_index is None or self.span_index < 0):
            raise ValueError("read action needs a nonnegative span index")
        if self.action == "read" and self.query is not None:
            raise ValueError("read cannot search with query; select a span or use search")
        if self.action == "read" and self.offset is not None and self.offset < 0:
            raise ValueError("read offset must be nonnegative")
        if self.action != "read" and self.offset is not None:
            raise ValueError("only read may have an offset")
        if self.action == "finish":
            if self.query is not None or self.reference_id is not None or self.span_index is not None:
                raise ValueError("finish cannot run a search or read")
            if not self.answer or self.completeness is None or self.sufficient is None:
                raise ValueError("finish needs answer, completeness, and sufficiency decision")
            if self.completeness == "complete" and not self.sufficient:
                raise ValueError("complete answer requires a positive sufficiency decision")
            if self.completeness == "complete" and self.gaps:
                raise ValueError("complete answer cannot retain evidence gaps")
            if self.completeness != "complete" and not self.gaps:
                raise ValueError("incomplete answer must name evidence gaps")
            if self.completeness == "unsupported" and (self.sufficient or self.citation_ids):
                raise ValueError("unsupported answer cannot claim sufficient cited evidence")
        return self


ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "version": {"type": "integer", "enum": [1]},
        "action": {"type": "string", "enum": ["search", "search_cards", "read", "finish"]},
        "query": {"type": ["string", "null"]},
        "reference_id": {"type": ["string", "null"]},
        "span_index": {"type": ["integer", "null"]},
        "answer": {"type": ["string", "null"]},
        "citation_ids": {"type": "array", "items": {"type": "string"}},
        "completeness": {"type": ["string", "null"], "enum": ["complete", "partial", "unsupported", None]},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "sufficient": {"type": ["boolean", "null"]},
    },
    "required": ["version", "action", "query", "reference_id", "span_index", "answer", "citation_ids",
                 "completeness", "gaps", "sufficient"],
    "additionalProperties": False,
}

ACTION_SCHEMA_V1 = ACTION_SCHEMA
ACTION_SCHEMA_V2 = copy.deepcopy(ACTION_SCHEMA_V1)
ACTION_SCHEMA_V2["properties"]["version"]["enum"] = [2]
ACTION_SCHEMA_V2["properties"]["action"]["enum"] = ["search", "search_cards", "find", "read", "finish"]
ACTION_SCHEMA_V2["properties"]["offset"] = {"type": ["integer", "null"], "description": "Character offset inside the selected original span for continuation reads."}
ACTION_SCHEMA_V2["properties"]["need_updates"] = {
    "type": "array", "description": "Track requested and newly surfaced material needs; resolve only with inspected evidence.",
    "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "description": {"type": "string"},
        "status": {"type": "string", "enum": ["open", "resolved", "excluded"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": ["string", "null"]}},
        "required": ["id", "description", "status", "evidence_ids", "rationale"],
        "additionalProperties": False}}
ACTION_SCHEMA_V2["required"] = [*ACTION_SCHEMA_V1["required"], "offset", "need_updates"]
