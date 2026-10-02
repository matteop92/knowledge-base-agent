"""Explicit storage selections, independent of the local run manifest."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from app.ingestion.contracts import StrictRecord
from app.ingestion.embeddings import EmbeddingSpec


def spec_id(spec: EmbeddingSpec) -> str:
    canonical = spec.model_dump(mode="json", exclude={"created_at"})
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SearchProfile(StrictRecord):
    profile_id: str = Field(min_length=1)
    variant: Literal["fixed_raw", "structural_raw"]
    chunking_version: str = Field(min_length=1)
    embedding_spec_id: str = Field(min_length=64, max_length=64)
    text_config: Literal["simple"] = "simple"
    required_artifact_kinds: list[Literal["context", "summary"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_artifacts(self) -> SearchProfile:
        if len(set(self.required_artifact_kinds)) != len(self.required_artifact_kinds):
            raise ValueError("required artifact kinds must be unique")
        return self


class PublicationSelection(StrictRecord):
    run_id: str = Field(min_length=1)
    profile_id: str = Field(min_length=1)
    required_artifact_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_artifacts(self) -> PublicationSelection:
        if len(set(self.required_artifact_ids)) != len(self.required_artifact_ids):
            raise ValueError("required artifact IDs must be unique")
        return self


class SearchHit(StrictRecord):
    rank: int
    score: float
    document_id: str
    run_id: str
    source_sha256: str
    root_alias: str
    relative_path: str
    chunk_id: str
    source_spans: list[dict]
    raw_text: str
    title: str | None = None
    people: list[str] = Field(default_factory=list)


class FusedHit(StrictRecord):
    rank: int
    rrf_score: float
    channels: list[str]
    hit: SearchHit


class SourcePassage(StrictRecord):
    document_id: str
    run_id: str
    source_sha256: str
    root_alias: str
    relative_path: str
    chunk_id: str
    start_char: int
    end_char: int
    text: str
    span_end_char: int | None = None
    next_offset: int | None = None
    truncated: bool = False
