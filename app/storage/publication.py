"""Transactionally select complete run/profile views for ordinary search."""

from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from app.storage import StorageError
from app.storage.records import PublicationSelection, SearchProfile


def create_profile(conn: psycopg.Connection, profile: SearchProfile) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select chunking_version from knowledge.embedding_specs where spec_id=%s", (profile.embedding_spec_id,))
        row = cur.fetchone()
        if row is None or row[0] != profile.chunking_version:
            raise StorageError("profile embedding spec has a different chunking version")
        cur.execute("select variant,chunking_version,spec_id,text_config,required_artifact_kinds from knowledge.profiles where profile_id=%s", (profile.profile_id,))
        old = cur.fetchone()
        expected = (profile.variant, profile.chunking_version, profile.embedding_spec_id, profile.text_config, profile.required_artifact_kinds)
        if old and tuple(old) != expected:
            raise StorageError("profile ID already has different settings")
        cur.execute("""insert into knowledge.profiles
            (profile_id,variant,chunking_version,spec_id,text_config,required_artifact_kinds)
            values (%s,%s,%s,%s,%s,%s) on conflict do nothing""",
            (profile.profile_id, *expected[:4], Jsonb(expected[4])))


def _validate(cur: psycopg.Cursor, selection: PublicationSelection) -> str:
    cur.execute("""select p.variant,p.chunking_version,p.spec_id,p.required_artifact_kinds,
        s.input_recipe_version,s.context_prompt_version,s.payload->'context_model',r.document_id
        from knowledge.profiles p join knowledge.embedding_specs s on s.spec_id=p.spec_id
        join knowledge.runs r on r.run_id=%s where p.profile_id=%s""",
        (selection.run_id, selection.profile_id))
    row = cur.fetchone()
    if row is None:
        raise StorageError("run or profile is unavailable")
    variant, version, sid, required_kinds, recipe, context_version, context_model, document_id = row
    cur.execute("""select count(*) from knowledge.chunks where run_id=%s and variant=%s and chunking_version=%s""",
                (selection.run_id, variant, version))
    count = cur.fetchone()[0]
    if count == 0:
        raise StorageError("selected chunk set is missing")
    cur.execute("""select count(*) from knowledge.embedding_vectors e
        join knowledge.artifacts a on a.run_id=e.run_id and a.artifact_id=e.artifact_id
        join knowledge.chunks c on c.run_id=e.run_id and c.chunk_id=e.chunk_id
        where e.run_id=%s and e.spec_id=%s and c.variant=%s and c.chunking_version=%s and a.status='ready'""",
        (selection.run_id, sid, variant, version))
    if cur.fetchone()[0] != count:
        raise StorageError("selected run has incomplete ready vectors")
    if recipe == "contextualized-v1":
        cur.execute("""select count(distinct c.chunk_id) from knowledge.chunks c
            join knowledge.contexts x on x.run_id=c.run_id and x.chunk_id=c.chunk_id
            join knowledge.artifacts a on a.run_id=x.run_id and a.artifact_id=x.artifact_id
            where c.run_id=%s and c.variant=%s and c.chunking_version=%s
              and a.status='ready' and x.payload->>'prompt_version'=%s
              and (%s::jsonb is null or x.payload->'model'=%s::jsonb)""",
            (selection.run_id, variant, version, context_version,
             Jsonb(context_model) if context_model is not None else None,
             Jsonb(context_model) if context_model is not None else None))
        if cur.fetchone()[0] != count:
            raise StorageError("contextualized profile needs matching ready contexts")
    if required_kinds and not selection.required_artifact_ids:
        raise StorageError("profile requires named enrichment artifacts")
    found_kinds = set()
    for artifact_id in selection.required_artifact_ids:
        cur.execute("select kind,status from knowledge.artifacts where run_id=%s and artifact_id=%s", (selection.run_id, artifact_id))
        artifact = cur.fetchone()
        if artifact is None or artifact[1] != "ready":
            raise StorageError("selected enrichment artifact is not ready")
        found_kinds.add(artifact[0])
    if not set(required_kinds).issubset(found_kinds):
        raise StorageError("required enrichment kind is absent")
    return document_id


def publish_run(conn: psycopg.Connection, selection: PublicationSelection) -> str:
    with conn.transaction(), conn.cursor() as cur:
        # Corpus lock serializes profile switches with every activation.
        cur.execute("select profile_id from knowledge.corpus_state where singleton=true for update")
        document_id = _validate(cur, selection)
        cur.execute("""insert into knowledge.active_runs (profile_id,document_id,run_id,selection)
            values (%s,%s,%s,%s) on conflict (profile_id,document_id) do update
            set run_id=excluded.run_id,selection=excluded.selection""",
            (selection.profile_id, document_id, selection.run_id, Jsonb(selection.model_dump(mode="json"))))
    return document_id


def select_profile(conn: psycopg.Connection, profile_id: str) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select profile_id from knowledge.corpus_state where singleton=true for update")
        current = cur.fetchone()[0]
        cur.execute("select 1 from knowledge.profiles where profile_id=%s", (profile_id,))
        if cur.fetchone() is None:
            raise StorageError("profile does not exist")
        if current is not None and current != profile_id:
            cur.execute("select document_id from knowledge.active_runs where profile_id=%s", (current,))
            documents = {row[0] for row in cur.fetchall()}
            cur.execute("select document_id from knowledge.active_runs where profile_id=%s", (profile_id,))
            if not documents.issubset({row[0] for row in cur.fetchall()}):
                raise StorageError("new profile lacks a complete active corpus")
        cur.execute("update knowledge.corpus_state set profile_id=%s where singleton=true", (profile_id,))


def retire_document(conn: psycopg.Connection, profile_id: str, document_id: str) -> bool:
    """Stop serving a deleted or emptied file; its runs and chunks stay as history."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select profile_id from knowledge.corpus_state where singleton=true for update")
        cur.execute("delete from knowledge.active_runs where profile_id=%s and document_id=%s",
                    (profile_id, document_id))
        return cur.rowcount == 1
