"""Versioned local run metadata with explicit incomplete and failed artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from app.ingestion.contracts import ParsedDocument, SourceLocator, StrictRecord
from app.models.client import ModelSpec, ModelUsage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_artifact_json(run_dir: Path, kind: str, artifact_id: str, data: Any) -> str:
    directory = run_dir / "artifacts" / kind
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact_id}.json"
    temporary = directory / f"{artifact_id}.json.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path.relative_to(run_dir).as_posix()


class ArtifactRecord(StrictRecord):
    artifact_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    version_inputs: dict[str, Any]
    status: Literal["pending", "ready", "failed"] = "pending"
    model: ModelSpec | None = None
    path: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    attempts: int = Field(default=1, ge=1)
    error_code: str | None = None
    created_at: str = Field(default_factory=_now)


class RunManifest(StrictRecord):
    schema_version: Literal["1"] = "1"
    document_id: str
    run_id: str
    source_sha256: str
    locator: SourceLocator
    parser_version: str
    source_kind: str
    created_at: str = Field(default_factory=_now)
    live_call_selected: bool = False
    request_budget: int = Field(default=0, ge=0)
    estimated_input_token_budget: int = Field(default=0, ge=0)
    artifacts: dict[str, ArtifactRecord] = Field(default_factory=dict)

    @classmethod
    def for_document(
        cls, document: ParsedDocument, *, live_call_selected: bool = False,
        request_budget: int = 0, estimated_input_token_budget: int = 0,
    ) -> RunManifest:
        return cls(
            document_id=document.document_id,
            run_id=document.run_id,
            source_sha256=document.source_sha256,
            locator=document.locator,
            parser_version=document.parser_version,
            source_kind=document.source_kind,
            live_call_selected=live_call_selected,
            request_budget=request_budget,
            estimated_input_token_budget=estimated_input_token_budget,
        )

    def begin(
        self, kind: str, version_inputs: dict[str, Any],
        model: ModelSpec | None = None,
    ) -> ArtifactRecord:
        signature = json.dumps(
            {"kind": kind, "version_inputs": version_inputs,
             "model": model.model_dump() if model else None},
            sort_keys=True, separators=(",", ":"),
        )
        artifact_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]
        existing = self.artifacts.get(artifact_id)
        if existing and existing.status == "ready":
            return existing
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind,
            version_inputs=version_inputs,
            model=model,
            attempts=existing.attempts + 1 if existing else 1,
        )
        self.artifacts[artifact_id] = record
        return record

    def succeed(
        self, artifact_id: str, path: str, usage: ModelUsage | None = None,
    ) -> None:
        record = self.artifacts[artifact_id]
        if not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError("artifact path must be relative to the run directory")
        self.artifacts[artifact_id] = record.model_copy(
            update={"status": "ready", "path": path,
                    "usage": usage or ModelUsage(), "error_code": None}
        )

    def fail(self, artifact_id: str, error_code: str) -> None:
        record = self.artifacts[artifact_id]
        self.artifacts[artifact_id] = record.model_copy(
            update={"status": "failed", "path": None, "error_code": error_code}
        )

    def save(self, run_dir: Path) -> Path:
        path = run_dir / "run-manifest.json"
        temporary = run_dir / "run-manifest.json.tmp"
        temporary.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return path

    @classmethod
    def load(cls, run_dir: Path) -> RunManifest:
        return cls.model_validate_json(
            (run_dir / "run-manifest.json").read_text(encoding="utf-8")
        )
