"""Opt-in, bounded local enrichment for checked source runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from app.ingestion.embeddings import EmbeddingSpec, embed_chunks
from app.ingestion.enrichment import _block_card_input, _group_card_input, _input, _prompt, extract_evidence_cards, generate_chunk_context
from app.ingestion.run_manifest import RunManifest
from app.ingestion.summary import summarize_document
from app.models.client import ModelSpec
from app.models.openrouter import OpenRouterClient
from app.storage.loader import connect_runtime, load_run, validate_run
from app.storage.publication import create_profile, publish_run, select_profile
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from app.storage.search import search_vector
from evals.compare_chunking import CONTEXT_PRICES, Prepared, _load_database_url, _load_key, prepare
from evals.compare_embeddings import PRIVACY, _select_live_manifest
from evals.trial_routes import provider_routes, verify_routes


EMBEDDING_MODEL = "perplexity/pplx-embed-v1-0.6b"
EMBEDDING_PRICE = 0.004
CONTEXT_PROMPT = "chunk_context_v2"


def _record_spend(report: dict, client: OpenRouterClient) -> None:
    report["billable_requests"] = len(client.billable_requests)
    report["rate_limit_retries"] = client.rate_limit_retries
    report["measured_usd"] = round(sum(
        ((usage.input_tokens or 0) * CONTEXT_PRICES.get(model_id, (EMBEDDING_PRICE, 0))[0]
         + (usage.output_tokens or 0) * CONTEXT_PRICES.get(model_id, (0, 0))[1])
        / 1_000_000 for model_id, usage in client.billable_requests
    ), 6)
    report["model_latency"] = {}
    for model_id in sorted({model for model, _ in client.request_timings}):
        samples = sorted(seconds for model, seconds in client.request_timings if model == model_id)
        report["model_latency"][model_id] = {
            "requests": len(samples),
            "p50_seconds": round(samples[(len(samples) - 1) // 2], 3),
            "p95_seconds": round(samples[min(len(samples) - 1, math.ceil(len(samples) * 0.95) - 1)], 3),
        }


def estimate(prepared: list[Prepared], context_id: str | None, cards_id: str,
             summary_id: str | None, contextual: bool,
             card_prompt_version: str = "evidence_cards_v5",
             summary_prompt_family: str = "v1") -> dict:
    if contextual != (context_id is not None):
        raise ValueError("context model is required only for contextual embeddings")
    context_prompt = _prompt(CONTEXT_PROMPT)[0] if contextual else ""
    card_prompt, _ = _prompt(card_prompt_version)
    chunks = [(item, chunk) for item in prepared for chunk in item.sets["structural_raw"].chunks]
    context_input = sum(len(context_prompt.encode()) + len(_input(item.parsed, chunk).encode())
                        for item, chunk in chunks) if contextual else 0
    card_input = sum(len(card_prompt.encode()) + len(
        (_group_card_input(item.parsed, chunk, 1800)
         if card_prompt_version == "evidence_cards_v5" else
         _block_card_input(item.parsed, chunk, 1800,
                           strict_blocks=card_prompt_version == "evidence_cards_v4")
         if card_prompt_version in ("evidence_cards_v3", "evidence_cards_v4", "evidence_cards_v5")
         else _input(item.parsed, chunk)).encode())
                     for item, chunk in chunks)
    if summary_id is None:
        summary_calls = 0
        summary_input = 0
    elif summary_prompt_family in ("v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17"):
        summary_calls = len(chunks) + len(prepared)
        summary_input = (sum(len(chunk.raw_text.encode("utf-8")) + 1024
                             for _, chunk in chunks)
                         + len(prepared) * (60_000 * 4 + 1024))
    else:
        summary_calls = 2 * len(chunks)
        summary_input = summary_calls * {"v1": 14_000, "v2": 24_000}[summary_prompt_family]
    embed_input = sum(len(chunk.raw_text.encode()) + (360 * 16 if contextual else 0)
                      for _, chunk in chunks)
    input_total = context_input + card_input + summary_input + embed_input
    output_context = len(chunks) * 360 * 4 if contextual else 0
    output_cards = len(chunks) * 700 * 4
    output_summary = summary_calls * (1200 if summary_prompt_family == "v6" else
                                      900 if summary_prompt_family in ("v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17") else
                                      700 if summary_prompt_family in ("v2", "v3", "v4", "v5") else 400) * 4
    ci, co = CONTEXT_PRICES[context_id] if context_id else (0, 0)
    ki, ko = CONTEXT_PRICES[cards_id]
    si, so = CONTEXT_PRICES[summary_id] if summary_id else (0, 0)
    cost = ((context_input * ci + output_context * co
             + card_input * ki + output_cards * ko
             + summary_input * si + output_summary * so
             + embed_input * EMBEDDING_PRICE) / 1_000_000)
    return {
        "documents": len(prepared), "structural_chunks": len(chunks),
        "request_bound": 1 + len(chunks) * (1 + int(contextual)) + summary_calls + sum(
            math.ceil(len(item.sets["structural_raw"].chunks) / 16) for item in prepared),
        "estimated_input_token_bound": input_total,
        "conservative_usd": round(cost, 6),
    }


def complete(prepared: list[Prepared], context_id: str | None, cards_id: str,
             summary_id: str | None, contextual: bool, cap: float, output: Path,
             *, card_prompt_version: str = "evidence_cards_v5",
             summary_prompt_family: str = "v1",
             request_limit: int | None = None) -> dict:
    plan = estimate(prepared, context_id, cards_id, summary_id, contextual,
                    card_prompt_version, summary_prompt_family)
    if cap <= 0 or plan["conservative_usd"] > cap:
        raise ValueError("conservative complete-run estimate exceeds spend cap")
    if request_limit is not None and request_limit < 1:
        raise ValueError("request limit must be positive")
    _load_key()
    _load_database_url()
    models = sorted({model_id for model_id in (context_id, cards_id, summary_id, EMBEDDING_MODEL)
                     if model_id is not None})
    request_budget = min(plan["request_bound"], request_limit) if request_limit else plan["request_bound"]
    client = OpenRouterClient(
        allow_live=True, request_budget=request_budget,
        estimated_input_token_budget=plan["estimated_input_token_bound"],
        provider=PRIVACY, model_providers=provider_routes(models),
    )
    routes = verify_routes(client, models,
                           structured_ids={model_id for model_id in (context_id, cards_id, summary_id)
                                           if model_id is not None})
    context_model = (ModelSpec(provider="openrouter", model_id=context_id,
                               model_version="accepted-2026-09-24")
                     if context_id else None)
    cards_model = ModelSpec(provider="openrouter", model_id=cards_id,
                            model_version="accepted-2026-09-24")
    summary_model = (ModelSpec(provider="openrouter", model_id=summary_id,
                               model_version="accepted-2026-09-24")
                     if summary_id else None)
    report = {"estimate": plan, "spend_cap_usd": cap, "privacy_route": PRIVACY,
              "eligible_zdr_routes": routes,
              "models": {"context": context_id, "cards": cards_id,
                         "summary": summary_id, "embedding": EMBEDDING_MODEL},
              "card_prompt_version": card_prompt_version,
              "summary_prompt_family": summary_prompt_family if summary_model else None,
              "request_budget": request_budget,
              "embedding_recipe": "contextualized-v1" if contextual else "raw-v1",
              "runs": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    for item in prepared:
        manifest = _select_live_manifest(item.run_dir, client)
        if summary_model is None and any(record.kind == "summary" and record.status == "ready"
                                         for record in manifest.artifacts.values()):
            raise ValueError("summary-free run contains a ready summary artifact")
        if context_model is None and any(record.kind == "context" and record.status == "ready"
                                         for record in manifest.artifacts.values()):
            raise ValueError("raw run contains a ready context artifact")
        chunks = item.sets["structural_raw"]
        entry = {"document_id": item.parsed.document_id, "run_id": item.parsed.run_id,
                 "source_sha256": item.parsed.source_sha256, "status": "pending"}
        report["runs"].append(entry)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        try:
            contexts = {}
            if context_model:
                for chunk in chunks.chunks:
                    contexts[chunk.chunk_id] = generate_chunk_context(
                        item.parsed, chunks, chunk, client, context_model, manifest,
                        item.run_dir, prompt_version=CONTEXT_PROMPT,
                    )
            for chunk in chunks.chunks:
                extract_evidence_cards(item.parsed, chunks, chunk, client,
                                       cards_model, manifest, item.run_dir,
                                       prompt_version=card_prompt_version)
            if summary_model:
                progress = summarize_document(item.parsed, chunks, client, summary_model,
                                              manifest, item.run_dir,
                                              prompt_family=summary_prompt_family)
                if progress.document_summary is None:
                    raise ValueError("document summary did not complete")
            spec = EmbeddingSpec(
                provider="openrouter", model=EMBEDDING_MODEL,
                model_version="accepted-2026-09-24", dimensions=1024,
                distance_metric="cosine",
                input_recipe_version="contextualized-v1" if contextual else "raw-v1",
                context_prompt_version=CONTEXT_PROMPT if contextual else None,
                context_model=context_model if contextual else None,
                document_input_type="search_document", query_input_type="search_query",
                chunking_version=chunks.chunking_version,
            )
            embeddings = embed_chunks(
                item.parsed, chunks, client, spec, manifest, item.run_dir,
                contexts=contexts if contextual else None,
            )
            if not embeddings.ready:
                raise ValueError("embedding batch did not complete")
            entry.update(status="ready", context_count=len(contexts),
                         card_artifact_count=sum(record.kind == "cards" and record.status == "ready"
                                                 and record.model == cards_model
                                                 for record in manifest.artifacts.values()),
                         summary_count=sum(record.kind == "summary" and record.status == "ready"
                                           and record.model == summary_model
                                           for record in manifest.artifacts.values()) if summary_model else 0,
                         embedded_chunks=embeddings.completed_chunks)
        except Exception as exc:
            entry.update(status="failed", error_class=type(exc).__name__)
            _record_spend(report, client)
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            raise
        _record_spend(report, client)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for item in prepared:
        validate_run(item.run_dir)
    with connect_runtime() as conn:
        for item in prepared:
            load_run(conn, item.run_dir)
        selected_spec = EmbeddingSpec(
            provider="openrouter", model=EMBEDDING_MODEL,
            model_version="accepted-2026-09-24", dimensions=1024,
            distance_metric="cosine",
            input_recipe_version="contextualized-v1" if contextual else "raw-v1",
            context_prompt_version=CONTEXT_PROMPT if contextual else None,
            context_model=context_model if contextual else None,
            document_input_type="search_document", query_input_type="search_query",
            chunking_version=prepared[0].sets["structural_raw"].chunking_version,
        )
        profile_id = "stage4-" + hashlib.sha256(
            (spec_id(selected_spec) + cards_id + (summary_id or "no-summary")
             + card_prompt_version + (summary_prompt_family if summary_id else "")).encode()
        ).hexdigest()[:12]
        create_profile(conn, SearchProfile(
            profile_id=profile_id, variant="structural_raw",
            chunking_version=selected_spec.chunking_version,
            embedding_spec_id=spec_id(selected_spec),
            required_artifact_kinds=(["context"] if contextual else []) + ["cards"]
                                    + (["summary"] if summary_model else []),
        ))
        for item in prepared:
            manifest = RunManifest.load(item.run_dir)
            required_kinds = {"cards"} | ({"context"} if contextual else set())
            if summary_model:
                required_kinds.add("summary")
            required_ids = [record.artifact_id for record in manifest.artifacts.values()
                            if record.kind in required_kinds and record.status == "ready"]
            publish_run(conn, PublicationSelection(
                run_id=item.parsed.run_id, profile_id=profile_id,
                required_artifact_ids=required_ids,
            ))
        checked_queries = [
            (item, next(q for q in item.questions if q["expected"] == "supported"))
            for item in prepared[:3]
        ]
        response = client.embed(
            model=selected_spec.model_spec(),
            texts=[question["question"] for _, question in checked_queries], dimensions=1024,
            input_type="search_query",
        )
        select_profile(conn, profile_id)
        by_run = {item.parsed.run_id: item for item in prepared}
        query_proofs = []
        for (item, question), vector in zip(checked_queries, response.vectors, strict=True):
            hits = search_vector(conn, vector, spec_id=spec_id(selected_spec),
                                 metric="cosine", k=5, profile_id=profile_id)
            resolved = 0
            for hit in hits:
                matched = by_run[hit.run_id]
                source = matched.parsed.source_text
                source_bytes = (matched.run_dir / "source.md").read_bytes()
                if hit.source_sha256 != matched.parsed.source_sha256 or hashlib.sha256(source_bytes).hexdigest() != hit.source_sha256:
                    raise ValueError("search hit source revision mismatch")
                for span in hit.source_spans:
                    start, end = span["start_char"], span["end_char"]
                    if not (0 <= start < end <= len(source) and source[start:end].strip()):
                        raise ValueError("search hit source span could not be resolved")
                    byte_start = len(source[:start].encode("utf-8"))
                    byte_end = len(source[:end].encode("utf-8"))
                    if source_bytes[byte_start:byte_end] != source[start:end].encode("utf-8"):
                        raise ValueError("search hit span differs from captured source bytes")
                    resolved += 1
            if not any(hit.document_id == item.parsed.document_id for hit in hits) or not resolved:
                raise ValueError("checked query did not retrieve its source with resolvable spans")
            query_proofs.append({"question_id": question["id"],
                                 "expected_document_id": item.parsed.document_id,
                                 "search_hits": len(hits),
                                 "expected_document_hits": sum(hit.document_id == item.parsed.document_id for hit in hits),
                                 "resolved_source_spans": resolved})
        report["publication"] = {"profile_id": profile_id,
                                 "published_runs": len(prepared),
                                 "checked_queries": query_proofs,
                                 "search_hits": sum(proof["search_hits"] for proof in query_proofs),
                                 "resolved_source_spans": sum(proof["resolved_source_spans"] for proof in query_proofs)}
        _record_spend(report, client)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--root-alias", default="primary")
    parser.add_argument("--eval-dir", type=Path, default=Path("evals"))
    parser.add_argument("--runtime-dir", type=Path, default=Path(".local/stage4-complete/runs"))
    parser.add_argument("--output", type=Path, default=Path(".local/stage4-complete/report.json"))
    parser.add_argument("--context-model", choices=sorted(CONTEXT_PRICES))
    parser.add_argument("--cards-model", choices=sorted(CONTEXT_PRICES), required=True)
    parser.add_argument("--summary-model", choices=sorted(CONTEXT_PRICES))
    parser.add_argument("--contextual-embeddings", action="store_true")
    parser.add_argument("--card-prompt-version", choices=("evidence_cards_v1", "evidence_cards_v2", "evidence_cards_v3", "evidence_cards_v4", "evidence_cards_v5"),
                        default="evidence_cards_v5")
    parser.add_argument("--summary-prompt-family", choices=("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15", "v16", "v17"), default="v1")
    parser.add_argument("--request-limit", type=int)
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.contextual_embeddings != (args.context_model is not None):
        parser.error("--context-model is required only with --contextual-embeddings")
    prepared = prepare(args.source_root, args.eval_dir, args.runtime_dir, args.root_alias,
                       resume=args.resume)
    plan = estimate(prepared, args.context_model, args.cards_model,
                    args.summary_model, args.contextual_embeddings,
                    args.card_prompt_version, args.summary_prompt_family)
    if not args.live:
        print(json.dumps(plan, indent=2))
        return 0
    report = complete(prepared, args.context_model, args.cards_model,
                      args.summary_model, args.contextual_embeddings,
                      args.max_usd, args.output,
                      card_prompt_version=args.card_prompt_version,
                      summary_prompt_family=args.summary_prompt_family,
                      request_limit=args.request_limit)
    print(json.dumps({"report_path": str(args.output),
                      "ready_runs": sum(item["status"] == "ready" for item in report["runs"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
