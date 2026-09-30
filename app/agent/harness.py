"""Stateless bounded investigation with citation provenance checks."""

from __future__ import annotations

import json
import copy
import re
import time
from pathlib import Path
from typing import Protocol
import psycopg

from app.agent.actions import ACTION_SCHEMA, AgentAction
from app.agent.records import AnswerLimits, AnswerResult
from app.models.client import ModelClient, ModelClientError, ModelSpec
from app.storage import StorageError
from app.storage.records import CardHit, FusedHit, SearchHit, SourcePassage

PROMPT_VERSION = "answer_agent_v6"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"
PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
CITATION = re.compile(r"\[(E\d+\.\d+)\]")


def span_hints(hit: SearchHit) -> list[dict]:
    """Chunk raw text concatenates its spans in order; previews are discovery only."""
    offset = 0
    result = []
    for index, span in enumerate(hit.source_spans):
        length = span["end_char"] - span["start_char"]
        result.append({"index": index, "start_char": span["start_char"],
                       "end_char": span["end_char"], "preview": hit.raw_text[offset:offset + min(length, 120)]})
        offset += length
    return result


class AnswerRetriever(Protocol):
    def search(self, query: str) -> list[FusedHit]: ...
    def search_cards(self, query: str) -> list[CardHit]: ...
    def read(self, reference: FusedHit | CardHit, span_index: int) -> SourcePassage: ...


