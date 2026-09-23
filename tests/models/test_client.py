import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app.models.client import ModelClientError, ModelSpec
from app.models.openrouter import OpenRouterClient


class ModelClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = ModelSpec(
            provider="openrouter", model_id="example/open-model", model_version="2026-09"
        )

    def test_live_calls_require_explicit_selection_and_budget(self) -> None:
        kwargs = dict(
            model=self.model, system_prompt="Return JSON", input_text="Private source",
            schema_name="example", json_schema={"type": "object"},
            max_output_tokens=32,
        )
        with self.assertRaisesRegex(ModelClientError, "explicit selection"):
            OpenRouterClient(api_key="test-key").generate_json(**kwargs)
        with self.assertRaisesRegex(ModelClientError, "budget exceeded"):
            OpenRouterClient(
                allow_live=True, api_key="test-key", request_budget=0,
                estimated_input_token_budget=100,
            ).generate_json(**kwargs)
        with self.assertRaisesRegex(ModelClientError, "not configured"):
            OpenRouterClient(
                allow_live=True, api_key="", request_budget=1,
                estimated_input_token_budget=100,
            ).generate_json(**kwargs)

    def test_structured_response_and_embedding_shape(self) -> None:
        client = OpenRouterClient(
            allow_live=True, api_key="test-key", request_budget=2,
            estimated_input_token_budget=100,
        )
        with patch.object(client, "_post", return_value={
            "choices": [{"message": {"content": '{"value": 3}'}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }) as post:
            result = client.generate_json(
                model=self.model, system_prompt="Return JSON", input_text="A source",
                schema_name="example", json_schema={"type": "object"},
                max_output_tokens=32,
            )
            self.assertEqual(result.data, {"value": 3})
            self.assertEqual(result.usage.total_tokens, 10)
            self.assertEqual(post.call_args.args[0], "/chat/completions")
        with patch.object(client, "_post", return_value={
            "data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ]
        }):
            embedded = client.embed(model=self.model, texts=["first", "second"], dimensions=2)
            self.assertEqual(embedded.vectors, [[0.1, 0.2], [0.3, 0.4]])

    def test_malformed_response_and_http_error_do_not_leak_source(self) -> None:
        client = OpenRouterClient(
            allow_live=True, api_key="test-key", request_budget=2,
            estimated_input_token_budget=100,
        )
        with patch.object(client, "_post", return_value={"choices": []}):
            with self.assertRaisesRegex(ModelClientError, "malformed structured output"):
                client.generate_json(
                    model=self.model, system_prompt="JSON", input_text="secret source",
                    schema_name="example", json_schema={"type": "object"},
                    max_output_tokens=32,
                )
        with patch("app.models.openrouter.request.urlopen", side_effect=HTTPError(
            "https://openrouter.ai", 429, "Too Many Requests", {}, None,
        )):
            with self.assertRaises(ModelClientError) as caught:
                client.embed(model=self.model, texts=["secret source"])
            self.assertIn("HTTP 429", str(caught.exception))
            self.assertNotIn("secret source", str(caught.exception))

    def test_timeout_reports_failure_without_source_text(self) -> None:
        client = OpenRouterClient(
            allow_live=True, api_key="test-key", request_budget=1,
            estimated_input_token_budget=100,
        )
        with patch("app.models.openrouter.request.urlopen", side_effect=TimeoutError):
            with self.assertRaises(ModelClientError) as caught:
                client.embed(model=self.model, texts=["secret source"])
        self.assertIn("TimeoutError", str(caught.exception))
        self.assertNotIn("secret source", str(caught.exception))

    def test_embedding_budget_counts_each_input(self) -> None:
        client = OpenRouterClient(
            allow_live=True, api_key="test-key", request_budget=1,
            estimated_input_token_budget=1,
        )
        with patch("app.models.openrouter.request.urlopen") as send:
            with self.assertRaisesRegex(ModelClientError, "budget exceeded"):
                client.embed(model=self.model, texts=["one", "two"], dimensions=1)
            send.assert_not_called()

    def test_embedding_response_requires_each_index_once(self) -> None:
        client = OpenRouterClient(
            allow_live=True, api_key="test-key", request_budget=2,
            estimated_input_token_budget=100,
        )
        for indices in ([0, 0], [0, 2]):
            with self.subTest(indices=indices), patch.object(client, "_post", return_value={
                "data": [
                    {"index": indices[0], "embedding": [0.1]},
                    {"index": indices[1], "embedding": [0.2]},
                ]
            }):
                with self.assertRaisesRegex(ModelClientError, "malformed embeddings"):
                    client.embed(model=self.model, texts=["one", "two"], dimensions=1)


if __name__ == "__main__":
    unittest.main()
