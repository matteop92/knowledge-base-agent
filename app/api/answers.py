"""Answers run as background jobs: a POST stores a running turn and the client polls it."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Protocol
from urllib.parse import quote

from app.agent.endpoints import check_answer_routes, measured_usd, private_routes, worst_case_usd
from app.agent.memory import (PRIOR_TURNS, REWRITE_INPUT_CHARS, REWRITE_MAX_OUTPUT_TOKENS, REWRITE_MODEL, PriorTurn,
                              prior_turn_from_receipt)
from app.agent.records import AnswerLimits, AnswerResult
from app.api.conversations import ConversationStore
from app.api.settings import Settings
from app.storage.search import LABEL_METADATA, source_author, source_label
from app.models.client import ModelClientError
from app.models.openrouter import completion_limit
from app.models.routes import PRICE_CEILINGS, TAGS, price_usd
from app.storage import StorageError
from app.storage.spend import SpendCapError, check_daily_cap, record_spend

log = logging.getLogger("knowledge.api.answers")
WEB_MAX_OUTPUT_TOKENS = 2500
# At about 110 output tokens a second a full 10,000-token reply takes about 90 s, so one request
# gets 120 s and the whole answer 300 s (the broad CLI profile, with the same limit, has 420 s).
WEB_REQUEST_TIMEOUT_SECONDS = 120


@dataclass
class AnswerOutcome:
    result: AnswerResult
    cost_usd: float
    # Token counts per model request (model, input, output, reasoning, finish reason); no text.
    model_usage: list[dict[str, Any]] = field(default_factory=list)


class AnswerFailed(Exception):
    """An answer failed after it may already have been billed; carries what it cost and its token counts."""

    def __init__(self, cost_usd: float, model_usage: list[dict[str, Any]] | None = None) -> None:
        super().__init__("answer failed")
        self.cost_usd = cost_usd
        self.model_usage = model_usage or []


class AnswerEngine(Protocol):
    limits: AnswerLimits

    def worst_case_usd(self) -> Decimal: ...

    def answer(self, question: str, prior: list[PriorTurn] | None = None) -> AnswerOutcome: ...


class OpenRouterAnswerEngine:
    """The accepted CLI configuration, run with a fresh client and read-only transaction per answer."""

    def __init__(self, settings: Settings, connect: Callable[[], Any]) -> None:
        self.settings = settings.answer
        self.connect = connect
        # GPT-5 mini spends most of its completion allowance on reasoning; at 900 (3,600 sent)
        # longer answers were cut off. 2,500 (10,000 sent) matches the broad CLI profile.
        self.limits = AnswerLimits(max_output_tokens=WEB_MAX_OUTPUT_TOKENS, max_seconds=self.settings.max_seconds,
                                   max_model_calls=self.settings.max_model_calls,
                                   host_reads=self.settings.host_reads, coverage_checks=self.settings.coverage_checks)

    def worst_case_usd(self) -> Decimal:
        # The CLI bound, priced at the approved ceilings because no endpoint has been checked yet,
        # plus one follow-up rewrite with every character counted as a token.
        answer = self.settings
        input_ceiling = Decimal(str(PRICE_CEILINGS[answer.model][0])) / Decimal(1_000_000)
        return worst_case_usd(
            input_byte_budget=answer.input_byte_budget, request_budget=answer.request_budget,
            max_output_tokens=self.limits.max_output_tokens,
            input_usd_per_token=max(input_ceiling, answer.max_embedding_usd_per_million / Decimal(1_000_000)),
            completion_usd_per_token=answer.max_generation_usd_per_million / Decimal(1_000_000)
        ) + Decimal(str(price_usd(REWRITE_MODEL, REWRITE_INPUT_CHARS,
                                  completion_limit(REWRITE_MODEL, "followup_rewrite_v1",
                                                   REWRITE_MAX_OUTPUT_TOKENS, None)[1]))) + self._router_worst_case()

    def _router_worst_case(self) -> Decimal:
        """A first question may also make one router call; a follow-up makes the rewrite instead."""
        from app.agent.properties import ROUTER_INPUT_CHARS, ROUTER_MAX_OUTPUT_TOKENS, ROUTER_MODEL
        if not self.settings.property_router:
            return Decimal(0)
        return Decimal(str(price_usd(ROUTER_MODEL, ROUTER_INPUT_CHARS,
                                     completion_limit(ROUTER_MODEL, "property_router_v1",
                                                      ROUTER_MAX_OUTPUT_TOKENS, None)[1])))

    def answer(self, question: str, prior: list[PriorTurn] | None = None) -> AnswerOutcome:
        from app.agent.harness import answer_question
        from app.agent.memory import build_memory
        from app.models.client import ModelSpec
        from app.models.openrouter import OpenRouterClient
        from app.storage.retrieval import DatabaseRetriever, selected_embedding_spec

        answer = self.settings
        private_route, model_providers = private_routes(answer.model, answer.embedding_provider_tag,
                                                        answer.generation_provider_tag)
        if prior or answer.property_router:
            model_providers[REWRITE_MODEL] = {**private_route, "only": [TAGS[REWRITE_MODEL]],
                                              "require_parameters": True}
        # A follow-up's rewrite gets its own request so the answer keeps its full allowance.
        client = OpenRouterClient(allow_live=True, request_budget=answer.request_budget + (1 if prior else 0)
                                  + (1 if answer.property_router and not prior else 0),
                                  estimated_input_token_budget=answer.input_byte_budget,
                                  conservative_input_budget=True, provider=private_route,
                                  model_providers=model_providers, timeout_seconds=WEB_REQUEST_TIMEOUT_SECONDS)
        routes, embedding_model, rewrite_prices = None, None, {}
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
                memory, query = None, None
                routing_started = time.monotonic()
                if not prior and answer.property_router:
                    routed = route_properties(client, conn, question, retriever, rewrite_prices)
                    if routed is not None:
                        return AnswerOutcome(routed, self._cost(client, routes, answer.model, embedding_model,
                                                                rewrite_prices), list(client.usage_log))
                if prior:
                    memory = build_memory(prior, active=active_revisions(conn, prior))
                    query = rewrite_followup(client, question, memory.turns, rewrite_prices)
                result = answer_question(question, retriever=retriever, client=client,
                                         model=ModelSpec(provider="openrouter", model_id=answer.model,
                                                         model_version="current"),
                                         limits=self.limits, reasoning_effort=answer.reasoning_effort,
                                         memory=memory, search_query=query)
                if not prior and answer.property_router:
                    # Time spent deciding the route is part of the answer's wait.
                    result = result.model_copy(update={"elapsed_seconds": time.monotonic() - routing_started})
        except Exception as exc:
            raise AnswerFailed(self._cost(client, routes, answer.model, embedding_model, rewrite_prices),
                               list(client.usage_log)) from exc
        return AnswerOutcome(result, self._cost(client, routes, answer.model, embedding_model, rewrite_prices),
                             list(client.usage_log))

    def _cost(self, client, routes, model: str, embedding_model: str | None = None,
              extra_prices: dict | None = None) -> float:
        """Measured cost, or the worst case when any request's usage is unknown."""
        if not client.request_timings:
            return 0.0
        if routes is None or embedding_model is None:
            return float(self.worst_case_usd())
        measured, known = measured_usd(client, routes, model=model, embedding_model=embedding_model,
                                       extra_prices=extra_prices)
        return float(measured if known else self.worst_case_usd())


