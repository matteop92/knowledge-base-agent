"""Deterministic fixed and structure-aware raw chunks with exact source spans."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.ingestion.contracts import Chunk, ChunkSet, DocumentBlock, ParsedDocument, SourceSpan
from app.ingestion.transcript_parser import _line_span


TOKENIZER_VERSION = "unicode-regex-token-v1"
FIXED_VERSION = "fixed-800-overlap-80-v1"
STRUCTURAL_VERSION = "structural-600-1000-v1"
TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
ELIGIBLE_KINDS = {
    "speaker_turn", "paragraph", "heading", "highlight",
    "list_item", "block_quote", "tip",
}


class ChunkingError(ValueError):
    """A source cannot be chunked without losing its eligible content."""


@dataclass(frozen=True)
class Fragment:
    start: int
    end: int
    block_index: int
    tokens: int


@dataclass(frozen=True)
class Unit:
    fragments: tuple[Fragment, ...]
    tokens: int
    kind: str
    group_id: str | None
    split: bool = False


def _token_segments(block: DocumentBlock) -> list[Fragment]:
    matches = list(TOKEN.finditer(block.raw_text))
    if not matches:
        raise ChunkingError(f"eligible block {block.index} has no tokens")
    return [
        Fragment(
            start=block.span.start_char if index == 0 else block.span.start_char + match.start(),
            end=(
                block.span.start_char + matches[index + 1].start()
                if index + 1 < len(matches) else block.span.end_char
            ),
            block_index=block.index,
            tokens=1,
        )
        for index, match in enumerate(matches)
    ]


def _join_segments(segments: list[Fragment]) -> list[Fragment]:
    joined: list[Fragment] = []
    for segment in segments:
        if (
            joined and joined[-1].block_index == segment.block_index
            and joined[-1].end == segment.start
        ):
            previous = joined[-1]
            joined[-1] = Fragment(
                previous.start, segment.end, segment.block_index,
                previous.tokens + segment.tokens,
            )
        else:
            joined.append(segment)
    return joined


def _chunk(
    document: ParsedDocument,
    fragments: list[Fragment],
    index: int,
    variant: str,
    version: str,
    reason: str,
    overlap_tokens: int,
) -> Chunk:
    fragments = _join_segments(fragments)
    spans = [_line_span(document.source_text, part.start, part.end) for part in fragments]
    block_indices = list(dict.fromkeys(part.block_index for part in fragments))
    blocks = [document.blocks[block_index] for block_index in block_indices]
    raw = "".join(document.source_text[part.start:part.end] for part in fragments)
    signature = "|".join(
        f"{part.start}:{part.end}" for part in fragments
    )
    chunk_id = hashlib.sha256(
        f"{document.document_id}|{document.run_id}|{variant}|{version}|{index}|{signature}".encode()
    ).hexdigest()[:24]
    return Chunk(
        chunk_id=chunk_id,
        index=index,
        source_spans=spans,
        block_indices=block_indices,
        raw_text=raw,
        token_count=sum(part.tokens for part in fragments),
        overlap_tokens=overlap_tokens,
        boundary_reason=reason,
        heading_path=next((block.heading_path for block in blocks if block.heading_path), None),
        content_roles=list(dict.fromkeys(block.content_role for block in blocks)),
        group_ids=list(dict.fromkeys(block.group_id for block in blocks if block.group_id)),
    )


def _eligible(document: ParsedDocument) -> list[DocumentBlock]:
    blocks = [block for block in document.blocks if block.kind in ELIGIBLE_KINDS]
    if not blocks:
        raise ChunkingError("document has no eligible source blocks")
    return blocks


def fixed_raw_chunks(document: ParsedDocument) -> ChunkSet:
    segments = [
        segment for block in _eligible(document) for segment in _token_segments(block)
    ]
    chunks: list[Chunk] = []
    start = 0
    while start < len(segments):
        end = min(start + 800, len(segments))
        chunks.append(
            _chunk(
                document, segments[start:end], len(chunks), "fixed_raw",
                FIXED_VERSION, "start" if start == 0 else "fixed_stride",
                0 if start == 0 else 80,
            )
        )
        if end == len(segments):
            break
        start = end - 80
    result = ChunkSet(
        document_id=document.document_id,
        run_id=document.run_id,
        source_sha256=document.source_sha256,
        variant="fixed_raw",
        chunking_version=FIXED_VERSION,
        tokenizer_version=TOKENIZER_VERSION,
        chunks=chunks,
    )
    result.validate_against(document)
    return result


def _split_oversized(document: ParsedDocument, block: DocumentBlock) -> list[Fragment]:
    segments = _token_segments(block)
    if len(segments) <= 1000:
        return [Fragment(block.span.start_char, block.span.end_char, block.index, len(segments))]
    pieces: list[Fragment] = []
    start = 0
    while start < len(segments):
        if len(segments) - start <= 1000:
            end = len(segments)
        else:
            low = start + 600
            high = min(start + 1000, len(segments) - 1)
            choices = [
                candidate for candidate in range(low, high + 1)
                if document.source_text[segments[candidate - 1].start:segments[candidate - 1].end]
                .rstrip().endswith((".", "?", "!", "\n"))
            ]
            end = min(choices, key=lambda item: abs(item - (start + 800))) if choices else start + 800
        pieces.append(
            Fragment(segments[start].start, segments[end - 1].end, block.index, end - start)
        )
        start = end
    return pieces


def _units(document: ParsedDocument) -> list[Unit]:
    units: list[Unit] = []
    for block in _eligible(document):
        pieces = _split_oversized(document, block)
        if len(pieces) > 1:
            units.extend(
                Unit((piece,), piece.tokens, block.kind, block.group_id, split=True)
                for piece in pieces
            )
            continue
        piece = pieces[0]
        if (
            block.group_id and units and units[-1].group_id == block.group_id
            and units[-1].tokens + piece.tokens <= 1000
        ):
            previous = units[-1]
            units[-1] = Unit(
                previous.fragments + (piece,), previous.tokens + piece.tokens,
                previous.kind, block.group_id,
            )
        else:
            units.append(Unit((piece,), piece.tokens, block.kind, block.group_id))
    return units


def _overlap_tail(document: ParsedDocument, fragments: list[Fragment]) -> list[Fragment]:
    if not fragments:
        return []
    last = fragments[-1]
    block = document.blocks[last.block_index]
    if block.group_id:
        return []
    if block.kind not in ("speaker_turn", "paragraph", "list_item", "block_quote"):
        return []
    if last.tokens <= 80:
        return [last]
    segments = [
        segment for segment in _token_segments(block)
        if last.start <= segment.start and segment.end <= last.end
    ]
    tail = segments[-80:]
    return [Fragment(tail[0].start, tail[-1].end, block.index, len(tail))]


def structural_raw_chunks(document: ParsedDocument) -> ChunkSet:
    units = _units(document)
    chunks: list[Chunk] = []
    current: list[Fragment] = []
    current_tokens = 0
    overlap = 0
    reason = "start"
    for unit in units:
        starts_heading = current_tokens >= 600 and unit.kind in ("heading", "tip") and any(
            document.blocks[part.block_index].kind not in ("heading", "tip")
            for part in current
        )
        if current and (starts_heading or current_tokens + unit.tokens > 1000):
            chunks.append(
                _chunk(
                    document, current, len(chunks), "structural_raw",
                    STRUCTURAL_VERSION, reason, overlap,
                )
            )
            carry = [] if starts_heading else _overlap_tail(document, current)
            if sum(part.tokens for part in carry) + unit.tokens > 1000:
                carry = []
            current = carry
            current_tokens = sum(part.tokens for part in carry)
            overlap = current_tokens
            reason = "heading_boundary" if starts_heading else "budget"
        if not current and unit.split:
            reason = "oversized_block"
        current.extend(unit.fragments)
        current_tokens += unit.tokens
    if current:
        chunks.append(
            _chunk(
                document, current, len(chunks), "structural_raw",
                STRUCTURAL_VERSION, reason, overlap,
            )
        )
    result = ChunkSet(
        document_id=document.document_id,
        run_id=document.run_id,
        source_sha256=document.source_sha256,
        variant="structural_raw",
        chunking_version=STRUCTURAL_VERSION,
        tokenizer_version=TOKENIZER_VERSION,
        chunks=chunks,
    )
    result.validate_against(document)
    return result
