"""Validate a local run fully, then stage it in one Postgres transaction."""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.ingestion.contracts import CaptureManifest, ChunkSet, ParsedDocument
from app.ingestion.embeddings import EmbeddingBatch
from app.ingestion.enrichment import ChunkContext
from app.ingestion.run_manifest import ArtifactRecord, RunManifest
from app.ingestion.summary import Summary
from app.storage import StorageError
from app.storage.records import spec_id


@dataclass(frozen=True)
class ValidatedArtifact:
    record: ArtifactRecord
    payload: ChunkContext | Summary | EmbeddingBatch | None


@dataclass(frozen=True)
class ValidatedRun:
    capture: CaptureManifest
    parsed: ParsedDocument
    source_bytes: bytes
    chunk_sets: tuple[ChunkSet, ChunkSet]
    manifest: RunManifest
    artifacts: tuple[ValidatedArtifact, ...]


def connect_runtime() -> psycopg.Connection:
    dsn = os.getenv("KNOWLEDGE_DATABASE_URL")
    if not dsn:
        raise StorageError("KNOWLEDGE_DATABASE_URL is required")
    # Let psycopg handle the credential; never include the DSN in our errors.
    return psycopg.connect(dsn, autocommit=True)


def _read_json(path: Path, model):
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _artifact_path(run_dir: Path, relative: str, kind: str, artifact_id: str) -> Path:
    if not relative or Path(relative).is_absolute() or "\\" in relative or ":" in relative:
        raise StorageError("artifact path must be local to the run directory")
    root = run_dir.resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    if path != root / "artifacts" / kind / f"{artifact_id}.json" or not path.is_file():
        raise StorageError("artifact path leaves the run artifact directory")
    return path


def validate_run(run_dir: Path) -> ValidatedRun:
    capture = _read_json(run_dir / "capture.json", CaptureManifest)
    parsed = _read_json(run_dir / "parsed.json", ParsedDocument)
    source_bytes = (run_dir / "source.md").read_bytes()
    source_text = source_bytes.decode("utf-8-sig")
    manifest = RunManifest.load(run_dir)
    identity = (capture.document_id, capture.run_id, capture.source_sha256, capture.locator)
    if hashlib.sha256(source_bytes).hexdigest() != capture.source_sha256 or source_text != parsed.source_text:
        raise StorageError("captured source does not match the parsed revision")
    if (parsed.document_id, parsed.run_id, parsed.source_sha256, parsed.locator) != identity:
        raise StorageError("parsed document does not match capture")
    if ((manifest.document_id, manifest.run_id, manifest.source_sha256, manifest.locator) != identity
        or manifest.parser_version != parsed.parser_version or manifest.source_kind != parsed.source_kind):
        raise StorageError("run manifest does not match capture")
    sets = tuple(_read_json(run_dir / f"{variant}.json", ChunkSet) for variant in ("fixed_raw", "structural_raw"))
    if any(item.variant != variant for item, variant in zip(sets, ("fixed_raw", "structural_raw"))):
        raise StorageError("chunk variant filename mismatch")
    for item in sets:
        item.validate_against(parsed)
    chunks = {chunk.chunk_id: chunk for item in sets for chunk in item.chunks}
    if len(chunks) != sum(len(item.chunks) for item in sets):
        raise StorageError("chunk IDs collide across variants")
    artifacts = []
    models = {"context": ChunkContext, "summary": Summary, "embedding": EmbeddingBatch}
    for artifact_id, record in manifest.artifacts.items():
        if artifact_id != record.artifact_id:
            raise StorageError("unknown or mismatched artifact")
        if record.kind == "cards":
            continue
        if record.kind not in models:
            raise StorageError("unknown or mismatched artifact")
        payload = None
        if record.status == "ready":
            if not record.path:
                raise StorageError("ready artifact has no path")
            path = _artifact_path(run_dir, record.path, record.kind, artifact_id)
            payload = _read_json(path, models[record.kind])
            if (payload.document_id, payload.run_id, payload.source_sha256) != identity[:3]:
                raise StorageError("artifact source revision mismatch")
            if payload.usage != record.usage:
                raise StorageError("artifact usage does not match manifest")
            if record.kind == "context":
                chunk = chunks.get(payload.chunk_id)
                if (chunk is None or payload.source_spans != chunk.source_spans or payload.model != record.model
                    or record.version_inputs.get("chunk_id") != payload.chunk_id
                    or record.version_inputs.get("prompt_version") != payload.prompt_version
                    or record.version_inputs.get("input_recipe_version") != payload.input_recipe_version):
                    raise StorageError("context chunk or model mismatch")
            elif record.kind == "summary":
                if (payload.model != record.model or any(cid not in chunks for cid in payload.chunk_ids)
                    or payload.chunk_ids != record.version_inputs.get("chunk_ids")
                    or payload.child_artifact_ids != record.version_inputs.get("child_artifact_ids")
                    or payload.prompt_version != record.version_inputs.get("prompt_version")):
                    raise StorageError("summary chunk or model mismatch")
                if payload.source_spans != [span for cid in payload.chunk_ids for span in chunks[cid].source_spans]:
                    raise StorageError("summary source spans do not match chunks")
                for span in payload.source_spans:
                    parsed._check_span(span)
            else:
                spec = payload.spec
                expected = record.version_inputs.get("spec")
                if expected != spec.model_dump(mode="json", exclude={"created_at"}) or record.model != spec.model_spec():
                    raise StorageError("embedding specification mismatch")
                if record.version_inputs.get("chunk_ids") != [item.chunk_id for item in payload.chunks]:
                    raise StorageError("embedding batch membership mismatch")
                context_hashes = record.version_inputs.get("context_sha256")
                if not isinstance(context_hashes, list) or len(context_hashes) != len(payload.chunks):
                    raise StorageError("embedding context inputs are incomplete")
                if spec.input_recipe_version == "raw-v1" and any(value is not None for value in context_hashes):
                    raise StorageError("raw embedding cannot name contexts")
                if spec.input_recipe_version == "contextualized-v1":
                    for item, expected_hash in zip(payload.chunks, context_hashes):
                        contexts = [
                            other for other in manifest.artifacts.values()
                            if other.kind == "context" and other.status == "ready" and other.path
                            and other.version_inputs.get("chunk_id") == item.chunk_id
                            and other.version_inputs.get("prompt_version") == spec.context_prompt_version
                            and (spec.context_model is None or other.model == spec.context_model)
                        ]
                        if not any(hashlib.sha256(_read_json(_artifact_path(run_dir, other.path, "context", other.artifact_id), ChunkContext).model_dump_json().encode()).hexdigest() == expected_hash for other in contexts):
                            raise StorageError("contextualized embedding has no matching context")
                matching_sets = [item for item in sets if item.chunking_version == spec.chunking_version]
                if len(matching_sets) != 1:
                    raise StorageError("embedding chunking version is ambiguous")
                selected = {item.chunk_id: item for item in matching_sets[0].chunks}
                for item in payload.chunks:
                    chunk = selected.get(item.chunk_id)
                    if chunk is None or item.source_spans != chunk.source_spans or len(item.vector) != spec.dimensions or any(not math.isfinite(value) for value in item.vector):
                        raise StorageError("embedding vector or provenance mismatch")
        elif record.path is not None:
            raise StorageError("non-ready artifact names a payload")
        artifacts.append(ValidatedArtifact(record, payload))
    return ValidatedRun(capture, parsed, source_bytes, sets, manifest, tuple(artifacts))


