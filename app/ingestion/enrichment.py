"""Grounded chunk context through the application's model interface."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import Field

from app.ingestion.contracts import (
    Chunk, ChunkSet, ParsedDocument, SourceSpan, StrictRecord,
)
from app.ingestion.run_manifest import RunManifest, write_artifact_json
from app.models.client import ModelClient, ModelClientError, ModelSpec, ModelUsage


PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts"
CONTEXT_PROMPT_VERSION = "chunk_context_v1"
CONTEXT_RECIPE_VERSION = "chunk-neighbors-v1"
TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class EnrichmentError(ValueError):
    """Model output could not be made into a grounded artifact."""


class ChunkContext(StrictRecord):
    document_id: str
    run_id: str
    source_sha256: str
    chunk_id: str
    source_spans: list[SourceSpan]
    context: str = Field(min_length=1)
    model: ModelSpec
    prompt_version: str
    input_recipe_version: str
    usage: ModelUsage


def _prompt(version: str) -> tuple[str, str]:
    body = (PROMPT_ROOT / f"{version}.md").read_text(encoding="utf-8")
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def _input(
    document: ParsedDocument, chunk: Chunk, *, neighbor_text: str = "",
    max_input_tokens: int = 1600,
) -> str:
    neighbor = neighbor_text[:400]
    text = (
        f"Title: {document.metadata.title}\n"
        f"Section: {' > '.join(chunk.heading_path or [])}\n"
        f"Roles: {', '.join(chunk.content_roles)}\n"
        f"Source spans: {[(span.start_char, span.end_char) for span in chunk.source_spans]}\n"
        f"Neighbor excerpt: {neighbor}\n"
        f"Raw chunk:\n{chunk.raw_text}"
    )
    if len(TOKEN.findall(text)) > max_input_tokens:
        raise EnrichmentError("bounded model input budget is too small for this chunk")
    return text


def _check_source(
    document: ParsedDocument, chunks: ChunkSet,
    manifest: RunManifest, client: ModelClient,
) -> None:
    chunks.validate_against(document)
    if (manifest.document_id, manifest.run_id, manifest.source_sha256) != (
        document.document_id, document.run_id, document.source_sha256
    ):
        raise EnrichmentError("run manifest does not match the parsed source")
    manifest.require_client_selection(client)


def _load_ready(run_dir: Path, record, model_type):
    if record.status == "ready" and record.path:
        path = run_dir / record.path
        if path.is_file():
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    return None


def generate_chunk_context(
    document: ParsedDocument, chunks: ChunkSet, chunk: Chunk,
    client: ModelClient, model: ModelSpec, manifest: RunManifest, run_dir: Path,
    *, neighbor_text: str = "", max_input_tokens: int = 1600,
    prompt_version: str = CONTEXT_PROMPT_VERSION,
) -> ChunkContext:
    _check_source(document, chunks, manifest, client)
    if chunk.chunk_id not in {item.chunk_id for item in chunks.chunks}:
        raise EnrichmentError("chunk is not in its declared variant")
    prompt, prompt_hash = _prompt(prompt_version)
    version_inputs = {
        "chunk_id": chunk.chunk_id,
        "chunking_version": chunks.chunking_version,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_hash,
        "input_recipe_version": CONTEXT_RECIPE_VERSION,
        "neighbor_sha256": hashlib.sha256(neighbor_text[:400].encode("utf-8")).hexdigest(),
    }
    record = manifest.begin("context", version_inputs, model)
    existing = _load_ready(run_dir, record, ChunkContext)
    if existing:
        return existing
    manifest.save(run_dir)
    try:
        input_text = _input(
            document, chunk, neighbor_text=neighbor_text,
            max_input_tokens=max_input_tokens,
        )
        result = client.generate_json(
            model=model, system_prompt=prompt, input_text=input_text,
            schema_name="chunk_context",
            json_schema={
                "type": "object", "properties": {"context": {"type": "string"}},
                "required": ["context"], "additionalProperties": False,
            },
            max_output_tokens=360,
        )
        context = result.data.get("context")
        if not isinstance(context, str) or not context.strip():
            raise EnrichmentError("model returned an empty context")
        artifact = ChunkContext(
            document_id=document.document_id, run_id=document.run_id,
            source_sha256=document.source_sha256, chunk_id=chunk.chunk_id,
            source_spans=chunk.source_spans, context=context.strip(), model=model,
            prompt_version=prompt_version,
            input_recipe_version=CONTEXT_RECIPE_VERSION, usage=result.usage,
        )
        path = write_artifact_json(
            run_dir, "context", record.artifact_id, artifact.model_dump(mode="json")
        )
        manifest.succeed(record.artifact_id, path, result.usage)
        manifest.save(run_dir)
        return artifact
    except (ModelClientError, EnrichmentError, ValueError, KeyError, TypeError, OSError) as exc:
        manifest.fail(record.artifact_id, type(exc).__name__)
        manifest.save(run_dir)
        raise
