"""Local, opt-in question command over published stored sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

from app.agent.harness import answer_question, PROMPT, PROMPT_PATH, PROMPT_VERSION
from app.agent.actions import ACTION_SCHEMA_V2
from app.agent.records import AnswerLimits
from app.models.client import ModelClientError, ModelSpec
from app.models.openrouter import OpenRouterClient
from app.storage import StorageError
from app.storage.loader import connect_runtime
from app.storage.retrieval import DatabaseRetriever, _price_ok, selected_embedding_spec


def _positive_decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("expected a positive decimal") from None
    if not number.is_finite() or number <= 0:
        raise argparse.ArgumentTypeError("expected a positive decimal")
    return number


def _check_generation_price(pricing: object, ceiling: Decimal) -> tuple[Decimal, Decimal]:
    _price_ok(pricing, ceiling)
    try:
        output = Decimal(str(pricing["completion"]))
        prompt = Decimal(str(pricing["prompt"]))
    except (KeyError, TypeError, InvalidOperation):
        raise ModelClientError("generation endpoint has no valid output price") from None
    if not output.is_finite() or output < 0 or output * 1_000_000 > ceiling:
        raise ModelClientError("generation endpoint exceeds price ceiling")
    return prompt, output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask a question of the selected private index")
    parser.add_argument("question")
    parser.add_argument("--live", action="store_true", help="explicitly allow private model calls")
    parser.add_argument("--dry-run", action="store_true", help="validate configuration and price bounds without generation or embeddings")
    parser.add_argument("--embedding-provider-tag", required=True)
    parser.add_argument("--generation-provider-tag", required=True)
    parser.add_argument("--max-embedding-usd-per-million", type=_positive_decimal, required=True)
    parser.add_argument("--max-generation-usd-per-million", type=_positive_decimal, required=True)
    parser.add_argument("--max-usd", type=_positive_decimal, required=True)
    parser.add_argument("--budget-profile", choices=("compact", "broad"), default="compact",
                        help="finite investigation allowances; broad permits more search, find, reads, and synthesis")
    parser.add_argument("--request-budget", type=int)
    parser.add_argument("--input-byte-budget", type=int,
                        help="conservative input allowance: request UTF-8 bytes plus 512 tokens per request")
    parser.add_argument("--model", default="openai/gpt-5-mini")
    parser.add_argument("--reasoning-effort", choices=("none", "minimal", "low", "medium", "high"), default="minimal")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report", type=Path, help="explicit private report under .local/")
    args = parser.parse_args(argv)
    limits = AnswerLimits.broad() if args.budget_profile == "broad" else AnswerLimits()
    if args.request_budget is None:
        args.request_budget = 28 if args.budget_profile == "broad" else 18
    if args.input_byte_budget is None:
        args.input_byte_budget = 700000 if args.budget_profile == "broad" else 400000
    if not args.question.strip():
        parser.error("question must not be empty")
    if args.reasoning_effort != "none" and not args.model.startswith("openai/"):
        parser.error("this model requires --reasoning-effort none")
    if (not args.live and not args.dry_run) or args.request_budget < 1 or args.input_byte_budget < 1:
        parser.error("answering requires --live or --dry-run and positive request/input budgets")
    if args.report is not None:
        private_root = (Path.cwd() / ".local").resolve()
        report = args.report.resolve()
        if not report.is_relative_to(private_root):
            parser.error("private reports must be under .local/")
        if report.exists():
            parser.error("report already exists; choose a distinct private report path")
    client = None
    metadata = None
    try:
        with connect_runtime() as conn, conn.transaction():
            conn.execute("set transaction isolation level repeatable read read only")
            sid, spec = selected_embedding_spec(conn)
            private_route = {"only": [args.embedding_provider_tag], "allow_fallbacks": False,
                             "data_collection": "deny", "zdr": True}
            generation_route = {**private_route, "only": [args.generation_provider_tag], "require_parameters": True}
            client = OpenRouterClient(allow_live=args.live and not args.dry_run, request_budget=args.request_budget,
                                      estimated_input_token_budget=args.input_byte_budget,
                                      conservative_input_budget=True,
                                      capture_failed_outputs=args.report is not None,
                                      provider=private_route,
                                      model_providers={args.model: generation_route})
            embedding_endpoint = client.require_zdr_route(spec.model, args.embedding_provider_tag)
            _price_ok(embedding_endpoint["pricing"], args.max_embedding_usd_per_million)
            generation_endpoint = client.require_zdr_route(args.model, args.generation_provider_tag,
                                                           structured=True)
            prompt_price, completion_price = _check_generation_price(
                generation_endpoint["pricing"], args.max_generation_usd_per_million)
            if args.reasoning_effort != "none" and "reasoning" not in generation_endpoint["supported_parameters"]:
                raise ModelClientError("generation endpoint lacks reasoning support")
            # Charge the entire allowed input budget at the dearer input price,
            # plus every allowed request at the completion-token bound.
            upper_bound = (Decimal(args.input_byte_budget) * max(
                prompt_price, Decimal(str(embedding_endpoint["pricing"]["prompt"])))
                + Decimal(args.request_budget * limits.max_output_tokens * 4) * completion_price)
            if upper_bound > args.max_usd:
                raise ModelClientError("configured worst-case spend exceeds --max-usd")
            hashes = dict(conn.execute("""select r.document_id,r.source_sha256 from knowledge.runs r
                join knowledge.active_runs ar on ar.run_id=r.run_id
                join knowledge.corpus_state state on state.profile_id=ar.profile_id""").fetchall())
            metadata = {"model": args.model, "reasoning_effort": args.reasoning_effort,
                        "generation_route": generation_endpoint, "embedding_route": embedding_endpoint,
                        "embedding_spec_id": sid, "source_revisions": hashes,
                        "prompt_version": PROMPT_VERSION, "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
                        "prompt_file_sha256": hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest(),
                        "action_schema_version": 2,
                        "action_schema_sha256": hashlib.sha256(json.dumps(ACTION_SCHEMA_V2, sort_keys=True).encode()).hexdigest(),
                        "budget_profile": args.budget_profile,
                        "limits": limits.model_dump(), "request_budget": args.request_budget,
                        "input_byte_budget": args.input_byte_budget, "input_budget_mode": "utf8_bytes_plus_512_per_request",
                        "max_usd": str(args.max_usd),
                        "worst_case_usd": str(upper_bound)}
            if args.dry_run:
                value = {"status": "dry_run", "configuration": metadata}
            else:
                retriever = DatabaseRetriever(conn, client, provider_tag=args.embedding_provider_tag,
                                              max_embedding_usd_per_million=args.max_embedding_usd_per_million)
                result = answer_question(args.question, retriever=retriever, client=client,
                                         model=ModelSpec(provider="openrouter", model_id=args.model,
                                                         model_version="current"),
                                         limits=limits, reasoning_effort=None if args.reasoning_effort == "none" else args.reasoning_effort)
                prices = {args.model: (prompt_price, completion_price),
                          spec.model: (Decimal(str(embedding_endpoint["pricing"]["prompt"])), Decimal(0))}
                known_usage = len(client.request_timings) == len(client.billable_requests) and all(usage.input_tokens is not None and (
                    model_id == spec.model or usage.output_tokens is not None)
                    for model_id, usage in client.billable_requests)
                measured = sum(Decimal(usage.input_tokens or 0) * prices[model_id][0]
                               + Decimal(usage.output_tokens or 0) * prices[model_id][1]
                               for model_id, usage in client.billable_requests)
                value = {"status": "completed" if result.termination == "completed" and known_usage else "failed", "configuration": metadata,
                         "result": result.model_dump(mode="json"), "usage_complete": known_usage,
                         "estimated_usd": str(measured) if known_usage else None,
                         "billable_requests": [{"model": model_id, "usage": usage.model_dump()}
                                               for model_id, usage in client.billable_requests],
                         "request_timings": client.request_timings,
                         "failed_model_outputs": client.failed_outputs if args.report is not None else []}
        if args.report is not None:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json or args.dry_run:
            print(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            print(result.answer)
            for ref, passage in result.citations.items():
                print(f"\n[{ref}] {passage.root_alias}/{passage.relative_path} "
                      f"({passage.start_char}:{passage.end_char})\n{passage.text[:420]}")
        return 0 if value["status"] != "failed" else 1
    except (StorageError, ModelClientError, psycopg.Error, OSError, ValueError, KeyboardInterrupt) as exc:
        if args.report is not None:
            failure = {"status": "failed", "configuration": metadata, "error_type": type(exc).__name__,
                       "usage_complete": False, "estimated_usd": None,
                       "failed_model_outputs": [] if client is None else client.failed_outputs,
                       "billable_requests": [] if client is None else [
                           {"model": model_id, "usage": usage.model_dump()}
                           for model_id, usage in client.billable_requests]}
            try:
                report.parent.mkdir(parents=True, exist_ok=True)
                with report.open("x", encoding="utf-8") as output:
                    json.dump(failure, output, ensure_ascii=False, indent=2)
            except OSError:
                pass
        safe_messages = {
            "KNOWLEDGE_DATABASE_URL is required": "Set KNOWLEDGE_DATABASE_URL to the private runtime connection.",
            "OPENROUTER_API_KEY is not configured": "Set OPENROUTER_API_KEY before endpoint checks or live calls.",
            "no search profile is selected": "Publish and select a complete search profile before answering.",
            "configured worst-case spend exceeds --max-usd": "The input/request budgets exceed the spending cap; reduce budgets or choose an approved cap.",
        }
        message = safe_messages.get(str(exc),
            "Check the database connection, pinned model routes, budgets, and the requested private report.")
        print(f"answer failed: {type(exc).__name__}. {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
