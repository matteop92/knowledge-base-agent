"""Bounded, opt-in summary model trial on checked source revisions."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from app.ingestion.contracts import ChunkSet, ParsedDocument
from app.ingestion.run_manifest import RunManifest
from app.ingestion.summary import summarize_document
from app.models.client import ModelClientError, ModelSpec
from app.models.openrouter import OpenRouterClient
from evals.compare_chunking import CONTEXT_PRICES, Prepared, _load_key, prepare
from evals.compare_embeddings import PRIVACY, _select_live_manifest
from evals.trial_routes import provider_routes, verify_routes

MODELS = (
    "openai/gpt-5-mini", "openai/gpt-6-luna", "qwen/qwen3.5-9b",
    "qwen/qwen3.5-35b-a3b", "mistralai/mistral-small-3.2-24b-instruct",
    "openai/gpt-oss-20b",
)


def sample(prepared: list[Prepared]) -> list[Prepared]:
    by_kind: dict[str, list[Prepared]] = {}
    for item in prepared:
        by_kind.setdefault(item.parsed.source_kind, []).append(item)
    return [min(by_kind[kind], key=lambda item: len(item.sets["structural_raw"].chunks))
            for kind in ("note", "article", "transcript") if by_kind.get(kind)]


def prepared_from_runs(runtime_dir: Path) -> list[Prepared]:
    """Use captured, versioned local runs without recapturing the external vault."""
    prepared = []
    for run_dir in sorted(runtime_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        parsed = ParsedDocument.model_validate_json((run_dir / "parsed.json").read_text(encoding="utf-8"))
        sets = {variant: ChunkSet.model_validate_json((run_dir / f"{variant}.json").read_text(encoding="utf-8"))
                for variant in ("fixed_raw", "structural_raw")}
        manifest = RunManifest.load(run_dir)
        if (manifest.run_id, manifest.document_id, manifest.source_sha256) != (
                parsed.run_id, parsed.document_id, parsed.source_sha256):
            raise ValueError("captured run identity changed")
        prepared.append(Prepared(run_dir, parsed, sets, [], {}))
    if len(prepared) != 6:
        raise ValueError("summary comparison requires the six captured local runs")
    return prepared


def estimate(summaries: list[Prepared], models: tuple[str, ...] = MODELS,
             summary_prompt_family: str = "v1") -> dict:
    section_count = sum(len(item.sets["structural_raw"].chunks) for item in summaries)
    if summary_prompt_family in tuple(f"v{i}" for i in range(3, 18)):
        calls = section_count + len(summaries)
        input_bound = (sum(len(chunk.raw_text.encode("utf-8")) + 1024
                           for item in summaries for chunk in item.sets["structural_raw"].chunks)
                       + len(summaries) * (60_000 * 4 + 1024))
    else:
        calls = 2 * section_count
        input_bound = calls * {"v1": 14_000, "v2": 24_000}[summary_prompt_family]
    per_model = {}
    for model in models:
        input_price, output_price = CONTEXT_PRICES[model]
        output_bound = (1200 if summary_prompt_family == "v6" else
                        900 if summary_prompt_family in tuple(f"v{i}" for i in range(7, 18)) else
                        700 if summary_prompt_family in ("v2", "v3", "v4", "v5") else 400)
        multiplier = 8 if model == "deepseek/deepseek-v4.1-flash" else 4
        per_model[model] = round((input_bound * input_price + calls * output_bound * multiplier * output_price)
                                 / 1_000_000, 6)
    return {"summary_documents": len(summaries), "summary_section_count": section_count,
            "request_bound_per_model": 3 * calls,
            "estimated_input_token_bound_per_model": 3 * input_bound,
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


def _model_report(prepared: list[Prepared], model: ModelSpec, timed: TimedClient) -> dict:
    records = [record for item in prepared for record in RunManifest.load(item.run_dir).artifacts.values()
               if record.model == model and record.kind == "summary"]
    ready = [record for record in records if record.status == "ready"]
    input_tokens = sum(usage.input_tokens or 0 for usage in timed.client.billable_usages)
    output_tokens = sum(usage.output_tokens or 0 for usage in timed.client.billable_usages)
    input_price, output_price = CONTEXT_PRICES[model.model_id]
    latencies = sorted(timed.latencies)
    return {"model": model.model_id, "ready_summaries": len(ready),
            "first_pass_ready_artifacts": sum(record.attempts == 1 for record in ready),
            "failed": [{"artifact_id": record.artifact_id, "kind": record.kind,
                        "error_code": record.error_code} for record in records if record.status == "failed"],
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "estimated_usd": round((input_tokens * input_price + output_tokens * output_price) / 1_000_000, 6),
            "fresh_request_latency_p50_seconds": round(latencies[len(latencies) // 2], 3) if latencies else None,
            "fresh_request_latency_p95_seconds": round(latencies[min(len(latencies) - 1, math.ceil(len(latencies) * 0.95) - 1)], 3) if latencies else None}


def run(prepared: list[Prepared], summaries: list[Prepared], cap: float, output: Path,
        models: tuple[str, ...] = MODELS, summary_prompt_family: str = "v1") -> dict:
    plan = estimate(summaries, models, summary_prompt_family)
    if cap <= 0 or plan["conservative_total_usd"] > cap:
        raise ValueError("conservative generation estimate exceeds the spend cap")
    _load_key()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"sample": {"summaries": [item.parsed.document_id for item in summaries]},
              "estimate": plan, "spend_cap_usd": cap, "privacy_route": PRIVACY,
              "summary_prompt_family": summary_prompt_family, "models": []}
    for model_id in models:
        model = ModelSpec(provider="openrouter", model_id=model_id, model_version="trial-2026-09-24")
        client = TimedClient(OpenRouterClient(
            allow_live=True, request_budget=plan["request_bound_per_model"],
            estimated_input_token_budget=plan["estimated_input_token_bound_per_model"],
            provider=PRIVACY, model_providers=provider_routes([model_id])))
        report.setdefault("eligible_zdr_routes", {}).update(verify_routes(client.client, [model_id], structured_ids={model_id}))
        manifests = {item.parsed.run_id: _select_live_manifest(item.run_dir, client) for item in prepared}
        errors = []
        for item in summaries:
            try:
                summarize_document(item.parsed, item.sets["structural_raw"], client,
                                   model, manifests[item.parsed.run_id], item.run_dir,
                                   prompt_family=summary_prompt_family)
            except Exception as exc:
                errors.append({"document_id": item.parsed.document_id, "error_class": type(exc).__name__,
                               "transport_error": str(exc) if isinstance(exc, ModelClientError) else None})
        report["models"].append({"status": "complete" if not errors else "complete_with_failures",
                                 "sample_errors": errors, **_model_report(prepared, model, client)})
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--root-alias", default="primary")
    parser.add_argument("--eval-dir", type=Path, default=Path("evals"))
    parser.add_argument("--runtime-dir", type=Path, default=Path(".local/stage4-generation/runs"))
    parser.add_argument("--existing-runtime-dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".local/stage4-generation/report.json"))
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--models", nargs="+", choices=tuple(CONTEXT_PRICES), default=list(MODELS))
    parser.add_argument("--summary-prompt-family", choices=tuple(f"v{i}" for i in range(1, 18)), default="v1")
    parser.add_argument("--all-summaries", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.existing_runtime_dir:
        if args.source_root or args.existing_runtime_dir.resolve() != args.runtime_dir.resolve():
            parser.error("existing run mode uses --runtime-dir without --source-root")
        prepared = prepared_from_runs(args.runtime_dir)
    else:
        if args.source_root is None:
            parser.error("--source-root or --existing-runtime-dir is required")
        prepared = prepare(args.source_root, args.eval_dir, args.runtime_dir, args.root_alias, resume=args.resume)
    summaries = prepared if args.all_summaries else sample(prepared)
    plan = estimate(summaries, tuple(args.models), args.summary_prompt_family)
    if not args.live:
        print(json.dumps(plan, indent=2))
        return 0
    report = run(prepared, summaries, args.max_usd, args.output, tuple(args.models), args.summary_prompt_family)
    print(json.dumps({"report_path": str(args.output), "models": [
        {"model": item["model"], "status": item["status"]} for item in report["models"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
