"""Validate source-span labels before using them in retrieval experiments."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.ingestion.contracts import ParsedDocument


class EvalLabelError(ValueError):
    """An eval label is stale or does not point to its claimed source text."""


def validate_eval_labels(path: Path, transcript: ParsedDocument) -> int:
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvalLabelError(f"cannot read eval labels: {exc}") from exc
    if not isinstance(data, dict):
        raise EvalLabelError("eval labels must be a mapping")
    if data.get("document_id") != transcript.document_id:
        raise EvalLabelError("eval document_id does not match parsed transcript")
    if data.get("source_sha256") != transcript.source_sha256:
        raise EvalLabelError("eval source_sha256 does not match parsed transcript")
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        raise EvalLabelError("eval labels need at least one question")
    seen: set[str] = set()
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("id"), str):
            raise EvalLabelError("each eval question needs an ID")
        if question["id"] in seen:
            raise EvalLabelError("eval question IDs must be unique")
        seen.add(question["id"])
        if not isinstance(question.get("question"), str) or not question["question"].strip():
            raise EvalLabelError(f"question {question['id']} needs nonempty text")
        expected = question.get("expected")
        if expected not in ("supported", "unsupported"):
            raise EvalLabelError(f"question {question['id']} has invalid expected value")
        must_find = question.get("must_find", [])
        useful = question.get("useful", [])
        if not isinstance(must_find, list) or not isinstance(useful, list):
            raise EvalLabelError(f"question {question['id']} spans must be lists")
        if expected == "supported" and not must_find:
            raise EvalLabelError(f"question {question['id']} needs must_find evidence")
        if expected == "unsupported" and (must_find or useful):
            raise EvalLabelError(f"unsupported question {question['id']} cannot claim evidence")
        for label in [*must_find, *useful]:
            _validate_span(label, transcript)
    return len(questions)


def _validate_span(label: Any, transcript: ParsedDocument) -> None:
    if not isinstance(label, dict):
        raise EvalLabelError("each eval span must be a mapping")
    start, end, quote = label.get("start_char"), label.get("end_char"), label.get("quote")
    if type(start) is not int or type(end) is not int or not isinstance(quote, str):
        raise EvalLabelError("eval span needs integer offsets and an exact quote")
    if not (0 <= start < end <= len(transcript.source_text)) or transcript.source_text[start:end] != quote:
        raise EvalLabelError("eval span does not match exact source text")
    if not quote.strip():
        raise EvalLabelError("eval quote cannot be blank")
    if not any(
        (block.content_span or block.span).start_char <= start
        and end <= (block.content_span or block.span).end_char
        for block in transcript.blocks
        if block.kind not in ("asset_reference", "separator")
    ):
        raise EvalLabelError("eval span must lie within an eligible source block")
