"""Map exact source labels to both raw chunk variants without overlap inflation."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from app.ingestion.contracts import ChunkSet, ParsedDocument, StrictRecord
from app.ingestion.eval_labels import EvalLabelError, validate_eval_labels


class SpanMapping(StrictRecord):
    question_id: str
    label_kind: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    chunk_ids: list[str]


class VariantMapping(StrictRecord):
    variant: str
    chunking_version: str
    labeled_span_count: int
    mappings: list[SpanMapping]


def map_eval_labels(
    path: Path, document: ParsedDocument, chunk_sets: list[ChunkSet],
) -> list[VariantMapping]:
    validate_eval_labels(path, document)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    outputs: list[VariantMapping] = []
    for chunk_set in chunk_sets:
        chunk_set.validate_against(document)
        mappings: list[SpanMapping] = []
        for question in data["questions"]:
            for label_kind in ("must_find", "useful"):
                for label in question.get(label_kind, []):
                    start, end = label["start_char"], label["end_char"]
                    matches = list(dict.fromkeys(
                        chunk.chunk_id for chunk in chunk_set.chunks
                        if any(
                            span.start_char <= start and end <= span.end_char
                            for span in chunk.source_spans
                        )
                    ))
                    if not matches:
                        raise EvalLabelError(
                            f"labeled span is missing from {chunk_set.variant} chunks"
                        )
                    mappings.append(SpanMapping(
                        question_id=question["id"], label_kind=label_kind,
                        start_char=start, end_char=end, chunk_ids=matches,
                    ))
        outputs.append(VariantMapping(
            variant=chunk_set.variant, chunking_version=chunk_set.chunking_version,
            labeled_span_count=len(mappings), mappings=mappings,
        ))
    return outputs
