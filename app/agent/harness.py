"""Stateless bounded investigation with citation provenance checks."""

from __future__ import annotations

import json
import copy
import re
import time
from pathlib import Path
from typing import Protocol
import psycopg

from app.agent.actions import ACTION_SCHEMA_V2, AgentAction
from app.agent.records import AnswerLimits, AnswerResult
from app.models.client import ModelClient, ModelClientError, ModelSpec
from app.storage import StorageError
from app.storage.records import CardHit, FusedHit, SearchHit, SourcePassage

PROMPT_VERSION = "answer_agent_v10"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"
PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
CITATION = re.compile(r"\[(E\d+\.\d+(?:@\d+)?)\]")


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
    def find(self, reference: FusedHit | CardHit, query: str) -> list[FusedHit]: ...
    def read(self, reference: FusedHit | CardHit, span_index: int, *, offset: int = 0) -> SourcePassage: ...


def answer_question(question: str, *, retriever: AnswerRetriever, client: ModelClient,
                    model: ModelSpec, limits: AnswerLimits | None = None,
                    reasoning_effort: str | None = None,
                    allow_legacy_actions: bool = False) -> AnswerResult:
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
    needs: dict[str, dict] = {"N1": {"description": question, "status": "open", "evidence_ids": [], "rationale": None}}
    actions: list[dict] = []
    calls = searches = card_searches = finds = reads = 0
    discoveries_since_read = 0
    finish_correction_used = False
    finalization_reason: str | None = None
    def stopped(reason: str, termination: str) -> AnswerResult:
        return AnswerResult(answer="I could not verify enough source evidence to answer safely.",
                            completeness="unsupported" if not inspected else "partial",
                            citations={}, gaps=[reason], actions=actions, model_calls=calls,
                            inspected=inspected, needs=needs, finalization_reason=finalization_reason,
                            termination=termination,
                            elapsed_seconds=time.monotonic() - started)
    feedback = "Begin by searching."
    while calls < limits.max_model_calls and time.monotonic() - started < limits.max_seconds:
        available = [{"id": key,
                      "path": item.hit.relative_path if isinstance(item, FusedHit) else item.relative_path,
                      "card_hint": None if isinstance(item, FusedHit) else {
                          name: str(getattr(item, name))[:180] for name in
                          ("statement", "situation", "action", "result", "principle")
                          if getattr(item, name)},
                      "read_ids": [ref for ref in inspected if ref.startswith(key + ".")],
                      "spans": [[hint["index"], hint["preview"][:80]]
                                for hint in span_hints(item.hit)] if isinstance(item, FusedHit) else [[0, "original card evidence"]]}
                     for key, item in candidates.items()]
        read_evidence = [{"id": key, "path": item.relative_path, "start_char": item.start_char,
                          "end_char": item.end_char, "span_end_char": item.span_end_char,
                          "next_offset": item.next_offset, "truncated": item.truncated,
                          "text": item.text}
                         for key, item in inspected.items()]
        input_text = json.dumps({"question": question, "candidates": available[-16:],
                                 "inspected": read_evidence, "needs": needs, "feedback": feedback,
                                 "remaining": {"searches": limits.max_searches - searches,
                                               "card_searches": limits.max_card_searches - card_searches,
                                               "finds": limits.max_finds - finds,
                                               "reads": limits.max_reads - reads,
                                               "model_calls": limits.max_model_calls - calls}}, ensure_ascii=False, separators=(",", ":"))
        # Leave room to synthesize the inspected evidence instead of spending
        # the entire allowance on another investigation turn.
        remaining_bytes = getattr(client, "remaining_input_bytes", None)
        next_request_bytes = len(json.dumps([PROMPT, input_text, ACTION_SCHEMA_V2]).encode("utf-8")) + 4096
        recent_timings = getattr(client, "request_timings", [])[-3:]
        finish_seconds = max(30.0, 2 * max((duration for _, duration in recent_timings), default=0.0))
        triggers = []
        if limits.max_model_calls >= 6 and limits.max_model_calls - calls <= 2:
            triggers.append("model_calls")
        if reads >= limits.max_reads: triggers.append("reads")
        if limits.max_seconds - (time.monotonic() - started) <= finish_seconds: triggers.append("time_reserve")
        if getattr(client, "remaining_requests", 99) <= 2: triggers.append("transport_requests")
        if isinstance(remaining_bytes, int) and remaining_bytes < 2 * next_request_bytes: triggers.append("input_reserve")
        force_finish = bool(triggers)
        schema = ACTION_SCHEMA_V2
        must_read = not force_finish and discoveries_since_read >= 2 and bool(candidates)
        if force_finish:
            finalization_reason = ",".join(triggers)
            value = json.loads(input_text)
            value["candidates"] = []
            value["feedback"] = feedback + "\nInvestigation allowance is ending. Return finish now using inspected originals, with honest remaining gaps. No further searches or reads."
            input_text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            schema = copy.deepcopy(ACTION_SCHEMA_V2)
            schema["properties"]["action"]["enum"] = ["finish"]
        elif must_read:
            value = json.loads(input_text)
            value["feedback"] = (feedback + "\nYou have enough discovery leads for now. "
                                 "Read one listed original span before another search or finish.")
            input_text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            schema = copy.deepcopy(ACTION_SCHEMA_V2)
            schema["properties"]["action"]["enum"] = ["read"]
        try:
            calls += 1
            result = client.generate_json(model=model, system_prompt=PROMPT,
                                          input_text=input_text, schema_name="answer_action_v2",
                                          json_schema=schema,
                                          max_output_tokens=limits.max_output_tokens,
                                          reasoning_effort=reasoning_effort)
            if result.model != model:
                raise ModelClientError("answer response model differs from requested model")
            payload = result.data
            ignored_draft_fields = []
            if isinstance(payload, dict) and payload.get("action") in (
                    "search", "search_cards", "find", "read"):
                draft_defaults = {"answer": None, "citation_ids": [],
                                  "completeness": None, "gaps": [], "sufficient": None}
                ignored_draft_fields = [key for key, default in draft_defaults.items()
                                        if payload.get(key) != default]
                if ignored_draft_fields:
                    payload = {**payload, **draft_defaults}
            action = AgentAction.model_validate(payload)
            if action.version != 2 and not allow_legacy_actions:
                raise ValueError("new answer runs require action schema version 2")
            if force_finish and action.action != "finish":
                raise ValueError("final allowance permits only finish")
            if must_read and action.action != "read":
                raise ValueError("discovery allowance requires an original read")
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
            feedback = ("Invalid model action. Use search or search_cards with a query; read with a known "
                        "reference_id and span_index, and no query; or finish with the answer and citations. "
                        "Only the fields for the chosen action may be non-null. Produce a valid bounded action.")
            invalid = {"action": "invalid", "reason": type(exc).__name__}
            if hasattr(exc, "errors"):
                invalid["fields"] = [{"path": ".".join(map(str, error["loc"])),
                                      "type": error["type"], "message": error["msg"]}
                                     for error in exc.errors(include_input=False)]
            actions.append(invalid)
            continue
        actions.append(action.model_dump(exclude={"answer"}))
        if ignored_draft_fields:
            actions[-1]["ignored_draft_fields"] = ignored_draft_fields
        if force_finish:
            actions[-1]["budget_finalization"] = True
            actions[-1]["finalization_reason"] = finalization_reason
        if action.version == 2:
            update_ids = [update.id for update in action.need_updates]
            if len(update_ids) != len(set(update_ids)) or any(
                    update.id == "N1" and update.status == "excluded" for update in action.need_updates):
                actions[-1]["rejected"] = "Conflicting or out-of-scope question needs"
                feedback = "Use each need ID once per action. The user's question N1 cannot be excluded as out of scope."
                continue
            invalid_need = next((update for update in action.need_updates
                                 if update.status == "resolved" and not set(update.evidence_ids).issubset(inspected)), None)
            if invalid_need:
                actions[-1]["rejected"] = "Need resolution cites unread evidence"
                feedback = f"Need {invalid_need.id} can be resolved only with inspected evidence IDs."
                continue
            for update in action.need_updates:
                needs[update.id] = update.model_dump()
        if action.action == "search":
            if searches >= limits.max_searches:
                feedback = "Search budget exhausted; inspect existing candidates or finish with gaps."
                continue
            searches += 1
            discoveries_since_read += 1
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
            discoveries_since_read += 1
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
            mode = (found[0].query_mode if found else
                    ("or_fallback_empty" if len(set(re.findall(r"\w+", action.query or "", re.UNICODE))) > 1
                     else "all_terms_empty"))
            actions[-1]["query_mode"] = mode
            feedback = (f"Found {len(found)} card hints ({mode}). Cards are discovery leads, not citation evidence; "
                        "read originals. If empty, shorten or vary the query using distinctive source concepts.")
        elif action.action == "find":
            ref = action.reference_id or ""
            if ref not in candidates or finds >= limits.max_finds:
                feedback = "Unknown document reference or find budget exhausted. Use a discovered reference and a short within-document query."
                actions[-1]["rejected"] = "Unknown reference or find budget"
                continue
            finds += 1
            discoveries_since_read += 1
            try:
                found = retriever.find(candidates[ref], action.query or "")
            except (StorageError, ModelClientError, psycopg.Error) as exc:
                actions[-1]["error_type"] = type(exc).__name__
                return stopped("Document find failed.", "tool_failed")
            lead = candidates[ref].hit if isinstance(candidates[ref], FusedHit) else candidates[ref]
            if any((item.hit.document_id, item.hit.run_id, item.hit.source_sha256) !=
                   (lead.document_id, lead.run_id, lead.source_sha256) for item in found):
                actions[-1]["rejected"] = "Document find changed source revision"
                return stopped("Document find provenance failed.", "tool_failed")
            actions[-1]["results"] = [{"rank": item.rank, "score": item.rrf_score,
                **item.hit.model_dump(exclude={"raw_text", "score", "rank"})} for item in found]
            for item in found:
                if not any(isinstance(old, FusedHit) and old.hit.run_id == item.hit.run_id
                           and old.hit.chunk_id == item.hit.chunk_id for old in candidates.values()):
                    candidates[f"E{len(candidates) + 1}"] = item
            feedback = f"Found {len(found)} original chunk leads in that document. Choose a span and read it before citing."
        elif action.action == "read":
            ref = action.reference_id or ""
            if ref not in candidates or reads >= limits.max_reads:
                feedback = "Unknown reference or read budget exhausted."
                continue
            span_index = action.span_index or 0
            offset = action.offset or 0
            citation_id = f"{ref}.{span_index}" + (f"@{offset}" if offset else "")
            if citation_id in inspected:
                feedback = f"{citation_id} is already inspected. Read a different span for missing details or finish using the existing evidence."
                actions[-1]["rejected"] = "Already inspected span"
                continue
            if citation_id not in inspected:
                try:
                    candidate = candidates[ref]
                    hit = candidate.hit if isinstance(candidate, FusedHit) else candidate
                    span = hit.source_spans[span_index] if isinstance(candidate, FusedHit) else hit.evidence_span
                    if offset < 0 or offset >= span["end_char"] - span["start_char"]:
                        raise ValueError("offset outside selected span")
                    start_char = span["start_char"] + offset
                    span_id = f"{ref}.{span_index}"
                    if any((key == span_id or key.startswith(span_id + "@")) and
                           old.start_char <= start_char < old.end_char for key, old in inspected.items()):
                        actions[-1]["rejected"] = "Already inspected offset"
                        feedback = "That offset lies inside an already inspected passage. Use its next_offset or a different span."
                        continue
                    reads += 1
                    passage = (retriever.read(candidate, span_index, offset=offset) if offset
                               else retriever.read(candidate, span_index))
                    if (passage.document_id, passage.run_id, passage.source_sha256, passage.chunk_id) != (
                        hit.document_id, hit.run_id, hit.source_sha256, hit.chunk_id
                    ) or not (passage.start_char == span["start_char"] + offset < passage.end_char <= span["end_char"]) or (
                        len(passage.text) != passage.end_char - passage.start_char
                    ):
                        raise ValueError("source read changed candidate provenance")
                    if passage.span_end_char is None:
                        next_offset = offset + len(passage.text)
                        passage = passage.model_copy(update={"span_end_char": span["end_char"],
                            "next_offset": next_offset if passage.end_char < span["end_char"] else None,
                            "truncated": passage.end_char < span["end_char"]})
                    inspected[citation_id] = passage
                    discoveries_since_read = 0
                    actions[-1]["inspected_id"] = citation_id
                    actions[-1]["next_offset"] = passage.next_offset
                except (StorageError, ModelClientError, psycopg.Error) as exc:
                    actions[-1]["error_type"] = type(exc).__name__
                    return stopped("Original source read failed.", "tool_failed")
                except (IndexError, ValueError):
                    actions[-1]["rejected"] = "Invalid span or changed provenance"
                    feedback = "Invalid span or offset for that candidate. A continuation offset must be inside the selected published span; stop at its end."
                    continue
            feedback = (f"Original passage {citation_id} inspected. Next offset: {inspected[citation_id].next_offset}. "
                        "Decide what it actually supports; continue only within the listed span.")
        else:
            unresolved = [key for key, value in needs.items() if value["status"] == "open"]
            if (action.version == 2 and unresolved and not force_finish and not finish_correction_used
                    and calls < limits.max_model_calls):
                finish_correction_used = True
                actions[-1]["rejected"] = "Material investigation needs remain open"
                feedback = (f"Before finishing, investigate or explicitly exclude these still-open material needs: "
                            f"{[(key, needs[key]['description']) for key in unresolved]}. "
                            "You have one correction opportunity; do not claim unread evidence was verified.")
                continue
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
            if action.completeness == "complete" and (not inspected or searches + card_searches + finds == 0):
                feedback = "Complete answer requires search and inspected evidence."
                continue
            private_gaps = list(action.gaps)
            completeness = action.completeness or "partial"
            if action.version == 2 and unresolved:
                private_gaps.extend(f"Unresolved need {key}: {needs[key]['description']}" for key in unresolved)
                if completeness == "complete":
                    completeness = "partial"
            return AnswerResult(answer=action.answer or "", completeness=completeness,
                                citations={ref: inspected[ref] for ref in action.citation_ids},
                                gaps=private_gaps, actions=actions, model_calls=calls,
                                inspected=inspected, needs=needs, finalization_reason=finalization_reason,
                                elapsed_seconds=time.monotonic() - started)
    return stopped("Investigation budget ended before a valid grounded answer.", "exhausted")
