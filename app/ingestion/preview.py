"""Local preview of one transcript ingestion run; no database or model calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.ingestion.eval_labels import EvalLabelError, validate_eval_labels
from app.ingestion.source_reader import SourceReadError, capture_source
from app.ingestion.transcript_parser import TranscriptParseError, parse_transcript


def preview_source(
    source_root: Path,
    relative_path: str,
    runtime_dir: Path,
    root_alias: str = "primary",
    eval_file: Path | None = None,
) -> dict[str, Any]:
    capture = capture_source(source_root, relative_path, runtime_dir, root_alias)
    parsed = parse_transcript(capture)
    question_count = validate_eval_labels(eval_file, parsed) if eval_file else None
    preview_path = capture.run_dir / "parsed.json"
    temporary_path = capture.run_dir / "parsed.json.tmp"
    temporary_path.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
    temporary_path.replace(preview_path)
    return {
        "document_id": parsed.document_id,
        "run_id": parsed.run_id,
        "source_sha256": parsed.source_sha256,
        "locator": parsed.locator.model_dump(),
        "title": parsed.metadata.title,
        "source_url": parsed.metadata.source_url,
        "source_origin": parsed.metadata.source_origin,
        "published": parsed.metadata.published.isoformat() if parsed.metadata.published else None,
        "date": parsed.metadata.date.isoformat() if parsed.metadata.date else None,
        "speakers": list(
            dict.fromkeys(block.speaker for block in parsed.blocks if block.kind == "speaker_turn")
        ),
        "turn_count": sum(block.kind == "speaker_turn" for block in parsed.blocks),
        "block_count": len(parsed.blocks),
        "excluded_span_counts": {
            reason: sum(item.reason == reason for item in parsed.excluded_spans)
            for reason in ("frontmatter", "whitespace", "export_furniture", "unclassified")
        },
        "unclassified_spans": [
            item.span.model_dump() for item in parsed.excluded_spans if item.reason == "unclassified"
        ],
        "first_turn_line": parsed.blocks[0].span.start_line,
        "last_turn_line": parsed.blocks[-1].span.end_line,
        "eval_question_count": question_count,
        "warnings": (
            ["unclassified source text"]
            if any(item.reason == "unclassified" for item in parsed.excluded_spans)
            else []
        ),
        "preview_path": str(preview_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview one local Markdown transcript")
    parser.add_argument("--source-root", default=os.getenv("KNOWLEDGE_SOURCE_ROOT"))
    parser.add_argument("--root-alias", default=os.getenv("KNOWLEDGE_SOURCE_ROOT_ALIAS", "primary"))
    parser.add_argument("--relative-path", required=True)
    parser.add_argument(
        "--runtime-dir", default=os.getenv("KNOWLEDGE_RUNTIME_DIR", ".local/ingestion-runs")
    )
    parser.add_argument("--eval-file", type=Path)
    args = parser.parse_args(argv)
    if not args.source_root:
        parser.error("set --source-root or KNOWLEDGE_SOURCE_ROOT")
    try:
        summary = preview_source(
            Path(args.source_root),
            args.relative_path,
            Path(args.runtime_dir),
            args.root_alias,
            args.eval_file,
        )
    except (SourceReadError, TranscriptParseError, EvalLabelError, ValidationError, OSError) as exc:
        print(f"preview failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
