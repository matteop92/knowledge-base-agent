"""Grounded chunk context and evidence cards through the application's model interface."""

from __future__ import annotations

import hashlib
import json
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
CARD_CHECK_PROMPT_VERSION = "evidence_card_check_v1"
CARD_CHECK_FIELDS = ("statement", "situation", "action", "result", "principle")
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
    check: dict[str, Any] | None = None


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


def _card_schema(*, quote_location: bool = False, block_location: bool = False,
                 group_location: bool = False, rich_descriptions: bool = False) -> dict[str, Any]:
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
    if quote_location:
        del properties["evidence_start_char"]
        del properties["evidence_end_char"]
    if block_location:
        del properties["evidence_start_char"]
        del properties["evidence_end_char"]
        del properties["evidence_text"]
        properties["evidence_block_index"] = {"type": "integer"}
    if group_location:
        del properties["evidence_start_char"]
        del properties["evidence_end_char"]
        del properties["evidence_text"]
        properties["evidence_group_index"] = {"type": "integer"}
    if rich_descriptions:
        descriptions = {
            "statement": "One short useful fact or episode directly supported by this group.",
            "situation": "Particular starting context or problem; null for a general claim.",
            "action": "Reported action or explicit advice; distinguish did from advised.",
            "result": "Observed or reported outcome of this episode; null for predictions or aspirations.",
            "principle": "Supported derived generalization that adds to the statement; otherwise null.",
            "evidence_group_index": "Zero-based index of exactly one numbered source group.",
        }
        for key, description in descriptions.items():
            if key in properties:
                properties[key]["description"] = description
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
            and start < (block.content_span or block.span).end_char
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


def _validated_quoted_card(
    item: dict[str, Any], index: int, document: ParsedDocument, chunk: Chunk,
) -> EvidenceCard:
    """Locate a unique exact model quote within the chunk's source blocks."""
    quote = item.get("evidence_text")
    if not isinstance(quote, str) or not quote.strip():
        raise EnrichmentError("card evidence quote is empty")
    matches = []
    for block in document.blocks:
        if block.index not in chunk.block_indices:
            continue
        span = block.content_span or block.span
        offset = span.start_char
        while True:
            start = document.source_text.find(quote, offset, span.end_char)
            if start < 0:
                break
            matches.append((start, start + len(quote)))
            offset = start + 1
    if len(set(matches)) != 1:
        raise EnrichmentError("card evidence quote must occur exactly once in its source blocks")
    start, end = matches[0]
    return _validated_card({**item, "evidence_start_char": start,
                            "evidence_end_char": end}, index, document, chunk)


def _card_block_span(block, chunk: Chunk) -> tuple[int, int]:
    block_span = block.content_span or block.span
    intersections = [(max(block_span.start_char, span.start_char),
                      min(block_span.end_char, span.end_char))
                     for span in chunk.source_spans]
    intersections = [(start, end) for start, end in intersections if start < end]
    if len(intersections) != 1:
        raise EnrichmentError("card source block has no unique span within its chunk")
    return intersections[0]


def _eligible_card_block(text: str) -> bool:
    return len(text.strip()) >= 100 and text.rstrip().rstrip('"”\'')[-1:] in ".?!"


def _block_card_input(document: ParsedDocument, chunk: Chunk, max_input_tokens: int,
                      *, strict_blocks: bool = False) -> str:
    blocks = []
    for block in document.blocks:
        if block.index in chunk.block_indices:
            start, end = _card_block_span(block, chunk)
            body = document.source_text[start:end]
            if not strict_blocks or _eligible_card_block(body):
                blocks.append(f"[{block.index}] {body}")
    text = (f"Title: {document.metadata.title}\n"
            f"Section: {' > '.join(chunk.heading_path or [])}\n"
            f"Source blocks (use the bracketed block index as evidence_block_index):\n"
            + "\n".join(blocks))
    if len(TOKEN.findall(text)) > max_input_tokens:
        raise EnrichmentError("bounded model input budget is too small for this chunk")
    return text


