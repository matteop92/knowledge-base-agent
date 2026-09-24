"""Parse Markdown podcast transcripts while retaining exact source positions."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from app.ingestion.contracts import (
    DocumentBlock,
    ExcludedSpan,
    ParsedDocument,
    ParseDiagnostic,
    SourceMetadata,
    SourceSpan,
)
from app.ingestion.source_reader import CapturedSource


SPEAKER_MARKER = re.compile(r"^\*\*(?P<speaker>[^*:\r\n]{1,120}):\*\*[ \t]*", re.MULTILINE)
KNOWN_SPEAKER_WITH_MISSING_BOLD = re.compile(
    r"^(?P<speaker>[^*:\r\n]{1,120}):\*\*[ \t]*", re.MULTILINE
)
BRACKET_MARKER = re.compile(
    r"^\\\[(?P<speaker>[^\\\]\r\n]{1,120})\\\][ \t]*(?:\r?\n)?",
    re.MULTILINE,
)
BRACKET_LIKE = re.compile(
    r"^\\?\[[^\r\n]{1,120}\\?\][ \t]*(?:\r?\n)?",
    re.MULTILINE,
)


class TranscriptParseError(ValueError):
    """A Markdown source does not match the supported transcript format."""


def _line_span(source_text: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(
        start_char=start,
        end_char=end,
        start_line=source_text.count("\n", 0, start) + 1,
        end_line=source_text.count("\n", 0, end - 1) + 1,
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TranscriptParseError("frontmatter keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TranscriptParseError("frontmatter contains an unsupported value type")


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TranscriptParseError(f"frontmatter {field} must be text")
    return value.strip() or None


def _optional_date(value: Any, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                raise TranscriptParseError(
                    f"frontmatter {field} must be an ISO date"
                ) from exc
    raise TranscriptParseError(f"frontmatter {field} must be an ISO date")


def _text_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise TranscriptParseError(f"frontmatter {field} must be text or a list of text")
    return [item.strip() for item in items if item.strip()]


def _read_frontmatter(source_text: str) -> tuple[dict[str, Any], str, int]:
    lines = source_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, "", 0
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        raise TranscriptParseError("frontmatter has no closing --- delimiter")
    raw_block = "".join(lines[: closing + 1])
    yaml_body = "".join(lines[1:closing])
    try:
        parsed = yaml.safe_load(yaml_body) or {}
    except yaml.YAMLError as exc:
        raise TranscriptParseError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise TranscriptParseError("frontmatter must be a mapping")
    return _json_value(parsed), raw_block, len(raw_block)


def parse_transcript(capture: CapturedSource) -> ParsedDocument:
    source_text = capture.source_text
    frontmatter, frontmatter_raw, body_start = _read_frontmatter(source_text)
    title = _optional_text(frontmatter.get("title"), "title")
    if title is None:
        title = Path(capture.manifest.locator.relative_path).stem
    authors = _text_list(frontmatter.get("author"), "author")
    authors = [author[2:-2] if author.startswith("[[") and author.endswith("]]") else author for author in authors]
    source_value = _optional_text(frontmatter.get("source"), "source")
    url_value = _optional_text(frontmatter.get("url"), "url")
    source_url = url_value or (
        source_value
        if source_value and source_value.startswith(("http://", "https://"))
        else None
    )
    metadata = SourceMetadata(
        title=title,
        source_url=source_url,
        source_origin=(
            source_value
            if source_value and not source_value.startswith(("http://", "https://"))
            else None
        ),
        authors=authors,
        published=_optional_date(frontmatter.get("published"), "published"),
        created=_optional_date(frontmatter.get("created"), "created"),
        date=_optional_date(frontmatter.get("date"), "date"),
        description=_optional_text(frontmatter.get("description"), "description"),
        tags=_text_list(frontmatter.get("tags"), "tags"),
        frontmatter_raw=frontmatter_raw,
        frontmatter=frontmatter,
    )
    markers = list(SPEAKER_MARKER.finditer(source_text, body_start))
    if not markers:
        raise TranscriptParseError("no supported **Speaker:** turns found")
    known_speakers = {marker.group("speaker").strip() for marker in markers}
    # Some transcripts omit the opening ** on an otherwise familiar speaker label.
    markers.extend(
        marker
        for marker in KNOWN_SPEAKER_WITH_MISSING_BOLD.finditer(source_text, body_start)
        if marker.group("speaker").strip() in known_speakers
    )
    markers.sort(key=lambda marker: marker.start())
    blocks: list[DocumentBlock] = []
    excluded: list[ExcludedSpan] = []

    def exclude(start: int, end: int, reason: str) -> None:
        if start < end:
            excluded.append(ExcludedSpan(
                span=_line_span(source_text, start, end),
                raw_text=source_text[start:end],
                reason=reason,
            ))

    exclude(0, body_start, "frontmatter")
    if body_start < markers[0].start():
        before = source_text[body_start:markers[0].start()]
        exclude(body_start, markers[0].start(), "whitespace" if not before.strip() else "unclassified")

    for index, marker in enumerate(markers):
        next_marker = markers[index + 1].start() if index + 1 < len(markers) else len(source_text)
        unknown = next(
            (
                candidate for candidate in KNOWN_SPEAKER_WITH_MISSING_BOLD.finditer(
                    source_text, marker.end(), next_marker
                )
                if candidate.group("speaker").strip() not in known_speakers
            ),
            None,
        )
        end = unknown.start() if unknown else next_marker
        if not source_text[marker.end() : end].strip():
            raise TranscriptParseError(f"speaker turn {index} has no content")
        blocks.append(
            DocumentBlock(
                index=index,
                kind="speaker_turn",
                speaker=marker.group("speaker").strip(),
                span=_line_span(source_text, marker.start(), end),
                content_span=_line_span(source_text, marker.end(), end),
                raw_text=source_text[marker.start() : end],
            )
        )
        if unknown:
            exclude(unknown.start(), next_marker, "unclassified")
    result = ParsedDocument(
        document_id=capture.manifest.document_id,
        run_id=capture.manifest.run_id,
        source_sha256=capture.manifest.source_sha256,
        locator=capture.manifest.locator,
        metadata=metadata,
        source_text=source_text,
        diagnostics=[
            ParseDiagnostic(
                code="unclassified_transcript_text",
                message="Text was not attributed to a speaker",
                span=item.span,
            )
            for item in excluded if item.reason == "unclassified"
        ],
        blocks=blocks,
        excluded_spans=excluded,
    )
    return result


def parse_bracket_transcript(capture: CapturedSource) -> ParsedDocument:
    """Keep the bracket transcript's introduction outside attributed turns."""
    from app.ingestion.markdown_parser import (
        _excluded,
        read_metadata,
        scan_region,
        unresolved_assets,
    )

    text = capture.source_text
    metadata, body_start = read_metadata(capture)
    markers = list(BRACKET_MARKER.finditer(text, body_start))
    if not markers:
        raise TranscriptParseError("no supported escaped bracket speaker turns found")
    blocks, excluded, headings = scan_region(text, body_start, markers[0].start())
    diagnostics: list[ParseDiagnostic] = []
    if body_start:
        excluded.insert(0, _excluded(text, 0, body_start, "frontmatter"))
    for marker_index, marker in enumerate(markers):
        next_marker = markers[marker_index + 1].start() if marker_index + 1 < len(markers) else len(text)
        malformed = next(
            (
                candidate for candidate in BRACKET_LIKE.finditer(
                    text, marker.end(), next_marker
                )
                if not BRACKET_MARKER.fullmatch(candidate.group(0))
            ),
            None,
        )
        end = malformed.start() if malformed else next_marker
        if not text[marker.end():end].strip():
            raise TranscriptParseError(f"speaker turn {marker_index} has no content")
        blocks.append(
            DocumentBlock(
                index=len(blocks),
                kind="speaker_turn",
                speaker=marker.group("speaker").strip(),
                span=_line_span(text, marker.start(), end),
                content_span=_line_span(text, marker.end(), end),
                raw_text=text[marker.start():end],
                heading_path=[label for _, label in headings] or None,
            )
        )
        if malformed:
            item = _excluded(text, malformed.start(), next_marker, "unclassified")
            excluded.append(item)
            diagnostics.append(
                ParseDiagnostic(
                    code="malformed_speaker_label",
                    message="Speaker-like label could not be classified",
                    span=item.span,
                )
            )
    return ParsedDocument(
        parser_version="markdown-bracket-speakers-v1",
        document_id=capture.manifest.document_id,
        run_id=capture.manifest.run_id,
        source_sha256=capture.manifest.source_sha256,
        locator=capture.manifest.locator,
        metadata=metadata,
        source_text=text,
        source_kind="transcript",
        route_reason="escaped bracket speaker labels",
        diagnostics=diagnostics,
        unresolved_assets=unresolved_assets(text),
        blocks=blocks,
        excluded_spans=excluded,
    )