def load_run(conn: psycopg.Connection, run_dir: Path) -> str:
    run = validate_run(run_dir)
    capture, parsed = run.capture, run.parsed
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (capture.run_id,))
        cur.execute("select root_alias, relative_path from knowledge.documents where document_id = %s", (capture.document_id,))
        row = cur.fetchone()
        locator = (capture.locator.root_alias, capture.locator.relative_path)
        if row and tuple(row) != locator:
            raise StorageError("document ID already belongs to another locator")
        cur.execute("select document_id from knowledge.documents where root_alias = %s and relative_path = %s", locator)
        row = cur.fetchone()
        if row and row[0] != capture.document_id:
            raise StorageError("locator already belongs to another document")
        cur.execute("insert into knowledge.documents values (%s,%s,%s) on conflict do nothing", (capture.document_id, *locator))
        cur.execute("select document_id, source_sha256, source_bytes, parsed from knowledge.runs where run_id = %s", (capture.run_id,))
        row = cur.fetchone()
        parsed_json = parsed.model_dump(mode="json")
        if row and (row[0] != capture.document_id or row[1] != capture.source_sha256 or bytes(row[2]) != run.source_bytes or row[3] != parsed_json):
            raise StorageError("run ID already contains different source inputs")
        cur.execute("""insert into knowledge.runs
            (run_id, document_id, source_sha256, source_bytes, source_text, parsed, parser_version, manifest)
            values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
            (capture.run_id, capture.document_id, capture.source_sha256, run.source_bytes, parsed.source_text,
             Jsonb(parsed_json), parsed.parser_version, Jsonb(run.manifest.model_dump(mode="json"))))
        cur.execute("select artifact_id from knowledge.artifacts where run_id=%s", (capture.run_id,))
        if not {item[0] for item in cur.fetchall()}.issubset(run.manifest.artifacts):
            raise StorageError("run manifest omitted an already stored artifact")
        for chunk_set in run.chunk_sets:
            key = (capture.run_id, chunk_set.variant, chunk_set.chunking_version)
            cur.execute("select payload from knowledge.chunk_sets where run_id=%s and variant=%s and chunking_version=%s", key)
            row = cur.fetchone()
            payload = chunk_set.model_dump(mode="json")
            if row and row[0] != payload:
                raise StorageError("chunk set changed under an existing identity")
            cur.execute("""insert into knowledge.chunk_sets
                (run_id,variant,chunking_version,tokenizer_version,payload)
                values (%s,%s,%s,%s,%s) on conflict do nothing""", (*key, chunk_set.tokenizer_version, Jsonb(payload)))
            for chunk in chunk_set.chunks:
                cur.execute("select payload from knowledge.chunks where run_id=%s and chunk_id=%s", (capture.run_id, chunk.chunk_id))
                row = cur.fetchone()
                value = chunk.model_dump(mode="json")
                if row and row[0] != value:
                    raise StorageError("chunk changed under an existing identity")
                cur.execute("""insert into knowledge.chunks
                    (run_id,chunk_id,variant,chunking_version,position,raw_text,source_spans,block_indices,content_roles,heading_path,payload)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                    (capture.run_id, chunk.chunk_id, chunk_set.variant, chunk_set.chunking_version,
                     chunk.index, chunk.raw_text, Jsonb(value["source_spans"]), Jsonb(value["block_indices"]),
                     Jsonb(value["content_roles"]), Jsonb(value["heading_path"]), Jsonb(value)))
        for artifact in run.artifacts:
            record, payload = artifact.record, artifact.payload
            value = payload.model_dump(mode="json") if payload else None
            cur.execute("""select kind,version_inputs,model,status,payload,attempts,usage,error_code from knowledge.artifacts
                where run_id=%s and artifact_id=%s for update""", (capture.run_id, record.artifact_id))
            old = cur.fetchone()
            model = record.model.model_dump(mode="json") if record.model else None
            usage = record.usage.model_dump(mode="json")
            if old:
                if old[:3] != (record.kind, record.version_inputs, model):
                    raise StorageError("artifact changed under an existing identity")
                if record.attempts < old[5] or (old[3] == "ready" and (
                    record.status != "ready" or old[4] != value or record.attempts != old[5]
                    or old[6] != usage or old[7] != record.error_code
                )) or (record.attempts == old[5] and old[3] == "failed"
                       and record.status != "failed"):
                    raise StorageError("artifact retry is older than its stored state")
                if record.attempts == old[5] and record.status == old[3] and (
                    old[4] != value or old[6] != usage or old[7] != record.error_code
                ):
                    raise StorageError("artifact changed within an existing attempt")
                if old[3] != "ready":
                    cur.execute("""update knowledge.artifacts set status=%s,payload=%s,usage=%s,attempts=%s,error_code=%s
                        where run_id=%s and artifact_id=%s""",
                        (record.status, Jsonb(value) if value is not None else None, Jsonb(usage),
                         record.attempts, record.error_code, capture.run_id, record.artifact_id))
            else:
                cur.execute("""insert into knowledge.artifacts
                    (run_id,artifact_id,kind,version_inputs,model,status,payload,usage,attempts,error_code)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (capture.run_id, record.artifact_id, record.kind, Jsonb(record.version_inputs),
                     Jsonb(model), record.status, Jsonb(value) if value is not None else None,
                     Jsonb(record.usage.model_dump(mode="json")), record.attempts, record.error_code))
            if payload is None:
                continue
            if record.kind == "context":
                cur.execute("insert into knowledge.contexts values (%s,%s,%s,%s) on conflict do nothing",
                            (capture.run_id, record.artifact_id, payload.chunk_id, Jsonb(value)))
            elif record.kind == "summary":
                cur.execute("insert into knowledge.summaries values (%s,%s,%s) on conflict do nothing",
                            (capture.run_id, record.artifact_id, Jsonb(value)))
            elif record.kind == "embedding":
                spec = payload.spec
                sid = spec_id(spec)
                cur.execute("select payload from knowledge.embedding_specs where spec_id=%s", (sid,))
                old_spec = cur.fetchone()
                spec_value = spec.model_dump(mode="json", exclude={"created_at"})
                if old_spec and old_spec[0] != spec_value:
                    raise StorageError("embedding spec changed under an existing identity")
                cur.execute("""insert into knowledge.embedding_specs
                    (spec_id,provider,model,model_version,dimensions,distance_metric,input_recipe_version,context_prompt_version,chunking_version,payload)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing""",
                    (sid, spec.provider, spec.model, spec.model_version, spec.dimensions, spec.distance_metric,
                     spec.input_recipe_version, spec.context_prompt_version, spec.chunking_version, Jsonb(spec_value)))
                for item in payload.chunks:
                    vector = "[" + ",".join(str(number) for number in item.vector) + "]"
                    cur.execute("""select artifact_id, embedding::text = (%s::extensions.vector)::text
                        from knowledge.embedding_vectors where run_id=%s and chunk_id=%s and spec_id=%s""",
                        (vector, capture.run_id, item.chunk_id, sid))
                    previous = cur.fetchone()
                    if previous and (previous[0] != record.artifact_id or not previous[1]):
                        raise StorageError("embedding changed under an existing identity")
                    cur.execute("""insert into knowledge.embedding_vectors
                        (run_id,chunk_id,spec_id,artifact_id,embedding) values (%s,%s,%s,%s,%s::extensions.vector)
                        on conflict do nothing""", (capture.run_id, item.chunk_id, sid, record.artifact_id, vector))
        cur.execute("update knowledge.runs set manifest=%s where run_id=%s",
                    (Jsonb(run.manifest.model_dump(mode="json")), capture.run_id))
    return capture.run_id
