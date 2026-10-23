import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agent.cli import main
from app.models.client import ModelClientError
from tests.storage.test_retrieval import hit


def setUpModule():
    os.makedirs(".local", exist_ok=True)


class CLITest(unittest.TestCase):
    def test_live_receipt_counts_refused_actions(self):
        from app.agent.records import AnswerResult
        from app.ingestion.embeddings import EmbeddingSpec
        from app.storage.records import spec_id
        spec = EmbeddingSpec(provider="openrouter", model="embed-model", model_version="1",
            dimensions=3, distance_metric="cosine", input_recipe_version="raw-v1", chunking_version="v1")
        client = MagicMock()
        client.request_timings = []
        client.billable_requests = []
        client.failed_outputs = []
        client.require_zdr_route.return_value = {"pricing": {"prompt": "0.000000004", "completion": "0.000002"},
            "supported_parameters": ["reasoning", "response_format", "structured_outputs"]}
        result = AnswerResult(answer="No support", completeness="unsupported", citations={},
            gaps=["No inspected source"], model_calls=3, actions=[
                {"action": "search", "rejected": "Repeated query"},
                {"action": "invalid", "reason": "ValueError"}, {"action": "finish"}])
        with tempfile.TemporaryDirectory(dir=".local") as folder, \
             patch("app.agent.cli.connect_runtime") as connect, \
             patch("app.agent.cli.OpenRouterClient", return_value=client), \
             patch("app.agent.cli.selected_embedding_spec", return_value=(spec_id(spec), spec)), \
             patch("app.agent.cli.DatabaseRetriever"), \
             patch("app.agent.cli.answer_question", return_value=result), \
             contextlib.redirect_stdout(io.StringIO()):
            connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            path = Path(folder) / "receipt.json"
            args = [a for a in self.args(path) if a != "--dry-run"] + ["--live"]
            self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(path.read_text())["refused_actions"], 2)

    def args(self, report):
        return ["A question", "--dry-run", "--embedding-provider-tag", "embed",
                "--generation-provider-tag", "generate", "--max-embedding-usd-per-million", "0.004",
                "--max-generation-usd-per-million", "2", "--max-usd", "1", "--report", str(report)]

    def test_failure_report_survives_and_does_not_expose_exception_contents(self):
        with tempfile.TemporaryDirectory(dir=".local") as folder:
            report = Path(folder) / "failure.json"
            with patch("app.agent.cli.connect_runtime", side_effect=ModelClientError("secret credential")), \
                 contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(self.args(report)), 1)
            self.assertTrue(report.exists())
            self.assertNotIn("secret credential", report.read_text())
            self.assertNotIn("secret credential", err.getvalue())

    def test_dry_run_checks_route_and_read_only_transaction_without_answering(self):
        from app.ingestion.embeddings import EmbeddingSpec
        from app.storage.records import spec_id
        spec = EmbeddingSpec(provider="openrouter", model="embed-model", model_version="1",
            dimensions=3, distance_metric="cosine", input_recipe_version="raw-v1", chunking_version="v1")
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [(hit("c", 0).document_id, "a" * 64)]
        client = MagicMock()
        client.require_zdr_route.return_value = {"pricing": {"prompt": "0.000000004", "completion": "0.000002"},
            "supported_parameters": ["reasoning", "response_format", "structured_outputs"]}
        with tempfile.TemporaryDirectory(dir=".local") as folder, \
             patch("app.agent.cli.connect_runtime") as connect, \
             patch("app.agent.cli.OpenRouterClient", return_value=client), \
             patch("app.agent.cli.selected_embedding_spec", return_value=(spec_id(spec), spec)), \
             patch("app.agent.cli.answer_question") as answer, contextlib.redirect_stdout(io.StringIO()):
            connect.return_value.__enter__.return_value = conn
            self.assertEqual(main(self.args(Path(folder) / "dry.json")), 0)
            answer.assert_not_called()
            conn.execute.assert_any_call("set transaction isolation level repeatable read read only")

    def test_broad_profile_records_finite_allowances_and_schema(self):
        from app.ingestion.embeddings import EmbeddingSpec
        from app.storage.records import spec_id
        spec = EmbeddingSpec(provider="openrouter", model="embed-model", model_version="1",
            dimensions=3, distance_metric="cosine", input_recipe_version="raw-v1", chunking_version="v1")
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        client = MagicMock()
        client.require_zdr_route.return_value = {"pricing": {"prompt": "0.000000004", "completion": "0.000002"},
            "supported_parameters": ["reasoning", "response_format", "structured_outputs"]}
        with tempfile.TemporaryDirectory(dir=".local") as folder, \
             patch("app.agent.cli.connect_runtime") as connect, \
             patch("app.agent.cli.OpenRouterClient", return_value=client), \
             patch("app.agent.cli.selected_embedding_spec", return_value=(spec_id(spec), spec)), \
             contextlib.redirect_stdout(io.StringIO()):
            connect.return_value.__enter__.return_value = conn
            path = Path(folder) / "broad.json"
            self.assertEqual(main(self.args(path) + ["--budget-profile", "broad"]), 0)
            config = json.loads(path.read_text())["configuration"]
            self.assertEqual(config["action_schema_version"], 3)
            self.assertEqual(config["limits"]["max_finds"], 4)
            self.assertEqual(config["limits"]["max_reads"], 10)
            self.assertEqual(config["request_budget"], 28)
