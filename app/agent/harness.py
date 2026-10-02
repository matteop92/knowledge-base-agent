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

PROMPT_VERSION = "answer_agent_v11"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"
PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
CITATION = re.compile(r"\[(E\d+\.\d+(?:@\d+)?)\]")


MAX_CANDIDATES_SHOWN = 20
MAX_FACTS_SHOWN = 5
# Fields that belong to each action; a stray value elsewhere is ignored, not fatal.
ACTION_FIELDS = {"search": {"query"}, "search_cards": {"query"}, "find": {"query", "reference_id"},
                 "read": {"reference_id", "span_index", "offset"}, "finish": set()}


def card_fact(card: dict) -> str:
    """One discovery line per card; never citation evidence."""
    parts = [f"{card.get('card_type') or 'card'}: {card.get('statement') or ''}"]
    parts.extend(f"{name}: {card[name]}" for name in ("situation", "action", "result", "principle")
                 if card.get(name))
    return " | ".join(parts)[:320]


def card_chunk(card: CardHit) -> FusedHit:
    """A card lead points at its whole chunk; the card span alone is the fallback."""
    hit = SearchHit(rank=card.rank, score=card.score, document_id=card.document_id, run_id=card.run_id,
                    source_sha256=card.source_sha256, root_alias=card.root_alias,
                    relative_path=card.relative_path, chunk_id=card.chunk_id,
                    source_spans=card.chunk_spans or [card.evidence_span],
                    raw_text=card.chunk_text or "")
    return FusedHit(rank=card.rank, rrf_score=card.score, channels=["cards"], hit=hit)


class AnswerRetriever(Protocol):
    def search(self, query: str) -> list[FusedHit]: ...
    def search_cards(self, query: str) -> list[CardHit]: ...
    def find(self, reference: FusedHit | CardHit, query: str) -> list[FusedHit]: ...
    def read(self, reference: FusedHit | CardHit, span_index: int, *, offset: int = 0) -> SourcePassage: ...


