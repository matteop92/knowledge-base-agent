"""Source-aligned Markdown parsing for prose, notes, articles, and book exports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from app.ingestion.contracts import (
    AssetReference,
    DocumentBlock,
    ExcludedSpan,
    ParsedDocument,
    ParseDiagnostic,
    SourceMetadata,
)
from app.ingestion.source_reader import CapturedSource
from app.ingestion.transcript_parser import (
    _line_span,
    _optional_date,
    _optional_text,
    _read_frontmatter,
    _text_list,
)


PARSER_VERSION = "markdown-structure-v1"
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*(?:\r?\n)?$")
LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")
TIP = re.compile(r"^\*\*(?:Tip|STEP)[ \t]*\d+:[^\r\n]*\*\*[ \t]*$", re.IGNORECASE)
IMAGE = re.compile(r"!\[[^\]]*\]\((?P<target>[^)]+)\)")
WRAPPER = re.compile(r"^(?:::|(?:\[\]\{#))")
HORIZONTAL_RULE = re.compile(r"^[ \t]*(?:---+|\*\*\*+|___+)[ \t]*$")
HeadingStack = list[tuple[int, str]]


class MarkdownParseError(ValueError):
    """The Markdown cannot be parsed without losing source provenance."""


def read_metadata(capture: CapturedSource) -> tuple[SourceMetadata, int]:
    frontmatter, frontmatter_raw, body_start = _read_frontmatter(capture.source_text)
    title = _optional_text(frontmatter.get("title"), "title")
    if title is None:
        title = Path(capture.manifest.locator.relative_path).stem
    authors = _text_list(frontmatter.get("author", frontmatter.get("authors")), "author")
    authors = [
        author[2:-2] if author.startswith("[[") and author.endswith("]]") else author
        for author in authors
    ]
    source = _optional_text(frontmatter.get("source"), "source")
    url = _optional_text(frontmatter.get("url"), "url")
    source_is_url = bool(source and source.startswith(("http://", "https://")))
    return SourceMetadata(
        title=title,
        source_url=url or (source if source_is_url else None),
        source_origin=source if source and not source_is_url else None,
        authors=authors,
        guests=[person.removeprefix("[[").removesuffix("]]") for person in _text_list(frontmatter.get("guest"), "guest")],
        published=_optional_date(frontmatter.get("published"), "published"),
        created=_optional_date(frontmatter.get("created"), "created"),
        date=_optional_date(frontmatter.get("date"), "date"),
        description=_optional_text(frontmatter.get("description"), "description"),
        tags=_text_list(frontmatter.get("tags"), "tags"),
        frontmatter_raw=frontmatter_raw,
        frontmatter=frontmatter,
    ), body_start


def _heading_title(raw: str) -> str:
    title = re.sub(r"\{[^{}]*\}", "", raw).strip()
    title = title.strip("*_ ")
    return title or raw.strip()


def _line_kind(line: str, mode: str) -> str:
    stripped = line.strip()
    if not stripped:
        return "whitespace"
    if mode == "book" and WRAPPER.match(stripped):
        return "furniture"
    if HEADING.match(line):
        return "heading"
    if HORIZONTAL_RULE.fullmatch(stripped):
        return "separator"
    if IMAGE.fullmatch(stripped):
        return "asset_reference"
    if TIP.fullmatch(stripped):
        return "tip"
    if LIST_ITEM.match(line):
        return "list_item"
    if line.lstrip().startswith(">"):
        return "block_quote"
    return "paragraph"


def scan_region(
    source_text: str,
    start: int,
    end: int,
    *,
    mode: Literal["generic", "book"] = "generic",
    content_role: Literal["source", "personal_note"] = "source",
    heading_stack: HeadingStack | None = None,
) -> tuple[list[DocumentBlock], list[ExcludedSpan], HeadingStack]:
    """Cover a line-aligned source region with blocks or explicit exclusions."""
    if not (0 <= start <= end <= len(source_text)):
        raise MarkdownParseError("invalid Markdown region")
    lines = source_text[start:end].splitlines(keepends=True)
    positions = [start]
    for line in lines:
        positions.append(positions[-1] + len(line))
    blocks: list[DocumentBlock] = []
    excluded: list[ExcludedSpan] = []
    stack = list(heading_stack or [])
    in_index = False
    index = 0
    while index < len(lines):
        line = lines[index]
        kind = _line_kind(line, mode)
        stripped = line.strip()
        if mode == "book" and kind == "heading":
            title = _heading_title(HEADING.match(line).group(2))
            if title.upper() == "TABLE OF CONTENTS":
                stop = index + 1
                while stop < len(lines) and lines[stop].strip() != ":::":
                    stop += 1
                if stop < len(lines):
                    stop += 1
                excluded.append(_excluded(source_text, positions[index], positions[stop], "export_furniture"))
                index = stop
                continue
            if title.upper() == "INDEX" or ".index}" in line:
                in_index = True
        if in_index:
            excluded.append(_excluded(source_text, positions[index], positions[len(lines)], "export_furniture"))
            break
        if kind in ("whitespace", "furniture"):
            stop = index + 1
            while stop < len(lines) and _line_kind(lines[stop], mode) == kind:
                stop += 1
            reason = "whitespace" if kind == "whitespace" else "export_furniture"
            excluded.append(_excluded(source_text, positions[index], positions[stop], reason))
            index = stop
            continue
        stop = index + 1
        if kind in ("paragraph", "block_quote", "list_item"):
            while stop < len(lines):
                next_kind = _line_kind(lines[stop], mode)
                if next_kind == "whitespace":
                    break
                if kind == "paragraph" and next_kind != "paragraph":
                    break
                if kind == "block_quote" and next_kind != "block_quote":
                    break
                if kind == "list_item" and next_kind not in ("paragraph",):
                    break
                stop += 1
        if kind == "heading":
            match = HEADING.match(line)
            level, title = len(match.group(1)), _heading_title(match.group(2))
            stack = [(depth, label) for depth, label in stack if depth < level]
            stack.append((level, title))
        path = [label for _, label in stack] or None
        blocks.append(
            DocumentBlock(
                index=len(blocks),
                kind=kind,
                span=_line_span(source_text, positions[index], positions[stop]),
                raw_text=source_text[positions[index]:positions[stop]],
                heading_path=path,
                content_role=content_role,
                asset_target=IMAGE.fullmatch(stripped).group("target") if kind == "asset_reference" else None,
            )
        )
        index = stop
    return blocks, excluded, stack


def _excluded(source_text: str, start: int, end: int, reason: str) -> ExcludedSpan:
    return ExcludedSpan(
        span=_line_span(source_text, start, end),
        raw_text=source_text[start:end],
        reason=reason,
    )


def unresolved_assets(source_text: str) -> list[AssetReference]:
    assets: list[AssetReference] = []
    for match in IMAGE.finditer(source_text):
        target = match.group("target").split()[0]
        if not target.startswith(("http://", "https://", "data:")):
            assets.append(
                AssetReference(
                    span=_line_span(source_text, match.start(), match.end()),
                    target=target,
                    reason="local asset was not captured",
                )
            )
    return assets


def parse_markdown(
    capture: CapturedSource,
    *,
    source_kind: str = "generic",
    route_reason: str = "generic Markdown fallback",
    structural_confidence: Literal["high", "reduced"] = "reduced",
) -> ParsedDocument:
    metadata, body_start = read_metadata(capture)
    text = capture.source_text
    mode = "book" if source_kind == "book" else "generic"
    blocks, excluded, _ = scan_region(text, body_start, len(text), mode=mode)
    if body_start:
        excluded.insert(0, _excluded(text, 0, body_start, "frontmatter"))
    diagnostics = []
    if structural_confidence == "reduced":
        diagnostics.append(
            ParseDiagnostic(code="generic_fallback", message="Markdown structure may be incomplete")
        )
    return ParsedDocument(
        parser_version=PARSER_VERSION,
        document_id=capture.manifest.document_id,
        run_id=capture.manifest.run_id,
        source_sha256=capture.manifest.source_sha256,
        locator=capture.manifest.locator,
        metadata=metadata,
        source_text=text,
        source_kind=source_kind,
        route_reason=route_reason,
        structural_confidence=structural_confidence,
        diagnostics=diagnostics,
        unresolved_assets=unresolved_assets(text),
        blocks=blocks,
        excluded_spans=excluded,
    )
