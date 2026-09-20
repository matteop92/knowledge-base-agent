"""The small model interface needed by enrichment and embeddings."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field

from app.ingestion.contracts import StrictRecord


class ModelClientError(RuntimeError):
    """A model request failed without exposing source text or credentials."""


class ModelSpec(StrictRecord):
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


class ModelUsage(StrictRecord):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class GenerationResult(StrictRecord):
    data: dict[str, Any]
    model: ModelSpec
    usage: ModelUsage = Field(default_factory=ModelUsage)


class EmbeddingResult(StrictRecord):
    vectors: list[list[float]]
    model: ModelSpec
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ModelClient(Protocol):
    def generate_json(
        self, *, model: ModelSpec, system_prompt: str, input_text: str,
        schema_name: str, json_schema: dict[str, Any], max_output_tokens: int,
    ) -> GenerationResult: ...

    def embed(
        self, *, model: ModelSpec, texts: list[str], dimensions: int | None = None,
    ) -> EmbeddingResult: ...
