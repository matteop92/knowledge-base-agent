"""Resumable, versioned local embeddings for raw or contextualized chunks."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.ingestion.contracts import ChunkSet, ParsedDocument, SourceSpan, StrictRecord
from app.ingestion.enrichment import ChunkContext
from app.ingestion.run_manifest import RunManifest, write_artifact_json
from app.models.client import ModelClient, ModelClientError, ModelSpec, ModelUsage


class EmbeddingSpec(StrictRecord):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    dimensions: int = Field(gt=0)
    distance_metric: Literal["cosine", "dot", "euclidean"]
    input_recipe_version: Literal["raw-v1", "contextualized-v1"]
    context_prompt_version: str | None = None
    chunking_version: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_spec(self) -> ModelSpec:
        return ModelSpec(
            provider=self.provider, model_id=self.model,
            model_version=self.model_version,
        )


class EmbeddedChunk(StrictRecord):
    chunk_id: str
    source_spans: list[SourceSpan]
    vector: list[float]


class EmbeddingBatch(StrictRecord):
    document_id: str
    run_id: str
    source_sha256: str
    spec: EmbeddingSpec
    chunks: list[EmbeddedChunk]
    usage: ModelUsage


class EmbeddingProgress(StrictRecord):
    completed_chunks: int
    total_chunks: int
    ready: bool
    artifact_ids: list[str]


def embed_chunks(
    document: ParsedDocument, chunks: ChunkSet, client: ModelClient,
    spec: EmbeddingSpec, manifest: RunManifest, run_dir: Path,
    *, contexts: dict[str, ChunkContext] | None = None,
    batch_size: int = 16, max_batches: int | None = None,
) -> EmbeddingProgress:
    chunks.validate_against(document)
    if (manifest.document_id, manifest.run_id, manifest.source_sha256) != (
        document.document_id, document.run_id, document.source_sha256
    ) or spec.chunking_version != chunks.chunking_version:
        raise ValueError("embedding inputs do not match the source run")
    if batch_size < 1 or (max_batches is not None and max_batches < 0):
        raise ValueError("invalid embedding batch budget")
    if spec.input_recipe_version == "contextualized-v1":
        if not contexts or not spec.context_prompt_version:
            raise ValueError("contextualized embeddings need versioned contexts")
        if any(
            chunk.chunk_id not in contexts
            or contexts[chunk.chunk_id].chunk_id != chunk.chunk_id
            or contexts[chunk.chunk_id].source_spans != chunk.source_spans
            or contexts[chunk.chunk_id].prompt_version != spec.context_prompt_version
            or (contexts[chunk.chunk_id].document_id, contexts[chunk.chunk_id].run_id,
                contexts[chunk.chunk_id].source_sha256) != (
                    document.document_id, document.run_id, document.source_sha256
                )
            for chunk in chunks.chunks
        ):
            raise ValueError("context is missing or from a different source version")
    elif spec.context_prompt_version is not None or contexts is not None:
        raise ValueError("raw embeddings cannot use contexts or a context prompt")
    model = spec.model_spec()
    completed = 0
    artifact_ids: list[str] = []
    new_batches = 0
    for start in range(0, len(chunks.chunks), batch_size):
        batch = chunks.chunks[start:start + batch_size]
        version = {
            "spec": spec.model_dump(mode="json", exclude={"created_at"}),
            "chunk_ids": [chunk.chunk_id for chunk in batch],
            "context_sha256": [
                hashlib.sha256(contexts[chunk.chunk_id].model_dump_json().encode()).hexdigest()
                if contexts else None
                for chunk in batch
            ],
        }
        record = manifest.begin("embedding", version, model)
        artifact_ids.append(record.artifact_id)
        if record.status == "ready" and record.path and (run_dir / record.path).is_file():
            existing = EmbeddingBatch.model_validate_json((run_dir / record.path).read_text(encoding="utf-8"))
            if (
                (existing.document_id, existing.run_id, existing.source_sha256)
                != (document.document_id, document.run_id, document.source_sha256)
                or existing.spec.model_dump(exclude={"created_at"})
                != spec.model_dump(exclude={"created_at"})
                or len(existing.chunks) != len(batch)
                or [item.chunk_id for item in existing.chunks] != [item.chunk_id for item in batch]
                or any(
                    item.source_spans != chunk.source_spans
                    for item, chunk in zip(existing.chunks, batch)
                )
                or any(
                    len(item.vector) != spec.dimensions
                    or any(not math.isfinite(value) for value in item.vector)
                    for item in existing.chunks
                )
            ):
                raise ValueError("stored embedding batch is invalid")
            completed += len(batch)
            continue
        if max_batches is not None and new_batches >= max_batches:
            manifest.save(run_dir)
            break
        manifest.save(run_dir)
        try:
            texts = [
                (f"{contexts[chunk.chunk_id].context}\n\n{chunk.raw_text}"
                 if contexts else chunk.raw_text)
                for chunk in batch
            ]
            result = client.embed(model=model, texts=texts, dimensions=spec.dimensions)
            if len(result.vectors) != len(batch):
                raise ValueError("embedding response count mismatch")
            if any(
                len(vector) != spec.dimensions
                or any(not math.isfinite(value) for value in vector)
                for vector in result.vectors
            ):
                raise ValueError("embedding dimension or finite-value check failed")
            artifact = EmbeddingBatch(
                document_id=document.document_id, run_id=document.run_id,
                source_sha256=document.source_sha256, spec=spec,
                chunks=[EmbeddedChunk(
                    chunk_id=chunk.chunk_id, source_spans=chunk.source_spans,
                    vector=vector,
                ) for chunk, vector in zip(batch, result.vectors)],
                usage=result.usage,
            )
            path = write_artifact_json(
                run_dir, "embedding", record.artifact_id, artifact.model_dump(mode="json")
            )
            manifest.succeed(record.artifact_id, path, result.usage)
            manifest.save(run_dir)
            completed += len(batch)
            new_batches += 1
        except (ModelClientError, ValueError, OSError, TypeError) as exc:
            manifest.fail(record.artifact_id, type(exc).__name__)
            manifest.save(run_dir)
            raise
    return EmbeddingProgress(
        completed_chunks=completed, total_chunks=len(chunks.chunks),
        ready=completed == len(chunks.chunks), artifact_ids=artifact_ids,
    )