def rewrite_followup(client, question: str, recent: list[dict[str, str]],
                     prices: dict[str, tuple[Decimal, Decimal]]) -> str | None:
    """The follow-up as a standalone query, or None to search with the follow-up itself.

    The rewrite is optional: if its route fails the privacy or price check, the answer goes ahead
    without it (nothing was billed). On success its checked per-token prices go into `prices`.
    """
    from app.agent.memory import standalone_query
    from app.models.client import ModelSpec
    from app.models.routes import verify_routes

    if not recent:
        return None
    try:
        checked = verify_routes(client, [REWRITE_MODEL], structured_ids={REWRITE_MODEL})[REWRITE_MODEL]
    except (ModelClientError, ValueError, KeyError) as exc:
        log.warning("follow-up rewrite skipped error=%s", type(exc).__name__)
        return None
    prices[REWRITE_MODEL] = (Decimal(str(checked["input_usd_per_million"])) / Decimal(1_000_000),
                             Decimal(str(checked["output_usd_per_million"])) / Decimal(1_000_000))
    return standalone_query(question, recent, client=client,
                            model=ModelSpec(provider="openrouter", model_id=REWRITE_MODEL, model_version="current"))


def route_properties(client, conn, question: str, retriever,
                     prices: dict[str, tuple[Decimal, Decimal]]) -> AnswerResult | None:
    """A count or list question answered from stored properties, or None for the answer loop.

    The router model (gpt-6-luna) is optional like the follow-up rewrite: if its route fails
    the privacy or price check, or the call fails, the question goes to the loop.
    """
    from app.agent.properties import ROUTER_MODEL, answer_properties, route_model, to_sources
    from app.models.client import ModelSpec
    from app.models.routes import verify_routes
    from app.storage.search import load_sources

    started = time.monotonic()
    sources = to_sources(load_sources(conn))
    try:
        checked = verify_routes(client, [ROUTER_MODEL], structured_ids={ROUTER_MODEL})[ROUTER_MODEL]
        prices[ROUTER_MODEL] = (Decimal(str(checked["input_usd_per_million"])) / Decimal(1_000_000),
                                Decimal(str(checked["output_usd_per_million"])) / Decimal(1_000_000))
        query = route_model(question, sources, client=client,
                            model=ModelSpec(provider="openrouter", model_id=ROUTER_MODEL, model_version="current"))
    except (ModelClientError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("property router skipped error=%s", type(exc).__name__)
        return None
    if query is None:
        return None
    try:
        return answer_properties(query, sources, retriever=retriever, started=started,
                                 model_calls=1, route={"model": ROUTER_MODEL})
    except (StorageError, IndexError, ValueError) as exc:
        log.warning("property answer skipped error=%s", type(exc).__name__)
        return None


def active_revisions(conn, prior: list[PriorTurn]) -> set[tuple[str, str]]:
    """(document, run) pairs that still serve answers, for the documents earlier turns read."""
    documents = list({item.document_id for turn in prior for item in turn.passages.values()})
    if not documents:
        return set()
    rows = conn.execute("""select r.document_id, r.run_id from knowledge.active_runs a
        join knowledge.runs r on r.run_id=a.run_id
        join knowledge.corpus_state s on s.profile_id=a.profile_id
        where r.document_id = any(%s)""", (documents,)).fetchall()
    return {(str(row[0]), str(row[1])) for row in rows}


def obsidian_link(vault_name: str, relative_path: str) -> str:
    return f"obsidian://open?vault={quote(vault_name, safe='')}&file={quote(relative_path, safe='')}"


def build_receipt(result: AnswerResult, labels: dict[str, tuple[str, str]], vault_name: str) -> dict[str, Any]:
    def passage(item) -> dict[str, Any]:
        # Revision ids let a follow-up carry this passage while its document is unchanged.
        return {"document_id": item.document_id, "title": labels.get(item.document_id, ("",))[0] or item.relative_path,
                "author": labels.get(item.document_id, ("", ""))[1],
                "run_id": item.run_id, "chunk_id": item.chunk_id, "source_sha256": item.source_sha256,
                "root_alias": item.root_alias, "relative_path": item.relative_path, "obsidian_url": obsidian_link(vault_name, item.relative_path),
                "start_char": item.start_char, "end_char": item.end_char, "text": item.text}

    return {"answer": result.answer, "completeness": result.completeness, "gaps": result.gaps,
            "citations": {ref: passage(item) for ref, item in result.citations.items()},
            "inspected": {ref: passage(item) for ref, item in result.inspected.items()},
            "termination": result.termination, "finalization_reason": result.finalization_reason,
            "needs": result.needs, "model_calls": result.model_calls, "memory": result.memory,
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
        thread = threading.Thread(target=self._run, args=(conversation_id, turn_id, question), daemon=True,
                                  name=f"answer-{turn_id}")
        self._threads = [item for item in self._threads if item.is_alive()] + [thread]
        thread.start()
        return conversation_id, turn_id

    def wait(self, timeout: float = 10) -> None:
        """Let running answers finish: used at shutdown and by tests."""
        deadline = time.monotonic() + timeout
        for thread in list(self._threads):
            thread.join(max(0, deadline - time.monotonic()))

    def _labels(self, result: AnswerResult) -> dict[str, tuple[str, str]]:
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
        return {row[0]: (source_label(row[2], row[1])[0], source_author(row[2], row[1])) for row in rows}

    def _record_spend(self, turn_id: str, cost: float) -> None:
        if not cost:
            return
        try:
            with self.connect() as conn:
                record_spend(conn, "answer", cost, turn_id)
        except Exception as exc:  # noqa: BLE001 - never leave the turn running because the ledger failed
            log.error("spend not recorded turn=%s cost_usd=%.6f error=%s", turn_id, cost, type(exc).__name__)

    def _run(self, conversation_id: str, turn_id: str, question: str) -> None:
        started = time.monotonic()
        cost = 0.0
        status, receipt, error = "failed", None, "the answer could not be completed; try again"
        try:
            prior = [prior_turn_from_receipt(asked, receipt)
                     for asked, receipt in self.store.prior_turns(conversation_id, turn_id, PRIOR_TURNS)]
            outcome = self.engine.answer(question, prior)
            cost = outcome.cost_usd
            status, error = turn_status(outcome.result)
            receipt = {**build_receipt(outcome.result, self._labels(outcome.result), self.settings.vault_name),
                       "model_usage": outcome.model_usage}
        except SpendCapError:
            error = "the daily spending cap was reached"
        except AnswerFailed as exc:
            cost = exc.cost_usd
            # Token counts only, so a failed answer still shows where its tokens went.
            receipt = {"model_usage": exc.model_usage} if exc.model_usage else None
            log.warning("answer failed turn=%s error=%s", turn_id, type(exc.__cause__).__name__)
        except Exception as exc:  # noqa: BLE001 - the turn must always leave the running state
            log.warning("answer failed turn=%s error=%s", turn_id, type(exc).__name__)
        finally:
            # Spend goes in the ledger before the turn stops counting as running for the cap check.
            self._record_spend(turn_id, cost)
            self.store.finish_turn(turn_id, status=status, receipt=receipt, cost_usd=cost, error=error)
            # Ids, timing and cost only; never question, answer or excerpt text.
            log.info("answer finished turn=%s seconds=%.1f cost_usd=%.6f", turn_id, time.monotonic() - started, cost)