def _validated_block_card(
    item: dict[str, Any], index: int, document: ParsedDocument, chunk: Chunk,
    *, strict_blocks: bool = False,
) -> EvidenceCard:
    block_index = item.get("evidence_block_index")
    if type(block_index) is not int or block_index not in chunk.block_indices:
        raise EnrichmentError("card selected an invalid source block")
    block = next(block for block in document.blocks if block.index == block_index)
    start, end = _card_block_span(block, chunk)
    quote = document.source_text[start:end]
    if not quote.strip():
        raise EnrichmentError("card selected an empty source block")
    if strict_blocks and not _eligible_card_block(quote):
        raise EnrichmentError("card selected a source fragment rather than a complete block")
    return _validated_card({**item, "evidence_start_char": start,
                            "evidence_end_char": end,
                            "evidence_text": quote}, index, document, chunk)


def _card_groups(document: ParsedDocument, chunk: Chunk, *,
                 split_highlights: bool = False) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    current_key: tuple[str, str | None] | None = None
    previous_index: int | None = None
    previous_kind: str | None = None
    for block in document.blocks:
        if block.index not in chunk.block_indices or block.kind == "heading":
            if current is not None:
                groups.append(current)
                current = None
            previous_index = None
            previous_kind = None
            continue
        start, end = _card_block_span(block, chunk)
        attribution = ("personal_note" if block.content_role == "personal_note"
                       else block.speaker or (
                           document.metadata.authors[0]
                           if len(document.metadata.authors) == 1 else None))
        key = (block.content_role, attribution)
        if (current is not None and key == current_key
                and previous_index is not None and block.index == previous_index + 1
                and not (split_highlights and (block.kind == "highlight" or previous_kind == "highlight"))
                and end - current[0] <= 1200):
            current = (current[0], end)
        else:
            if current is not None:
                groups.append(current)
            current = (start, end)
        current_key = key
        previous_index = block.index
        previous_kind = block.kind
    if current is not None:
        groups.append(current)
    return [(start, end) for start, end in groups
            if len(document.source_text[start:end].strip()) >= 100]


def _group_card_input(document: ParsedDocument, chunk: Chunk, max_input_tokens: int,
                      *, explicit_indices: bool = False,
                      split_highlights: bool = False) -> str:
    groups = _card_groups(document, chunk, split_highlights=split_highlights)
    body = "\n".join(f"[{index}] {document.source_text[start:end]}"
                     for index, (start, end) in enumerate(groups))
    index_hint = (f"Valid evidence_group_index values (zero-based): "
                  f"{', '.join(str(index) for index in range(len(groups)))}\n"
                  if explicit_indices else "")
    text = (f"Title: {document.metadata.title}\n"
            f"Section: {' > '.join(chunk.heading_path or [])}\n"
            f"{index_hint}"
            f"Evidence groups (use bracketed number as evidence_group_index):\n{body}")
    if len(TOKEN.findall(text)) > max_input_tokens:
        raise EnrichmentError("bounded model input budget is too small for this chunk")
    return text


def _single_group_input(document: ParsedDocument, chunk: Chunk, start: int, end: int) -> str:
    return (f"Title: {document.metadata.title}\n"
            f"Section: {' > '.join(chunk.heading_path or [])}\n"
            f"Evidence group (evidence_group_index is 0):\n[0] {document.source_text[start:end]}")


def _validated_group_card(
    item: dict[str, Any], index: int, document: ParsedDocument, chunk: Chunk,
    *, split_highlights: bool = False,
) -> EvidenceCard:
    group_index = item.get("evidence_group_index")
    groups = _card_groups(document, chunk, split_highlights=split_highlights)
    if type(group_index) is not int or not (0 <= group_index < len(groups)):
        raise EnrichmentError("card selected an invalid source group")
    start, end = groups[group_index]
    return _validated_card({**item, "evidence_start_char": start,
                            "evidence_end_char": end,
                            "evidence_text": document.source_text[start:end]},
                           index, document, chunk)


def _usage_sum(first: ModelUsage, second: ModelUsage) -> ModelUsage:
    def add(a: int | None, b: int | None) -> int | None:
        return None if a is None and b is None else (a or 0) + (b or 0)
    return ModelUsage(input_tokens=add(first.input_tokens, second.input_tokens),
                      output_tokens=add(first.output_tokens, second.output_tokens),
                      total_tokens=add(first.total_tokens, second.total_tokens))


