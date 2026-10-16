"""Answer result and per-invocation limits."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.ingestion.contracts import StrictRecord
from app.storage.records import SourcePassage


class AnswerLimits(StrictRecord):
    max_model_calls: int = Field(default=12, ge=1, le=40)
    max_searches: int = Field(default=5, ge=1, le=20)
    max_finds: int = Field(default=2, ge=0, le=10)
    max_reads: int = Field(default=8, ge=1, le=30)
    max_seconds: float = Field(default=120, gt=0, le=600)
    max_output_tokens: int = Field(default=900, ge=100, le=3000)

    @classmethod
    def broad(cls) -> AnswerLimits:
        return cls(max_model_calls=20, max_searches=7,
                   max_finds=4, max_reads=10, max_seconds=420, max_output_tokens=2500)


class AnswerResult(StrictRecord):
    answer: str
    completeness: Literal["complete", "partial", "unsupported"]
    citations: dict[str, SourcePassage]
    gaps: list[str]
    actions: list[dict]
    model_calls: int
    inspected: dict[str, SourcePassage] = Field(default_factory=dict)
    needs: dict[str, dict] = Field(default_factory=dict)
    finalization_reason: str | None = None
    host_search: dict | None = None
    termination: Literal["completed", "exhausted", "model_failed", "tool_failed"] = "completed"
    elapsed_seconds: float = Field(default=0, ge=0)
