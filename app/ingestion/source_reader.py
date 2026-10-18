"""Read and snapshot a local source without changing the original file."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.ingestion.contracts import CaptureManifest, SourceLocator


class SourceReadError(ValueError):
    """The selected file cannot be safely read as a supported source."""


@dataclass(frozen=True)
class CapturedSource:
    manifest: CaptureManifest
    source_bytes: bytes
    source_text: str
    run_dir: Path


def document_id_for(root_alias: str, relative_path: str) -> str:
    """Stable document ID for a locator; case-only path changes keep the same ID."""
    locator_path = unicodedata.normalize("NFC", relative_path)
    identity = f"knowledge-agent://{root_alias.casefold()}/{locator_path.casefold()}"
    return str(uuid5(NAMESPACE_URL, identity))


def capture_source(
    source_root: Path,
    relative_path: str,
    runtime_dir: Path,
    root_alias: str = "primary",
) -> CapturedSource:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", root_alias):
        raise SourceReadError("root alias must use letters, digits, hyphens, or underscores")
    relative = Path(relative_path)
    if not relative_path or relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise SourceReadError("source locator must be a relative path inside the source root")
    if relative.suffix.lower() != ".md":
        raise SourceReadError("only Markdown .md sources are supported")
    try:
        root = source_root.resolve(strict=True)
        if not root.is_dir():
            raise SourceReadError("source root is not a directory")
        source_path = (root / relative).resolve(strict=True)
        if not source_path.is_relative_to(root) or not source_path.is_file():
            raise SourceReadError("source path is not a file inside the source root")
        original_bytes = source_path.read_bytes()
    except (OSError, RuntimeError) as exc:
        raise SourceReadError(f"cannot read source: {exc}") from exc
    if not original_bytes:
        raise SourceReadError("source file is empty")
    try:
        source_text = original_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceReadError("source file is not valid UTF-8") from exc
    if not source_text.strip():
        raise SourceReadError("source file contains no text")

    locator_path = unicodedata.normalize("NFC", relative.as_posix())
    locator = SourceLocator(root_alias=root_alias, relative_path=locator_path)
    manifest = CaptureManifest(
        document_id=document_id_for(root_alias, locator_path),
        run_id=str(uuid4()),
        source_sha256=hashlib.sha256(original_bytes).hexdigest(),
        locator=locator,
    )
    run_dir = runtime_dir / manifest.run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "source.md").write_bytes(original_bytes)
        (run_dir / "capture.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise SourceReadError(f"cannot save source snapshot: {exc}") from exc
    return CapturedSource(manifest, original_bytes, source_text, run_dir)
