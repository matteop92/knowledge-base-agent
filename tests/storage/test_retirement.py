"""Disposable Postgres proof for retirement; set KNOWLEDGE_TEST_DATABASE_URL to run it."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from app.storage.loader import load_run
from app.storage.publication import create_profile, publish_run, retire_document, select_profile
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from app.storage.search import search_text
from tests.storage.database import apply_migrations
from tests.storage.test_stage3_integration import fixture_run


@unittest.skipUnless(os.getenv("KNOWLEDGE_TEST_DATABASE_URL"), "set disposable KNOWLEDGE_TEST_DATABASE_URL")
class RetirementTest(unittest.TestCase):
    def test_retired_document_leaves_search_and_keeps_history(self):
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            apply_migrations(conn.cursor())
            conn.execute("update knowledge.corpus_state set profile_id=null where singleton=true")
            conn.execute("truncate knowledge.documents cascade")
            conn.execute("delete from knowledge.profiles")
            conn.execute("set role knowledge_runtime")
            with tempfile.TemporaryDirectory() as folder:
                run_dir, spec, _ = fixture_run(Path(folder), "# Orchard\n\nThe medlar is ripe.\n")
                load_run(conn, run_dir)
                create_profile(conn, SearchProfile(profile_id="retire", variant="fixed_raw",
                                                   chunking_version=spec.chunking_version,
                                                   embedding_spec_id=spec_id(spec)))
                document = publish_run(conn, PublicationSelection(run_id=run_dir.name, profile_id="retire"))
                select_profile(conn, "retire")
                self.assertEqual(len(search_text(conn, "medlar")), 1)
                self.assertTrue(retire_document(conn, "retire", document))
                self.assertFalse(retire_document(conn, "retire", document))
                self.assertEqual(search_text(conn, "medlar"), [])
                self.assertIsNotNone(conn.execute("select 1 from knowledge.runs where run_id=%s",
                                                  (run_dir.name,)).fetchone())


if __name__ == "__main__":
    unittest.main()
