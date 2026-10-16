"""Server configuration from environment variables; secrets never live in the repo."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


@dataclass(frozen=True)
class AnswerSettings:
    """Mirrors the accepted CLI defaults: GPT-5 mini at medium reasoning on pinned private routes."""

    model: str = "openai/gpt-5-mini"
    reasoning_effort: str | None = "medium"
    generation_provider_tag: str = "azure"
    embedding_provider_tag: str = "perplexity/int8"
    max_generation_usd_per_million: Decimal = Decimal("2")
    max_embedding_usd_per_million: Decimal = Decimal("0.004")
    max_usd_per_answer: Decimal = Decimal("0.40")
    request_budget: int = 18
    input_byte_budget: int = 800_000


@dataclass(frozen=True)
class Settings:
    database_url: str
    supabase_url: str
    supabase_publishable_key: str
    allowed_user_id: str
    allowed_email: str
    supabase_jwt_secret: str | None = None
    vault_name: str = "New Vault"
    cookie_secure: bool = True
    session_days: int = 30
    daily_cap_usd: Decimal = Decimal("2")
    answer: AnswerSettings = field(default_factory=AnswerSettings)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=_required("KNOWLEDGE_DATABASE_URL"),
            supabase_url=_required("SUPABASE_URL").rstrip("/"),
            supabase_publishable_key=_required("SUPABASE_PUBLISHABLE_KEY"),
            allowed_user_id=_required("KNOWLEDGE_ALLOWED_USER_ID"),
            allowed_email=_required("KNOWLEDGE_ALLOWED_EMAIL").strip().lower(),
            supabase_jwt_secret=os.getenv("SUPABASE_JWT_SECRET") or None,
            vault_name=os.getenv("KNOWLEDGE_VAULT_NAME", "New Vault"),
            cookie_secure=os.getenv("KNOWLEDGE_COOKIE_SECURE", "true").lower() != "false",
            daily_cap_usd=Decimal(os.getenv("KNOWLEDGE_DAILY_CAP_USD", "2")),
            answer=AnswerSettings(max_usd_per_answer=Decimal(os.getenv("KNOWLEDGE_MAX_USD_PER_ANSWER", "0.40"))),
        )