def answer_question(question: str, *, retriever: AnswerRetriever, client: ModelClient,
                    model: ModelSpec, limits: AnswerLimits | None = None,
                    reasoning_effort: str | None = None,
                    allow_legacy_actions: bool = False, use_cards: bool = True) -> AnswerResult:
    """All mutable budgets, references, and evidence belong to this invocation."""
    if not question.strip():
        raise ValueError("question must not be empty")
    limits = limits or AnswerLimits()
    started = time.monotonic()
    set_deadline = getattr(client, "set_deadline", None)
    if callable(set_deadline):
        set_deadline(started + limits.max_seconds)
    candidates: dict[str, FusedHit] = {}
    best_rank: dict[str, int] = {}
    facts: dict[str, list[str]] = {}
    read_refs: set[str] = set()
    host_search: dict | None = None
    inspected: dict[str, SourcePassage] = {}
    needs: dict[str, dict] = {"N1": {"description": question, "status": "open", "evidence_ids": [], "rationale": None}}
    actions: list[dict] = []
    calls = searches = card_searches = finds = reads = 0
    discoveries_since_read = 0
    finish_correction_used = False
    reads_at_coverage_check: int | None = None
    # Withholding cards removes both the card facts and the card-search action.
    base_schema = ACTION_SCHEMA_V2
    if not use_cards:
        base_schema = copy.deepcopy(ACTION_SCHEMA_V2)
        base_schema["properties"]["action"]["enum"] = ["search", "find", "read", "finish"]
    coverage_checks = 0
    queries_run: dict[tuple[str, str], list[str]] = {}
    finalization_reason: str | None = None
    def stopped(reason: str, termination: str) -> AnswerResult:
        return AnswerResult(answer="I could not verify enough source evidence to answer safely.",
                            completeness="unsupported" if not inspected else "partial",
                            citations={}, gaps=[reason], actions=actions, model_calls=calls,
                            inspected=inspected, needs=needs, finalization_reason=finalization_reason,
                            host_search=host_search, termination=termination,
                            elapsed_seconds=time.monotonic() - started)
    def register(found: list[FusedHit]) -> list[str]:
        """One candidate per chunk, keeping its best rank and its card facts."""
        refs, fresh = [], []
        for item in found:
            ref = next((key for key, old in candidates.items() if old.hit.run_id == item.hit.run_id
                        and old.hit.chunk_id == item.hit.chunk_id), None)
            if ref is None:
                ref = f"E{len(candidates) + 1}"
                candidates[ref] = item
                fresh.append(ref)
            best_rank[ref] = min(best_rank.get(ref, item.rank), item.rank)
            refs.append(ref)
        chunk_cards = getattr(retriever, "chunk_cards", None)
        if fresh and use_cards and callable(chunk_cards):
            cards = chunk_cards([candidates[ref] for ref in fresh])
            for ref in fresh:
                hit = candidates[ref].hit
                facts[ref] = [card_fact(card) for card in cards.get((hit.run_id, hit.chunk_id), [])]
        return refs

    def unread() -> list[str]:
        return sorted((ref for ref in candidates if ref not in read_refs),
                      key=lambda ref: (best_rank[ref], int(ref[1:])))

    def shown() -> list[str]:
        """Best-ranked unread chunks, plus the two best of any source they leave out."""
        ordered = unread()
        view = ordered[:MAX_CANDIDATES_SHOWN]
        per_source: dict[str, int] = {}
        for ref in view:
            source = candidates[ref].hit.document_id
            per_source[source] = per_source.get(source, 0) + 1
        for ref in ordered[MAX_CANDIDATES_SHOWN:]:
            source = candidates[ref].hit.document_id
            if per_source.get(source, 0) < 2:
                per_source[source] = per_source.get(source, 0) + 1
                view.append(ref)
        return view

    def leads(refs: list[str]) -> list[tuple[str, str]]:
        return [(ref, candidates[ref].hit.relative_path.rsplit("/", 1)[-1][:40])
                for ref in dict.fromkeys(refs) if ref not in read_refs][:8]

    # The host searches with the question itself so the first turn already has ranked leads.
    try:
        found = retriever.search(question)
        register(found)
    except (StorageError, ModelClientError, psycopg.Error) as exc:
        host_search = {"query": question, "error_type": type(exc).__name__}
        return stopped("Hybrid retrieval failed.", "tool_failed")
    host_search = {"query": question, "results": [
        {"rank": item.rank, "rrf_score": item.rrf_score, "channels": item.channels,
         **item.hit.model_dump(exclude={"raw_text", "score", "rank"})} for item in found]}
    feedback = (f"The host searched with the question and found {len(found)} chunks, listed best first. "
                "Read promising chunks, or search with a short specific query.")
    while calls < limits.max_model_calls and time.monotonic() - started < limits.max_seconds:
        available = [{"id": ref, "path": candidates[ref].hit.relative_path, "rank": best_rank[ref],
                      "preview": candidates[ref].hit.raw_text[:200],
                      "facts": facts.get(ref, [])[:MAX_FACTS_SHOWN]}
                     for ref in shown()]
        read_evidence = [{"chunk": ref, "path": candidates[ref].hit.relative_path,
                          "passages": [[key, item.text] for key, item in inspected.items()
                                       if key.startswith(ref + ".")]}
                         for ref in candidates if ref in read_refs]
        input_text = json.dumps({"question": question, "candidates": available,
                                 "inspected": read_evidence, "needs": needs, "feedback": feedback,
                                 "remaining": {"searches": limits.max_searches - searches,
                                               "card_searches": limits.max_card_searches - card_searches,
                                               "finds": limits.max_finds - finds,
                                               "reads": limits.max_reads - reads,
                                               "model_calls": limits.max_model_calls - calls}}, ensure_ascii=False, separators=(",", ":"))
        # Leave room to synthesize the inspected evidence instead of spending
        # the entire allowance on another investigation turn.
        remaining_bytes = getattr(client, "remaining_input_bytes", None)
        next_request_bytes = len(json.dumps([PROMPT, input_text, base_schema]).encode("utf-8")) + 4096
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
        schema = base_schema
        must_read = not force_finish and discoveries_since_read >= 2 and bool(unread())
        if force_finish:
            finalization_reason = ",".join(triggers)
            value = json.loads(input_text)
            value["candidates"] = []
            value["feedback"] = feedback + "\nInvestigation allowance is ending. Return finish now using inspected originals. Put remaining gaps in the structured gaps field; mention one in the answer only if it materially affects the request. No further searches or reads."
            input_text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            schema = copy.deepcopy(base_schema)
            schema["properties"]["action"]["enum"] = ["finish"]
        elif must_read:
            value = json.loads(input_text)
            value["feedback"] = (feedback + "\nYou have enough discovery leads for now. "
                                 "Read one listed chunk before another search or finish.")
            input_text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            schema = copy.deepcopy(base_schema)
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
            ignored_fields = []
            if isinstance(payload, dict) and payload.get("action") in ACTION_FIELDS:
                ignored_fields = [key for key in ("query", "reference_id", "span_index", "offset")
                                  if key not in ACTION_FIELDS[payload["action"]] and payload.get(key) is not None]
                payload = {**payload, **dict.fromkeys(ignored_fields)}
                # The model often names a passage (E6.5) where the chunk (E6) is meant.
                if isinstance(payload.get("reference_id"), str):
                    payload["reference_id"] = payload["reference_id"].split(".")[0].split("@")[0]
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
            invalid = {"action": "invalid", "reason": type(exc).__name__}
            detail = str(exc)
            if hasattr(exc, "errors"):
                invalid["fields"] = [{"path": ".".join(map(str, error["loc"])),
                                      "type": error["type"], "message": error["msg"]}
                                     for error in exc.errors(include_input=False)]
                detail = "; ".join(f"{item['path'] or 'action'}: {item['message']}" for item in invalid["fields"])
            feedback = (f"Invalid action: {detail[:600]}. Use search, search_cards, or find with a query; "
                        "read with a listed candidate reference_id; or finish with the answer and citations. "
                        "Produce a valid bounded action.")
            actions.append(invalid)
            continue
        actions.append(action.model_dump(exclude={"answer"}))
        if ignored_draft_fields:
            actions[-1]["ignored_draft_fields"] = ignored_draft_fields
        if ignored_fields:
            actions[-1]["ignored_fields"] = ignored_fields
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
        if action.action == "search_cards" and not use_cards:
            actions[-1]["rejected"] = "Card search is not available"
            feedback = "Card search is not available in this run. Use search, find, read, or finish."
            continue
        if action.action in ("search", "search_cards"):
            query_key = (action.action, " ".join((action.query or "").lower().split()))
            if query_key in queries_run:
                actions[-1]["rejected"] = "Repeated query"
                feedback = (f"You already ran that query; repeating it finds nothing new. Its results are among the "
                            f"candidates: {leads(queries_run[query_key])}. Read one, use find inside a candidate's "
                            "document, try different words, or finish and state the gap.")
                continue
        if action.action == "search":
            if searches >= limits.max_searches:
                actions[-1]["rejected"] = "Search budget exhausted"
                feedback = "Search budget exhausted; inspect existing candidates or finish with gaps."
                continue
            searches += 1
            discoveries_since_read += 1
            try:
                found = retriever.search(action.query or "")
                refs = register(found)
            except (StorageError, ModelClientError, psycopg.Error) as exc:
                actions[-1]["error_type"] = type(exc).__name__
                return stopped("Hybrid retrieval failed.", "tool_failed")
            actions[-1]["results"] = [{"rank": item.rank, "rrf_score": item.rrf_score,
                "channels": item.channels, **item.hit.model_dump(exclude={"raw_text", "score", "rank"})}
                for item in found]
            queries_run[query_key] = refs
            feedback = (f"Found {len(found)} chunks; best unread matches: {leads(refs)}. "
                        "Read originals before citing.")
        elif action.action == "search_cards":
            if card_searches >= limits.max_card_searches:
                actions[-1]["rejected"] = "Card-search budget exhausted"
                feedback = "Card-search budget exhausted; inspect existing candidates or finish with gaps."
                continue
            card_searches += 1
            discoveries_since_read += 1
            try:
                found = retriever.search_cards(action.query or "")
                refs = register([card_chunk(item) for item in found])
            except (StorageError, ModelClientError, psycopg.Error) as exc:
                actions[-1]["error_type"] = type(exc).__name__
                return stopped("Card retrieval failed.", "tool_failed")
            actions[-1]["results"] = [item.model_dump(exclude={"statement", "chunk_spans", "chunk_text"})
                                      for item in found]
            for ref, item in zip(refs, found):
                fact = card_fact({"card_type": "matched card", **item.model_dump()})
                facts[ref] = [fact, *[old for old in facts.get(ref, []) if old != fact]]
            mode = (found[0].query_mode if found else
                    ("or_fallback_empty" if len(set(re.findall(r"\w+", action.query or "", re.UNICODE))) > 1
                     else "all_terms_empty"))
            actions[-1]["query_mode"] = mode
            queries_run[query_key] = refs
            feedback = (f"Found {len(found)} card hints ({mode}) in chunks {leads(refs)}. "
                        "Cards are discovery leads, not citation evidence; "
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
            lead = candidates[ref].hit
            if any((item.hit.document_id, item.hit.run_id, item.hit.source_sha256) !=
                   (lead.document_id, lead.run_id, lead.source_sha256) for item in found):
                actions[-1]["rejected"] = "Document find changed source revision"
                return stopped("Document find provenance failed.", "tool_failed")
            actions[-1]["results"] = [{"rank": item.rank, "score": item.rrf_score,
                **item.hit.model_dump(exclude={"raw_text", "score", "rank"})} for item in found]
            try:
                refs = register(found)
            except (StorageError, psycopg.Error) as exc:
                actions[-1]["error_type"] = type(exc).__name__
                return stopped("Document find failed.", "tool_failed")
            feedback = (f"Found {len(found)} chunks in that document; best unread matches: {leads(refs)}. "
                        "Read one before citing.")
        elif action.action == "read":
            ref = action.reference_id or ""
            if ref not in candidates or reads >= limits.max_reads:
                feedback = f"Unknown reference or read budget exhausted. Unread candidates: {unread()[:8]}."
                actions[-1]["rejected"] = "Unknown reference or read budget"
                continue
            if ref in read_refs:
                feedback = (f"Chunk {ref} is already fully inspected; its passages are under `inspected`. "
                            f"Read a different chunk ({unread()[:8]}), search, or finish.")
                actions[-1]["rejected"] = "Already inspected chunk"
                continue
            # One read inspects every span of the chunk; each passage keeps its own citation ID.
            candidate = candidates[ref]
            hit = candidate.hit
            passages: dict[str, SourcePassage] = {}
            try:
                for span_index, span in enumerate(hit.source_spans):
                    offset = 0
                    while offset < span["end_char"] - span["start_char"]:
                        citation_id = f"{ref}.{span_index}" + (f"@{offset}" if offset else "")
                        passage = (retriever.read(candidate, span_index, offset=offset) if offset
                                   else retriever.read(candidate, span_index))
                        if (passage.document_id, passage.run_id, passage.source_sha256, passage.chunk_id) != (
                            hit.document_id, hit.run_id, hit.source_sha256, hit.chunk_id
                        ) or not (passage.start_char == span["start_char"] + offset < passage.end_char <= span["end_char"]) or (
                            len(passage.text) != passage.end_char - passage.start_char
                        ):
                            raise ValueError("source read changed candidate provenance")
                        offset += len(passage.text)
                        if passage.span_end_char is None:
                            passage = passage.model_copy(update={"span_end_char": span["end_char"],
                                "next_offset": offset if passage.end_char < span["end_char"] else None,
                                "truncated": passage.end_char < span["end_char"]})
                        passages[citation_id] = passage
            except (StorageError, ModelClientError, psycopg.Error) as exc:
                actions[-1]["error_type"] = type(exc).__name__
                return stopped("Original source read failed.", "tool_failed")
            except (IndexError, ValueError):
                actions[-1]["rejected"] = "Invalid span or changed provenance"
                feedback = f"Chunk {ref} could not be read from the published source. Choose another candidate."
                continue
            reads += 1
            inspected.update(passages)
            read_refs.add(ref)
            discoveries_since_read = 0
            actions[-1]["inspected_ids"] = list(passages)
            feedback = (f"Chunk {ref} inspected as {len(passages)} passages ({next(iter(passages), ref)} onward). "
                        "Cite the specific passage ID that states each claim.")
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
            # A source already in use often has more relevant chunks. Ask at most twice, and
            # only after new reading; a repeat finish without reading is accepted.
            used_documents = {candidates[ref].hit.document_id for ref in read_refs}
            same_source = [ref for ref in unread() if candidates[ref].hit.document_id in used_documents]
            if (same_source and not force_finish and reads < limits.max_reads
                    and calls < limits.max_model_calls and reads != reads_at_coverage_check
                    and coverage_checks < 2):
                reads_at_coverage_check = reads
                coverage_checks += 1
                actions[-1]["rejected"] = "Unread chunks remain in sources already used"
                unread_leads = [(ref, (facts.get(ref) or [candidates[ref].hit.raw_text[:120]])[0][:160])
                                for ref in same_source[:8]]
                feedback = (f"Before finishing, check these unread chunks from sources you already used: {unread_leads}. "
                            "Read each one that covers a material point your answer lacks, then finish. "
                            "If none adds anything, finish again without reading.")
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
            if action.completeness == "complete" and not inspected:
                feedback = "Complete answer requires inspected evidence."
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
                                host_search=host_search, elapsed_seconds=time.monotonic() - started)
    return stopped("Investigation budget ended before a valid grounded answer.", "exhausted")
