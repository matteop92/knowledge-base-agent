"""Disposable Postgres proof; set KNOWLEDGE_TEST_DATABASE_URL to run it."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import psycopg

from app.ingestion.contracts import ChunkSet
from app.ingestion.embeddings import EmbeddedChunk, EmbeddingBatch, EmbeddingSpec
from app.ingestion.preview import preview_markdown
from app.ingestion.run_manifest import RunManifest, write_artifact_json
from app.models.client import ModelUsage
from app.storage import StorageError
from app.storage.loader import load_run
from app.storage.publication import create_profile, publish_run, select_profile
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from app.storage.search import search_text, search_vector


def add_vectors(run_dir: Path, spec: EmbeddingSpec):
    chunk_set = ChunkSet.model_validate_json((run_dir / "fixed_raw.json").read_text(encoding="utf-8"))
    result = RunManifest.load(run_dir)
    version = {"spec": spec.model_dump(mode="json", exclude={"created_at"}),
               "chunk_ids": [item.chunk_id for item in chunk_set.chunks],
               "context_sha256": [None] * len(chunk_set.chunks)}
    record = result.begin("embedding", version, spec.model_spec())
    from app.ingestion.contracts import CaptureManifest
    capture = CaptureManifest.model_validate_json((run_dir / "capture.json").read_text(encoding="utf-8"))
    batch = EmbeddingBatch(document_id=capture.document_id, run_id=capture.run_id,
                           source_sha256=capture.source_sha256, spec=spec,
                           chunks=[EmbeddedChunk(chunk_id=item.chunk_id, source_spans=item.source_spans,
                                                 vector=[1.0, 0.0, 0.0]) for item in chunk_set.chunks],
                           usage=ModelUsage())
    path = write_artifact_json(run_dir, "embedding", record.artifact_id, batch.model_dump(mode="json"))
    result.succeed(record.artifact_id, path)
    result.save(run_dir)


def fixture_run(base: Path, text: str, *, vectors: bool = True):
    source = base / "vault" / "Notes"
    source.mkdir(parents=True, exist_ok=True)
    (source / "sample.md").write_text(text, encoding="utf-8")
    result = preview_markdown(base / "vault", "Notes/sample.md", base / "runs")
    run_dir = base / "runs" / result["run_id"]
    chunk_set = ChunkSet.model_validate_json((run_dir / "fixed_raw.json").read_text(encoding="utf-8"))
    spec = EmbeddingSpec(provider="fixture", model="synthetic", model_version="1", dimensions=3,
                         distance_metric="cosine", input_recipe_version="raw-v1",
                         chunking_version=chunk_set.chunking_version)
    if vectors:
        add_vectors(run_dir, spec)
    return run_dir, spec, chunk_set


@unittest.skipUnless(os.getenv("KNOWLEDGE_TEST_DATABASE_URL"), "set disposable KNOWLEDGE_TEST_DATABASE_URL")
class Stage3IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True)
        with cls.conn.cursor() as cur:
            for role in ("anon", "authenticated"):
                cur.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname='{role}') then create role {role} nologin; end if; end $$")
            cur.execute(Path("supabase/migrations/20260923152944_private_knowledge_index.sql").read_text(encoding="utf-8"))
            cur.execute("set role knowledge_runtime")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_stale_retry_preserves_the_newer_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            run_dir, _, _ = fixture_run(Path(folder), "# Note\n\nA grounded claim.\n", vectors=False)
            manifest = RunManifest.load(run_dir)
            first = manifest.begin("context", {"chunk_id": "synthetic"})
            manifest.save(run_dir)
            stale = (run_dir / "run-manifest.json").read_text(encoding="utf-8")
            load_run(self.conn, run_dir)

            retried = manifest.begin("context", {"chunk_id": "synthetic"})
            self.assertEqual(retried.artifact_id, first.artifact_id)
            manifest.fail(retried.artifact_id, "model_error")
            manifest.save(run_dir)
            load_run(self.conn, run_dir)

            (run_dir / "run-manifest.json").write_text(stale, encoding="utf-8")
            with self.assertRaises(StorageError):
                load_run(self.conn, run_dir)
            with self.conn.cursor() as cur:
                cur.execute("select manifest from knowledge.runs where run_id=%s", (run_dir.name,))
                stored = cur.fetchone()[0]["artifacts"][first.artifact_id]
            self.assertEqual(stored["attempts"], 2)
            self.assertEqual(stored["status"], "failed")

    def test_concurrent_publication_keeps_one_complete_run(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            first, spec, _ = fixture_run(base, "# Orchard\n\nThe saffron apple is ready.\n")
            second, _, _ = fixture_run(base, "# Orchard\n\nThe violet fig is ready.\n")
            for run_dir in (first, second):
                load_run(self.conn, run_dir)
            profile_id = "concurrent-publication"
            create_profile(self.conn, SearchProfile(
                profile_id=profile_id, variant="fixed_raw",
                chunking_version=spec.chunking_version, embedding_spec_id=spec_id(spec)))
            barrier = threading.Barrier(2)

            def activate(run_id: str) -> None:
                with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
                    conn.execute("set role knowledge_runtime")
                    barrier.wait(timeout=10)
                    publish_run(conn, PublicationSelection(run_id=run_id, profile_id=profile_id))

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(activate, run_dir.name) for run_dir in (first, second)]
                for future in futures:
                    future.result(timeout=20)
            select_profile(self.conn, profile_id)
            with self.conn.cursor() as cur:
                cur.execute("select run_id from knowledge.active_runs where profile_id=%s", (profile_id,))
                winner = cur.fetchone()[0]
            self.assertIn(winner, {first.name, second.name})
            hits = search_text(self.conn, "ready")
            self.assertTrue(hits)
            self.assertEqual({hit.run_id for hit in hits}, {winner})

    def test_load_publish_search_replace_and_deny_public(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            first, spec, chunks = fixture_run(base, "# Orchard\n\nThe amber pear is ripe and sweet.\n")
            self.assertEqual(load_run(self.conn, first), first.name)
            self.assertEqual(load_run(self.conn, first), first.name)
            with self.assertRaises(psycopg.Error), self.conn.transaction(), self.conn.cursor() as cur:
                cur.execute("""insert into knowledge.chunks
                    (run_id,chunk_id,variant,chunking_version,position,raw_text,source_spans,
                     block_indices,content_roles,heading_path,payload)
                    select run_id,'bad-span',variant,chunking_version,999,raw_text,
                     '[{"start_char":999999,"end_char":1000000,"start_line":1,"end_line":1}]'::jsonb,
                     block_indices,content_roles,heading_path,payload
                    from knowledge.chunks where run_id=%s limit 1""", (first.name,))
            with self.assertRaises(psycopg.Error), self.conn.transaction(), self.conn.cursor() as cur:
                cur.execute("""insert into knowledge.embedding_vectors
                    (run_id,chunk_id,spec_id,artifact_id,embedding)
                    select run_id,chunk_id,spec_id,artifact_id,'[1,0]'::extensions.vector
                    from knowledge.embedding_vectors where run_id=%s limit 1""", (first.name,))
            profile = SearchProfile(profile_id="baseline", variant="fixed_raw",
                                    chunking_version=spec.chunking_version, embedding_spec_id=spec_id(spec))
            create_profile(self.conn, profile)
            selection = PublicationSelection(run_id=first.name, profile_id="baseline")
            publish_run(self.conn, selection)
            select_profile(self.conn, "baseline")
            lexical = search_text(self.conn, "amber pear")
            self.assertTrue(search_text(self.conn, "Which orchard item was mentioned in the story?"))
            semantic = search_vector(self.conn, [1.0, 0.0, 0.0], spec_id=spec_id(spec), metric="cosine")
            self.assertTrue(lexical)
            self.assertEqual(lexical[0].chunk_id, semantic[0].chunk_id)
            self.assertEqual(lexical[0].run_id, first.name)
            span = lexical[0].source_spans[0]
            with self.conn.cursor() as cur:
                cur.execute("select source_text from knowledge.runs where run_id=%s", (first.name,))
                source_text = cur.fetchone()[0]
            self.assertIn(source_text[span["start_char"]:span["end_char"]], lexical[0].raw_text)
            self.assertEqual(search_text(self.conn, "!!!"), [])
            with self.assertRaises(StorageError):
                search_vector(self.conn, [1.0, 0.0], spec_id=spec_id(spec), metric="cosine")

            second, _, _ = fixture_run(base, "# Orchard\n\nThe blue plum is tart.\n", vectors=False)
            load_run(self.conn, second)
            with self.assertRaises(StorageError):
                publish_run(self.conn, PublicationSelection(run_id=second.name, profile_id="baseline"))
            self.assertEqual(search_text(self.conn, "amber pear")[0].run_id, first.name)
            self.assertEqual(search_text(self.conn, "blue plum"), [])
            self.assertTrue(search_text(self.conn, "blue plum", profile_id="baseline", historical_run_id=second.name))
            add_vectors(second, spec)
            load_run(self.conn, second)
            with self.conn.cursor() as cur:
                cur.execute("select manifest from knowledge.runs where run_id=%s", (second.name,))
                stored_manifest = cur.fetchone()[0]
            self.assertTrue(any(item["status"] == "ready" and item["kind"] == "embedding"
                                for item in stored_manifest["artifacts"].values()))
            publish_run(self.conn, PublicationSelection(run_id=second.name, profile_id="baseline"))
            self.assertEqual(search_text(self.conn, "blue plum")[0].run_id, second.name)
            self.assertEqual(search_text(self.conn, "amber pear"), [])
            self.assertTrue(search_text(self.conn, "amber pear", profile_id="baseline", historical_run_id=first.name))

            alternate_spec = spec.model_copy(update={"model_version": "2"})
            add_vectors(second, alternate_spec)
            load_run(self.conn, second)
            alternate = SearchProfile(profile_id="alternate", variant="fixed_raw",
                                      chunking_version=spec.chunking_version,
                                      embedding_spec_id=spec_id(alternate_spec))
            create_profile(self.conn, alternate)
            with self.assertRaises(StorageError):
                select_profile(self.conn, "alternate")
            publish_run(self.conn, PublicationSelection(run_id=second.name, profile_id="alternate"))
            select_profile(self.conn, "alternate")
            self.assertEqual(search_vector(self.conn, [1.0, 0.0, 0.0],
                                           spec_id=spec_id(alternate_spec), metric="cosine")[0].run_id, second.name)

            with self.conn.cursor() as cur:
                cur.execute("set role anon")
                try:
                    with self.assertRaises(psycopg.Error):
                        cur.execute("select source_text from knowledge.runs limit 1")
                finally:
                    cur.execute("reset role")
            manifest = RunManifest.load(first)
            del manifest.artifacts[next(iter(manifest.artifacts))]
            manifest.save(first)
            with self.assertRaises(StorageError):
                load_run(self.conn, first)


class LoaderValidationTest(unittest.TestCase):
    def test_missing_database_url_has_no_secret_in_error(self):
        from app.storage.loader import connect_runtime
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(StorageError, "KNOWLEDGE_DATABASE_URL is required"):
            connect_runtime()

    def test_changed_snapshot_is_rejected_before_database_write(self):
        with tempfile.TemporaryDirectory() as folder:
            run_dir, _, _ = fixture_run(Path(folder), "# Note\n\nA grounded claim.\n", vectors=False)
            (run_dir / "source.md").write_text("changed", encoding="utf-8")
            from app.storage.loader import validate_run
            with self.assertRaises(StorageError):
                validate_run(run_dir)
