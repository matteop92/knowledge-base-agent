"""Grounded chunk context and evidence cards through the application's model interface."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import Field

from app.ingestion.contracts import (
    Chunk, ChunkSet, EvidenceCard, ParsedDocument, SourceSpan, StrictRecord,
)
from app.ingestion.run_manifest import RunManifest, write_artifact_json
from app.ingestion.transcript_parser import _line_span
from app.models.client import ModelClient, ModelClientError, ModelSpec, ModelUsage


PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts"
CONTEXT_PROMPT_VERSION = "chunk_context_v1"
CARDS_PROMPT_VERSION = "evidence_cards_v1"
CONTEXT_RECIPE_VERSION = "chunk-neighbors-v1"
CARDS_RECIPE_VERSION = "source-offset-cards-v1"
TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
CARD_TYPES = [
    "example", "principle", "claim", "counterexample", "framework",
    "tactic", "mistake", "lesson",
]


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


class EvidenceCardsArtifact(StrictRecord):
    document_id: str
    run_id: str
    source_sha256: str
    chunk_id: str
    cards: list[EvidenceCard]
    model: ModelSpec
    prompt_version: str
    extraction_version: str
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


def _check_source(document: ParsedDocument, chunks: ChunkSet, manifest: RunManifest) -> None:
    chunks.validate_against(document)
    if (manifest.document_id, manifest.run_id, manifest.source_sha256) != (
        document.document_id, document.run_id, document.source_sha256
    ):
        raise EnrichmentError("run manifest does not match the parsed source")


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
) -> ChunkContext:
    _check_source(document, chunks, manifest)
    if chunk.chunk_id not in {item.chunk_id for item in chunks.chunks}:
        raise EnrichmentError("chunk is not in its declared variant")
    prompt, prompt_hash = _prompt(CONTEXT_PROMPT_VERSION)
    version_inputs = {
        "chunk_id": chunk.chunk_id,
        "chunking_version": chunks.chunking_version,
        "prompt_version": CONTEXT_PROMPT_VERSION,
        "prompt_sha256": prompt_hash,
        "input_recipe_version": CONTEXT_RECIPE_VERSION,
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
            max_output_tokens=180,
        )
        context = result.data.get("context")
        if not isinstance(context, str) or not context.strip():
            raise EnrichmentError("model returned an empty context")
        artifact = ChunkContext(
            document_id=document.document_id, run_id=document.run_id,
            source_sha256=document.source_sha256, chunk_id=chunk.chunk_id,
            source_spans=chunk.source_spans, context=context.strip(), model=model,
            prompt_version=CONTEXT_PROMPT_VERSION,
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


def _card_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "card_type": {"type": "string", "enum": CARD_TYPES},
        "statement": {"type": "string"},
        "situation": {"type": ["string", "null"]},
        "action": {"type": ["string", "null"]},
        "result": {"type": ["string", "null"]},
        "principle": {"type": ["string", "null"]},
        "evidence_start_char": {"type": "integer"},
        "evidence_end_char": {"type": "integer"},
        "evidence_text": {"type": "string"},
    }
    return {
        "type": "object",
        "properties": {
            "cards": {
                "type": "array",
                "items": {
                    "type": "object", "properties": properties,
                    "required": list(properties), "additionalProperties": False,
                },
            }
        },
        "required": ["cards"], "additionalProperties": False,
    }


def _validated_card(
    item: dict[str, Any], index: int, document: ParsedDocument, chunk: Chunk,
) -> EvidenceCard:
    start, end = item.get("evidence_start_char"), item.get("evidence_end_char")
    if type(start) is not int or type(end) is not int or not (0 <= start < end <= len(document.source_text)):
        raise EnrichmentError("card has invalid evidence offsets")
    supporting = next(
        (
            block for block in document.blocks
            if block.index in chunk.block_indices
            and (block.content_span or block.span).start_char <= start
            and end <= (block.content_span or block.span).end_char
        ), None,
    )
    if supporting is None:
        raise EnrichmentError("card evidence is outside its source block")
    attribution = (
        "personal_note" if supporting.content_role == "personal_note"
        else supporting.speaker or (
            document.metadata.authors[0] if len(document.metadata.authors) == 1 else None
        )
    )
    card = EvidenceCard(
        card_id=hashlib.sha256(
            f"{chunk.chunk_id}|{index}|{start}:{end}".encode()
        ).hexdigest()[:24],
        card_type=item["card_type"], statement=item["statement"],
        situation=item.get("situation"), action=item.get("action"),
        result=item.get("result"), principle=item.get("principle"),
        principle_is_derived=item.get("principle") is not None,
        evidence_span=_line_span(document.source_text, start, end),
        evidence_text=item["evidence_text"],
        document_id=document.document_id, run_id=document.run_id,
        source_sha256=document.source_sha256, chunk_id=chunk.chunk_id,
        content_role=supporting.content_role, attribution=attribution,
    )
    card.validate_against(document, chunk)
    return card


def extract_evidence_cards(
    document: ParsedDocument, chunks: ChunkSet, chunk: Chunk,
    client: ModelClient, model: ModelSpec, manifest: RunManifest, run_dir: Path,
    *, max_input_tokens: int = 1600,
) -> EvidenceCardsArtifact:
    _check_source(document, chunks, manifest)
    if chunk.chunk_id not in {item.chunk_id for item in chunks.chunks}:
        raise EnrichmentError("chunk is not in its declared variant")
    prompt, prompt_hash = _prompt(CARDS_PROMPT_VERSION)
    version_inputs = {
        "chunk_id": chunk.chunk_id,
        "chunking_version": chunks.chunking_version,
        "prompt_version": CARDS_PROMPT_VERSION,
        "prompt_sha256": prompt_hash,
        "input_recipe_version": CARDS_RECIPE_VERSION,
        "extraction_version": "cards-v1",
    }
    record = manifest.begin("cards", version_inputs, model)
    existing = _load_ready(run_dir, record, EvidenceCardsArtifact)
    if existing:
        return existing
    manifest.save(run_dir)
    try:
        result = client.generate_json(
            model=model, system_prompt=prompt,
            input_text=_input(document, chunk, max_input_tokens=max_input_tokens),
            schema_name="evidence_cards", json_schema=_card_schema(),
            max_output_tokens=700,
        )
        proposed = result.data.get("cards")
        if not isinstance(proposed, list) or any(not isinstance(item, dict) for item in proposed):
            raise EnrichmentError("model returned malformed cards")
        cards = [_validated_card(item, index, document, chunk) for index, item in enumerate(proposed)]
        artifact = EvidenceCardsArtifact(
            document_id=document.document_id, run_id=document.run_id,
            source_sha256=document.source_sha256, chunk_id=chunk.chunk_id,
            cards=cards, model=model, prompt_version=CARDS_PROMPT_VERSION,
            extraction_version="cards-v1", usage=result.usage,
        )
        path = write_artifact_json(
            run_dir, "cards", record.artifact_id, artifact.model_dump(mode="json")
        )
        manifest.succeed(record.artifact_id, path, result.usage)
        manifest.save(run_dir)
        return artifact
    except (ModelClientError, EnrichmentError, ValueError, KeyError, TypeError, OSError) as exc:
        manifest.fail(record.artifact_id, type(exc).__name__)
        manifest.save(run_dir)
        raise