def answer_question(question: str, *, retriever: AnswerRetriever, client: ModelClient,
                    model: ModelSpec, limits: AnswerLimits | None = None,
                    reasoning_effort: str | None = None) -> AnswerResult:
    """All mutable budgets, references, and evidence belong to this invocation."""
    if not question.strip():
        raise ValueError("question must not be empty")
    limits = limits or AnswerLimits()
    started = time.monotonic()
    set_deadline = getattr(client, "set_deadline", None)
    if callable(set_deadline):
        set_deadline(started + limits.max_seconds)
    candidates: dict[str, FusedHit | CardHit] = {}
    inspected: dict[str, SourcePassage] = {}
    actions: list[dict] = []
    calls = searches = card_searches = reads = 0
    def stopped(reason: str, termination: str) -> AnswerResult:
        return AnswerResult(answer="I could not verify enough source evidence to answer safely.",
                            completeness="unsupported" if not inspected else "partial",
                            citations={}, gaps=[reason], actions=actions, model_calls=calls,
                            inspected=inspected, termination=termination,
                            elapsed_seconds=time.monotonic() - started)
    feedback = "Begin by searching."
    while calls < limits.max_model_calls and time.monotonic() - started < limits.max_seconds:
        available = [{"id": key,
                      "path": item.hit.relative_path if isinstance(item, FusedHit) else item.relative_path,
                      "card_hint": None if isinstance(item, FusedHit) else item.statement[:240],
                      "read_indices": [int(ref.split(".")[1]) for ref in inspected if ref.startswith(key + ".")],
                      "spans": [[hint["index"], hint["preview"][:80]]
                                for hint in span_hints(item.hit)] if isinstance(item, FusedHit) else [[0, "original card evidence"]]}
                     for key, item in candidates.items()]
        read_evidence = [{"id": key, "path": item.relative_path,
                          "text": item.text}
                         for key, item in inspected.items()]
        input_text = json.dumps({"question": question, "candidates": available[-30:],
                                 "inspected": read_evidence, "feedback": feedback,
                                 "remaining": {"searches": limits.max_searches - searches,
                                               "card_searches": limits.max_card_searches - card_searches,
                                               "reads": limits.max_reads - reads,
                                               "model_calls": limits.max_model_calls - calls}}, ensure_ascii=False, separators=(",", ":"))
        # Leave room to synthesize the inspected evidence instead of spending
        # the entire allowance on another investigation turn.
        remaining_bytes = getattr(client, "remaining_input_bytes", None)
        next_request_bytes = len(json.dumps([PROMPT, input_text, ACTION_SCHEMA]).encode("utf-8")) + 4096
        recent_timings = getattr(client, "request_timings", [])[-3:]
        finish_seconds = max(30.0, 2 * max((duration for _, duration in recent_timings), default=0.0))
        force_finish = (limits.max_model_calls - calls <= 1 or reads >= limits.max_reads
                        or limits.max_seconds - (time.monotonic() - started) <= finish_seconds
                        or getattr(client, "remaining_requests", 99) <= 2
                        or (isinstance(remaining_bytes, int) and remaining_bytes < 2 * next_request_bytes))
        schema = ACTION_SCHEMA
        if force_finish:
            value = json.loads(input_text)
            value["candidates"] = []
            value["feedback"] = feedback + "\nInvestigation allowance is ending. Return finish now using inspected originals, with honest remaining gaps. No further searches or reads."
            input_text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            schema = copy.deepcopy(ACTION_SCHEMA)
            schema["properties"]["action"]["enum"] = ["finish"]
        try:
            calls += 1
            result = client.generate_json(model=model, system_prompt=PROMPT,
                                          input_text=input_text, schema_name="answer_action_v1",
                                          json_schema=schema,
                                          max_output_tokens=limits.max_output_tokens,
                                          reasoning_effort=reasoning_effort)
            if result.model != model:
                raise ModelClientError("answer response model differs from requested model")
            action = AgentAction.model_validate(result.data)
            if force_finish and action.action != "finish":
                raise ValueError("final allowance permits only finish")
        except ModelClientError as exc:
            safe_reasons = {
                "model request or estimated input budget exceeded": "request_or_input_budget",
                "answer time budget exceeded": "time_budget",
                "answer response model differs from requested model": "model_mismatch",
                "OpenRouter structured output was incomplete (finish=length)": "completion_budget",
                "OpenRouter returned malformed structured output (finish=stop)": "malformed_output",
                "OpenRouter structured output was incomplete (finish=error)": "provider_completion_error",
                "OpenRouter structured output was incomplete (finish=content_filter)": "provider_content_filter",
            }
            reason = safe_reasons.get(str(exc), "model_or_route_failure")
            if reason == "malformed_output" and calls > 1:
                actions.append({"action": "invalid", "reason": reason})
                feedback = "Your previous response was not valid JSON. Return exactly one JSON action object matching every required schema field. Put answer prose inside the answer string; no prose or Markdown fences outside the JSON object."
                continue
            actions.append({"action": "model_failed", "reason": reason})
            return stopped(f"Model stopped: {reason}.", "model_failed")
        except ValueError as exc:
            feedback = f"Invalid model action: {type(exc).__name__}. Produce a valid bounded action."
            actions.append({"action": "invalid", "reason": type(exc).__name__})
            continue
        actions.append(action.model_dump(exclude={"answer"}))
        if force_finish:
            actions[-1]["budget_finalization"] = True
        if action.action == "search":
            if searches >= limits.max_searches:
                feedback = "Search budget exhausted; inspect existing candidates or finish with gaps."
                continue
            searches += 1
            try:
                found = retriever.search(action.query or "")
            except (StorageError, ModelClientError, psycopg.Error) as exc:
                actions[-1]["error_type"] = type(exc).__name__
                return stopped("Hybrid retrieval failed.", "tool_failed")
            actions[-1]["results"] = [{"rank": item.rank, "rrf_score": item.rrf_score,
                "channels": item.channels, **item.hit.model_dump(exclude={"raw_text", "score", "rank"})}
                for item in found]
            for item in found:
                if not any(isinstance(old, FusedHit) and old.hit.run_id == item.hit.run_id
                           and old.hit.chunk_id == item.hit.chunk_id for old in candidates.values()):
                    candidates[f"E{len(candidates) + 1}"] = item
            feedback = f"Found {len(found)} fused candidates. Inspect original passages before citing."
        elif action.action == "search_cards":
            if card_searches >= limits.max_card_searches:
                feedback = "Card-search budget exhausted; inspect existing candidates or finish with gaps."
                continue
            card_searches += 1
            try:
                found = retriever.search_cards(action.query or "")
            except (StorageError, ModelClientError, psycopg.Error) as exc:
                actions[-1]["error_type"] = type(exc).__name__
                return stopped("Card retrieval failed.", "tool_failed")
            actions[-1]["results"] = [item.model_dump(exclude={"statement"}) for item in found]
            for item in found:
                if not any(isinstance(old, CardHit) and old.run_id == item.run_id
                           and old.evidence_span == item.evidence_span for old in candidates.values()):
                    candidates[f"E{len(candidates) + 1}"] = item
            feedback = f"Found {len(found)} card hints. Cards are not citation evidence; read originals."
        elif action.action == "read":
            ref = action.reference_id or ""
            if ref not in candidates or reads >= limits.max_reads:
                feedback = "Unknown reference or read budget exhausted."
                continue
            span_index = action.span_index or 0
            citation_id = f"{ref}.{span_index}"
            if citation_id in inspected:
                feedback = f"{citation_id} is already inspected. Read a different span for missing details or finish using the existing evidence."
                actions[-1]["rejected"] = "Already inspected span"
                continue
            if citation_id not in inspected:
                reads += 1
                try:
                    candidate = candidates[ref]
                    hit = candidate.hit if isinstance(candidate, FusedHit) else candidate
                    span = hit.source_spans[span_index] if isinstance(candidate, FusedHit) else hit.evidence_span
                    passage = retriever.read(candidate, span_index)
                    if (passage.document_id, passage.run_id, passage.source_sha256, passage.chunk_id) != (
                        hit.document_id, hit.run_id, hit.source_sha256, hit.chunk_id
                    ) or not (span["start_char"] <= passage.start_char < passage.end_char <= span["end_char"]) or (
                        len(passage.text) != passage.end_char - passage.start_char
                    ):
                        raise ValueError("source read changed candidate provenance")
                    inspected[citation_id] = passage
                    actions[-1]["inspected_id"] = citation_id
                except (StorageError, ModelClientError, psycopg.Error) as exc:
                    actions[-1]["error_type"] = type(exc).__name__
                    return stopped("Original source read failed.", "tool_failed")
                except (IndexError, ValueError):
                    actions[-1]["rejected"] = "Invalid span or changed provenance"
                    feedback = "Invalid source-span index for that candidate."
                    continue
            feedback = f"Original passage {citation_id} inspected. Decide what it actually supports."
        else:
            cited = set(action.citation_ids)
            inline = set(CITATION.findall(action.answer or ""))
            if cited != inline or not cited.issubset(inspected):
                actions[-1]["rejected"] = "Fabricated, unread, or non-inline citation"
                actions[-1]["rejected_answer"] = action.answer
                feedback = (f"Declared citation IDs: {sorted(cited)}; IDs found in answer brackets: {sorted(inline)}; "
                            f"inspected IDs: {sorted(inspected)}. Use exactly [E1.3] style inline brackets "
                            "in the answer string, and list exactly those IDs in citation_ids. Revise the answer.")
                continue
            if action.completeness != "unsupported" and not cited:
                feedback = "Supported or partial answer needs inspected inline citations."
                continue
            if action.completeness == "complete" and (not inspected or searches + card_searches == 0):
                feedback = "Complete answer requires search and inspected evidence."
                continue
            return AnswerResult(answer=action.answer or "", completeness=action.completeness or "partial",
                                citations={ref: inspected[ref] for ref in action.citation_ids},
                                gaps=action.gaps, actions=actions, model_calls=calls,
                                inspected=inspected, elapsed_seconds=time.monotonic() - started)
    return stopped("Investigation budget ended before a valid grounded answer.", "exhausted")
