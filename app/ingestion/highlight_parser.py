"""Parse Kindle highlight exports without crediting personal notes to the author."""

from __future__ import annotations

import re

from app.ingestion.contracts import DocumentBlock, ParsedDocument
from app.ingestion.markdown_parser import (
    _excluded,
    read_metadata,
    scan_region,
    unresolved_assets,
)
from app.ingestion.source_reader import CapturedSource
from app.ingestion.transcript_parser import _line_span


PARSER_VERSION = "kindle-highlights-v1"
LABEL = re.compile(
    r"^\*\*(?P<kind>Highlight|My note)\b(?P<details>[^\r\n]*?)\*\*[ \t]*(?:\r?\n)?",
    re.MULTILINE,
)
HEADING_LINE = re.compile(r"^#{1,6}[ \t]+[^\r\n]*(?:\r?\n)?", re.MULTILINE)
PAGE = re.compile(r"\bp\.[ \t]*(\d+)", re.IGNORECASE)


def parse_highlights(capture: CapturedSource) -> ParsedDocument:
    text = capture.source_text
    metadata, body_start = read_metadata(capture)
    events = sorted(
        [(match.start(), match.end(), "label", match) for match in LABEL.finditer(text, body_start)]
        + [(match.start(), match.end(), "heading", match) for match in HEADING_LINE.finditer(text, body_start)]
    )
    blocks: list[DocumentBlock] = []
    excluded = []
    headings = []
    cursor = body_start
    current_group = None
    for event_index, (start, marker_end, kind, match) in enumerate(events):
        if cursor < start:
            more_blocks, more_excluded, headings = scan_region(
                text, cursor, start, heading_stack=headings
            )
            blocks.extend(more_blocks)
            excluded.extend(more_excluded)
        if kind == "heading":
            more_blocks, more_excluded, headings = scan_region(
                text, start, marker_end, heading_stack=headings
            )
            blocks.extend(more_blocks)
            excluded.extend(more_excluded)
            current_group = None
            cursor = marker_end
            continue
        end = events[event_index + 1][0] if event_index + 1 < len(events) else len(text)
        details = match.group("details")
        page_match = PAGE.search(details)
        is_highlight = match.group("kind") == "Highlight"
        if is_highlight:
            current_group = f"highlight-{len(blocks)}"
        content = text[marker_end:end]
        content_start = marker_end + len(content) - len(content.lstrip())
        content_end = end - (len(content) - len(content.rstrip()))
        blocks.append(
            DocumentBlock(
                index=0,
                kind="highlight" if is_highlight else "paragraph",
                span=_line_span(text, start, end),
                content_span=(
                    _line_span(text, content_start, content_end)
                    if content_start < content_end else None
                ),
                raw_text=text[start:end],
                heading_path=[label for _, label in headings] or None,
                page_reference=page_match.group(1) if page_match else None,
                content_role="source" if is_highlight else "personal_note",
                group_id=current_group,
            )
        )
        cursor = end
    if cursor < len(text):
        more_blocks, more_excluded, headings = scan_region(
            text, cursor, len(text), heading_stack=headings
        )
        blocks.extend(more_blocks)
        excluded.extend(more_excluded)
    if body_start:
        excluded.insert(0, _excluded(text, 0, body_start, "frontmatter"))
    blocks.sort(key=lambda block: block.span.start_char)
    blocks = [block.model_copy(update={"index": index}) for index, block in enumerate(blocks)]
    return ParsedDocument(
        parser_version=PARSER_VERSION,
        document_id=capture.manifest.document_id,
        run_id=capture.manifest.run_id,
        source_sha256=capture.manifest.source_sha256,
        locator=capture.manifest.locator,
        metadata=metadata,
        source_text=text,
        source_kind="highlights",
        route_reason="Kindle highlight and My note labels",
        unresolved_assets=unresolved_assets(text),
        blocks=blocks,
        excluded_spans=excluded,
    )
