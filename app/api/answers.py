"""Answers run as background jobs: a POST stores a running turn and the client polls it."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Protocol
from urllib.parse import quote

from app.agent.endpoints import check_answer_routes, measured_usd, private_routes, worst_case_usd
from app.agent.records import AnswerLimits, AnswerResult
from app.api.conversations import ConversationStore
from app.api.settings import Settings
from app.storage.search import LABEL_METADATA, source_label
from app.models.routes import PRICE_CEILINGS
from app.storage.spend import SpendCapError, check_daily_cap, record_spend

log = logging.getLogger("knowledge.api.answers")


@dataclass
class AnswerOutcome:
    result: AnswerResult
    cost_usd: float


class AnswerFailed(Exception):
    """An answer failed after it may already have been billed; carries what it cost."""

    def __init__(self, cost_usd: float) -> None:
        super().__init__("answer failed")
        self.cost_usd = cost_usd


class AnswerEngine(Protocol):
    limits: AnswerLimits

    def worst_case_usd(self) -> Decimal: ...

    def answer(self, question: str) -> AnswerOutcome: ...


class OpenRouterAnswerEngine:
    """The accepted CLI configuration, run with a fresh client and read-only transaction per answer."""

    def __init__(self, settings: Settings, connect: Callable[[], Any]) -> None:
        self.settings = settings.answer
        self.connect = connect
        self.limits = AnswerLimits()

    def worst_case_usd(self) -> Decimal:
        # The CLI bound, priced at the approved ceilings because no endpoint has been checked yet.
        answer = self.settings
        input_ceiling = Decimal(str(PRICE_CEILINGS[answer.model][0])) / Decimal(1_000_000)
        return worst_case_usd(
            input_byte_budget=answer.input_byte_budget, request_budget=answer.request_budget,
            max_output_tokens=self.limits.max_output_tokens,
            input_usd_per_token=max(input_ceiling, answer.max_embedding_usd_per_million / Decimal(1_000_000)),
            completion_usd_per_token=answer.max_generation_usd_per_million / Decimal(1_000_000))

    def answer(self, question: str) -> AnswerOutcome:
        from app.agent.harness import answer_question
        from app.models.client import ModelSpec
        from app.models.openrouter import OpenRouterClient
        from app.storage.retrieval import DatabaseRetriever, selected_embedding_spec

        answer = self.settings
        private_route, model_providers = private_routes(answer.model, answer.embedding_provider_tag,
                                                        answer.generation_provider_tag)
        client = OpenRouterClient(allow_live=True, request_budget=answer.request_budget,
                                  estimated_input_token_budget=answer.input_byte_budget,
                                  conservative_input_budget=True, provider=private_route,
                                  model_providers=model_providers)
        routes, embedding_model = None, None
        try:
            # The deadline covers the endpoint checks too, so a slow preflight cannot outlive the turn.
            client.set_deadline(time.monotonic() + self.limits.max_seconds)
            with self.connect() as conn, conn.transaction():
                conn.execute("set transaction isolation level repeatable read read only")
                _, spec = selected_embedding_spec(conn)
                embedding_model = spec.model
                routes = check_answer_routes(
                    client, embedding_model=embedding_model, model=answer.model,
                    embedding_tag=answer.embedding_provider_tag, generation_tag=answer.generation_provider_tag,
                    max_embedding_usd_per_million=answer.max_embedding_usd_per_million,
                    max_generation_usd_per_million=answer.max_generation_usd_per_million,
                    reasoning_effort=answer.reasoning_effort)
                retriever = DatabaseRetriever(conn, client, provider_tag=answer.embedding_provider_tag,
                                              max_embedding_usd_per_million=answer.max_embedding_usd_per_million)
                result = answer_question(question, retriever=retriever, client=client,
                                         model=ModelSpec(provider="openrouter", model_id=answer.model,
                                                         model_version="current"),
                                         limits=self.limits, reasoning_effort=answer.reasoning_effort)
        except Exception as exc:
            raise AnswerFailed(self._cost(client, routes, answer.model, embedding_model)) from exc
        return AnswerOutcome(result, self._cost(client, routes, answer.model, embedding_model))

    def _cost(self, client, routes, model: str, embedding_model: str | None = None) -> float:
        """Measured cost, or the worst case when any request's usage is unknown."""
        if not client.request_timings:
            return 0.0
        if routes is None or embedding_model is None:
            return float(self.worst_case_usd())
        measured, known = measured_usd(client, routes, model=model, embedding_model=embedding_model)
        return float(measured if known else self.worst_case_usd())


def obsidian_link(vault_name: str, relative_path: str) -> str:
    return f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(relative_path, safe='')}"


