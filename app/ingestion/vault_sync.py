"""Incrementally index selected vault folders: ingest changed files, retire deleted ones.

Only the folders named in KNOWLEDGE_INCLUDED_FOLDERS are read. A file
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

import psycopg

from app.ingestion.chunking import STRUCTURAL_VERSION
from app.ingestion.contracts import ChunkSet, ParsedDocument
from app.ingestion.embeddings import EmbeddingSpec, embed_chunks
from app.ingestion.preview import preview_markdown
from app.ingestion.properties import (
    PROMPT_PATH as PROPERTIES_PROMPT, PROMPT_VERSION as PROPERTIES_VERSION, PropertiesResult, TagList, classify_properties, missing_fields,
    model_input, model_spec as properties_model, store_properties, worst_case_usd as properties_worst_case,
)
from app.ingestion.run_manifest import RunManifest
from app.env import load_env_file
from app.ingestion.source_reader import document_id_for
from app.models.client import ModelClientError
from app.models.routes import (
    EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, EMBEDDING_MODEL_VERSION, PRIVACY,
    price_usd, provider_routes, verify_routes,
)
from app.storage.loader import connect_runtime, load_run, validate_run
from app.storage.publication import create_profile, publish_run, retire_document, select_profile
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from app.storage.spend import SpendCapError, check_daily_cap, record_spend


SKIPPED_DIRECTORIES = {".obsidian", ".trash"}
# Obsidian Sync names a conflict copy "Note (Conflicted copy Device 202610161200).md".
CONFLICT_COPY = re.compile(r"\((?:conflicted copy|conflict)\b", re.IGNORECASE)
GUARD_FRACTION = 0.10
GUARD_COUNT = 20
EMBEDDING_BATCH = 16
VECTOR_BYTES = EMBEDDING_DIMENSIONS * 4
# Outages and network or database errors say nothing about the file, so its next pass retries it.
TRANSIENT_ERRORS = (ModelClientError, psycopg.Error, OSError)


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
    """Top-level folder names, deduplicated without regard to case."""
    items = value.split(",") if isinstance(value, str) else list(value or [])
    folders: dict[str, str] = {}
    for item in items:
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
    properties_usd: float = 0.0

    @property
    def input_bytes(self) -> int:
        return sum(len(chunk.raw_text.encode("utf-8")) for chunk in self.chunks.chunks)

    @property
    def requests(self) -> int:
        return math.ceil(len(self.chunks.chunks) / EMBEDDING_BATCH)

    @property
    def embedding_usd(self) -> float:
        # UTF-8 bytes bound the token count from above.
        return price_usd(EMBEDDING_MODEL, self.input_bytes)

    @property
    def worst_case_usd(self) -> float:
        return self.embedding_usd + self.properties_usd

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


def usage_mark(client) -> tuple[int, int]:
    return (len(getattr(client, "request_timings", []) or []), len(getattr(client, "billable_requests", []) or []))


def measured_cost(client, mark: tuple[int, int], worst_case: float) -> float:
    """Measured cost since the mark; a request with no usage record (timeout, error) counts at the worst case."""
    requests = getattr(client, "billable_requests", None)
    if requests is None:
        return worst_case
    timings = getattr(client, "request_timings", None)
    if timings is not None and len(timings) - mark[0] > len(requests) - mark[1]:
        return worst_case
    total = 0.0
    for model_id, usage in requests[mark[1]:]:
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


def openrouter_client(models: list[str], *, requests: int, input_bytes: int, structured: set[str]):
    """One live client pinned to zero-retention routes; fails closed if a route changed."""
    from app.models.openrouter import OpenRouterClient

    client = OpenRouterClient(allow_live=True, request_budget=max(1, requests),
                              estimated_input_token_budget=max(1, input_bytes),
                              provider=PRIVACY, model_providers=provider_routes(models))
    verify_routes(client, models, structured_ids=structured)
    return client


def classifier_input_bytes(parsed: ParsedDocument, tag_list: TagList) -> int:
    return len(PROPERTIES_PROMPT.read_text(encoding="utf-8").encode("utf-8")) + len(
        model_input(parsed, tag_list).encode("utf-8"))


def _live_client(prepared: list[PreparedFile], backfill: list[ParsedDocument], tag_list: TagList | None):
    classify = ([item.parsed for item in prepared if missing_fields(item.parsed)] + backfill) if tag_list else []
    properties = [properties_model().model_id] if classify else []
    return openrouter_client(
        [EMBEDDING_MODEL, *properties], requests=sum(item.requests for item in prepared) + len(classify),
        input_bytes=sum(item.input_bytes for item in prepared)
        + sum(classifier_input_bytes(parsed, tag_list) for parsed in classify),
        structured=set(properties))


# Stored results from these versions count as current: v4 only added a rule about which tags may be proposed, so notes
# tagged by v3 keep their tags and only new and edited notes use v4. Leave an old version out to have it redone.
PROPERTIES_VERSIONS = ("properties_v3", PROPERTIES_VERSION)


def _needs_properties(conn, profile_id: str, skip: set[str]) -> list[ParsedDocument]:
    """Active files with no current properties: loaded before the tag list was approved, or tagged by an older prompt."""
    rows = conn.execute("""select r.parsed from knowledge.active_runs a
        join knowledge.runs r on r.run_id=a.run_id
        left join knowledge.document_properties p on p.document_id=r.document_id and p.source_sha256=r.source_sha256
        where a.profile_id=%s and (p.document_id is null or p.prompt_version <> all(%s))
        order by r.document_id""", (profile_id, list(PROPERTIES_VERSIONS))).fetchall()
    # Stored as JSON, so parse it in JSON mode (dates arrive as ISO strings).
    parsed = [ParsedDocument.model_validate_json(json.dumps(row[0])) for row in rows]
    return [item for item in parsed if item.document_id not in skip and missing_fields(item)]


def _classify_and_store(conn, parsed: ParsedDocument, client, tag_list: TagList, report: dict[str, Any]) -> None:
    """A classifier failure is recorded for this revision; the file is still indexed."""
    try:
        result = classify_properties(parsed, tag_list, client)
        store_properties(conn, parsed, status="ready", result=result)
        report["properties_ready"] = report.get("properties_ready", 0) + 1
        if result.proposed_tag:
            report.setdefault("proposed_tags", {})
            report["proposed_tags"][result.proposed_tag] = report["proposed_tags"].get(result.proposed_tag, 0) + 1
    except TRANSIENT_ERRORS:
        # An outage is not a fact about this file: store nothing so the next pass tries again.
        report["properties_retry"] = report.get("properties_retry", 0) + 1
    except Exception as exc:  # noqa: BLE001
        store_properties(conn, parsed, status="failed", result=PropertiesResult({}, None, None),
                         error=_failure_reason(exc))
        report["properties_failed"] = report.get("properties_failed", 0) + 1


def _backfill_properties(conn, backfill: list[ParsedDocument], report: dict[str, Any], client, tag_list: TagList,
                         budget_left: float, bulk_load: bool) -> None:
    spent = 0.0
    for parsed in backfill:
        worst = properties_worst_case(parsed, tag_list)
        if spent + worst > budget_left:
            report["stopped"] = "pass spend limit reached during properties backfill"
            break
        if not bulk_load:
            try:
                check_daily_cap(conn, Decimal(str(worst)))
            except SpendCapError:
                report["stopped"] = "daily spend cap reached during properties backfill"
                break
        mark = usage_mark(client)
        _classify_and_store(conn, parsed, client, tag_list, report)
        cost = measured_cost(client, mark, worst)
        if cost > 0:
            record_spend(conn, "bulk_load" if bulk_load else "properties", cost, parsed.run_id)
        spent += cost
    report["spent_usd"] = round(report.get("spent_usd", 0) + spent, 8)


def _by_folder(prepared: list[PreparedFile]) -> dict[str, dict[str, Any]]:
    folders: dict[str, dict[str, Any]] = {}
    for item in prepared:
        entry = folders.setdefault(PurePosixPath(item.vault_file.relative_path).parts[0],
                                   {"files": 0, "chunks": 0, "worst_case_usd": 0.0})
        entry["files"] += 1
        entry["chunks"] += len(item.chunks.chunks)
        entry["worst_case_usd"] = round(entry["worst_case_usd"] + item.worst_case_usd, 8)
    return dict(sorted(folders.items()))


def run_pass(settings: VaultSettings, *, conn=None, live: bool = False, max_usd: float | None = None,
             allow_mass_retire: bool = False, client=None, bulk_load: bool = False,
             tag_list: TagList | None = None, retire: bool = True) -> dict[str, Any]:
    """One indexing pass. Without `live` it parses and estimates only.

    Properties are generated only with an approved tag list; the estimate always includes them.
    With `retire=False` nothing is retired: the server's first pass after a start uses it, because the
    vault copy may still be syncing and a half-downloaded folder must not look like deleted notes.
    """
    if live and (max_usd is None or max_usd <= 0):
        raise VaultSyncError("a live pass needs a positive --max-usd")
    if live and conn is None:
        raise VaultSyncError("a live pass needs a database connection")
    report: dict[str, Any] = {"mode": "live" if live else "dry-run",
                              "included_folders": list(settings.included_folders),
                              "properties": "on" if tag_list and tag_list.approved else "off: no approved tag list"}
    estimate_tags = tag_list or TagList(tags=())
    if tag_list is not None and not tag_list.approved:
        tag_list = None
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
        parse_failures: dict[str, list[str]] = {}
        try:
            for document_id, item in plan.to_ingest:
                try:
                    ready = prepare_file(settings, document_id, item, runtime_dir)
                except Exception as exc:  # noqa: BLE001 - one broken file must not stop the pass
                    parse_failures.setdefault(type(exc).__name__, []).append(item.relative_path)
                    if live:
                        failures.record(document_id, item.relative_path, item.sha256, _failure_reason(exc))
                    continue
                if missing_fields(ready.parsed):
                    ready.properties_usd = properties_worst_case(ready.parsed, estimate_tags)
                prepared.append(ready)
            ingesting = {document_id for document_id, _ in plan.to_ingest}
            backfill = _needs_properties(conn, profile_id, ingesting) if conn is not None and tag_list else []
            backfill_usd = sum(properties_worst_case(parsed, tag_list) for parsed in backfill)
            report.update(
                parse_failures={reason: len(paths) for reason, paths in parse_failures.items()},
                parse_failure_paths=parse_failures, ready_to_embed=len(prepared),
                properties_backfill=len(backfill),
                chunks=sum(len(item.chunks.chunks) for item in prepared),
                chunk_tokens=sum(chunk.token_count for item in prepared for chunk in item.chunks.chunks),
                embedding_input_bytes=sum(item.input_bytes for item in prepared),
                requests=sum(item.requests for item in prepared),
                embedding_worst_case_usd=round(sum(item.embedding_usd for item in prepared), 8),
                properties_worst_case_usd=round(sum(item.properties_usd for item in prepared) + backfill_usd, 8),
                worst_case_usd=round(sum(item.worst_case_usd for item in prepared) + backfill_usd, 8),
                projected_db_bytes=sum(item.projected_db_bytes for item in prepared),
                by_folder=_by_folder(prepared),
            )
            if not live:
                return report
            # No model client, and no endpoint check, on a pass with nothing to embed or tag.
            client = client or (_live_client(prepared, backfill, tag_list) if prepared or backfill else None)
            _embed_and_publish(settings, conn, prepared, report, failures, spec, profile_id,
                               client, max_usd, bulk_load, tag_list)
            if tag_list and backfill and not report["stopped"]:
                _backfill_properties(conn, backfill, report, client, tag_list, max_usd - report["spent_usd"],
                                     bulk_load)
            if retire:
                _retire(conn, plan, report, profile_id, allow_mass_retire)
            else:
                report["retired"] = 0
                report["retire_held"] = "retirement skipped on the first pass after a start"
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
                       max_usd, bulk_load, tag_list=None) -> None:
    """The one-time bulk load is recorded as such and runs under its own limit instead of the daily cap."""
    spent = 0.0
    published, failed = 0, {}
    report["stopped"] = None
    profile_ready = conn.execute("select 1 from knowledge.profiles where profile_id=%s", (profile_id,)).fetchone() is not None
    for item in prepared:
        if spent + item.worst_case_usd > max_usd:
            report["stopped"] = "pass spend limit reached"
            break
        if not bulk_load:
            try:
                check_daily_cap(conn, Decimal(str(item.worst_case_usd)))
            except SpendCapError:
                report["stopped"] = "daily spend cap reached"
                break
        mark = usage_mark(client)
        try:
            manifest = _select_live_manifest(item.run_dir, client)
            progress = embed_chunks(item.parsed, item.chunks, client, spec.model_copy(), manifest, item.run_dir,
                                    batch_size=EMBEDDING_BATCH)
            if not progress.ready:
                raise ValueError("embedding batch did not complete")
            validate_run(item.run_dir)
            load_run(conn, item.run_dir)
            if tag_list and missing_fields(item.parsed):
                _classify_and_store(conn, item.parsed, client, tag_list, report)
            if not profile_ready:
                create_profile(conn, SearchProfile(profile_id=profile_id, variant="structural_raw",
                                                   chunking_version=spec.chunking_version,
                                                   embedding_spec_id=spec_id(spec)))
                profile_ready = True
            publish_run(conn, PublicationSelection(run_id=item.parsed.run_id, profile_id=profile_id))
            failures.clear(item.document_id)
            published += 1
        except Exception as exc:  # noqa: BLE001 - the previous active run keeps serving
            # Remember only failures about the file itself; an outage must not freeze the file until it changes.
            if not isinstance(exc, TRANSIENT_ERRORS):
                failures.record(item.document_id, item.vault_file.relative_path, item.vault_file.sha256,
                                _failure_reason(exc))
            failed[type(exc).__name__] = failed.get(type(exc).__name__, 0) + 1
        finally:
            cost = measured_cost(client, mark, item.worst_case_usd)
            if cost > 0:
                record_spend(conn, "bulk_load" if bulk_load else "indexing", cost, item.parsed.run_id)
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


def propose_tags(settings: VaultSettings, sample_size: int, *, max_usd: float, client=None,
                 output: Path | None = None, seed: int = 17) -> dict[str, Any]:
    """Draft config/tags.yaml from tags already in use plus model suggestions for a sample of files."""
    import random
    from collections import Counter

    from app.ingestion.properties import TAGS_PATH, draft_tag_list, suggest_tags

    if sample_size < 1 or max_usd <= 0:
        raise VaultSyncError("--propose-tags needs a positive sample size and --max-usd")
    scan = scan_vault(settings)
    candidates = sorted((item for item in scan.files.items() if not item[1].empty), key=lambda pair: pair[1].relative_path)
    sample = random.Random(seed).sample(candidates, min(sample_size, len(candidates)))
    existing, suggested = Counter(), Counter()
    open_list = TagList(tags=())
    with tempfile.TemporaryDirectory(prefix="vault-tags-") as folder:
        parsed_files = []
        for document_id, item in candidates:
            try:
                parsed = prepare_file(settings, document_id, item, Path(folder)).parsed
            except Exception:  # noqa: BLE001 - unparseable files are reported by the dry run
                continue
            existing.update(tag.strip().lower() for tag in parsed.metadata.tags if tag.strip())
            if (document_id, item) in sample:
                parsed_files.append(parsed)
        worst = sum(properties_worst_case(parsed, open_list) for parsed in parsed_files)
        if worst > max_usd:
            raise VaultSyncError(f"tag proposal worst case ${worst:.4f} exceeds --max-usd")
        if client is None:
            model_id = properties_model().model_id
            client = openrouter_client([model_id], requests=len(parsed_files),
                                        input_bytes=sum(classifier_input_bytes(p, open_list) for p in parsed_files),
                                        structured={model_id})
        failed = 0
        for parsed in parsed_files:
            try:
                suggested.update(suggest_tags(parsed, client).properties["tags"])
            except Exception:  # noqa: BLE001
                failed += 1
    spent = measured_cost(client, (0, 0), worst)
    draft = draft_tag_list(existing, suggested, output or TAGS_PATH)
    return {"sampled_files": len(parsed_files), "failed_calls": failed, "files_scanned": len(candidates),
            "distinct_existing_tags": len(existing), "draft_tags": len(draft["tags"]),
            "spent_usd": round(spent, 8), "tag_list_path": str(output or TAGS_PATH)}


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
    mode.add_argument("--propose-tags", type=int, metavar="N",
                      help="draft config/tags.yaml from N sampled files (needs --max-usd); changes nothing else")
    parser.add_argument("--max-usd", type=float, help="spend limit for this pass (required with --live)")
    parser.add_argument("--allow-mass-retire", action="store_true")
    parser.add_argument("--bulk-load", action="store_true",
                        help="record spend as the one-time bulk load, outside the daily cap")
    parser.add_argument("--no-database", action="store_true", help="dry run without comparing to the index")
    parser.add_argument("--source-root")
    parser.add_argument("--folders", help="comma-separated top-level folders")
    parser.add_argument("--runtime-dir")
    args = parser.parse_args(argv)
    load_env_file()
    try:
        settings = settings_from_env(args)
        tag_list = TagList.load()  # the file; a live pass below reads the tags table instead
        if args.propose_tags is not None:
            report = propose_tags(settings, args.propose_tags, max_usd=args.max_usd or 0)
        elif args.dry_run and args.no_database:
            report = run_pass(settings, tag_list=tag_list)
        else:
            with connect_runtime() as conn:
                tag_list = TagList.load_from_database(conn) if args.live else tag_list
                report = run_pass(settings, conn=conn, live=args.live, max_usd=args.max_usd,
                                  allow_mass_retire=args.allow_mass_retire,
                                  bulk_load=args.bulk_load, tag_list=tag_list)
    except VaultSyncError as exc:
        print(f"vault sync stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
