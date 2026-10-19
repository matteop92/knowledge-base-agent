"""Pinned private answer routes, their price checks and spend measurement.

The command-line tool and the web server both answer through these, so a route,
price or accounting rule changes in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.client import ModelClientError
from app.storage.retrieval import price_ok


def check_generation_price(pricing: object, ceiling: Decimal) -> tuple[Decimal, Decimal]:
    price_ok(pricing, ceiling)
    try:
        output = Decimal(str(pricing["completion"]))
        prompt = Decimal(str(pricing["prompt"]))
    except (KeyError, TypeError, InvalidOperation):
        raise ModelClientError("generation endpoint has no valid output price") from None
    if not output.is_finite() or output < 0 or output * 1_000_000 > ceiling:
        raise ModelClientError("generation endpoint exceeds price ceiling")
    return prompt, output


def private_routes(model: str, embedding_tag: str, generation_tag: str) -> tuple[dict, dict]:
    """The default embedding route and the per-model generation route, both zero-retention."""
    private_route = {"only": [embedding_tag], "allow_fallbacks": False, "data_collection": "deny", "zdr": True}
    return private_route, {model: {**private_route, "only": [generation_tag], "require_parameters": True}}


@dataclass(frozen=True)
class AnswerRoutes:
    embedding_endpoint: dict[str, Any]
    generation_endpoint: dict[str, Any]
    prompt_price: Decimal
    completion_price: Decimal

    @property
    def embedding_price(self) -> Decimal:
        return Decimal(str(self.embedding_endpoint["pricing"]["prompt"]))


def check_answer_routes(client, *, embedding_model: str, model: str, embedding_tag: str, generation_tag: str,
                        max_embedding_usd_per_million: Decimal, max_generation_usd_per_million: Decimal,
                        reasoning_effort: str | None) -> AnswerRoutes:
    """Fail closed unless both pinned endpoints are private, priced under their ceilings and capable."""
    embedding_endpoint = client.require_zdr_route(embedding_model, embedding_tag)
    price_ok(embedding_endpoint["pricing"], max_embedding_usd_per_million)
    generation_endpoint = client.require_zdr_route(model, generation_tag, structured=True)
    prompt_price, completion_price = check_generation_price(generation_endpoint["pricing"],
                                                            max_generation_usd_per_million)
    if reasoning_effort and "reasoning" not in generation_endpoint["supported_parameters"]:
        raise ModelClientError("generation endpoint lacks reasoning support")
    return AnswerRoutes(embedding_endpoint, generation_endpoint, prompt_price, completion_price)


def worst_case_usd(*, input_byte_budget: int, request_budget: int, max_output_tokens: int,
                   input_usd_per_token: Decimal, completion_usd_per_token: Decimal) -> Decimal:
    """The whole input allowance at the dearer input price plus every request at the completion bound."""
    return (Decimal(input_byte_budget) * input_usd_per_token
            + Decimal(request_budget * max_output_tokens * 4) * completion_usd_per_token)


def measured_usd(client, routes: AnswerRoutes, *, model: str, embedding_model: str,
                 extra_prices: dict[str, tuple[Decimal, Decimal]] | None = None) -> tuple[Decimal, bool]:
    """Measured spend and whether every request reported its usage.

    `extra_prices` adds per-token (prompt, completion) prices for other generation models,
    such as the follow-up rewrite.

    A request that timed out or failed still has a timing but no billable record, so the
    counts must match; generation requests must also report output tokens.
    """
    prices = {model: (routes.prompt_price, routes.completion_price),
              embedding_model: (routes.embedding_price, Decimal(0)), **(extra_prices or {})}
    known = len(client.request_timings) == len(client.billable_requests) and all(
        usage.input_tokens is not None and (model_id == embedding_model or usage.output_tokens is not None)
        for model_id, usage in client.billable_requests)
    measured = sum((Decimal(usage.input_tokens or 0) * prices[model_id][0]
                    + Decimal(usage.output_tokens or 0) * prices[model_id][1]
                    for model_id, usage in client.billable_requests), Decimal(0))
    return measured, known
