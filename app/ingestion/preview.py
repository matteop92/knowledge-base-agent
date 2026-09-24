"""Local previews of Markdown ingestion runs; no database or model calls."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.ingestion.eval_labels import EvalLabelError, validate_eval_labels
from app.ingestion.eval_mapping import map_eval_labels
from app.ingestion.chunking import fixed_raw_chunks, structural_raw_chunks
from app.ingestion.router import SourceKind, route_source
from app.ingestion.run_manifest import RunManifest
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


def preview_markdown(
    source_root: Path, relative_path: str, runtime_dir: Path,
    root_alias: str = "primary", eval_file: Path | None = None,
    source_kind: SourceKind | None = None,
) -> dict[str, Any]:
    """Capture, route, chunk, and report a Markdown file without network calls."""
    capture = capture_source(source_root, relative_path, runtime_dir, root_alias)
    parsed = route_source(capture, source_kind)
    fixed = fixed_raw_chunks(parsed)
    structural = structural_raw_chunks(parsed)
    manifest = RunManifest.for_document(parsed)
    outputs = {
        "parsed": parsed.model_dump(mode="json"),
        "fixed_raw": fixed.model_dump(mode="json"),
        "structural_raw": structural.model_dump(mode="json"),
    }
    paths = {}
    for name, data in outputs.items():
        path = capture.run_dir / f"{name}.json"
        temporary = capture.run_dir / f"{name}.json.tmp"
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        paths[name] = str(path)
    mapping = map_eval_labels(eval_file, parsed, [fixed, structural]) if eval_file else []
    if mapping:
        path = capture.run_dir / "eval_mapping.json"
        path.write_text(
            json.dumps([item.model_dump(mode="json") for item in mapping], indent=2),
            encoding="utf-8",
        )
        paths["eval_mapping"] = str(path)
    manifest.save(capture.run_dir)
    excluded = {
        reason: sum(item.reason == reason for item in parsed.excluded_spans)
        for reason in ("frontmatter", "whitespace", "export_furniture", "unclassified")
    }
    return {
        "document_id": parsed.document_id,
        "run_id": parsed.run_id,
        "source_sha256": parsed.source_sha256,
        "locator": parsed.locator.model_dump(),
        "title": parsed.metadata.title,
        "source_kind": parsed.source_kind,
        "route_reason": parsed.route_reason,
        "structural_confidence": parsed.structural_confidence,
        "block_count": len(parsed.blocks),
        "block_kinds": {
            kind: sum(block.kind == kind for block in parsed.blocks)
            for kind in sorted({block.kind for block in parsed.blocks})
        },
        "content_roles": {
            role: sum(block.content_role == role for block in parsed.blocks)
            for role in sorted({block.content_role for block in parsed.blocks})
        },
        "excluded_span_counts": excluded,
        "unresolved_asset_count": len(parsed.unresolved_assets),
        "diagnostics": [item.model_dump() for item in parsed.diagnostics],
        "chunk_variants": {
            item.variant: {
                "chunking_version": item.chunking_version,
                "chunk_count": len(item.chunks),
                "boundary_reasons": {
                    reason: sum(chunk.boundary_reason == reason for chunk in item.chunks)
                    for reason in sorted({chunk.boundary_reason for chunk in item.chunks})
                },
                "token_min": min(chunk.token_count for chunk in item.chunks),
                "token_max": max(chunk.token_count for chunk in item.chunks),
            } for item in (fixed, structural)
        },
        "eval_mapping": {
            item.variant: item.labeled_span_count for item in mapping
        },
        "artifact_versions": {},
        "model_usage": {"input_tokens": 0, "output_tokens": 0},
        "paths": paths,
        "manifest_path": str(capture.run_dir / "run-manifest.json"),
    }


def inspect_run(run_dir: Path) -> dict[str, Any]:
    """Show model activity and artifact versions for an existing local run."""
    manifest = RunManifest.load(run_dir)
    by_kind: dict[str, list[dict[str, Any]]] = {}
    input_tokens = output_tokens = 0
    for record in manifest.artifacts.values():
        by_kind.setdefault(record.kind, []).append({
            "artifact_id": record.artifact_id,
            "status": record.status,
            "version_inputs": record.version_inputs,
            "model": record.model.model_dump() if record.model else None,
            "error_code": record.error_code,
        })
        input_tokens += record.usage.input_tokens or 0
        output_tokens += record.usage.output_tokens or 0
    return {
        "document_id": manifest.document_id,
        "run_id": manifest.run_id,
        "source_sha256": manifest.source_sha256,
        "source_kind": manifest.source_kind,
        "live_call_selected": manifest.live_call_selected,
        "request_budget": manifest.request_budget,
        "estimated_input_token_budget": manifest.estimated_input_token_budget,
        "model_usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "artifact_versions": by_kind,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview one local Markdown source")
    parser.add_argument("--source-root", default=os.getenv("KNOWLEDGE_SOURCE_ROOT"))
    parser.add_argument("--root-alias", default=os.getenv("KNOWLEDGE_SOURCE_ROOT_ALIAS", "primary"))
    parser.add_argument("--relative-path")
    parser.add_argument(
        "--runtime-dir", default=os.getenv("KNOWLEDGE_RUNTIME_DIR", ".local/ingestion-runs")
    )
    parser.add_argument("--eval-file", type=Path)
    parser.add_argument("--source-kind", choices=list(SourceKind.__args__))
    parser.add_argument("--inspect-run", type=Path)
    args = parser.parse_args(argv)
    if not args.inspect_run and not args.source_root:
        parser.error("set --source-root or KNOWLEDGE_SOURCE_ROOT")
    if not args.inspect_run and not args.relative_path:
        parser.error("set --relative-path")
    try:
        summary = (
            inspect_run(args.inspect_run) if args.inspect_run else
            preview_markdown(
                Path(args.source_root), args.relative_path, Path(args.runtime_dir),
                args.root_alias, args.eval_file, args.source_kind,
            )
        )
    except (SourceReadError, TranscriptParseError, EvalLabelError, ValidationError, OSError, ValueError) as exc:
        print(f"preview failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
