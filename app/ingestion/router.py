"""Select a Markdown parser from an override, source folder, and content signatures."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from app.ingestion.contracts import ParsedDocument
from app.ingestion.highlight_parser import LABEL, parse_highlights
from app.ingestion.markdown_parser import parse_markdown
from app.ingestion.source_reader import CapturedSource
from app.ingestion.transcript_parser import (
    BRACKET_MARKER,
    SPEAKER_MARKER,
    parse_bracket_transcript,
    parse_transcript,
)


SourceKind = Literal[
    "transcript_bold", "transcript_bracket", "highlights",
    "book", "article", "note", "generic",
]


def choose_route(
    capture: CapturedSource, override: SourceKind | None = None
) -> tuple[SourceKind, str]:
    if override is not None:
        return override, f"explicit source-kind override: {override}"
    folder = PurePosixPath(capture.manifest.locator.relative_path).parts[0].casefold()
    text = capture.source_text
    bold_count = len(SPEAKER_MARKER.findall(text))
    bracket_count = len(BRACKET_MARKER.findall(text))
    if folder == "transcripts" and bracket_count:
        return "transcript_bracket", "Transcripts folder and escaped bracket speaker labels"
    if folder == "transcripts" and bold_count:
        return "transcript_bold", "Transcripts folder and bold speaker labels"
    if folder == "book highlights" and LABEL.search(text):
        return "highlights", "Book Highlights folder and Kindle labels"
    if folder == "books":
        return "book", "Books folder and Markdown book structure"
    if folder == "articles":
        return "article", "Articles folder and Markdown prose structure"
    if folder == "notes":
        return "note", "Notes folder and Markdown note structure"
    if bold_count >= 3:
        return "transcript_bold", "repeated bold speaker labels"
    if bracket_count >= 3:
        return "transcript_bracket", "repeated escaped bracket speaker labels"
    if LABEL.search(text):
        return "highlights", "Kindle highlight labels"
    return "generic", "no reliable format signature; generic Markdown fallback"


def route_source(
    capture: CapturedSource, override: SourceKind | None = None
) -> ParsedDocument:
    kind, reason = choose_route(capture, override)
    if kind == "transcript_bold":
        return parse_transcript(capture).model_copy(update={"route_reason": reason})
    if kind == "transcript_bracket":
        return parse_bracket_transcript(capture).model_copy(update={"route_reason": reason})
    if kind == "highlights":
        return parse_highlights(capture).model_copy(update={"route_reason": reason})
    return parse_markdown(
        capture,
        source_kind=kind,
        route_reason=reason,
        structural_confidence="reduced" if kind == "generic" else "high",
    )
