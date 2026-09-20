"""Validated, source-aligned records for podcast transcript ingestion."""

from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1"
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
    """Half-open Unicode character offsets into ParsedTranscript.source_text."""

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
    authors: list[str] = Field(default_factory=list)
    published: date | None = None
    created: date | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    frontmatter_raw: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class TranscriptTurn(StrictRecord):
    index: int = Field(ge=0)
    speaker: str = Field(min_length=1)
    span: SourceSpan
    content_span: SourceSpan
    raw_text: str = Field(min_length=1)
    timestamp_start: str | None = None
    timestamp_end: str | None = None

    @model_validator(mode="after")
    def check_content_span(self) -> TranscriptTurn:
        if not (
            self.span.start_char <= self.content_span.start_char
            < self.content_span.end_char <= self.span.end_char
        ):
            raise ValueError("content span must lie inside turn span")
        return self


class CaptureManifest(StrictRecord):
    schema_version: str = SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    document_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: SourceLocator


class ParsedTranscript(StrictRecord):
    schema_version: str = SCHEMA_VERSION
    parser_version: str = PARSER_VERSION
    document_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: SourceLocator
    metadata: SourceMetadata
    source_text: str = Field(min_length=1)
    turns: list[TranscriptTurn] = Field(min_length=1)

    @model_validator(mode="after")
    def check_provenance(self) -> ParsedTranscript:
        previous_end = 0
        for expected_index, turn in enumerate(self.turns):
            if turn.index != expected_index:
                raise ValueError("turn indices must be sequential")
            if turn.span.start_char < previous_end:
                raise ValueError("turn spans must be ordered and nonoverlapping")
            self._check_span(turn.span)
            self._check_span(turn.content_span)
            if self.source_text[turn.span.start_char : turn.span.end_char] != turn.raw_text:
                raise ValueError("turn raw_text does not match source span")
            previous_end = turn.span.end_char
        return self

    def _check_span(self, span: SourceSpan) -> None:
        if span.end_char > len(self.source_text):
            raise ValueError("source span extends beyond source_text")
        start_line = self.source_text.count("\n", 0, span.start_char) + 1
        end_line = self.source_text.count("\n", 0, span.end_char - 1) + 1
        if (span.start_line, span.end_line) != (start_line, end_line):
            raise ValueError("source span line numbers do not match source_text")
