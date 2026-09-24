"""Direct OpenRouter adapter with an explicit live-call and budget gate."""

from __future__ import annotations

import json
import os
import re
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
    ) -> None:
        self._allow_live = allow_live
        self._request_budget = request_budget
        self._estimated_input_token_budget = estimated_input_token_budget
        self._requests_left = request_budget
        self._input_tokens_left = estimated_input_token_budget
        self._timeout_seconds = timeout_seconds
        self._api_key = os.getenv("OPENROUTER_API_KEY") if api_key is None else api_key
        self._provider = provider

    @property
    def live_run_selection(self) -> tuple[bool, int, int]:
        """Starting selection and budgets to record in the run manifest."""
        return (
            self._allow_live, self._request_budget,
            self._estimated_input_token_budget,
        )

    def _reserve(self, inputs: list[str]) -> None:
        if not self._allow_live:
            raise ModelClientError("live model calls require explicit selection")
        if not self._api_key:
            raise ModelClientError("OPENROUTER_API_KEY is not configured")
        estimate = sum(
            len(re.findall(r"\w+|[^\w\s]", item)) for item in inputs
        )
        if self._requests_left <= 0 or estimate > self._input_tokens_left:
            raise ModelClientError("model request or estimated input budget exceeded")
        self._requests_left -= 1
        self._input_tokens_left -= estimate

    def _post(self, route: str, payload: dict[str, Any], inputs: list[str]) -> dict[str, Any]:
        self._reserve(inputs)
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
        try:
            with request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                data = json.load(response)
        except error.HTTPError as exc:
            raise ModelClientError(f"OpenRouter returned HTTP {exc.code}") from None
        except (error.URLError, TimeoutError, OSError) as exc:
            raise ModelClientError(f"OpenRouter request failed: {type(exc).__name__}") from None
        except (ValueError, TypeError):
            raise ModelClientError("OpenRouter returned invalid JSON") from None
        if not isinstance(data, dict):
            raise ModelClientError("OpenRouter returned an invalid response")
        return data

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
            "temperature": 0,
            "max_tokens": max_output_tokens,
        }
        if self._provider is not None:
            payload["provider"] = self._provider
        response = self._post("/chat/completions", payload, [system_prompt, input_text])
        try:
            content = response["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            choices = response.get("choices")
            finish = choices[0].get("finish_reason") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
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
        route = provider if provider is not None else self._provider
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