def _checked_cards(
    cards: list[EvidenceCard], client: ModelClient, model: ModelSpec,
) -> tuple[list[EvidenceCard], dict[str, Any], ModelUsage]:
    """Second model pass: drop cards and clear fields their own passage does not support.

    Each request shows one evidence passage and only its cards, so a detail
    borrowed from a neighbouring passage cannot look supported.
    """
    summary = {"prompt_version": CARD_CHECK_PROMPT_VERSION, "proposed_cards": len(cards),
               "dropped_cards": 0, "cleared_fields": dict.fromkeys(CARD_CHECK_FIELDS[1:], 0)}
    prompt, _ = _prompt(CARD_CHECK_PROMPT_VERSION)
    verdict = {"type": "string", "enum": ["supported", "unsupported", "empty"]}
    schema = {"type": "object", "properties": {"checks": {"type": "array", "items": {
        "type": "object",
        "properties": {"card_index": {"type": "integer"},
                       **dict.fromkeys(CARD_CHECK_FIELDS, verdict),
                       "reason": {"type": ["string", "null"]}},
        "required": ["card_index", *CARD_CHECK_FIELDS, "reason"],
        "additionalProperties": False}}},
        "required": ["checks"], "additionalProperties": False}
    passages: dict[tuple[int, int], list[int]] = {}
    for index, card in enumerate(cards):
        passages.setdefault((card.evidence_span.start_char, card.evidence_span.end_char), []).append(index)
    usage = ModelUsage()
    verdicts: dict[int, dict[str, Any]] = {}
    for indices in passages.values():
        listing = [{"card_index": local, **{field: getattr(cards[index], field) for field in CARD_CHECK_FIELDS}}
                   for local, index in enumerate(indices)]
        result = client.generate_json(
            model=model, system_prompt=prompt,
            input_text=(f"Source passage:\n{cards[indices[0]].evidence_text}\n\n"
                        f"Proposed cards:\n{json.dumps(listing, ensure_ascii=False)}"),
            schema_name="evidence_card_check", json_schema=schema, max_output_tokens=1200,
        )
        usage = _usage_sum(usage, result.usage)
        checks = result.data.get("checks")
        if (not isinstance(checks, list) or any(not isinstance(item, dict) for item in checks)
                or sorted(item.get("card_index", -1) for item in checks) != list(range(len(indices)))):
            raise EnrichmentError("card check did not cover each proposed card exactly once")
        for item in checks:
            verdicts[indices[item["card_index"]]] = item
    kept = []
    for index, card in enumerate(cards):
        check = verdicts[index]
        if check.get("statement") != "supported":
            summary["dropped_cards"] += 1
            continue
        cleared = [field for field in CARD_CHECK_FIELDS[1:]
                   if getattr(card, field) is not None and check.get(field) != "supported"]
        for field in cleared:
            summary["cleared_fields"][field] += 1
        updates: dict[str, Any] = dict.fromkeys(cleared)
        if "principle" in cleared:
            updates["principle_is_derived"] = False
        kept.append(card.model_copy(update=updates))
    return kept, summary, usage


