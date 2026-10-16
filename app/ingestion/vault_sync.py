"""Incrementally index selected vault folders: ingest changed files, retire deleted ones.

Only the folders named in KNOWLEDGE_INCLUDED_FOLDERS, plus Inbox, are read. A file
is ingested when its bytes differ from its active run; a file that disappears or is
emptied stops being served. A dry run parses everything and makes no model calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

from app.ingestion.chunking import STRUCTURAL_VERSION
from app.ingestion.contracts import ChunkSet, ParsedDocument
from app.ingestion.embeddings import EmbeddingSpec, embed_chunks
from app.ingestion.preview import preview_markdown
from app.ingestion.run_manifest import RunManifest
from app.ingestion.source_reader import document_id_for
from app.models.routes import (
    EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, EMBEDDING_MODEL_VERSION, PRIVACY,
    price_usd, provider_routes, verify_routes,
)
from app.storage.loader import connect_runtime, load_run, validate_run
from app.storage.publication import create_profile, publish_run, retire_document, select_profile
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from app.storage.spend import SpendCapError, check_daily_cap, record_spend


INBOX = "Inbox"
SKIPPED_DIRECTORIES = {".obsidian", ".trash"}
# Obsidian Sync names a conflict copy "Note (Conflicted copy Device 202610161200).md".
CONFLICT_COPY = re.compile(r"\((?:conflicted copy|conflict)\b", re.IGNORECASE)
GUARD_FRACTION = 0.10
GUARD_COUNT = 20
EMBEDDING_BATCH = 16
VECTOR_BYTES = EMBEDDING_DIMENSIONS * 4


class VaultSyncError(RuntimeError):
    """The pass cannot start safely."""


@dataclass(frozen=True)
class VaultSettings:
    source_root: Path
    root_alias: str
    included_folders: tuple[str, ...]
    runtime_dir: Path
    excluded_folders: tuple[str, ...] = ()

    @property
    def state_dir(self) -> Path:
        return self.runtime_dir / "vault-sync"


def parse_folders(value: str | list[str] | None) -> tuple[str, ...]:
    """Top-level folder names, deduplicated without regard to case; Inbox is always included."""
    items = value.split(",") if isinstance(value, str) else list(value or [])
    folders: dict[str, str] = {}
    for item in [*items, INBOX]:
        name = unicodedata.normalize("NFC", item.strip().strip("/\\"))
        if not name:
            continue
        if "/" in name or "\\" in name or name in (".", ".."):
            raise VaultSyncError("included folders must be top-level folder names")
        folders.setdefault(name.casefold(), name)
    return tuple(folders.values())


@dataclass(frozen=True)
class VaultFile:
    relative_path: str
    path: Path
    sha256: str
    size: int
    empty: bool


@dataclass
class Scan:
    files: dict[str, VaultFile]
    missing_folders: list[str]
    duplicates: list[str]


def scan_vault(settings: VaultSettings) -> Scan:
    root = settings.source_root
    present = {entry.name.casefold(): entry.name for entry in root.iterdir() if entry.is_dir()}
    excluded = {unicodedata.normalize("NFC", item).strip("/").casefold() for item in settings.excluded_folders}
    files: dict[str, VaultFile] = {}
    missing, duplicates = [], []
    for folder in settings.included_folders:
        actual = present.get(folder.casefold())
        if actual is None:
            if folder.casefold() != INBOX.casefold():
                missing.append(folder)
            continue
        for directory, subdirectories, names in os.walk(root / actual):
            relative_dir = Path(directory).relative_to(root).as_posix()
            subdirectories[:] = sorted(
                name for name in subdirectories
                if not name.startswith(".") and name not in SKIPPED_DIRECTORIES
                and unicodedata.normalize("NFC", f"{relative_dir}/{name}").casefold() not in excluded
            )
            for name in sorted(names):
                if not name.lower().endswith(".md") or CONFLICT_COPY.search(name):
                    continue
                path = Path(directory) / name
                relative = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
                data = path.read_bytes()
                document_id = document_id_for(settings.root_alias, relative)
                if document_id in files:
                    duplicates.append(relative)
                    continue
                files[document_id] = VaultFile(
                    relative_path=relative, path=path, sha256=hashlib.sha256(data).hexdigest(),
                    size=len(data), empty=not data.decode("utf-8-sig", errors="replace").strip(),
                )
    return Scan(files, missing, duplicates)


def embedding_spec() -> EmbeddingSpec:
    return EmbeddingSpec(
        provider="openrouter", model=EMBEDDING_MODEL, model_version=EMBEDDING_MODEL_VERSION,
        dimensions=EMBEDDING_DIMENSIONS, distance_metric="cosine", input_recipe_version="raw-v1",
        document_input_type="search_document", query_input_type="search_query",
        chunking_version=STRUCTURAL_VERSION,
        # A fixed timestamp keeps the spec identical across passes; it is excluded from spec_id.
        created_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
    )


def profile_id_for(spec: EmbeddingSpec) -> str:
    return "vault-structural-raw-" + spec_id(spec)[:12]


def active_documents(conn, profile_id: str, root_alias: str) -> dict[str, tuple[str, str]]:
    rows = conn.execute("""select d.document_id, d.relative_path, r.source_sha256
        from knowledge.active_runs a join knowledge.runs r on r.run_id=a.run_id
        join knowledge.documents d on d.document_id=a.document_id
        where a.profile_id=%s and d.root_alias=%s""", (profile_id, root_alias)).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


class FailureLog:
    """Remembers files that failed, by content hash, so a broken file is not retried every pass."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, str]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {})

    def failed(self, document_id: str, sha256: str) -> bool:
        return self.entries.get(document_id, {}).get("sha256") == sha256

    def record(self, document_id: str, relative_path: str, sha256: str, reason: str) -> None:
        self.entries[document_id] = {"relative_path": relative_path, "sha256": sha256, "reason": reason}

    def clear(self, document_id: str) -> None:
        self.entries.pop(document_id, None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


@dataclass
class PassPlan:
    to_ingest: list[tuple[str, VaultFile]] = field(default_factory=list)
    unchanged: int = 0
    retire_missing: list[tuple[str, str]] = field(default_factory=list)
    retire_unselected: list[tuple[str, str]] = field(default_factory=list)
    skipped_failed: list[str] = field(default_factory=list)
    skipped_empty: list[str] = field(default_factory=list)
    guard: str | None = None


def _top_folder(relative_path: str) -> str:
    return PurePosixPath(relative_path).parts[0].casefold()


def plan_pass(settings: VaultSettings, scan: Scan, active: dict[str, tuple[str, str]],
              failures: FailureLog) -> PassPlan:
    plan = PassPlan()
    for document_id, item in scan.files.items():
        current = active.get(document_id)
        if item.empty:
            # An emptied file is treated as deleted; a new empty file is simply skipped.
            if current:
                plan.retire_missing.append((document_id, current[0]))
            else:
                plan.skipped_empty.append(item.relative_path)
        elif current and current[1] == item.sha256:
            plan.unchanged += 1
        elif failures.failed(document_id, item.sha256):
            plan.skipped_failed.append(item.relative_path)
        else:
            plan.to_ingest.append((document_id, item))
    included = {folder.casefold() for folder in settings.included_folders}
    for document_id, (relative_path, _) in active.items():
        if document_id in scan.files:
            continue
        target = plan.retire_missing if _top_folder(relative_path) in included else plan.retire_unselected
        target.append((document_id, relative_path))
    if scan.missing_folders:
        plan.guard = "included folder is missing: " + ", ".join(scan.missing_folders)
    elif plan.retire_unselected:
        plan.guard = f"{len(plan.retire_unselected)} files are in folders no longer selected"
    elif len(plan.retire_missing) > GUARD_COUNT or (
            active and len(plan.retire_missing) > GUARD_FRACTION * len(active)):
        plan.guard = f"{len(plan.retire_missing)} of {len(active)} indexed files would be retired"
    return plan


@dataclass
class PreparedFile:
    document_id: str
    vault_file: VaultFile
    run_dir: Path
    parsed: ParsedDocument
    chunks: ChunkSet

    @property
    def input_bytes(self) -> int:
        return sum(len(chunk.raw_text.encode("utf-8")) for chunk in self.chunks.chunks)

    @property
    def requests(self) -> int:
        return math.ceil(len(self.chunks.chunks) / EMBEDDING_BATCH)

    @property
    def worst_case_usd(self) -> float:
        # UTF-8 bytes bound the token count from above.
        return price_usd(EMBEDDING_MODEL, self.input_bytes)

    @property
    def projected_db_bytes(self) -> int:
        stored = sum(path.stat().st_size for path in self.run_dir.rglob("*.json"))
        return stored + 2 * self.vault_file.size + len(self.chunks.chunks) * VECTOR_BYTES


def prepare_file(settings: VaultSettings, document_id: str, item: VaultFile, runtime_dir: Path) -> PreparedFile:
    result = preview_markdown(settings.source_root, item.relative_path, runtime_dir, settings.root_alias)
    run_dir = runtime_dir / result["run_id"]
    parsed = ParsedDocument.model_validate_json((run_dir / "parsed.json").read_text(encoding="utf-8"))
    if parsed.source_sha256 != item.sha256:
        raise ValueError("file changed while it was being indexed")
    chunks = ChunkSet.model_validate_json((run_dir / "structural_raw.json").read_text(encoding="utf-8"))
    return PreparedFile(document_id, item, run_dir, parsed, chunks)


def _failure_reason(exc: Exception) -> str:
    message = str(exc).splitlines()[0][:120] if str(exc) else ""
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


@contextmanager
def _pass_lock(settings: VaultSettings):
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    path = settings.state_dir / "pass.lock"
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise VaultSyncError(f"another indexing pass is running (remove {path} if it is stale)") from None
    try:
        os.write(handle, str(os.getpid()).encode())
        os.close(handle)
        yield
    finally:
        path.unlink(missing_ok=True)


def _measured_usd(client, start: int, worst_case: float) -> float:
    """Measured embedding cost since `start`; unknown usage counts at its worst-case bound."""
    requests = getattr(client, "billable_requests", None)
    if requests is None:
        return worst_case
    total = 0.0
    for model_id, usage in requests[start:]:
        if usage.input_tokens is None:
            return worst_case
        total += price_usd(model_id, usage.input_tokens, usage.output_tokens or 0)
    return total


def _select_live_manifest(run_dir: Path, client) -> RunManifest:
    manifest = RunManifest.load(run_dir)
    selection = getattr(client, "live_run_selection", None)
    if selection is not None:
        manifest.live_call_selected, manifest.request_budget, manifest.estimated_input_token_budget = selection
    manifest.save(run_dir)
    return manifest


def _live_client(prepared: list[PreparedFile]):
    from app.models.openrouter import OpenRouterClient

    client = OpenRouterClient(
        allow_live=True, request_budget=max(1, sum(item.requests for item in prepared)),
        estimated_input_token_budget=max(1, sum(item.input_bytes for item in prepared)),
        provider=PRIVACY, model_providers=provider_routes([EMBEDDING_MODEL]),
    )
    verify_routes(client, [EMBEDDING_MODEL])
    return client


def run_pass(settings: VaultSettings, *, conn=None, live: bool = False, max_usd: float | None = None,
             allow_mass_retire: bool = False, client=None, spend_source: str = "indexing",
             enforce_daily_cap: bool = True) -> dict[str, Any]:
    """One indexing pass. Without `live` it parses and estimates only."""
    if live and (max_usd is None or max_usd <= 0):
        raise VaultSyncError("a live pass needs a positive --max-usd")
    if live and conn is None:
        raise VaultSyncError("a live pass needs a database connection")
    report: dict[str, Any] = {"mode": "live" if live else "dry-run",
                              "included_folders": list(settings.included_folders)}
    if not settings.source_root.is_dir():
        report["guard"] = "vault root is missing; nothing was read or retired"
        return report
    with _pass_lock(settings):
        spec = embedding_spec()
        profile_id = profile_id_for(spec)
        scan = scan_vault(settings)
        active = active_documents(conn, profile_id, settings.root_alias) if conn is not None else {}
        failures = FailureLog(settings.state_dir / "failures.json")
        plan = plan_pass(settings, scan, active, failures)
        report.update(profile_id=profile_id, files_seen=len(scan.files), active_before=len(active),
                      unchanged=plan.unchanged, to_ingest=len(plan.to_ingest),
                      skipped_empty=len(plan.skipped_empty), skipped_failed=len(plan.skipped_failed),
                      duplicate_paths=scan.duplicates, guard=plan.guard,
                      retire_missing=len(plan.retire_missing), retire_unselected=len(plan.retire_unselected))
        scratch = Path(tempfile.mkdtemp(prefix="vault-dry-run-")) if not live else None
        runtime_dir = scratch or settings.runtime_dir
        prepared: list[PreparedFile] = []
        parse_failures: dict[str, int] = {}
        try:
            for document_id, item in plan.to_ingest:
                try:
                    prepared.append(prepare_file(settings, document_id, item, runtime_dir))
                except Exception as exc:  # noqa: BLE001 - one broken file must not stop the pass
                    reason = type(exc).__name__
                    parse_failures[reason] = parse_failures.get(reason, 0) + 1
                    if live:
                        failures.record(document_id, item.relative_path, item.sha256, _failure_reason(exc))
            report.update(
                parse_failures=parse_failures, ready_to_embed=len(prepared),
                chunks=sum(len(item.chunks.chunks) for item in prepared),
                chunk_tokens=sum(chunk.token_count for item in prepared for chunk in item.chunks.chunks),
                embedding_input_bytes=sum(item.input_bytes for item in prepared),
                requests=sum(item.requests for item in prepared),
                worst_case_usd=round(sum(item.worst_case_usd for item in prepared), 8),
                projected_db_bytes=sum(item.projected_db_bytes for item in prepared),
            )
            if not live:
                return report
            _embed_and_publish(settings, conn, prepared, report, failures, spec, profile_id,
                               client or _live_client(prepared), max_usd, spend_source, enforce_daily_cap)
            _retire(conn, plan, report, profile_id, allow_mass_retire)
            corpus = conn.execute("select profile_id from knowledge.corpus_state where singleton=true").fetchone()[0]
            if corpus is None and report["published"]:
                select_profile(conn, profile_id)
                report["selected_profile"] = profile_id
            report["active_after"] = len(active_documents(conn, profile_id, settings.root_alias))
            return report
        finally:
            if live:
                failures.save()
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)


