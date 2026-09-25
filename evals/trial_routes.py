"""Pinned private routes for the agreed Stage 4 trial."""

from __future__ import annotations

from app.models.openrouter import OpenRouterClient


TAGS = {
    "openai/gpt-4.1-mini": "azure",
    "openai/gpt-5-mini": "azure",
    "openai/gpt-6-luna": "azure",
    "qwen/qwen3.5-9b": "siliconflow/fp8",
    "qwen/qwen3.5-35b-a3b": "deepinfra/fp8",
    "mistralai/mistral-small-3.2-24b-instruct": "deepinfra/fp8",
    "openai/gpt-oss-20b": "akashml/fp4",
    "google/gemini-3.1-flash-lite": "google-vertex/global/flex",
    "deepseek/deepseek-v4.1-flash": "deepinfra/fp8",
    "z-ai/glm-5.3-flash": "deepinfra/fp4",
    "perplexity/pplx-embed-v1-0.6b": "perplexity/int8",
}

PRICE_CEILINGS = {
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "openai/gpt-5-mini": (0.25, 2.00),
    "openai/gpt-6-luna": (0.10, 0.50),
    "qwen/qwen3.5-9b": (0.10, 0.15),
    "qwen/qwen3.5-35b-a3b": (0.14, 1.00),
    "mistralai/mistral-small-3.2-24b-instruct": (0.075, 0.20),
    "openai/gpt-oss-20b": (0.02, 0.10),
    "google/gemini-3.1-flash-lite": (0.125, 0.75),
    "deepseek/deepseek-v4.1-flash": (0.14, 0.42),
    "z-ai/glm-5.3-flash": (0.075, 0.25),
    "perplexity/pplx-embed-v1-0.6b": (0.004, 0.0),
}


def provider_routes(model_ids: list[str]) -> dict[str, dict]:
    return {model_id: {"only": [TAGS[model_id]], "allow_fallbacks": False,
                       "require_parameters": True, "data_collection": "deny", "zdr": True}
            for model_id in model_ids}


def verify_routes(client: OpenRouterClient, model_ids: list[str],
                  *, structured_ids: set[str] | None = None) -> dict[str, dict]:
    """Fail closed if an endpoint changes its privacy, capability, or price."""
    checked = {}
    for model_id in model_ids:
        endpoint = client.require_zdr_route(
            model_id, TAGS[model_id], structured=model_id in (structured_ids or set()),
        )
        pricing = endpoint["pricing"]
        if not isinstance(pricing, dict):
            raise ValueError("pinned endpoint has no pricing")
        actual = (float(pricing["prompt"]) * 1_000_000,
                  float(pricing["completion"]) * 1_000_000)
        ceilings = PRICE_CEILINGS[model_id]
        if any(value > ceiling + 1e-9 for value, ceiling in zip(actual, ceilings)):
            raise ValueError("pinned endpoint price exceeds the approved ceiling")
        checked[model_id] = {"tag": endpoint["tag"],
                             "input_usd_per_million": actual[0],
                             "output_usd_per_million": actual[1]}
    return checked
