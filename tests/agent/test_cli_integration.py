"""Disposable database plus real model adapter with synthetic HTTP responses."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg

from app.agent.cli import main
from app.storage.loader import load_run
from app.storage.publication import create_profile, publish_run, select_profile
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from tests.agent.test_actions import action_v3
from tests.storage.database import apply_migrations
from tests.storage.test_stage3_integration import fixture_run


def setUpModule():
    os.makedirs(".local", exist_ok=True)


@unittest.skipUnless(os.getenv("KNOWLEDGE_TEST_DATABASE_URL"), "set disposable KNOWLEDGE_TEST_DATABASE_URL")
class CLIIntegrationTest(unittest.TestCase):
    def test_cli_reads_published_fixture_without_vault_and_isolates_requests(self):
        dsn = os.environ["KNOWLEDGE_TEST_DATABASE_URL"]
        with psycopg.connect(dsn, autocommit=True) as setup:
            apply_migrations(setup.cursor())
            setup.execute("set role knowledge_runtime")
            with tempfile.TemporaryDirectory() as folder:
                run_dir, spec, _ = fixture_run(Path(folder), "# Orchard\n\nThe pear is ripe.\n")
                # The HTTP adapter requires an OpenRouter model; fixture vectors remain synthetic.
                from tests.storage.test_stage3_integration import add_vectors
                spec = spec.model_copy(update={"provider": "openrouter", "model": "fixture/embed"})
                add_vectors(run_dir, spec)
                load_run(setup, run_dir)
                create_profile(setup, SearchProfile(profile_id="cli-proof", variant="fixed_raw",
                    chunking_version=spec.chunking_version, embedding_spec_id=spec_id(spec)))
                publish_run(setup, PublicationSelection(run_id=run_dir.name, profile_id="cli-proof"))
                select_profile(setup, "cli-proof")
        current = []
        inputs = []
        responses = iter([action_v3("search", query="ripe pear"),
                          action_v3("read", reference_id="E1", span_index=1),
                          action_v3("finish", answer="The pear is ripe. Example: choose it for today's snack [E1.1].",
                                 citation_ids=["E1.1"], completeness="complete", sufficient=True,
                                 need_updates=[{"id": "N1", "description": "Explain the orchard",
                                                "status": "resolved", "evidence_ids": ["E1.1"], "rationale": None}])])
        second = False

        def connect():
            conn = psycopg.connect(dsn, autocommit=True)
            conn.execute("set role knowledge_runtime")
            current[:] = [conn]
            return conn

        def http(req, **kwargs):
            if req.full_url.endswith("/endpoints/zdr"):
                data = {"data": [{"model_id": model, "tag": tag,
                    "pricing": {"prompt": "0.000000004", "completion": "0.000002"},
                    "supported_parameters": ["response_format", "structured_outputs", "reasoning"]}
                    for model, tag in (("fixture/embed", "embed"), ("openai/gpt-5-mini", "generate"))]}
            elif req.full_url.endswith("/embeddings"):
                data = {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}],
                        "usage": {"prompt_tokens": 10, "total_tokens": 10}}
            else:
                payload = json.loads(req.data)
                inputs.append(payload["messages"][1]["content"])
                self.assertEqual(current[0].execute("show transaction_read_only").fetchone()[0], "on")
                with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction), current[0].transaction():
                    current[0].execute("update knowledge.corpus_state set profile_id=profile_id where false")
                proposed = (action_v3("finish", answer="Old evidence [E1.1]", citation_ids=["E1.1"],
                                   completeness="complete", sufficient=True) if second else next(responses))
                data = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(proposed)}}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}
            return io.BytesIO(json.dumps(data).encode())

        with tempfile.TemporaryDirectory(dir=".local") as folder, \
             patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-key"}), \
             patch("app.agent.cli.connect_runtime", side_effect=connect), \
             patch("app.models.openrouter.request.urlopen", side_effect=http), \
             contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop("KNOWLEDGE_SOURCE_ROOT", None)
            base = ["Explain the orchard", "--live", "--embedding-provider-tag", "embed",
                    "--generation-provider-tag", "generate", "--max-embedding-usd-per-million", "0.004",
                    "--max-generation-usd-per-million", "2", "--max-usd", "1"]
            first_path, second_path = Path(folder) / "first.json", Path(folder) / "second.json"
            self.assertEqual(main(base + ["--report", str(first_path)]), 0)
            first = json.loads(first_path.read_text())
            self.assertEqual(first["result"]["citations"]["E1.1"]["text"].strip(), "The pear is ripe.")
            self.assertTrue(first["usage_complete"])
            second = True
            inputs.clear()
            self.assertEqual(main(base + ["--report", str(second_path)]), 1)
            self.assertFalse(json.loads(second_path.read_text())["result"]["inspected"])
            self.assertTrue(all(not json.loads(value)["inspected"] for value in inputs))
