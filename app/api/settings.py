"""Server configuration from environment variables; secrets never live in the repo."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal

from app.models.routes import EMBEDDING_MODEL, PRICE_CEILINGS, TAGS
from app.storage.spend import daily_cap_usd


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


ANSWER_MODEL = "openai/gpt-5-mini"


@dataclass(frozen=True)
class AnswerSettings:
    """The accepted answer configuration: GPT-5 mini at medium reasoning on the pinned private routes.

    Provider tags and price ceilings come from app.models.routes, so a route change reaches the web app too.
    """

    model: str = ANSWER_MODEL
    reasoning_effort: str | None = "medium"
    generation_provider_tag: str = TAGS[ANSWER_MODEL]
    embedding_provider_tag: str = TAGS[EMBEDDING_MODEL]
    max_generation_usd_per_million: Decimal = Decimal(str(PRICE_CEILINGS[ANSWER_MODEL][1]))
    max_embedding_usd_per_million: Decimal = Decimal(str(PRICE_CEILINGS[EMBEDDING_MODEL][0]))
    max_usd_per_answer: Decimal = Decimal("0.60")
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
    # Index the synced vault copy in the background; off until the vault sync has been set up.
    sync_enabled: bool = False
    # Captures write new notes into the vault copy, so they need the sync to be two-way.
    sync_mode: str = "pull-only"
    vault_root: str = "/data/vault"
    capture_folder: str = "Knowledge"
    index_interval_seconds: int = 300
    index_max_usd_per_pass: Decimal = Decimal("0.10")
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
            daily_cap_usd=daily_cap_usd(),
            sync_enabled=os.getenv("KNOWLEDGE_SYNC_ENABLED", "false").lower() == "true",
            sync_mode=os.getenv("KNOWLEDGE_SYNC_MODE", "pull-only"),
            vault_root=os.getenv("KNOWLEDGE_SOURCE_ROOT", "/data/vault"),
            capture_folder=os.getenv("KNOWLEDGE_CAPTURE_FOLDER", "Knowledge"),
            index_interval_seconds=int(os.getenv("KNOWLEDGE_INDEX_INTERVAL_SECONDS", "300")),
            index_max_usd_per_pass=Decimal(os.getenv("KNOWLEDGE_INDEX_MAX_USD_PER_PASS", "0.10")),
            answer=AnswerSettings(max_usd_per_answer=Decimal(os.getenv("KNOWLEDGE_MAX_USD_PER_ANSWER", "0.60"))),
        )
