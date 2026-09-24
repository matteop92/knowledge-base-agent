"""Opt-in private A/B comparison of chunk boundaries and chunk context.

The report contains IDs and metrics only. Source snapshots, generated context,
and vectors stay in ignored local run directories and the local database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.ingestion.contracts import ChunkSet, ParsedDocument
from app.ingestion.embeddings import EmbeddedChunk, EmbeddingBatch, EmbeddingSpec
from app.ingestion.enrichment import (
    ChunkContext, _input as context_input,
    _prompt as context_prompt, generate_chunk_context,
)
from app.ingestion.eval_mapping import map_eval_labels
from app.ingestion.preview import preview_markdown
from app.ingestion.run_manifest import RunManifest, write_artifact_json
from app.models.client import ModelSpec
from app.models.openrouter import OpenRouterClient
from app.storage import StorageError
from app.storage.loader import connect_runtime, load_run
from app.storage.publication import create_profile
from app.storage.records import SearchProfile, spec_id
from app.storage.search import search_vector
from evals.compare_embeddings import (
    Candidate, PRIVACY, _coverage, _embed_batch, _select_live_manifest, discover,
)


EMBEDDING = Candidate("perplexity/pplx-embed-v1-0.6b", 1024, 0.004,
                      "search_query", "search_document")
CONTEXT_MODEL = ModelSpec(provider="openrouter", model_id="openai/gpt-4.1-mini",
                          model_version="trial-2026-09-24")
CONTEXT_INPUT_USD_PER_MILLION = 0.40
CONTEXT_OUTPUT_USD_PER_MILLION = 1.60
TRIAL_CONTEXT_PROMPT_VERSION = "chunk_context_v2"
K_VALUES = (5, 10, 20)


@dataclass
class Prepared:
    run_dir: Path
    parsed: ParsedDocument
    sets: dict[str, ChunkSet]
    questions: list[dict]
    mappings: dict[str, dict[str, list[set[str]]]]


def prepare(source_root: Path, eval_dir: Path, runtime_dir: Path, alias: str,
            *, resume: bool = False) -> list[Prepared]:
    prepared = []
    reusable = []
    if resume and runtime_dir.is_dir():
        for directory in runtime_dir.iterdir():
            if (directory / "parsed.json").is_file() and (directory / "run-manifest.json").is_file():
                parsed = ParsedDocument.model_validate_json((directory / "parsed.json").read_text(encoding="utf-8"))
                manifest = RunManifest.load(directory)
                reusable.append((directory, parsed, sum(record.status == "ready" for record in manifest.artifacts.values())))
    for source, labels in discover(source_root, eval_dir, alias).items():
        expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        candidates = [entry for entry in reusable if entry[1].source_sha256 == expected_sha
                      and entry[1].locator.relative_path == source.relative_to(source_root).as_posix()]
        if candidates:
            run_dir = max(candidates, key=lambda entry: entry[2])[0]
        else:
            preview = preview_markdown(source_root, source.relative_to(source_root).as_posix(),
                                       runtime_dir, root_alias=alias, eval_file=labels)
            run_dir = runtime_dir / preview["run_id"]
        parsed = ParsedDocument.model_validate_json((run_dir / "parsed.json").read_text(encoding="utf-8"))
        sets = {variant: ChunkSet.model_validate_json(
            (run_dir / f"{variant}.json").read_text(encoding="utf-8"))
            for variant in ("fixed_raw", "structural_raw")}
        mappings = {}
        for mapped in map_eval_labels(labels, parsed, list(sets.values())):
            by_question: dict[str, list[set[str]]] = {}
            for item in mapped.mappings:
                if item.label_kind == "must_find":
                    by_question.setdefault(item.question_id, []).append(set(item.chunk_ids))
            mappings[mapped.variant] = by_question
        questions = yaml.safe_load(labels.read_text(encoding="utf-8"))["questions"]
        prepared.append(Prepared(run_dir, parsed, sets, questions, mappings))
    return prepared


def estimate(prepared: list[Prepared]) -> dict:
    structural = [chunk for item in prepared for chunk in item.sets["structural_raw"].chunks]
    fixed = [chunk for item in prepared for chunk in item.sets["fixed_raw"].chunks]
    questions = [q["question"] for item in prepared for q in item.questions
                 if q["expected"] == "supported"]
    prompt, _ = context_prompt(TRIAL_CONTEXT_PROMPT_VERSION)
    context_input_bytes = sum(len(prompt.encode("utf-8")) + len(context_input(item.parsed, chunk).encode("utf-8"))
                              for item in prepared for chunk in item.sets["structural_raw"].chunks)
    # At most 360 output tokens per generated context. Six UTF-8 bytes per
    # output token is a generous bound for the later contextual embeddings.
    embedding_input_bytes = sum(len(chunk.raw_text.encode("utf-8")) for chunk in fixed)
    embedding_input_bytes += 2 * sum(len(chunk.raw_text.encode("utf-8")) for chunk in structural)
    embedding_input_bytes += sum(len(question.encode("utf-8")) for question in questions)
    embedding_input_bytes += len(structural) * 360 * 6
    worst_case_usd = (
        context_input_bytes * CONTEXT_INPUT_USD_PER_MILLION
        + len(structural) * 360 * CONTEXT_OUTPUT_USD_PER_MILLION
        + embedding_input_bytes * EMBEDDING.usd_per_million
    ) / 1_000_000
    return {"documents": len(prepared), "fixed_chunks": len(fixed),
            "structural_chunks": len(structural), "supported_questions": len(questions),
            "context_input_byte_bound": context_input_bytes,
            "embedding_input_byte_bound": embedding_input_bytes,
            "worst_case_usd": worst_case_usd}


class Spend:
    def __init__(self, cap: float) -> None:
        self.cap = cap
        self.embedding_input_tokens = 0
        self.context_input_tokens = 0
        self.context_output_tokens = 0

    @property
    def usd(self) -> float:
        return (self.embedding_input_tokens * EMBEDDING.usd_per_million
                + self.context_input_tokens * CONTEXT_INPUT_USD_PER_MILLION
                + self.context_output_tokens * CONTEXT_OUTPUT_USD_PER_MILLION) / 1_000_000

    def add_embedding(self, tokens: int | None) -> None:
        if tokens is None:
            raise StorageError("embedding response omitted input token usage")
        self.embedding_input_tokens += tokens
        self._check()

    def add_context(self, context: ChunkContext) -> None:
        if context.usage.input_tokens is None or context.usage.output_tokens is None:
            raise StorageError("context response omitted token usage")
        self.context_input_tokens += context.usage.input_tokens
        self.context_output_tokens += context.usage.output_tokens
        self._check()

    def _check(self) -> None:
        if self.usd > self.cap:
            raise StorageError("measured trial spend exceeded its cap")

    def report(self) -> dict:
        return {"estimated_usd": self.usd,
                "embedding_input_tokens": self.embedding_input_tokens,
                "context_input_tokens": self.context_input_tokens,
                "context_output_tokens": self.context_output_tokens}


def _load_key() -> None:
    if os.getenv("OPENROUTER_API_KEY"):
        return
    path = Path(".env")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                os.environ["OPENROUTER_API_KEY"] = line.partition("=")[2].strip().strip('"\'')
                break
    if not os.getenv("OPENROUTER_API_KEY"):
        raise StorageError("OPENROUTER_API_KEY is required for a live trial")


def _embed_set(item: Prepared, chunks: ChunkSet, spec: EmbeddingSpec,
               contexts: dict[str, ChunkContext] | None, client: OpenRouterClient,
               manifest: RunManifest, spend: Spend) -> None:
    for offset in range(0, len(chunks.chunks), 16):
        batch = chunks.chunks[offset:offset + 16]
        version = {"spec": spec.model_dump(mode="json", exclude={"created_at"}),
                   "chunk_ids": [chunk.chunk_id for chunk in batch],
                   "context_sha256": [hashlib.sha256(contexts[chunk.chunk_id].model_dump_json().encode()).hexdigest()
                                      if contexts else None for chunk in batch]}
        record = manifest.begin("embedding", version, spec.model_spec())
        if record.status == "ready":
            continue
        manifest.save(item.run_dir)
        texts = [(contexts[chunk.chunk_id].context + "\n\n" if contexts else "") + chunk.raw_text
                 for chunk in batch]
        response = _embed_batch(client, EMBEDDING, texts, EMBEDDING.document_type, [], [])
        spend.add_embedding(response.usage.input_tokens)
        payload = EmbeddingBatch(document_id=item.parsed.document_id, run_id=item.parsed.run_id,
                                 source_sha256=item.parsed.source_sha256, spec=spec,
                                 chunks=[EmbeddedChunk(chunk_id=chunk.chunk_id,
                                                       source_spans=chunk.source_spans, vector=vector)
                                         for chunk, vector in zip(batch, response.vectors)],
                                 usage=response.usage)
        path = write_artifact_json(item.run_dir, "embedding", record.artifact_id,
                                   payload.model_dump(mode="json"))
        manifest.succeed(record.artifact_id, path, response.usage)
        manifest.save(item.run_dir)


def _ranked_hits(conn, vector: list[float], spec: EmbeddingSpec, profile_id: str,
                 prepared: list[Prepared]):
    hits = []
    for item in prepared:
        hits.extend(search_vector(conn, vector, spec_id=spec_id(spec), metric="cosine", k=20,
                                  profile_id=profile_id, historical_run_id=item.parsed.run_id))
    return sorted(hits, key=lambda hit: (hit.score, hit.chunk_id, hit.run_id))[:20]


def _same_structural_sources(baseline: list[Prepared], contextual: list[Prepared]) -> None:
    by_document = {item.parsed.document_id: item for item in baseline}
    if len(by_document) != len(contextual):
        raise StorageError("context trial corpus differs from the completed baseline")
    for item in contextual:
        old = by_document.get(item.parsed.document_id)
        if old is None or old.parsed.source_sha256 != item.parsed.source_sha256:
            raise StorageError("context trial source revision differs from the baseline")
        old_chunks = old.sets["structural_raw"].chunks
        new_chunks = item.sets["structural_raw"].chunks
        if len(old_chunks) != len(new_chunks) or any(
            left.raw_text != right.raw_text or left.source_spans != right.source_spans
            for left, right in zip(old_chunks, new_chunks)
        ):
            raise StorageError("context trial changed the structural chunk boundaries")


def compare(prepared: list[Prepared], max_usd: float, output_dir: Path,
            *, baseline: list[Prepared] | None = None) -> dict:
    plan = estimate(prepared)
    if max_usd <= 0 or plan["worst_case_usd"] > max_usd:
        raise StorageError("conservative trial estimate exceeds the spend cap")
    _load_key()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    completed_a = []
    if baseline is not None:
        _same_structural_sources(baseline, prepared)
        if not report_path.is_file():
            raise StorageError("context-only run needs the completed A report")
        prior = json.loads(report_path.read_text(encoding="utf-8"))
        completed_a = [profile for profile in prior["profiles"]
                       if profile["name"] in ("fixed_raw", "structural_raw") and profile["status"] == "complete"]
        if len(completed_a) != 2 or prior["embedding_model"] != EMBEDDING.model:
            raise StorageError("context-only run needs two compatible completed A profiles")
    spend = Spend(max_usd)
    client = OpenRouterClient(allow_live=True, request_budget=200,
                              estimated_input_token_budget=2 * (plan["context_input_byte_bound"]
                                                                 + plan["embedding_input_byte_bound"]),
                              provider=PRIVACY)
    manifests = {item.parsed.run_id: _select_live_manifest(item.run_dir, client)
                 for item in prepared}
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "spend_cap_usd": max_usd,
              "estimate": plan, "embedding_model": EMBEDDING.model,
              "context_model": CONTEXT_MODEL.model_id, "context_prompt_version": TRIAL_CONTEXT_PROMPT_VERSION,
              "privacy_route": PRIVACY, "profiles": completed_a, "cost": spend.report(),
              "context_only": baseline is not None,
              "note": "Exact vector retrieval only; no fusion or answer evaluation."}
    with connect_runtime() as conn:
        query_pairs = [(item, q) for item in prepared for q in item.questions
                       if q["expected"] == "supported"]
        query_response = _embed_batch(client, EMBEDDING, [q["question"] for _, q in query_pairs],
                                      EMBEDDING.query_type, [], [])
        spend.add_embedding(query_response.usage.input_tokens)
        for name, variant, recipe in (
            ("fixed_raw", "fixed_raw", "raw-v1"),
            ("structural_raw", "structural_raw", "raw-v1"),
            ("structural_contextual", "structural_raw", "contextualized-v1"),
        ):
            if baseline is not None and name != "structural_contextual":
                continue
            profile_report = {"name": name, "variant": variant, "input_recipe_version": recipe,
                              "questions": [], "status": "pending"}
            report["profiles"].append(profile_report)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            try:
                specs = []
                for item in prepared:
                    chunks = item.sets[variant]
                    spec = EmbeddingSpec(provider="openrouter", model=EMBEDDING.model,
                                         model_version="trial-2026-09-23", dimensions=EMBEDDING.dimensions,
                                         distance_metric="cosine", input_recipe_version=recipe,
                                         context_prompt_version=TRIAL_CONTEXT_PROMPT_VERSION if recipe == "contextualized-v1" else None,
                                         chunking_version=chunks.chunking_version)
                    specs.append(spec)
                    manifest = manifests[item.parsed.run_id]
                    contexts = None
                    if recipe == "contextualized-v1":
                        contexts = {}
                        for chunk in chunks.chunks:
                            context = generate_chunk_context(item.parsed, chunks, chunk, client,
                                                             CONTEXT_MODEL, manifest, item.run_dir,
                                                             prompt_version=TRIAL_CONTEXT_PROMPT_VERSION)
                            contexts[chunk.chunk_id] = context
                            spend.add_context(context)
                    _embed_set(item, chunks, spec, contexts, client, manifest, spend)
                    manifest.save(item.run_dir)
                    load_run(conn, item.run_dir)
                spec = specs[0]
                profile_id = "chunk-trial-" + name
                create_profile(conn, SearchProfile(profile_id=profile_id, variant=variant,
                                                   chunking_version=spec.chunking_version,
                                                   embedding_spec_id=spec_id(spec),
                                                   required_artifact_kinds=["context"] if contexts else []))
                for (item, question), vector in zip(query_pairs, query_response.vectors):
                    hits = _ranked_hits(conn, vector, spec, profile_id, prepared)
                    profile_report["questions"].append({"document_id": item.parsed.document_id,
                                                        "question_id": question["id"],
                                                        "coverage": _coverage(hits, item.mappings[variant][question["id"]])})
                profile_report["chunk_count"] = sum(len(item.sets[variant].chunks) for item in prepared)
                profile_report["context_count"] = profile_report["chunk_count"] if contexts else 0
                profile_report["metrics"] = {
                    str(k): {"covered": sum(q["coverage"][str(k)]["covered"] for q in profile_report["questions"]),
                             "required": sum(q["coverage"][str(k)]["required"] for q in profile_report["questions"]),
                             "complete_questions": sum(q["coverage"][str(k)]["complete"] for q in profile_report["questions"])}
                    for k in K_VALUES}
                profile_report["status"] = "complete"
            except Exception as exc:
                profile_report["status"] = "failed"
                profile_report["error_class"] = type(exc).__name__
                raise
            finally:
                report["cost"] = spend.report()
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--root-alias", default="primary")
    parser.add_argument("--eval-dir", type=Path, default=Path("evals"))
    parser.add_argument("--output-dir", type=Path, default=Path(".local/chunking-trial"))
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--context-only", action="store_true")
    args = parser.parse_args()
    baseline = prepare(args.source_root, args.eval_dir, args.output_dir / "runs", args.root_alias,
                       resume=True) if args.context_only else None
    runtime_dir = args.output_dir / ("context-runs" if args.context_only else "runs")
    prepared = prepare(args.source_root, args.eval_dir, runtime_dir, args.root_alias,
                       resume=args.resume)
    plan = estimate(prepared)
    if not args.live:
        print(json.dumps(plan, indent=2))
        return 0
    report = compare(prepared, args.max_usd, args.output_dir, baseline=baseline)
    print(json.dumps({"report_path": str(args.output_dir / "report.json"),
                      "cost": report["cost"],
                      "metrics": {item["name"]: item.get("metrics") for item in report["profiles"]}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
