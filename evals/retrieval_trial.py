"""Revision-bound recall of the active index; no vault access or generated answers."""

from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

import yaml

from app.models.openrouter import OpenRouterClient
from app.storage import StorageError
from app.storage.loader import connect_runtime
from app.storage.retrieval import embed_queries, fuse_hits, read_passage, search_cards, selected_embedding_spec
from app.storage.search import search_text, search_vector

K_VALUES = (5, 10, 20)


def coverage(hits, question: dict, k: int) -> dict:
    missing = []
    labels = question.get("must_find", [])
    for index, label in enumerate(labels):
        if not any(hit.document_id == question["document_id"]
                   and hit.source_sha256 == question["source_sha256"]
                   and any(span["start_char"] <= label["start_char"]
                           and label["end_char"] <= span["end_char"]
                           for span in hit.source_spans) for hit in hits[:k]):
            missing.append(index)
    return {"covered": len(labels) - len(missing), "required": len(labels),
            "recall": (len(labels) - len(missing)) / len(labels) if labels else None,
            "complete": not missing if labels else None, "missing_span_indices": missing}


def checked_questions(conn, eval_dir: Path) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""select r.document_id,r.source_sha256,r.source_text from knowledge.runs r
            join knowledge.active_runs ar on ar.run_id=r.run_id
            join knowledge.corpus_state state on state.profile_id=ar.profile_id""")
        active = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    questions = []
    documents = set()
    for path in sorted(eval_dir.glob("questions_*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        document_id, sha = data["document_id"], data["source_sha256"]
        if document_id in documents or document_id not in active or active[document_id][0] != sha:
            raise StorageError("retrieval labels do not match the selected source revisions")
        documents.add(document_id)
        seen = set()
        for question in data["questions"]:
            if question["id"] in seen:
                raise StorageError("duplicate retrieval question")
            seen.add(question["id"])
            for label in question.get("must_find", []) + question.get("useful", []):
                start, end = label["start_char"], label["end_char"]
                if not 0 <= start < end <= len(active[document_id][1]) or (
                    active[document_id][1][start:end] != label["quote"]
                ):
                    raise StorageError("retrieval label does not match stored original text")
            questions.append({**question, "document_id": document_id, "source_sha256": sha})
    if not questions or documents != set(active):
        raise StorageError("retrieval labels must cover the selected corpus")
    return questions


def run(conn, client, *, provider_tag: str, price_ceiling: Decimal, max_usd: Decimal,
        eval_dir: Path, live: bool, report: dict) -> None:
    sid, spec = selected_embedding_spec(conn)
    questions = checked_questions(conn, eval_dir)
    byte_bound = sum(len(q["question"].encode("utf-8")) for q in questions)
    bound = Decimal(byte_bound) * price_ceiling / 1_000_000
    if not max_usd.is_finite() or max_usd <= 0 or bound > max_usd:
        raise StorageError("retrieval embedding byte-cost bound exceeds spending ceiling")
    report.update(status="dry_run", spec_id=sid, embedding_model=spec.model,
                  provider_tag=provider_tag, query_input_type=spec.query_input_type,
                  questions=len(questions), supported_questions=sum(q["expected"] == "supported" for q in questions),
                  input_byte_bound=byte_bound, conservative_usd=float(bound), max_usd=float(max_usd),
                  source_revisions={q["document_id"]: q["source_sha256"] for q in questions})
    if not live:
        return
    report.update(status="running", results=[])
    started = time.perf_counter()
    _, _, embedded = embed_queries(conn, client, [q["question"] for q in questions],
                                    provider_tag=provider_tag, max_embedding_usd_per_million=price_ceiling)
    report["embedding_seconds"] = time.perf_counter() - started
    if embedded.usage.input_tokens is None:
        raise StorageError("query embeddings omitted usage; trial cost is unknown")
    report["embedding_input_tokens"] = embedded.usage.input_tokens
    # Conservative estimate at the approved ceiling rather than a stale advertised price.
    report["estimated_usd_upper_bound"] = float(Decimal(embedded.usage.input_tokens) * price_ceiling / 1_000_000)
    if Decimal(embedded.usage.input_tokens) * price_ceiling / 1_000_000 > max_usd:
        raise StorageError("reported embedding spend exceeds ceiling")
    for question, vector in zip(questions, embedded.vectors):
        vector_hits = search_vector(conn, vector, spec_id=sid, metric=spec.distance_metric, k=20)
        text_hits = search_text(conn, question["question"], k=20)
        fused = fuse_hits(vector_hits, text_hits)
        channels = {"vector": vector_hits, "text": text_hits, "fused": [item.hit for item in fused]}
        report["results"].append({"document_id": question["document_id"], "question_id": question["id"],
            "expected": question["expected"],
            "coverage": {name: {str(k): coverage(hits, question, k) for k in K_VALUES}
                         for name, hits in channels.items()},
            "ranks": {name: [{"run_id": hit.run_id, "chunk_id": hit.chunk_id} for hit in hits]
                      for name, hits in channels.items()}})
    report["metrics"] = {}
    for name in ("vector", "text", "fused"):
        report["metrics"][name] = {}
        for k in K_VALUES:
            rows = [r["coverage"][name][str(k)] for r in report["results"] if r["expected"] == "supported"]
            covered, required = sum(r["covered"] for r in rows), sum(r["required"] for r in rows)
            report["metrics"][name][str(k)] = {"covered": covered, "required": required,
                "recall": covered / required if required else None,
                "complete_questions": sum(r["complete"] is True for r in rows)}
    probes = []
    for query in ("customer", "hiring", "anger"):
        started = time.perf_counter()
        cards = search_cards(conn, query, k=20)
        seconds = time.perf_counter() - started
        for card in cards:
            span = card.evidence_span
            read_passage(conn, document_id=card.document_id, run_id=card.run_id,
                         source_sha256=card.source_sha256, chunk_id=card.chunk_id,
                         start_char=span["start_char"], end_char=min(span["end_char"], span["start_char"] + 2400))
        probes.append({"query": query, "distinct_cards": len(cards), "seconds": seconds,
                       "verified_original_reads": len(cards)})
    report.update(status="complete", card_probes=probes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider-tag", required=True)
    parser.add_argument("--max-embedding-price", type=Decimal, default=Decimal("0.004"))
    parser.add_argument("--max-usd", type=Decimal, required=True)
    parser.add_argument("--eval-dir", type=Path, default=Path("evals"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    path = args.report.resolve()
    if not path.is_relative_to(Path(".local").resolve()) or path.exists():
        parser.error("report must be a new file under .local")
    if not args.max_embedding_price.is_finite() or args.max_embedding_price < 0:
        parser.error("embedding price ceiling must be finite and nonnegative")
    report = {"status": "pending"}
    client = OpenRouterClient(allow_live=args.live, request_budget=1,
        estimated_input_token_budget=20000, provider={"only": [args.provider_tag],
        "allow_fallbacks": False, "data_collection": "deny", "zdr": True})
    try:
        with connect_runtime() as conn, conn.transaction():
            conn.execute("set transaction isolation level repeatable read read only")
            run(conn, client, provider_tag=args.provider_tag, price_ceiling=args.max_embedding_price,
                max_usd=args.max_usd, eval_dir=args.eval_dir, live=args.live, report=report)
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status="failed", error_type=type(exc).__name__)
    finally:
        report["billable_requests"] = [{"model": model, "usage": usage.model_dump()}
                                       for model, usage in client.billable_requests]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "metrics", "estimated_usd_upper_bound", "error_type")
                      if key in report}))
    return 0 if report["status"] in ("dry_run", "complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
