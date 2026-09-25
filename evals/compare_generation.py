"""Bounded, opt-in card and summary model trial on checked source revisions.

The committed code and console report contain IDs and metrics only. Generated
text and source snapshots remain in ignored local run directories.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from app.ingestion.enrichment import EvidenceCardsArtifact, _block_card_input, _group_card_input, _input, _prompt, extract_evidence_cards
from app.ingestion.run_manifest import RunManifest
from app.ingestion.summary import summarize_document
from app.models.client import ModelClientError, ModelSpec
from app.models.openrouter import OpenRouterClient
from evals.compare_chunking import CONTEXT_PRICES, Prepared, _load_key, prepare
from evals.compare_embeddings import PRIVACY, _select_live_manifest
from evals.trial_routes import provider_routes, verify_routes


# Preserve the original screen as the default; new candidates are selected
# explicitly so an omitted --models flag cannot silently broaden a live run.
MODELS = (
    "openai/gpt-5-mini", "openai/gpt-6-luna", "qwen/qwen3.5-9b",
    "qwen/qwen3.5-35b-a3b", "mistralai/mistral-small-3.2-24b-instruct",
    "openai/gpt-oss-20b",
)
SAMPLE_POSITIONS = (0, 0.25, 0.5, 0.75, 1)


def sample(prepared: list[Prepared]) -> tuple[list[tuple[Prepared, object]], list[Prepared]]:
    cards = []
    by_kind: dict[str, list[Prepared]] = {}
    for item in prepared:
        by_kind.setdefault(item.parsed.source_kind, []).append(item)
    for kind in ("note", "article", "highlights", "transcript"):
        items = sorted(by_kind.get(kind, []), key=lambda item: item.parsed.document_id)
        for item in items:
            chunks = item.sets["structural_raw"].chunks
            if kind in ("note", "article"):
                positions = range(len(chunks))
            elif kind == "highlights":
                positions = sorted({0, len(chunks) // 2, len(chunks) - 1})
            else:
                positions = sorted({round((len(chunks) - 1) * fraction) for fraction in SAMPLE_POSITIONS})
            cards.extend((item, chunks[position]) for position in positions)
    summaries = []
    for kind in ("note", "article", "transcript"):
        items = by_kind.get(kind, [])
        if items:
            summaries.append(min(items, key=lambda item: len(item.sets["structural_raw"].chunks)))
    return cards, summaries


def estimate(cards: list[tuple[Prepared, object]], summaries: list[Prepared],
             models: tuple[str, ...] = MODELS,
             card_prompt_version: str = "evidence_cards_v1",
             summary_prompt_family: str = "v1") -> dict:
    cards_prompt, _ = _prompt(card_prompt_version)
    cards_input_bound = sum(len(cards_prompt.encode()) + len(
        (_group_card_input(item.parsed, chunk, 1800)
         if card_prompt_version == "evidence_cards_v5" else
         _block_card_input(item.parsed, chunk, 1800,
                           strict_blocks=card_prompt_version == "evidence_cards_v4")
         if card_prompt_version in ("evidence_cards_v3", "evidence_cards_v4")
         else _input(item.parsed, chunk)).encode())
                            for item, chunk in cards)
    section_count = sum(len(item.sets["structural_raw"].chunks) for item in summaries)
    if summary_prompt_family in ("v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"):
        # v3 writes one bounded section summary per chunk and combines them
        # directly; four bytes per character bounds the final UTF-8 input.
        summary_calls_bound = section_count + len(summaries)
        summary_input_bound = (sum(len(chunk.raw_text.encode("utf-8")) + 1024
                                   for item in summaries
                                   for chunk in item.sets["structural_raw"].chunks)
                               + len(summaries) * (60_000 * 4 + 1024))
    else:
        # A hierarchy with groups of at most four has fewer than two nodes per leaf.
        summary_calls_bound = 2 * section_count
        summary_input_bound = summary_calls_bound * {"v1": 14_000, "v2": 24_000}[summary_prompt_family]
    per_model = {}
    for model in models:
        input_price, output_price = CONTEXT_PRICES[model]
        summary_output_bound = (1200 if summary_prompt_family == "v6" else
                                900 if summary_prompt_family in ("v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15") else
                                700 if summary_prompt_family in ("v2", "v3", "v4", "v5") else 400)
        summary_multiplier = 8 if model == "deepseek/deepseek-v4.1-flash" else 4
        cost = ((cards_input_bound + summary_input_bound) * input_price
                + (len(cards) * 700 * 4
                   + summary_calls_bound * summary_output_bound * summary_multiplier) * output_price) / 1_000_000
        per_model[model] = round(cost, 6)
    return {"card_chunks": len(cards), "summary_documents": len(summaries),
            "summary_section_count": section_count,
            "request_bound_per_model": 3 * (len(cards) + summary_calls_bound),
            "estimated_input_token_bound_per_model": 3 * (cards_input_bound + summary_input_bound),
            "conservative_usd_by_model": per_model,
            "conservative_total_usd": round(sum(per_model.values()), 6)}


class TimedClient:
    def __init__(self, client: OpenRouterClient) -> None:
        self.client = client
        self.latencies: list[float] = []

    @property
    def live_run_selection(self):
        return self.client.live_run_selection

    def generate_json(self, **kwargs):
        started = time.perf_counter()
        try:
            return self.client.generate_json(**kwargs)
        finally:
            self.latencies.append(time.perf_counter() - started)


def _model_report(prepared: list[Prepared], model: ModelSpec,
                  timed: TimedClient) -> dict:
    records = []
    card_count = 0
    for item in prepared:
        manifest = RunManifest.load(item.run_dir)
        for record in manifest.artifacts.values():
            if record.model != model or record.kind not in ("cards", "summary"):
                continue
            records.append(record)
            if record.kind == "cards" and record.status == "ready" and record.path:
                artifact = EvidenceCardsArtifact.model_validate_json(
                    (item.run_dir / record.path).read_text(encoding="utf-8")
                )
                card_count += len(artifact.cards)
    ready = [record for record in records if record.status == "ready"]
    input_tokens = sum(usage.input_tokens or 0 for usage in timed.client.billable_usages)
    output_tokens = sum(usage.output_tokens or 0 for usage in timed.client.billable_usages)
    input_price, output_price = CONTEXT_PRICES[model.model_id]
    latencies = timed.latencies
    return {
        "model": model.model_id,
        "ready_cards": sum(record.kind == "cards" for record in ready),
        "returned_valid_cards": card_count,
        "ready_summaries": sum(record.kind == "summary" for record in ready),
        "first_pass_ready_artifacts": sum(record.attempts == 1 for record in ready),
        "failed": [{"artifact_id": record.artifact_id, "kind": record.kind,
                    "error_code": record.error_code} for record in records if record.status == "failed"],
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "estimated_usd": round((input_tokens * input_price + output_tokens * output_price) / 1_000_000, 6),
        "fresh_request_latency_p50_seconds": round(sorted(latencies)[len(latencies) // 2], 3) if latencies else None,
        "fresh_request_latency_p95_seconds": round(sorted(latencies)[min(len(latencies) - 1, math.ceil(len(latencies) * 0.95) - 1)], 3) if latencies else None,
    }


def run(prepared: list[Prepared], cards: list[tuple[Prepared, object]],
        summaries: list[Prepared], cap: float, output: Path,
        models: tuple[str, ...] = MODELS,
        card_prompt_version: str = "evidence_cards_v1",
        summary_prompt_family: str = "v1") -> dict:
    plan = estimate(cards, summaries, models, card_prompt_version, summary_prompt_family)
    if cap <= 0 or plan["conservative_total_usd"] > cap:
        raise ValueError("conservative generation estimate exceeds the spend cap")
    _load_key()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"sample": {"cards": [{"document_id": item.parsed.document_id,
                                     "chunk_id": chunk.chunk_id} for item, chunk in cards],
                         "summaries": [item.parsed.document_id for item in summaries]},
              "estimate": plan, "spend_cap_usd": cap, "privacy_route": PRIVACY,
              "card_prompt_version": card_prompt_version,
              "summary_prompt_family": summary_prompt_family,
              "models": []}
    for model_id in models:
        model = ModelSpec(provider="openrouter", model_id=model_id,
                          model_version="trial-2026-09-24")
        client = TimedClient(OpenRouterClient(
            allow_live=True, request_budget=plan["request_bound_per_model"],
            estimated_input_token_budget=plan["estimated_input_token_bound_per_model"],
            provider=PRIVACY, model_providers=provider_routes([model_id]),
        ))
        routes = verify_routes(client.client, [model_id], structured_ids={model_id})
        report.setdefault("eligible_zdr_routes", {}).update(routes)
        manifests = {item.parsed.run_id: _select_live_manifest(item.run_dir, client)
                     for item in prepared}
        errors = []
        for item, chunk in cards:
            try:
                extract_evidence_cards(item.parsed, item.sets["structural_raw"], chunk,
                                       client, model, manifests[item.parsed.run_id], item.run_dir,
                                       prompt_version=card_prompt_version)
            except Exception as exc:
                errors.append({"chunk_id": chunk.chunk_id, "error_class": type(exc).__name__,
                               "transport_error": str(exc) if isinstance(exc, ModelClientError) else None})
        for item in summaries:
            try:
                summarize_document(item.parsed, item.sets["structural_raw"], client,
                                   model, manifests[item.parsed.run_id], item.run_dir,
                                   prompt_family=summary_prompt_family)
            except Exception as exc:
                errors.append({"document_id": item.parsed.document_id,
                               "error_class": type(exc).__name__,
                               "transport_error": str(exc) if isinstance(exc, ModelClientError) else None})
        report["models"].append({"status": "complete" if not errors else "complete_with_failures",
                                 "sample_errors": errors,
                                 **_model_report(prepared, model, client)})
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--root-alias", default="primary")
    parser.add_argument("--eval-dir", type=Path, default=Path("evals"))
    parser.add_argument("--runtime-dir", type=Path, default=Path(".local/stage4-generation/runs"))
    parser.add_argument("--output", type=Path, default=Path(".local/stage4-generation/report.json"))
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--models", nargs="+", choices=tuple(CONTEXT_PRICES), default=list(MODELS))
    parser.add_argument("--card-prompt-version", choices=("evidence_cards_v1", "evidence_cards_v2", "evidence_cards_v3", "evidence_cards_v4", "evidence_cards_v5"),
                        default="evidence_cards_v1")
    parser.add_argument("--summary-prompt-family", choices=("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11", "v12", "v13", "v14", "v15"), default="v1")
    parser.add_argument("--all-cards", action="store_true")
    parser.add_argument("--all-summaries", action="store_true")
    parser.add_argument("--cards-only", action="store_true")
    parser.add_argument("--summaries-only", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    prepared = prepare(args.source_root, args.eval_dir, args.runtime_dir, args.root_alias,
                       resume=args.resume)
    cards, summaries = sample(prepared)
    if args.all_cards:
        cards = [(item, chunk) for item in prepared
                 for chunk in item.sets["structural_raw"].chunks]
    if args.all_summaries:
        summaries = prepared
    if args.cards_only and args.summaries_only:
        parser.error("choose only one of --cards-only or --summaries-only")
    if args.cards_only:
        summaries = []
    if args.summaries_only:
        cards = []
    plan = estimate(cards, summaries, tuple(args.models), args.card_prompt_version,
                    args.summary_prompt_family)
    if not args.live:
        print(json.dumps(plan, indent=2))
        return 0
    report = run(prepared, cards, summaries, args.max_usd, args.output,
                 tuple(args.models), args.card_prompt_version,
                 args.summary_prompt_family)
    print(json.dumps({"report_path": str(args.output),
                      "models": [{"model": item["model"], "status": item["status"]}
                                 for item in report["models"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
