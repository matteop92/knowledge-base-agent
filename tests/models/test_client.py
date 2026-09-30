import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app.models.client import ModelClientError, ModelSpec
from app.models.openrouter import OpenRouterClient


class ModelClientTests(unittest.TestCase):
    def test_malformed_generation_is_retained_only_with_explicit_capture(self):
        import json
        response = {'choices':[{'finish_reason':'stop','message':{'content':'private malformed JSON'}}],
                    'usage':{'prompt_tokens':4,'completion_tokens':3}}
        for capture in (False, True):
            client = OpenRouterClient(allow_live=True, api_key='synthetic', request_budget=1,
                                      estimated_input_token_budget=100, capture_failed_outputs=capture)
            with patch('app.models.openrouter.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
                with self.assertRaises(ModelClientError):
                    client.generate_json(model=self.model, system_prompt='Return JSON', input_text='source',
                        schema_name='test', json_schema={'type':'object'}, max_output_tokens=32)
            self.assertEqual(bool(client.failed_outputs), capture)
            if capture:
                self.assertEqual(client.failed_outputs[0]['message']['content'], 'private malformed JSON')

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
            OpenRouterClient(allow_live=True, api_key="", request_budget=1,
                             estimated_input_token_budget=100).generate_json(**kwargs)

    def test_answer_byte_budget_accounts_for_request_body_before_egress(self):
        client = OpenRouterClient(allow_live=True, api_key="test-key", request_budget=1,
                                  estimated_input_token_budget=600, conservative_input_budget=True)
        with patch("app.models.openrouter.request.urlopen") as send:
            with self.assertRaisesRegex(ModelClientError, "budget exceeded"):
                client.embed(model=self.model, texts=["word" * 200])
            send.assert_not_called()

    def test_expired_answer_deadline_prevents_another_http_request(self):
        client = OpenRouterClient(allow_live=True, api_key="test-key", request_budget=1,
                                  estimated_input_token_budget=100)
        client.set_deadline(0)
        with patch("app.models.openrouter.request.urlopen") as send, self.assertRaises(ModelClientError):
            client.embed(model=self.model, texts=["source"])
        send.assert_not_called()

    def test_reasoning_override_and_truncation_do_not_change_defaults(self):
        client = OpenRouterClient()
        model = ModelSpec(provider="openrouter", model_id="openai/gpt-5-mini", model_version="1")
        response = {"choices": [{"finish_reason": "stop", "message": {"content": '{"value":1}'}}]}
        kwargs = dict(model=model, system_prompt="JSON", input_text="Source", schema_name="example",
                      json_schema={"type": "object"}, max_output_tokens=20)
        with patch.object(client, "_post", return_value=response) as post:
            client.generate_json(**kwargs)
            self.assertEqual(post.call_args.args[1]["reasoning"], {"effort": "minimal"})
            client.generate_json(**kwargs, reasoning_effort="medium")
            self.assertEqual(post.call_args.args[1]["reasoning"], {"effort": "medium"})
            self.assertEqual(post.call_args.args[1]["max_completion_tokens"], 80)
        response["choices"][0]["finish_reason"] = "length"
        with patch.object(client, "_post", return_value=response), self.assertRaises(ModelClientError):
            client.generate_json(**kwargs)

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

    def test_privacy_route_applies_to_generation_and_embeddings(self) -> None:
        privacy = {"data_collection": "deny", "zdr": True}
        client = OpenRouterClient(allow_live=True, api_key="test-key", request_budget=2,
                                  estimated_input_token_budget=100, provider=privacy)
        with patch.object(client, "_post", return_value={
            "choices": [{"message": {"content": '{"context": "brief"}'}}],
        }) as post:
            client.generate_json(model=self.model, system_prompt="JSON", input_text="Source",
                                 schema_name="context", json_schema={"type": "object"},
                                 max_output_tokens=20)
            self.assertEqual(post.call_args.args[1]["provider"], privacy)
        with patch.object(client, "_post", return_value={
            "data": [{"index": 0, "embedding": [0.1, 0.2]}],
        }) as post:
            client.embed(model=self.model, texts=["Source"], dimensions=2)
            self.assertEqual(post.call_args.args[1]["provider"], privacy)

    def test_malformed_response_and_http_error_do_not_leak_source(self) -> None:
        client = OpenRouterClient(
            allow_live=True, api_key="test-key", request_budget=3,
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
        )), patch("app.models.openrouter.time.sleep"):
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

    def test_api_error_in_success_http_response_is_reported_safely(self) -> None:
        client = OpenRouterClient(allow_live=True, api_key="test-key", request_budget=3,
                                  estimated_input_token_budget=100)
        error_payload = b'{"error":{"code":429,"message":"provider rejected request"},"id":"x"}'
        with patch("app.models.openrouter.request.urlopen",
                   side_effect=[io.BytesIO(error_payload) for _ in range(3)]) as send, \
             patch("app.models.openrouter.time.sleep"):
            with self.assertRaisesRegex(ModelClientError, "API error 429") as caught:
                client.embed(model=self.model, texts=["secret source"])
        self.assertEqual(send.call_count, 3)
        self.assertEqual(client.rate_limit_retries, 2)
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

    def test_private_route_check_requires_current_zdr_endpoint(self) -> None:
        client = OpenRouterClient(api_key="test-key", provider={"data_collection": "deny", "zdr": True})
        payload = b'{"data":[{"model_id":"example/open-model","tag":"provider-one"}]}'
        with patch("app.models.openrouter.request.urlopen", return_value=io.BytesIO(payload)) as send:
            self.assertEqual(client.eligible_zdr_routes(["example/open-model"]),
                             {"example/open-model": ["provider-one"]})
            self.assertIn("/endpoints/zdr", send.call_args.args[0].full_url)
        with patch("app.models.openrouter.request.urlopen", return_value=io.BytesIO(payload)):
            with self.assertRaisesRegex(ModelClientError, "no current ZDR endpoint"):
                client.eligible_zdr_routes(["example/absent-model"])

    def test_pinned_route_is_used_for_generation_and_verified_before_use(self) -> None:
        route = {"only": ["provider-one"], "allow_fallbacks": False,
                 "require_parameters": True, "data_collection": "deny", "zdr": True}
        client = OpenRouterClient(allow_live=True, api_key="test-key", request_budget=1,
                                  estimated_input_token_budget=100,
                                  provider={"data_collection": "deny", "zdr": True},
                                  model_providers={self.model.model_id: route})
        payload = b'{"data":[{"model_id":"example/open-model","tag":"provider-one","pricing":{"prompt":"0.0000001","completion":"0.0000002"},"supported_parameters":["response_format","structured_outputs"]}]}'
        with patch("app.models.openrouter.request.urlopen", return_value=io.BytesIO(payload)):
            self.assertEqual(client.require_zdr_route(self.model.model_id, "provider-one",
                                                       structured=True)["tag"], "provider-one")
        with patch.object(client, "_post", return_value={
            "choices": [{"message": {"content": '{"value": 1}'}}],
        }) as post:
            client.generate_json(model=self.model, system_prompt="JSON", input_text="Source",
                                 schema_name="example", json_schema={"type": "object"},
                                 max_output_tokens=20)
            self.assertEqual(post.call_args.args[1]["provider"], route)
            self.assertEqual(post.call_args.args[1]["max_tokens"], 40)

    def test_deepseek_structured_generation_disables_reasoning(self) -> None:
        client = OpenRouterClient(allow_live=True, api_key="test-key", request_budget=1,
                                  estimated_input_token_budget=100)
        model = ModelSpec(provider="openrouter", model_id="deepseek/deepseek-v4.1-flash",
                          model_version="trial")
        with patch.object(client, "_post", return_value={
            "choices": [{"message": {"content": '{"value": 1}'}}],
        }) as post:
            client.generate_json(model=model, system_prompt="JSON", input_text="Source",
                                 schema_name="example", json_schema={"type": "object"},
                                 max_output_tokens=20)
            self.assertEqual(post.call_args.args[1]["reasoning"], {"enabled": False})
            self.assertEqual(post.call_args.args[1]["max_tokens"], 80)
            client.generate_json(model=model, system_prompt="JSON", input_text="Source",
                                 schema_name="summary", json_schema={"type": "object"},
                                 max_output_tokens=20)
            self.assertEqual(post.call_args.args[1]["max_tokens"], 160)


if __name__ == "__main__":
    unittest.main()
