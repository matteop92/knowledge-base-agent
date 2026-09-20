"""Validated, source-aligned records for captured Markdown documents."""

from __future__ import annotations

from datetime import date as Date
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CAPTURE_SCHEMA_VERSION = "1"
DOCUMENT_SCHEMA_VERSION = "2"
PARSER_VERSION = "markdown-speakers-v1"


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceLocator(StrictRecord):
    root_alias: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)

    @field_validator("relative_path")
    @classmethod
    def check_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
            raise ValueError("source locator must be a portable relative path")
        return value


class SourceSpan(StrictRecord):
    """Half-open Unicode character offsets into ParsedDocument.source_text."""

    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def check_order(self) -> SourceSpan:
        if self.end_char <= self.start_char or self.end_line < self.start_line:
            raise ValueError("source span must have increasing offsets and lines")
        return self


class SourceMetadata(StrictRecord):
    title: str = Field(min_length=1)
    source_url: str | None = None
    source_origin: str | None = None
    authors: list[str] = Field(default_factory=list)
    published: Date | None = None
    created: Date | None = None
    date: Date | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    frontmatter_raw: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_url")
    @classmethod
    def check_source_url(cls, value: str | None) -> str | None:
        if value is not None:
            parts = None
            try:
                parts = urlsplit(value)
                valid_host = bool(parts.hostname) and parts.port != 0
            except ValueError:
                valid_host = False
            if (
                parts is None
                or parts.scheme not in ("http", "https")
                or not valid_host
                or any(char.isspace() for char in value)
            ):
                raise ValueError("source_url must be an HTTP(S) URL")
        return value


class DocumentBlock(StrictRecord):
    index: int = Field(ge=0)
    kind: Literal["speaker_turn", "paragraph", "heading", "highlight"]
    span: SourceSpan
    raw_text: str = Field(min_length=1)
    content_span: SourceSpan | None = None
    heading_path: list[str] | None = None
    speaker: str | None = None
    page_reference: str | None = None
    content_role: Literal["source", "personal_note"] = "source"
    timestamp_start: str | None = None
    timestamp_end: str | None = None

    @model_validator(mode="after")
    def check_attributes(self) -> DocumentBlock:
        if self.content_span is not None and not (
            self.span.start_char <= self.content_span.start_char
            < self.content_span.end_char <= self.span.end_char
        ):
            raise ValueError("content span must lie inside block span")
        if self.kind == "speaker_turn" and not self.speaker:
            raise ValueError("speaker turn needs a speaker")
        if self.kind != "speaker_turn" and self.speaker is not None:
            raise ValueError("only speaker turns can have a speaker")
        return self


class ExcludedSpan(StrictRecord):
    span: SourceSpan
    raw_text: str = Field(min_length=1)
    reason: Literal["frontmatter", "whitespace", "export_furniture", "unclassified"]


class CaptureManifest(StrictRecord):
    schema_version: Literal["1"] = CAPTURE_SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    document_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: SourceLocator


class ParsedDocument(StrictRecord):
    schema_version: Literal["2"] = DOCUMENT_SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    document_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: SourceLocator
    metadata: SourceMetadata
    source_text: str = Field(min_length=1)
    blocks: list[DocumentBlock] = Field(default_factory=list)
    excluded_spans: list[ExcludedSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_provenance(self) -> ParsedDocument:
        previous_end = 0
        for expected_index, block in enumerate(self.blocks):
            if block.index != expected_index:
                raise ValueError("block indices must be sequential")
            if block.span.start_char < previous_end:
                raise ValueError("blocks must be ordered and nonoverlapping")
            self._check_item(block.span, block.raw_text)
            if block.content_span is not None:
                self._check_span(block.content_span)
            previous_end = block.span.end_char
        for excluded in self.excluded_spans:
            self._check_item(excluded.span, excluded.raw_text)
            if excluded.reason == "whitespace" and excluded.raw_text.strip():
                raise ValueError("whitespace exclusion contains content")
        ranges = sorted(
            [(block.span.start_char, block.span.end_char) for block in self.blocks]
            + [(item.span.start_char, item.span.end_char) for item in self.excluded_spans]
        )
        cursor = 0
        for start, end in ranges:
            if start != cursor:
                raise ValueError("source coverage has an overlap or unreported gap")
            cursor = end
        if cursor != len(self.source_text):
            raise ValueError("source coverage has an overlap or unreported gap")
        return self

    def _check_item(self, span: SourceSpan, raw_text: str) -> None:
        self._check_span(span)
        if self.source_text[span.start_char : span.end_char] != raw_text:
            raise ValueError("raw_text does not match source span")

    def _check_span(self, span: SourceSpan) -> None:
        if span.end_char > len(self.source_text):
            raise ValueError("source span extends beyond source_text")
        start_line = self.source_text.count("\n", 0, span.start_char) + 1
        end_line = self.source_text.count("\n", 0, span.end_char - 1) + 1
        if (span.start_line, span.end_line) != (start_line, end_line):
            raise ValueError("source span line numbers do not match source_text")