def build_receipt(result: AnswerResult, labels: dict[str, str], vault_name: str) -> dict[str, Any]:
    def passage(item) -> dict[str, Any]:
        return {"document_id": item.document_id, "title": labels.get(item.document_id) or item.relative_path,
                "relative_path": item.relative_path, "obsidian_url": obsidian_link(vault_name, item.relative_path),
                "start_char": item.start_char, "end_char": item.end_char, "text": item.text}

    return {"answer": result.answer, "completeness": result.completeness, "gaps": result.gaps,
            "citations": {ref: passage(item) for ref, item in result.citations.items()},
            "inspected": {ref: passage(item) for ref, item in result.inspected.items()},
            "termination": result.termination, "finalization_reason": result.finalization_reason,
            "needs": result.needs, "model_calls": result.model_calls,
            "elapsed_seconds": result.elapsed_seconds}


def turn_status(result: AnswerResult) -> tuple[str, str | None]:
    if result.termination in ("model_failed", "tool_failed"):
        return "failed", "the answer could not be completed; try again"
    if result.termination == "exhausted" or result.completeness != "complete" or result.gaps:
        return "done_with_gaps", None
    return "done", None


class AnswerJobs:
    """Starts answers in worker threads and records their outcome and spend."""

    def __init__(self, store: ConversationStore, engine: AnswerEngine, connect: Callable[[], Any],
                 settings: Settings) -> None:
        self.store, self.engine, self.connect, self.settings = store, engine, connect, settings
        self._threads: list[threading.Thread] = []
        self._start_lock = threading.Lock()

    def check_cap(self) -> None:
        # Answers still running have recorded nothing yet, so count each at its worst case.
        reserved = self.engine.worst_case_usd() * (self.store.running_count() + 1)
        with self.connect() as conn:
            check_daily_cap(conn, reserved, self.settings.daily_cap_usd)

    def expire_stale(self) -> int:
        """Fail turns past the time limit; their answer may have been billed, so charge the worst case."""
        expired = self.store.interrupt_running()
        for turn_id in expired:
            self._record_spend(turn_id, float(self.engine.worst_case_usd()))
        return len(expired)

    def start(self, conversation_id: str | None, question: str) -> tuple[str, str]:
        with self._start_lock:
            self.check_cap()
            conversation_id, turn_id = self.store.start_turn(conversation_id, question)
        thread = threading.Thread(target=self._run, args=(turn_id, question), daemon=True, name=f"answer-{turn_id}")
        self._threads = [item for item in self._threads if item.is_alive()] + [thread]
        thread.start()
        return conversation_id, turn_id

    def wait(self, timeout: float = 10) -> None:
        """Let running answers finish: used at shutdown and by tests."""
        deadline = time.monotonic() + timeout
        for thread in list(self._threads):
            thread.join(max(0, deadline - time.monotonic()))

    def _labels(self, result: AnswerResult) -> dict[str, str]:
        documents = {item.document_id: item.relative_path
                     for item in [*result.citations.values(), *result.inspected.values()]}
        if not documents:
            return {}
        with self.connect() as conn:
            rows = conn.execute(f"""select r.document_id, d.relative_path, {LABEL_METADATA}
                from knowledge.active_runs a join knowledge.runs r on r.run_id=a.run_id
                join knowledge.documents d on d.document_id=r.document_id
                join knowledge.corpus_state s on s.profile_id=a.profile_id
                where r.document_id = any(%s)""", (list(documents),)).fetchall()
        return {row[0]: source_label(row[2], row[1])[0] for row in rows}

    def _record_spend(self, turn_id: str, cost: float) -> None:
        if not cost:
            return
        try:
            with self.connect() as conn:
                record_spend(conn, "answer", cost, turn_id)
        except Exception as exc:  # noqa: BLE001 - never leave the turn running because the ledger failed
            log.error("spend not recorded turn=%s cost_usd=%.6f error=%s", turn_id, cost, type(exc).__name__)

    def _run(self, turn_id: str, question: str) -> None:
        started = time.monotonic()
        cost = 0.0
        status, receipt, error = "failed", None, "the answer could not be completed; try again"
        try:
            outcome = self.engine.answer(question)
            cost = outcome.cost_usd
            status, error = turn_status(outcome.result)
            receipt = build_receipt(outcome.result, self._labels(outcome.result), self.settings.vault_name)
        except SpendCapError:
            error = "the daily spending cap was reached"
        except AnswerFailed as exc:
            cost = exc.cost_usd
            log.warning("answer failed turn=%s error=%s", turn_id, type(exc.__cause__).__name__)
        except Exception as exc:  # noqa: BLE001 - the turn must always leave the running state
            log.warning("answer failed turn=%s error=%s", turn_id, type(exc).__name__)
        finally:
            # Spend goes in the ledger before the turn stops counting as running for the cap check.
            self._record_spend(turn_id, cost)
            self.store.finish_turn(turn_id, status=status, receipt=receipt, cost_usd=cost, error=error)
            # Ids, timing and cost only; never question, answer or excerpt text.
            log.info("answer finished turn=%s seconds=%.1f cost_usd=%.6f", turn_id, time.monotonic() - started, cost)