def extract_evidence_cards(
    document: ParsedDocument, chunks: ChunkSet, chunk: Chunk,
    client: ModelClient, model: ModelSpec, manifest: RunManifest, run_dir: Path,
    *, max_input_tokens: int = 1600,
    prompt_version: str = CARDS_PROMPT_VERSION,
) -> EvidenceCardsArtifact:
    _check_source(document, chunks, manifest, client)
    if chunk.chunk_id not in {item.chunk_id for item in chunks.chunks}:
        raise EnrichmentError("chunk is not in its declared variant")
    if prompt_version not in ("evidence_cards_v1", "evidence_cards_v2", "evidence_cards_v3", "evidence_cards_v4", "evidence_cards_v5", "evidence_cards_v6", "evidence_cards_v7", "evidence_cards_v8", "evidence_cards_v9"):
        raise EnrichmentError("unsupported evidence-card prompt version")
    quote_location = prompt_version == "evidence_cards_v2"
    block_location = prompt_version in ("evidence_cards_v3", "evidence_cards_v4")
    strict_blocks = prompt_version == "evidence_cards_v4"
    group_location = prompt_version in ("evidence_cards_v5", "evidence_cards_v6", "evidence_cards_v7", "evidence_cards_v8", "evidence_cards_v9")
    split_highlights = prompt_version in ("evidence_cards_v8", "evidence_cards_v9")
    checked = prompt_version == "evidence_cards_v9"
    extraction_version = ("same-speaker-group-v5" if group_location else
                          "complete-block-v4" if strict_blocks else
                          "block-locate-v3" if block_location else
                          "quote-locate-v2" if quote_location else "cards-v1")
    recipe_version = ("isolated-checked-speaker-groups-v9" if checked else
                      "separate-highlights-speaker-groups-v8" if split_highlights else
                      "explicit-zero-based-speaker-groups-v7" if prompt_version == "evidence_cards_v7" else
                      "contiguous-speaker-groups-v5" if group_location else
                      "self-contained-block-cards-v4" if strict_blocks else
                      "source-block-cards-v3" if block_location else
                      "exact-quote-cards-v2" if quote_location else CARDS_RECIPE_VERSION)
    prompt, prompt_hash = _prompt(prompt_version)
    version_inputs = {
        "chunk_id": chunk.chunk_id,
        "chunking_version": chunks.chunking_version,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_hash,
        "input_recipe_version": recipe_version,
        "extraction_version": extraction_version,
    }
    if checked:
        version_inputs["check_prompt_version"] = CARD_CHECK_PROMPT_VERSION
        version_inputs["check_prompt_sha256"] = _prompt(CARD_CHECK_PROMPT_VERSION)[1]
    record = manifest.begin("cards", version_inputs, model)
    existing = _load_ready(run_dir, record, EvidenceCardsArtifact)
    if existing:
        return existing
    manifest.save(run_dir)
    try:
        schema = _card_schema(quote_location=quote_location, block_location=block_location,
                              group_location=group_location, rich_descriptions=split_highlights)
        if checked:
            # One request per evidence group: the model never sees a neighbouring
            # passage, so a card cannot borrow details from one.
            proposed, usage = [], ModelUsage()
            for group_index, (start, end) in enumerate(
                    _card_groups(document, chunk, split_highlights=split_highlights)):
                result = client.generate_json(
                    model=model, system_prompt=prompt,
                    input_text=_single_group_input(document, chunk, start, end),
                    schema_name="evidence_cards", json_schema=schema, max_output_tokens=1000,
                )
                items = result.data.get("cards")
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    raise EnrichmentError("model returned malformed cards")
                proposed.extend({**item, "evidence_group_index": group_index} for item in items)
                usage = _usage_sum(usage, result.usage)
        else:
            result = client.generate_json(
                model=model, system_prompt=prompt,
                input_text=(_group_card_input(document, chunk, max(max_input_tokens, 1800),
                                              explicit_indices=prompt_version in ("evidence_cards_v7", "evidence_cards_v8"),
                                              split_highlights=split_highlights)
                            if group_location else
                            _block_card_input(document, chunk, max(max_input_tokens, 1800),
                                              strict_blocks=strict_blocks) if block_location
                            else _input(document, chunk, max_input_tokens=max_input_tokens)),
                schema_name="evidence_cards", json_schema=schema,
                max_output_tokens=1400 if prompt_version in ("evidence_cards_v6", "evidence_cards_v7", "evidence_cards_v8") else 700,
            )
            proposed, usage = result.data.get("cards"), result.usage
            if not isinstance(proposed, list) or any(not isinstance(item, dict) for item in proposed):
                raise EnrichmentError("model returned malformed cards")
        cards = [(_validated_group_card(item, index, document, chunk,
                                        split_highlights=split_highlights) if group_location else
                  _validated_block_card(item, index, document, chunk,
                                        strict_blocks=strict_blocks) if block_location else
                  _validated_quoted_card(item, index, document, chunk) if quote_location else
                  _validated_card(item, index, document, chunk))
                 for index, item in enumerate(proposed)]
        check = None
        if checked:
            cards, check, check_usage = _checked_cards(cards, client, model)
            usage = _usage_sum(usage, check_usage)
        artifact = EvidenceCardsArtifact(
            document_id=document.document_id, run_id=document.run_id,
            source_sha256=document.source_sha256, chunk_id=chunk.chunk_id,
            cards=cards, model=model, prompt_version=prompt_version,
            extraction_version=extraction_version, usage=usage, check=check,
        )
        path = write_artifact_json(
            run_dir, "cards", record.artifact_id, artifact.model_dump(mode="json")
        )
        manifest.succeed(record.artifact_id, path, usage)
        manifest.save(run_dir)
        return artifact
    except (ModelClientError, EnrichmentError, ValueError, KeyError, TypeError, OSError) as exc:
        manifest.fail(record.artifact_id, type(exc).__name__)
        manifest.save(run_dir)
        raise
