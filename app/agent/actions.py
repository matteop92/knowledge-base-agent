"""Strict versioned JSON actions independent of provider-native tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.ingestion.contracts import StrictRecord


class AgentAction(StrictRecord):
    version: Literal[1]
    action: Literal["search", "search_cards", "read", "finish"]
    query: str | None
    reference_id: str | None
    span_index: int | None
    answer: str | None
    citation_ids: list[str]
    completeness: Literal["complete", "partial", "unsupported"] | None
    gaps: list[str]
    sufficient: bool | None

    @model_validator(mode="after")
    def check_action(self) -> AgentAction:
        if self.action in ("search", "search_cards") and (not self.query or not self.query.strip()):
            raise ValueError("search action needs a query")
        if self.action == "read" and not self.reference_id:
            raise ValueError("read action needs a reference ID")
        if self.action == "read" and (self.span_index is None or self.span_index < 0):
            raise ValueError("read action needs a nonnegative span index")
        if self.action == "finish":
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
