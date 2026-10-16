"""Direct OpenRouter adapter with an explicit live-call and budget gate."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib import error, request

from app.models.client import (
    EmbeddingResult,
    GenerationResult,
    ModelClientError,
    ModelSpec,
    ModelUsage,
)


class OpenRouterClient:
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self, *, allow_live: bool = False, request_budget: int = 0,
        estimated_input_token_budget: int = 0, timeout_seconds: float = 60,
        api_key: str | None = None, provider: dict[str, Any] | None = None,
        model_providers: dict[str, dict[str, Any]] | None = None,
        conservative_input_budget: bool = False,
        capture_failed_outputs: bool = False,
    ) -> None:
        self._allow_live = allow_live
        self._request_budget = request_budget
        self._estimated_input_token_budget = estimated_input_token_budget
        self._requests_left = request_budget
        self._input_tokens_left = estimated_input_token_budget
        self._timeout_seconds = timeout_seconds
        self._api_key = os.getenv("OPENROUTER_API_KEY") if api_key is None else api_key
        self._provider = provider
        self._model_providers = model_providers or {}
        self._conservative_input_budget = conservative_input_budget
        self._capture_failed_outputs = capture_failed_outputs
        self.failed_outputs: list[dict[str, Any]] = []
        self._deadline: float | None = None
        self.billable_usages: list[ModelUsage] = []
        self.billable_requests: list[tuple[str, ModelUsage]] = []
        self.request_timings: list[tuple[str, float]] = []
        self.rate_limit_retries = 0

    @property
    def remaining_input_bytes(self) -> int | None:
        return self._input_tokens_left if self._conservative_input_budget else None

    @property
    def remaining_requests(self) -> int:
        return self._requests_left

    @property
    def live_run_selection(self) -> tuple[bool, int, int]:
        """Starting selection and budgets to record in the run manifest."""
        return (
            self._allow_live, self._request_budget,
            self._estimated_input_token_budget,
        )

    def set_deadline(self, monotonic_deadline: float) -> None:
        """Bound all HTTP calls for this answer, including endpoint preflight."""
        self._deadline = monotonic_deadline

    def _timeout(self) -> float:
        if self._deadline is None:
            return self._timeout_seconds
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ModelClientError("answer time budget exceeded")
        return min(self._timeout_seconds, remaining)

    def _reserve(self, inputs: list[str]) -> None:
        if not self._allow_live:
            raise ModelClientError("live model calls require explicit selection")
        if not self._api_key:
            raise ModelClientError("OPENROUTER_API_KEY is not configured")
        estimate = (sum(len(item.encode("utf-8")) for item in inputs) + 512
                    if self._conservative_input_budget else sum(
                        len(re.findall(r"\w+|[^\w\s]", item)) for item in inputs))
        if self._requests_left <= 0 or estimate > self._input_tokens_left:
            raise ModelClientError("model request or estimated input budget exceeded")
        self._requests_left -= 1
        self._input_tokens_left -= estimate

    def eligible_zdr_routes(self, model_ids: list[str]) -> dict[str, list[str]]:
        """Check current ZDR endpoints before sending private source text."""
        if not self._api_key:
            raise ModelClientError("OPENROUTER_API_KEY is not configured")
        if self._provider != {"data_collection": "deny", "zdr": True}:
            raise ModelClientError("private route must deny collection and require ZDR")
        http_request = request.Request(
            self.BASE_URL + "/endpoints/zdr",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        try:
            with request.urlopen(http_request, timeout=self._timeout()) as response:
                data = json.load(response)
        except error.HTTPError as exc:
            raise ModelClientError(f"OpenRouter ZDR route check returned HTTP {exc.code}") from None
        except (error.URLError, TimeoutError, OSError) as exc:
            raise ModelClientError(f"OpenRouter ZDR route check failed: {type(exc).__name__}") from None
        except (ValueError, TypeError):
            raise ModelClientError("OpenRouter ZDR route check returned invalid JSON") from None
        endpoints = data.get("data") if isinstance(data, dict) else None
        if not isinstance(endpoints, list):
            raise ModelClientError("OpenRouter ZDR route check returned invalid endpoints")
        routes = {model_id: [] for model_id in model_ids}
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            model_id = endpoint.get("model_id")
            name = endpoint.get("tag") or endpoint.get("provider_name")
            if model_id in routes and isinstance(name, str):
                routes[model_id].append(name)
        if any(not names for names in routes.values()):
            raise ModelClientError("one or more models have no current ZDR endpoint")
        return routes

    def require_zdr_route(self, model_id: str, tag: str, *, structured: bool = False) -> dict[str, Any]:
        """Return current metadata only for an explicitly pinned private endpoint."""
        if self._provider is None or self._provider.get("zdr") is not True or self._provider.get("data_collection") != "deny":
            raise ModelClientError("private route must deny collection and require ZDR")
        if not self._api_key:
            raise ModelClientError("OPENROUTER_API_KEY is not configured")
        http_request = request.Request(
            self.BASE_URL + "/endpoints/zdr",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        try:
            with request.urlopen(http_request, timeout=self._timeout()) as response:
                data = json.load(response)
        except (error.HTTPError, error.URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
            raise ModelClientError(f"OpenRouter ZDR route check failed: {type(exc).__name__}") from None
        endpoints = data.get("data") if isinstance(data, dict) else None
        if not isinstance(endpoints, list):
            raise ModelClientError("OpenRouter ZDR route check returned invalid endpoints")
        matching = [endpoint for endpoint in endpoints if isinstance(endpoint, dict)
                    and endpoint.get("model_id") == model_id and endpoint.get("tag") == tag]
        if len(matching) != 1:
            raise ModelClientError("pinned model endpoint is not currently ZDR eligible")
        endpoint = matching[0]
        supported = endpoint.get("supported_parameters") or []
        if structured and not {"response_format", "structured_outputs"}.issubset(set(supported)):
            raise ModelClientError("pinned model endpoint lacks structured output support")
        return {"model_id": model_id, "tag": tag, "pricing": endpoint.get("pricing"),
                "supported_parameters": supported}

    def _post(self, route: str, payload: dict[str, Any], inputs: list[str]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            self.BASE_URL + route,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        for attempt in range(3):
            self._reserve([body.decode("utf-8")] if self._conservative_input_budget else inputs)
            started = time.perf_counter()
            try:
                with request.urlopen(http_request, timeout=self._timeout()) as response:
                    data = json.load(response)
            except error.HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    self.rate_limit_retries += 1
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise ModelClientError(f"OpenRouter returned HTTP {exc.code}") from None
            except (error.URLError, TimeoutError, OSError) as exc:
                raise ModelClientError(f"OpenRouter request failed: {type(exc).__name__}") from None
            except (ValueError, TypeError):
                raise ModelClientError("OpenRouter returned invalid JSON") from None
            finally:
                self.request_timings.append((str(payload.get("model", "")),
                                             time.perf_counter() - started))
            if not isinstance(data, dict):
                raise ModelClientError("OpenRouter returned an invalid response")
            api_error = data.get("error")
            if isinstance(api_error, dict):
                code = api_error.get("code")
                if code == 429 and attempt < 2:
                    self.rate_limit_retries += 1
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise ModelClientError(f"OpenRouter returned API error {code}")
            usage = self._usage(data)
            self.billable_usages.append(usage)
            self.billable_requests.append((str(payload.get("model", "")), usage))
            return data
        raise ModelClientError("OpenRouter rate limit retries exhausted")

    @staticmethod
    def _usage(data: dict[str, Any]) -> ModelUsage:
        usage = data.get("usage") or {}
        return ModelUsage(
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    def generate_json(
        self, *, model: ModelSpec, system_prompt: str, input_text: str,
        schema_name: str, json_schema: dict[str, Any], max_output_tokens: int,
        reasoning_effort: str | None = None,
    ) -> GenerationResult:
        if model.provider != "openrouter":
            raise ModelClientError("OpenRouter adapter requires an openrouter model")
        payload = {
            "model": model.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": input_text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
            },
        }
        # Reasoning and constrained JSON consume completion tokens before the
        # visible object appears. The caller's bound is for visible content.
        completion_budget = max_output_tokens * 4
        if reasoning_effort is not None and reasoning_effort not in (
            "minimal", "low", "medium", "high"
        ):
            raise ModelClientError("unsupported reasoning effort")
        if model.model_id in ("openai/gpt-5-mini", "openai/gpt-6-luna") or reasoning_effort:
            # OpenRouter's unified reasoning setting; callers check that the pinned route
            # lists "reasoning" before asking for an effort on a non-OpenAI model.
            limit = "max_completion_tokens" if model.model_id.startswith("openai/") else "max_tokens"
            payload[limit] = completion_budget
            payload["reasoning"] = {"effort": reasoning_effort or "minimal"}
        else:
            payload["temperature"] = 0
            # DeepSeek's longer document summaries need more room to finish
            # constrained JSON even with reasoning disabled.
            deepseek = model.model_id == "deepseek/deepseek-v4.1-flash"
            multiplier = 2
            if deepseek:
                multiplier = 8 if schema_name == "summary" else 4
            payload["max_tokens"] = max_output_tokens * multiplier
            if model.model_id.startswith("qwen/qwen3.5-") or deepseek:
                payload["reasoning"] = {"enabled": False}
        route = self._model_providers.get(model.model_id, self._provider)
        if route is not None:
            payload["provider"] = route
        response = self._post("/chat/completions", payload, [system_prompt, input_text])
        choices = response.get("choices")
        finish = choices[0].get("finish_reason") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        if finish not in (None, "stop"):
            if self._capture_failed_outputs:
                message = choices[0].get("message")
                self.failed_outputs.append({"model": model.model_id, "finish_reason": finish,
                                            "message": {key: message.get(key) for key in ("role", "content", "refusal")} if isinstance(message, dict) else None})
            raise ModelClientError(f"OpenRouter structured output was incomplete (finish={finish})")
        try:
            content = response["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            if self._capture_failed_outputs:
                message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
                self.failed_outputs.append({"model": model.model_id, "finish_reason": finish,
                                            "message": {key: message.get(key) for key in ("role", "content", "refusal")} if isinstance(message, dict) else None})
            raise ModelClientError(f"OpenRouter returned malformed structured output (finish={finish})") from None
        return GenerationResult(data=parsed, model=model, usage=self._usage(response))

    def embed(
        self, *, model: ModelSpec, texts: list[str], dimensions: int | None = None,
        input_type: str | None = None, provider: dict[str, Any] | None = None,
    ) -> EmbeddingResult:
        if model.provider != "openrouter":
            raise ModelClientError("OpenRouter adapter requires an openrouter model")
        if not texts or any(not text.strip() for text in texts):
            raise ModelClientError("embedding input must be nonempty")
        payload: dict[str, Any] = {"model": model.model_id, "input": texts}
        if dimensions is not None:
            payload["dimensions"] = dimensions
        if input_type is not None:
            if input_type not in ("search_document", "search_query"):
                raise ModelClientError("unsupported embedding input_type")
            payload["input_type"] = input_type
        route = provider if provider is not None else self._model_providers.get(model.model_id, self._provider)
        if route is not None:
            payload["provider"] = route
        response = self._post("/embeddings", payload, texts)
        try:
            items = sorted(response["data"], key=lambda item: item["index"])
            if (
                len(items) != len(texts)
                or any(type(item["index"]) is not int for item in items)
                or [item["index"] for item in items] != list(range(len(texts)))
            ):
                raise ValueError
            vectors = [item["embedding"] for item in items]
        except (KeyError, TypeError, ValueError):
            raise ModelClientError("OpenRouter returned malformed embeddings") from None
        return EmbeddingResult(vectors=vectors, model=model, usage=self._usage(response))
