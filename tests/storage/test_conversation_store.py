"""Disposable Postgres proof for the web-app tables; set KNOWLEDGE_TEST_DATABASE_URL to run it."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.storage.loader import load_run
from app.storage.publication import create_profile, publish_run
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from tests.storage.database import apply_migrations
from tests.storage.test_stage3_integration import fixture_run

NEW_TABLES = ("conversations", "turns", "capture_jobs", "sessions", "feedback", "spend")


@unittest.skipUnless(os.getenv("KNOWLEDGE_TEST_DATABASE_URL"), "set disposable KNOWLEDGE_TEST_DATABASE_URL")
class ConversationStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True)
        apply_migrations(cls.conn.cursor())
        cls.conn.execute("set role knowledge_runtime")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def conversation(self, title="What did Derek Sivers say?"):
        return self.conn.execute(
            "insert into knowledge.conversations (title) values (%s) returning conversation_id", (title,)).fetchone()[0]

    def test_turns_read_back_in_order_and_finish_with_a_receipt(self):
        conversation = self.conversation()
        for position, question in ((1, "First question?"), (2, "And a follow-up?")):
            self.conn.execute("insert into knowledge.turns (conversation_id, position, question) values (%s,%s,%s)",
                              (conversation, position, question))
        rows = self.conn.execute("select position, question, status from knowledge.turns where conversation_id=%s order by position",
                                 (conversation,)).fetchall()
        self.assertEqual(rows, [(1, "First question?", "running"), (2, "And a follow-up?", "running")])
        self.conn.execute("""update knowledge.turns set status='done', receipt=%s, cost_usd=0.012, finished_at=now()
            where conversation_id=%s and position=1""", (Jsonb({"answer": "A cited answer."}), conversation))
        status, receipt = self.conn.execute("select status, receipt from knowledge.turns where conversation_id=%s and position=1",
                                            (conversation,)).fetchone()
        self.assertEqual((status, receipt), ("done", {"answer": "A cited answer."}))

    def test_a_finished_turn_needs_a_finish_time(self):
        conversation = self.conversation()
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.conn.execute("insert into knowledge.turns (conversation_id, position, question, status) values (%s,1,'Q?','done')",
                              (conversation,))

    def test_runtime_can_delete_sessions_and_feedback(self):
        token = hashlib.sha256(b"cookie value").hexdigest()
        self.conn.execute("insert into knowledge.sessions (token_sha256, expires_at) values (%s, now() + interval '30 days')", (token,))
        self.conn.execute("delete from knowledge.sessions where token_sha256=%s", (token,))
        self.assertIsNone(self.conn.execute("select 1 from knowledge.sessions where token_sha256=%s", (token,)).fetchone())
        conversation = self.conversation()
        turn = self.conn.execute("insert into knowledge.turns (conversation_id, position, question) values (%s,1,'Q?') returning turn_id",
                                 (conversation,)).fetchone()[0]
        self.conn.execute("insert into knowledge.feedback (turn_id, verdict, note) values (%s,'down','Missed a source')", (turn,))
        self.conn.execute("delete from knowledge.feedback where turn_id=%s", (turn,))

    def test_spend_rows_record_their_day_and_source(self):
        self.conn.execute("insert into knowledge.spend (source, estimated_usd, job_id) values ('answer', 0.02, 'turn-1')")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.conn.execute("insert into knowledge.spend (source, estimated_usd) values ('unknown', 0.02)")

    def test_retiring_a_document_keeps_its_run_and_chunks(self):
        with tempfile.TemporaryDirectory() as folder:
            run_dir, spec, _ = fixture_run(Path(folder), "# Orchard\n\nThe retired quince is ripe.\n",
                                                   relative_path="Notes/retired.md")
            load_run(self.conn, run_dir)
            profile = "retirement-proof"
            create_profile(self.conn, SearchProfile(profile_id=profile, variant="fixed_raw",
                                                    chunking_version=spec.chunking_version, embedding_spec_id=spec_id(spec)))
            document = publish_run(self.conn, PublicationSelection(run_id=run_dir.name, profile_id=profile))
            count_chunks = "select count(*) from knowledge.chunks where run_id=%s"
            before = self.conn.execute(count_chunks, (run_dir.name,)).fetchone()[0]
            self.assertGreater(before, 0)
            self.conn.execute("delete from knowledge.active_runs where profile_id=%s and document_id=%s", (profile, document))
            self.assertIsNone(self.conn.execute("select 1 from knowledge.active_runs where document_id=%s", (document,)).fetchone())
            self.assertIsNotNone(self.conn.execute("select 1 from knowledge.runs where run_id=%s", (run_dir.name,)).fetchone())
            self.assertEqual(self.conn.execute(count_chunks, (run_dir.name,)).fetchone()[0], before)

    def test_api_roles_cannot_read_the_new_tables(self):
        for role in ("anon", "authenticated"):
            for table in NEW_TABLES:
                with self.subTest(role=role, table=table):
                    self.conn.execute("reset role")
                    self.conn.execute(f"set role {role}")
                    try:
                        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                            self.conn.execute(f"select * from knowledge.{table}")
                    finally:
                        self.conn.execute("reset role")
                        self.conn.execute("set role knowledge_runtime")


if __name__ == "__main__":
    unittest.main()
