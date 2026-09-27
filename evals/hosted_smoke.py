"""Load and search one synthetic hosted source; keep the report free of source text."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import psycopg

from app.ingestion.contracts import ChunkSet
from app.ingestion.embeddings import EmbeddedChunk, EmbeddingBatch, EmbeddingSpec
from app.ingestion.preview import preview_markdown
from app.ingestion.run_manifest import RunManifest, write_artifact_json
from app.models.client import ModelUsage
from app.storage.loader import load_run, validate_run
from app.storage.publication import create_profile, publish_run, select_profile
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from app.storage.search import search_text, search_vector


ROOT = Path('.local/stage3-hosted-smoke')
PROFILE = 'hosted-smoke-synthetic-v1'
TEXT = '# Hosted smoke fixture\n\nThe copper lantern marks the path to the observatory.\n'


def prepare() -> tuple[Path, EmbeddingSpec, ChunkSet]:
    source = ROOT / 'vault' / 'Notes' / 'sample.md'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(TEXT, encoding='utf-8')
    preview = preview_markdown(ROOT / 'vault', 'Notes/sample.md', ROOT / 'runs',
                               root_alias='hosted-smoke')
    run_dir = ROOT / 'runs' / preview['run_id']
    chunks = ChunkSet.model_validate_json((run_dir / 'fixed_raw.json').read_text(encoding='utf-8'))
    spec = EmbeddingSpec(provider='fixture', model='synthetic', model_version='1',
                         dimensions=3, distance_metric='cosine',
                         input_recipe_version='raw-v1',
                         chunking_version=chunks.chunking_version)
    manifest = RunManifest.load(run_dir)
    record = manifest.begin('embedding', {
        'spec': spec.model_dump(mode='json', exclude={'created_at'}),
        'chunk_ids': [chunk.chunk_id for chunk in chunks.chunks],
        'context_sha256': [None] * len(chunks.chunks),
    }, spec.model_spec())
    batch = EmbeddingBatch(document_id=preview['document_id'], run_id=preview['run_id'],
                           source_sha256=preview['source_sha256'], spec=spec,
                           chunks=[EmbeddedChunk(chunk_id=chunk.chunk_id,
                                                 source_spans=chunk.source_spans,
                                                 vector=[1.0, 0.0, 0.0])
                                   for chunk in chunks.chunks],
                           usage=ModelUsage())
    path = write_artifact_json(run_dir, 'embedding', record.artifact_id,
                               batch.model_dump(mode='json'))
    manifest.succeed(record.artifact_id, path)
    manifest.save(run_dir)
    validate_run(run_dir)
    return run_dir, spec, chunks


def verify_dsn(dsn: str) -> None:
    parsed = urlparse(dsn)
    if (parsed.hostname != 'aws-0-eu-central-1.pooler.supabase.com'
        or parsed.port != 5432 or parsed.username != 'knowledge_runtime_hosted.ielkvsfcnwtaljnemxua'
        or parsed.path != '/postgres' or parse_qs(parsed.query).get('sslmode') != ['require']):
        raise ValueError('hosted DSN must use the project session pooler and restricted login with TLS')


def resume(run_id: str) -> tuple[Path, EmbeddingSpec, ChunkSet]:
    if str(UUID(run_id)) != run_id:
        raise ValueError('resume run ID must be a canonical UUID')
    run_dir = ROOT / 'runs' / run_id
    chunks = ChunkSet.model_validate_json((run_dir / 'fixed_raw.json').read_text(encoding='utf-8'))
    spec = EmbeddingSpec(provider='fixture', model='synthetic', model_version='1',
                         dimensions=3, distance_metric='cosine',
                         input_recipe_version='raw-v1',
                         chunking_version=chunks.chunking_version)
    validate_run(run_dir)
    return run_dir, spec, chunks


def run(resume_run_id: str | None = None) -> dict:
    dsn = os.environ['KNOWLEDGE_HOSTED_DATABASE_URL']
    verify_dsn(dsn)
    run_dir, spec, chunks = resume(resume_run_id) if resume_run_id else prepare()
    source_bytes = (run_dir / 'source.md').read_bytes()
    report = {'run_id': run_dir.name, 'profile_id': PROFILE,
              'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
              'synthetic_chunks': len(chunks.chunks)}
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        if not conn.pgconn.ssl_in_use:
            raise ValueError('hosted client connection is not using TLS')
        role = conn.execute('select current_user').fetchone()[0]
        if role != 'knowledge_runtime_hosted':
            raise ValueError('hosted connection is not the restricted runtime login')
        previous = conn.execute('select profile_id from knowledge.corpus_state where singleton=true').fetchone()[0]
        if previous is not None:
            raise ValueError('hosted corpus already has a selected profile; smoke test will not switch it')
        if conn.execute("select has_schema_privilege('anon','knowledge','USAGE') or has_schema_privilege('authenticated','knowledge','USAGE')").fetchone()[0]:
            raise ValueError('knowledge schema is exposed to a public API role')
        if conn.execute("select has_table_privilege(current_user,'knowledge.documents','DELETE')").fetchone()[0]:
            raise ValueError('runtime login unexpectedly has delete permission')
        loaded = load_run(conn, run_dir)
        if loaded != run_dir.name:
            raise ValueError('loaded run ID mismatch')
        create_profile(conn, SearchProfile(profile_id=PROFILE, variant='fixed_raw',
                                           chunking_version=spec.chunking_version,
                                           embedding_spec_id=spec_id(spec)))
        publish_run(conn, PublicationSelection(run_id=loaded, profile_id=PROFILE))
        try:
            select_profile(conn, PROFILE)
            lexical = search_text(conn, 'copper lantern', k=5)
            semantic = search_vector(conn, [1.0, 0.0, 0.0],
                                     spec_id=spec_id(spec), metric='cosine', k=5)
            if not lexical or not semantic or any(hit.run_id != loaded for hit in lexical + semantic):
                raise ValueError('hosted search did not return the synthetic run')
            stored_bytes, stored_text = conn.execute(
                'select source_bytes,source_text from knowledge.runs where run_id=%s',
                (loaded,)).fetchone()
            stored_bytes = bytes(stored_bytes)
            if stored_bytes != source_bytes:
                raise ValueError('stored source bytes changed')
            if stored_bytes.decode('utf-8') != stored_text:
                raise ValueError('stored decoded source differs from captured bytes')
            resolved = 0
            for hit in lexical + semantic:
                for span in hit.source_spans:
                    start, end = span['start_char'], span['end_char']
                    if not (0 <= start < end <= len(stored_text) and stored_text[start:end].strip()):
                        raise ValueError('search span is invalid')
                    bstart = len(stored_text[:start].encode('utf-8'))
                    bend = len(stored_text[:end].encode('utf-8'))
                    if stored_bytes[bstart:bend] != stored_text[start:end].encode('utf-8'):
                        raise ValueError('search span differs from stored source bytes')
                    resolved += 1
            report.update(runtime_role=role, client_tls=True, loaded=True, published=True,
                          lexical_hits=len(lexical), semantic_hits=len(semantic),
                          resolved_source_spans=resolved,
                          active_run_count=conn.execute('select count(*) from knowledge.active_runs where profile_id=%s',
                                                        (PROFILE,)).fetchone()[0])
        finally:
            conn.execute('update knowledge.corpus_state set profile_id=null where singleton=true and profile_id=%s',
                         (PROFILE,))
        report['selected_profile_restored_to_null'] = conn.execute(
            'select profile_id is null from knowledge.corpus_state where singleton=true').fetchone()[0]
    (ROOT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Synthetic hosted Supabase smoke test')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true', help='validate one local synthetic run without database access')
    mode.add_argument('--live', action='store_true', help='load, publish, and search the synthetic run in hosted Supabase')
    parser.add_argument('--resume-run-id', help='reuse an already prepared or loaded synthetic run')
    args = parser.parse_args()
    if args.resume_run_id and not args.live:
        parser.error('--resume-run-id requires --live')
    try:
        if args.prepare:
            path, _, chunks = prepare()
            print(json.dumps({'local_synthetic_run': path.name, 'chunks': len(chunks.chunks)}))
        else:
            print(json.dumps(run(args.resume_run_id)))
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error_class': type(exc).__name__,
                          'message': str(exc) if isinstance(exc, ValueError) else None}))
        raise SystemExit(1)
