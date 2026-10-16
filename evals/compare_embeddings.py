"""Opt-in private comparison of raw-chunk embedding models on checked questions.

Reports contain IDs and metrics only. Captures and vectors stay under ignored .local/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import yaml

from app.ingestion.contracts import ChunkSet, ParsedDocument
from app.ingestion.embeddings import EmbeddedChunk, EmbeddingBatch, EmbeddingSpec
from app.ingestion.eval_mapping import map_eval_labels
from app.ingestion.preview import preview_markdown
from app.ingestion.run_manifest import RunManifest, write_artifact_json
from app.models.client import ModelSpec
from app.models.openrouter import OpenRouterClient
from app.storage import StorageError
from app.storage.loader import connect_runtime, load_run
from app.storage.publication import create_profile, publish_run, select_profile
from app.storage.records import PublicationSelection, SearchProfile, spec_id
from app.storage.search import search_text, search_vector
from app.models.routes import PRIVACY


@dataclass(frozen=True)
class Candidate:
    model: str
    dimensions: int
    usd_per_million: float
    query_type: str | None
    document_type: str | None


# Prices and dimensions must be reconfirmed at trial time.
CANDIDATES = (
    Candidate("baai/bge-m3", 1024, 0.01, None, None),
    Candidate("perplexity/pplx-embed-v1-0.6b", 1024, 0.004, "search_query", "search_document"),
)
K_VALUES = (5, 10, 20)


@dataclass
class Prepared:
    run_dir: Path
    parsed: ParsedDocument
    chunks: ChunkSet
    questions: list[dict]
    mapping: dict[str, list[set[str]]]


def discover(source_root: Path, eval_dir: Path, alias: str) -> dict[Path, Path]:
    labels = {}
    for path in sorted(eval_dir.glob("questions_*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        labels[(data["document_id"], data["source_sha256"])] = path
    found = {}
    for source in source_root.rglob("*.md"):
        relative = source.relative_to(source_root).as_posix()
        identity = f"knowledge-agent://{alias.casefold()}/{relative.casefold()}"
        doc_id = str(uuid5(NAMESPACE_URL, identity))
        if not any(key[0] == doc_id for key in labels):
            continue
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        label = labels.get((doc_id, sha))
        if label:
            found[source] = label
    if len(found) != len(labels):
        raise StorageError("some checked source files are absent or have changed hashes")
    return found


def prepare(source_root: Path, eval_dir: Path, runtime_dir: Path, alias: str) -> list[Prepared]:
    outputs = []
    for source, labels in discover(source_root, eval_dir, alias).items():
        relative = source.relative_to(source_root).as_posix()
        result = preview_markdown(source_root, relative, runtime_dir, root_alias=alias, eval_file=labels)
        run_dir = runtime_dir / result["run_id"]
        parsed = ParsedDocument.model_validate_json((run_dir / "parsed.json").read_text(encoding="utf-8"))
        chunks = ChunkSet.model_validate_json((run_dir / "fixed_raw.json").read_text(encoding="utf-8"))
        data = yaml.safe_load(labels.read_text(encoding="utf-8"))
        mappings = map_eval_labels(labels, parsed, [chunks])[0].mappings
        by_question: dict[str, list[set[str]]] = {}
        for item in mappings:
            if item.label_kind == "must_find":
                by_question.setdefault(item.question_id, []).append(set(item.chunk_ids))
        outputs.append(Prepared(run_dir, parsed, chunks, data["questions"], by_question))
    return outputs


def estimate_cost(prepared: list[Prepared], candidates: tuple[Candidate, ...]) -> dict:
    texts = [chunk.raw_text for item in prepared for chunk in item.chunks.chunks]
    questions = [q["question"] for item in prepared for q in item.questions]
    byte_bound = sum(len(text.encode("utf-8")) for text in [*texts, *questions])
    # Byte count is a conservative upper bound for ordinary subword token counts.
    return {"chunks": len(texts), "questions": len(questions), "input_byte_bound": byte_bound,
            "worst_case_usd": sum(byte_bound * c.usd_per_million / 1_000_000 for c in candidates)}


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


def _coverage(hits, labels: list[set[str]]) -> dict:
    ranked = [hit.chunk_id for hit in hits]
    return {str(k): {"covered": sum(bool(set(ranked[:k]) & choices) for choices in labels),
                     "required": len(labels),
                     "complete": all(set(ranked[:k]) & choices for choices in labels)}
            for k in K_VALUES}


def _embed_batch(client: OpenRouterClient, candidate: Candidate, texts: list[str], input_type: str | None,
                 latency: list[float], usage: list[int]):
    started = time.perf_counter()
    response = client.embed(model=ModelSpec(provider="openrouter", model_id=candidate.model,
                                           model_version="trial-2026-09-23"), texts=texts,
        input_type=input_type, provider=PRIVACY)
    latency.append(time.perf_counter() - started)
    if response.usage.input_tokens is None:
        raise StorageError("provider did not report input token usage")
    usage.append(response.usage.input_tokens)
    if any(len(vector) != candidate.dimensions or any(not math.isfinite(value) for value in vector)
           for vector in response.vectors):
        raise StorageError("provider returned invalid embedding dimensions or values")
    return response


def _select_live_manifest(run_dir: Path, client: OpenRouterClient) -> RunManifest:
    manifest = RunManifest.load(run_dir)
    selected, requests, input_tokens = client.live_run_selection
    if not selected or requests < 1 or input_tokens < 1:
        raise StorageError("live trial requires a selected client with positive budgets")
    manifest.live_call_selected = selected
    manifest.request_budget = requests
    manifest.estimated_input_token_budget = input_tokens
    manifest.require_client_selection(client)
    # Persist the audit choice before any private text leaves this machine.
    manifest.save(run_dir)
    return manifest


def compare(prepared: list[Prepared], max_usd: float, output_dir: Path,
            full_corpus_source_bytes: int) -> dict:
    if not os.getenv("OPENROUTER_API_KEY") and Path(".env").is_file():
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                os.environ["OPENROUTER_API_KEY"] = line.partition("=")[2].strip().strip('"\'')
                break
    if not os.getenv("OPENROUTER_API_KEY"):
        raise StorageError("OPENROUTER_API_KEY is required for a live trial")
    estimate = estimate_cost(prepared, CANDIDATES)
    if estimate["worst_case_usd"] > max_usd:
        raise StorageError("conservative input estimate exceeds the trial spend cap")
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_source_bytes = sum((item.run_dir / "source.md").stat().st_size for item in prepared)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "spend_cap_usd": max_usd,
              "estimate": estimate, "profiles": [], "selected_model": None,
              "privacy_route": PRIVACY, "candidate_count": len(CANDIDATES),
              "sample_source_bytes": sample_source_bytes,
              "full_corpus_source_bytes": full_corpus_source_bytes,
              "selection_gates": {"required_span_recall_at_10": .8,
                                  "complete_question_rate_at_10": .7},
              "excluded_candidate": {"model": "qwen/qwen3-embedding-8b",
                                     "reason": "default 4096 dimensions exceed vector HNSW 2000 and halfvec HNSW 4000; no verified reduced route"}}
    spent = 0.0
    with connect_runtime() as conn:
        for candidate in CANDIDATES:
            model_report = {"model": candidate.model, "dimensions": candidate.dimensions,
                            "price_usd_per_million": candidate.usd_per_million,
                            "query_input_type": candidate.query_type,
                            "document_input_type": candidate.document_type,
                            "input_recipe_version": "raw-v1",
                            "provider_route": "automatic with privacy filters", "questions": []}
            report["profiles"].append(model_report)
            latency: list[float] = []
            usage: list[int] = []
            client = OpenRouterClient(allow_live=True, request_budget=200,
                                      estimated_input_token_budget=estimate["input_byte_bound"] * 2)
            profile_id = "trial-" + hashlib.sha256(candidate.model.encode()).hexdigest()[:12]
            try:
                for item in prepared:
                    spec = EmbeddingSpec(provider="openrouter", model=candidate.model,
                                         model_version="trial-2026-09-23", dimensions=candidate.dimensions,
                                         distance_metric="cosine", input_recipe_version="raw-v1",
                                         chunking_version=item.chunks.chunking_version)
                    manifest = _select_live_manifest(item.run_dir, client)
                    for offset in range(0, len(item.chunks.chunks), 16):
                        batch = item.chunks.chunks[offset:offset + 16]
                        response = _embed_batch(client, candidate, [chunk.raw_text for chunk in batch],
                                                candidate.document_type, latency, usage)
                        version = {"spec": spec.model_dump(mode="json", exclude={"created_at"}),
                                   "chunk_ids": [chunk.chunk_id for chunk in batch],
                                   "context_sha256": [None] * len(batch)}
                        artifact = manifest.begin("embedding", version, spec.model_spec())
                        payload = EmbeddingBatch(document_id=item.parsed.document_id, run_id=item.parsed.run_id,
                                                 source_sha256=item.parsed.source_sha256, spec=spec,
                                                 chunks=[EmbeddedChunk(chunk_id=chunk.chunk_id,
                                                                       source_spans=chunk.source_spans, vector=vector)
                                                         for chunk, vector in zip(batch, response.vectors)],
                                                 usage=response.usage)
                        path = write_artifact_json(item.run_dir, "embedding", artifact.artifact_id,
                                                   payload.model_dump(mode="json"))
                        manifest.succeed(artifact.artifact_id, path, response.usage)
                    manifest.save(item.run_dir)
                    load_run(conn, item.run_dir)
                    if item is prepared[0]:
                        create_profile(conn, SearchProfile(profile_id=profile_id, variant="fixed_raw",
                                                           chunking_version=spec.chunking_version,
                                                           embedding_spec_id=spec_id(spec)))
                    publish_run(conn, PublicationSelection(run_id=item.parsed.run_id, profile_id=profile_id))
                select_profile(conn, profile_id)
                document_usage = sum(usage)
                for item in prepared:
                    for question in item.questions:
                        if question["expected"] != "supported":
                            continue
                        vector = _embed_batch(client, candidate, [question["question"]],
                                              candidate.query_type, latency, usage).vectors[0]
                        semantic = search_vector(conn, vector, spec_id=spec_id(spec), metric="cosine", k=20)
                        lexical = search_text(conn, question["question"], k=20)
                        labels = item.mapping[question["id"]]
                        model_report["questions"].append({"id": question["id"],
                                                           "semantic": _coverage(semantic, labels),
                                                           "fts": _coverage(lexical, labels)})
                total_spans = sum(q["semantic"]["10"]["required"] for q in model_report["questions"])
                covered = sum(q["semantic"]["10"]["covered"] for q in model_report["questions"])
                complete = sum(q["semantic"]["10"]["complete"] for q in model_report["questions"])
                model_report["recall_at_10"] = covered / total_spans
                model_report["complete_question_rate_at_10"] = complete / len(model_report["questions"])
                model_report["request_latency_p50_seconds"] = statistics.median(latency)
                model_report["request_latency_p95_seconds"] = _percentile(latency, .95)
                model_report["input_tokens"] = sum(usage)
                model_report["document_input_tokens"] = document_usage
                model_report["query_input_tokens"] = sum(usage) - document_usage
                model_report["measured_cost_usd"] = sum(usage) * candidate.usd_per_million / 1_000_000
                model_report["estimated_full_corpus_index_usd"] = (
                    document_usage * full_corpus_source_bytes / sample_source_bytes
                    * candidate.usd_per_million / 1_000_000)
                model_report["estimated_reindex_usd"] = model_report["estimated_full_corpus_index_usd"]
                model_report["estimated_query_usd"] = (
                    model_report["query_input_tokens"] / len(model_report["questions"])
                    * candidate.usd_per_million / 1_000_000)
                model_report["vector_bytes_estimate"] = estimate["chunks"] * candidate.dimensions * 4
                model_report["full_corpus_vector_bytes_estimate"] = round(
                    model_report["vector_bytes_estimate"] * full_corpus_source_bytes / sample_source_bytes)
                model_report["hnsw_index_bytes"] = None
                model_report["hnsw_note"] = "No approximate index before exact-search quality selection"
                for mode in ("semantic", "fts"):
                    model_report[f"{mode}_recall"] = {
                        str(k): sum(q[mode][str(k)]["covered"] for q in model_report["questions"])
                        / sum(q[mode][str(k)]["required"] for q in model_report["questions"])
                        for k in K_VALUES}
                model_report["status"] = "complete"
                spent += model_report["measured_cost_usd"]
            except Exception as exc:
                model_report["status"] = "failed"
                model_report["error_class"] = type(exc).__name__
                spent += sum(usage) * candidate.usd_per_million / 1_000_000
            finally:
                report["measured_spend_usd"] = spent
                (output_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    feasible = [item for item in report["profiles"] if item["status"] == "complete"
                and item["recall_at_10"] >= .8 and item["complete_question_rate_at_10"] >= .7]
    if feasible:
        report["selected_model"] = min(feasible, key=lambda item: item["measured_cost_usd"])["model"]
    (output_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--root-alias", default="primary")
    parser.add_argument("--eval-dir", type=Path, default=Path("evals"))
    parser.add_argument("--runtime-dir", type=Path, default=Path(".local/ingestion-runs"))
    parser.add_argument("--output-dir", type=Path, default=Path(".local/embedding-trial"))
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    prepared = prepare(args.source_root, args.eval_dir, args.runtime_dir, args.root_alias)
    estimate = estimate_cost(prepared, CANDIDATES)
    if not args.live:
        print(json.dumps({"matched_documents": len(prepared), **estimate}, indent=2))
        return 0
    full_corpus_source_bytes = sum(path.stat().st_size for path in args.source_root.rglob("*.md"))
    report = compare(prepared, args.max_usd, args.output_dir, full_corpus_source_bytes)
    print(json.dumps({"report_path": str(args.output_dir / "report.json"),
                      "selected_model": report["selected_model"],
                      "measured_spend_usd": report["measured_spend_usd"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
