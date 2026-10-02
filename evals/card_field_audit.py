"""Inspect selected card occupancy and prepare private, source-bound field labels."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.ingestion.contracts import Chunk, ParsedDocument
from app.ingestion.enrichment import EvidenceCardsArtifact, _card_groups
from app.ingestion.run_manifest import RunManifest
from app.storage.loader import connect_runtime

FIELDS = ("situation", "action", "result", "principle")


def occupancy(cards: list[dict]) -> dict:
    counts = Counter(field for card in cards for field in FIELDS if card.get(field))
    return {"cards": len(cards), "fields": {field: counts[field] for field in FIELDS},
            "all_null": sum(not any(card.get(field) for field in FIELDS) for card in cards),
            "types": dict(sorted(Counter(card["card_type"] for card in cards).items()))}


def check_label(label: dict, group: dict) -> None:
    for key in ("document_id", "run_id", "source_sha256", "chunk_id",
                "start_char", "end_char", "group_sha256"):
        if label.get(key) != group[key]:
            raise ValueError(f"stale field label: {key}")
    decisions = label.get("fields", {})
    if set(decisions) != set(FIELDS):
        raise ValueError("field label must cover all four fields")
    for field, decision in decisions.items():
        if decision.get("status") not in ("supported", "absent", "ambiguous"):
            raise ValueError(f"invalid {field} applicability")
        if decision["status"] == "ambiguous" and not decision.get("reason"):
            raise ValueError(f"ambiguous {field} needs a reason")


def field_recall(groups: list[dict], labels: list[dict], cards: list[dict]) -> dict:
    by_group = {(g["document_id"], g["chunk_id"], g["start_char"], g["end_char"]): g
                for g in groups}
    chunk_splits = {}
    by_card_group: dict[tuple, list[dict]] = defaultdict(list)
    for card in cards:
        span = card["evidence_span"]
        key = (card["document_id"], card["chunk_id"], span["start_char"], span["end_char"])
        group = by_group.get(key)
        if group and (card.get("source_sha256", group["source_sha256"]) != group["source_sha256"]
                      or card.get("run_id", group["run_id"]) != group["run_id"]):
            raise ValueError("candidate card has stale source provenance")
        by_card_group[key].append(card)
    counts = {field: Counter() for field in FIELDS}
    for label in labels:
        key = (label["document_id"], label["chunk_id"], label["start_char"], label["end_char"])
        group = by_group.get(key)
        if group is None:
            raise ValueError("label points outside the selected source groups")
        check_label(label, group)
        split = label.get("split")
        if split is not None:
            if split not in ("development", "validation"):
                raise ValueError("invalid audit split")
            chunk_key = (label["document_id"], label["chunk_id"])
            if chunk_key in chunk_splits and chunk_splits[chunk_key] != split:
                raise ValueError("one generation chunk cannot cross audit splits")
            chunk_splits[chunk_key] = split
        for field, decision in label["fields"].items():
            counts[field][decision["status"]] += 1
            if decision["status"] == "supported":
                if any(card.get(field) for card in by_card_group[key]):
                    counts[field]["populated"] += 1
                else:
                    counts[field]["missed"] += 1
    return {field: dict(sorted(counts[field].items())) for field in FIELDS}


def selected_cards(conn) -> list[dict]:
    rows = conn.execute("""select c.payload from knowledge.cards c
        join knowledge.artifacts a on a.run_id=c.run_id and a.artifact_id=c.artifact_id
        join knowledge.active_runs ar on ar.run_id=c.run_id
        join knowledge.corpus_state state on state.profile_id=ar.profile_id
        join knowledge.profiles p on p.profile_id=ar.profile_id
        join knowledge.chunks ch on ch.run_id=c.run_id and ch.chunk_id=c.chunk_id
        where a.status='ready' and a.kind='cards'
          and (ar.selection->'required_artifact_ids') ? c.artifact_id
          and ch.variant=p.variant and ch.chunking_version=p.chunking_version
        order by c.run_id,c.chunk_id,c.card_id""").fetchall()
    return [row[0] for row in rows]


def selected_chunk_sample(conn) -> list[dict]:
    rows = conn.execute("""select r.document_id,ch.chunk_id,ch.position
        from knowledge.corpus_state state
        join knowledge.active_runs ar on ar.profile_id=state.profile_id
        join knowledge.runs r on r.run_id=ar.run_id
        join knowledge.profiles p on p.profile_id=ar.profile_id
        join knowledge.chunks ch on ch.run_id=r.run_id
        where ch.variant=p.variant and ch.chunking_version=p.chunking_version
        order by r.document_id,ch.position""").fetchall()
    by_document: dict[str, list[str]] = defaultdict(list)
    for document_id, chunk_id, _ in rows:
        by_document[document_id].append(chunk_id)
    sample = []
    for document_id, chunk_ids in sorted(by_document.items()):
        for index in sorted({0, len(chunk_ids) // 2, len(chunk_ids) - 1}):
            sample.append({"document_id": document_id, "chunk_id": chunk_ids[index]})
    return sample


def candidate_cards(runtime_dir: Path, sample: list[dict],
                    prompt_version: str = "evidence_cards_v8") -> tuple[list[dict], dict]:
    """Read one ready candidate card artifact for each screened captured chunk."""
    manifests = {}
    cards = []
    first_response = 0
    for item in sample:
        document_id, chunk_id = item["document_id"], item["chunk_id"]
        candidates = []
        for run_dir in runtime_dir.iterdir():
            if not run_dir.is_dir():
                continue
            manifest = manifests.setdefault(run_dir, RunManifest.load(run_dir))
            if manifest.document_id == document_id:
                candidates.append((run_dir, manifest))
        if len(candidates) != 1:
            raise ValueError("candidate has missing or duplicated document run")
        run_dir, manifest = candidates[0]
        records = [record for record in manifest.artifacts.values()
                   if record.kind == "cards" and record.version_inputs.get("chunk_id") == chunk_id
                   and record.version_inputs.get("prompt_version") == prompt_version]
        if len(records) != 1 or records[0].status != "ready" or not records[0].path:
            raise ValueError("candidate screen has missing or non-ready card artifact")
        record = records[0]
        artifact = EvidenceCardsArtifact.model_validate_json(
            (run_dir / record.path).read_text(encoding="utf-8"))
        if (artifact.document_id, artifact.run_id, artifact.source_sha256, artifact.chunk_id,
                artifact.prompt_version) != (document_id, manifest.run_id,
                                             manifest.source_sha256, chunk_id, prompt_version):
            raise ValueError("candidate artifact has stale source identity")
        cards.extend(card.model_dump(mode="json") for card in artifact.cards)
        first_response += record.attempts == 1
    return cards, {"ready_chunks": len(sample), "first_response_ready": first_response}


def numeric_screen(old: dict, new: dict) -> dict:
    fields = FIELDS
    eligible = sum(old[field].get("supported", 0) for field in fields)
    old_covered = sum(old[field].get("populated", 0) for field in fields)
    new_covered = sum(new[field].get("populated", 0) for field in fields)
    if eligible == 0:
        raise ValueError("field screen has no applicable labels")
    overall = new_covered / eligible
    gain = (new_covered - old_covered) / eligible
    per_field = {field: (new[field].get("populated", 0) /
                         old[field]["supported"] if old[field].get("supported") else None)
                 for field in fields}
    passes = (overall >= 0.5 and gain >= 0.4
              and all(per_field[field] is not None and per_field[field] >= 0.5
                      for field in ("situation", "action", "result")))
    return {"applicable": eligible, "old_covered": old_covered,
            "candidate_populated_pending_grounding_review": new_covered,
            "candidate_provisional_recall": overall,
            "provisional_gain": gain, "per_field_provisional_recall": per_field,
            "numeric_gate": "provisional_pass" if passes else "fail"}


def source_groups(conn, cards: list[dict], sample: list[dict]) -> list[dict]:
    groups = {}
    for item in sample:
        row = conn.execute("""select r.run_id,r.source_sha256,r.source_bytes,r.parsed,ch.payload
            from knowledge.corpus_state state
            join knowledge.active_runs ar on ar.profile_id=state.profile_id
            join knowledge.runs r on r.run_id=ar.run_id
            join knowledge.profiles p on p.profile_id=ar.profile_id
            join knowledge.chunks ch on ch.run_id=r.run_id
            where r.document_id=%s and ch.chunk_id=%s
              and ch.variant=p.variant and ch.chunking_version=p.chunking_version""",
                           (item["document_id"], item["chunk_id"])).fetchone()
        if row is None:
            raise ValueError(f"sample chunk is not selected: {item['document_id']} {item['chunk_id']}")
        if hashlib.sha256(row[2]).hexdigest() != row[1]:
            raise ValueError("captured source bytes changed")
        document = ParsedDocument.model_validate_json(json.dumps(row[3]))
        chunk = Chunk.model_validate_json(json.dumps(row[4]))
        if document.source_sha256 != row[1]:
            raise ValueError("parsed source hash changed")
        for start, end in _card_groups(document, chunk, split_highlights=True):
            passage = document.source_text[start:end]
            key = (item["document_id"], item["chunk_id"], start, end)
            groups[key] = {"document_id": item["document_id"], "run_id": row[0],
                           "source_sha256": row[1], "chunk_id": item["chunk_id"],
                           "start_char": start, "end_char": end,
                           "group_sha256": hashlib.sha256(passage.encode()).hexdigest(),
                           "source_excerpt": passage, "cards": []}
    for card in cards:
        span = card["evidence_span"]
        key = (card["document_id"], card["chunk_id"], span["start_char"], span["end_char"])
        if key not in groups:
            continue
        group = groups[key]
        if (group["source_excerpt"] != card["evidence_text"]
                or group["run_id"] != card["run_id"]
                or group["source_sha256"] != card["source_sha256"]):
            raise ValueError("selected card span disagrees with captured source")
        group["cards"].append({"card_id": card["card_id"], "card_type": card["card_type"],
                               **{field: card.get(field) for field in FIELDS}})
    return list(groups.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-report", type=Path)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--candidate-runs", type=Path)
    parser.add_argument("--candidate-prompt-version", default="evidence_cards_v8")
    args = parser.parse_args()
    root = Path(".local").resolve()
    target = args.private_output.resolve()
    if not target.is_relative_to(root) or target.exists():
        parser.error("choose a new private output under .local")
    with connect_runtime() as conn, conn.transaction():
        conn.execute("set transaction isolation level repeatable read read only")
        cards = selected_cards(conn)
        sample = (json.loads(args.sample_report.read_text(encoding="utf-8"))["sample"]["cards"]
                  if args.sample_report else selected_chunk_sample(conn))
        groups = source_groups(conn, cards, sample)
    report = {"selected_occupancy": occupancy(cards), "sample_chunks": len(sample),
              "sample_groups": len(groups), "sample": sample, "groups": groups}
    if args.labels:
        labels = json.loads(args.labels.read_text(encoding="utf-8"))["labels"]
        report["field_recall"] = field_recall(groups, labels, cards)
    if args.candidate_runs:
        if not args.labels:
            parser.error("candidate scoring requires source-bound field labels")
        candidate_sample = sample
        candidate, readiness = candidate_cards(args.candidate_runs, candidate_sample, args.candidate_prompt_version)
        candidate_recall = field_recall(groups, labels, candidate)
        report["candidate"] = {**readiness, "occupancy": occupancy(candidate),
                               "field_recall": candidate_recall,
                               "numeric_screen": numeric_screen(report["field_recall"], candidate_recall)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("groups", "sample")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