def _embed_and_publish(settings, conn, prepared, report, failures, spec, profile_id, client,
                       max_usd, spend_source, enforce_daily_cap) -> None:
    spent = 0.0
    published, failed = 0, {}
    report["stopped"] = None
    profile_ready = conn.execute("select 1 from knowledge.profiles where profile_id=%s", (profile_id,)).fetchone() is not None
    for item in prepared:
        if spent + item.worst_case_usd > max_usd:
            report["stopped"] = "pass spend limit reached"
            break
        if enforce_daily_cap:
            try:
                check_daily_cap(conn, Decimal(str(item.worst_case_usd)))
            except SpendCapError:
                report["stopped"] = "daily spend cap reached"
                break
        start = len(getattr(client, "billable_requests", []) or [])
        try:
            manifest = _select_live_manifest(item.run_dir, client)
            progress = embed_chunks(item.parsed, item.chunks, client, spec.model_copy(), manifest, item.run_dir,
                                    batch_size=EMBEDDING_BATCH)
            if not progress.ready:
                raise ValueError("embedding batch did not complete")
            validate_run(item.run_dir)
            load_run(conn, item.run_dir)
            if not profile_ready:
                create_profile(conn, SearchProfile(profile_id=profile_id, variant="structural_raw",
                                                   chunking_version=spec.chunking_version,
                                                   embedding_spec_id=spec_id(spec)))
                profile_ready = True
            publish_run(conn, PublicationSelection(run_id=item.parsed.run_id, profile_id=profile_id))
            failures.clear(item.document_id)
            published += 1
        except Exception as exc:  # noqa: BLE001 - the previous active run keeps serving
            failures.record(item.document_id, item.vault_file.relative_path, item.vault_file.sha256,
                            _failure_reason(exc))
            failed[type(exc).__name__] = failed.get(type(exc).__name__, 0) + 1
        finally:
            cost = _measured_usd(client, start, item.worst_case_usd)
            if cost > 0:
                record_spend(conn, spend_source, cost, item.parsed.run_id)
            spent += cost
    report.update(published=published, ingest_failures=failed, spent_usd=round(spent, 8))


def _retire(conn, plan: PassPlan, report: dict[str, Any], profile_id: str, allow_mass_retire: bool) -> None:
    targets = list(plan.retire_missing)
    if plan.guard and not allow_mass_retire:
        report["retired"] = 0
        report["retire_held"] = "guard tripped; rerun with --allow-mass-retire after checking"
        return
    if allow_mass_retire:
        targets += plan.retire_unselected
    report["retired"] = sum(retire_document(conn, profile_id, document_id) for document_id, _ in targets)


def load_env_file(path: Path = Path(".env")) -> None:
    """Fill unset variables from a local .env; the last non-empty value wins."""
    if not path.is_file():
        return
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        value = value.strip().strip("'\"")
        if sep and key.strip() and not key.lstrip().startswith("#") and value:
            values[key.strip()] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def settings_from_env(args: argparse.Namespace) -> VaultSettings:
    root = args.source_root or os.getenv("KNOWLEDGE_SOURCE_ROOT")
    folders = args.folders or os.getenv("KNOWLEDGE_INCLUDED_FOLDERS")
    if not root:
        raise VaultSyncError("set KNOWLEDGE_SOURCE_ROOT to the vault root")
    if not folders:
        raise VaultSyncError("set KNOWLEDGE_INCLUDED_FOLDERS to the vault folders to index")
    excluded = [item.strip() for item in os.getenv("KNOWLEDGE_EXCLUDED_FOLDERS", "").split(",") if item.strip()]
    return VaultSettings(
        source_root=Path(root), root_alias=os.getenv("KNOWLEDGE_SOURCE_ROOT_ALIAS", "primary"),
        included_folders=parse_folders(folders),
        runtime_dir=Path(args.runtime_dir or os.getenv("KNOWLEDGE_RUNTIME_DIR", ".local/ingestion-runs")),
        excluded_folders=tuple(excluded),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index selected vault folders incrementally")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="parse and estimate; no model calls or writes")
    mode.add_argument("--live", action="store_true", help="embed, load, publish and retire")
    parser.add_argument("--max-usd", type=float, help="spend limit for this pass (required with --live)")
    parser.add_argument("--allow-mass-retire", action="store_true")
    parser.add_argument("--bulk-load", action="store_true",
                        help="record spend as the one-time bulk load, outside the daily cap")
    parser.add_argument("--no-database", action="store_true", help="dry run without comparing to the index")
    parser.add_argument("--source-root")
    parser.add_argument("--folders", help="comma-separated top-level folders; Inbox is always added")
    parser.add_argument("--runtime-dir")
    args = parser.parse_args(argv)
    load_env_file()
    try:
        settings = settings_from_env(args)
        if args.dry_run and args.no_database:
            report = run_pass(settings)
        else:
            with connect_runtime() as conn:
                report = run_pass(settings, conn=conn, live=args.live, max_usd=args.max_usd,
                                  allow_mass_retire=args.allow_mass_retire,
                                  spend_source="bulk_load" if args.bulk_load else "indexing",
                                  enforce_daily_cap=not args.bulk_load)
    except VaultSyncError as exc:
        print(f"vault sync stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
