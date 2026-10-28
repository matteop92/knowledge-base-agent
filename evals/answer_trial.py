"""Frozen pilot review sheets and separate, inspectable answer measurements.

Semantic ratings are human assessments, not inferred from citation counts.
Unreviewed items remain unreviewed rather than becoming passes or failures.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path

from evals.answer_rubric import AnswerRubric, load_rubric, rubric_fingerprint


def load_submitted_review(raw_path: Path, receipt_path: Path) -> dict:
    """Verify the immutable human submission without inferring missing labels."""
    raw = raw_path.read_bytes()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if hashlib.sha256(raw).hexdigest() != receipt.get("raw_sha256"):
        raise ValueError("submitted review checksum changed")
    marker = "identified failures: "
    message = raw.decode("utf-8")
    if marker not in message:
        raise ValueError("submitted review has no rating payload")
    ratings = json.loads(message.split(marker, 1)[1])
    entries = receipt.get("entries", [])
    if len(entries) != receipt.get("entry_count") or set(ratings) != {entry["identity"] for entry in entries}:
        raise ValueError("submitted review identities differ from receipt")
    for entry in entries:
        answer_path = Path(entry["answer_receipt"])
        if hashlib.sha256(answer_path.read_bytes()).hexdigest() != entry["answer_receipt_sha256"]:
            raise ValueError("historical answer receipt changed")
    normalized = {}
    for identity, rating in ratings.items():
        normalized[identity] = {name: ({key: value or None for key, value in field.items()}
                                       if isinstance(field, dict) else field or None)
                                for name, field in rating.items()}
    return {"entry_count": len(entries), "raw": ratings, "normalized": normalized,
            "raw_sha256": receipt["raw_sha256"]}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_configuration(rubric: AnswerRubric, config: dict, directory: Path, ledger_path: Path,
                      observed_hashes: dict[str, str], *, invoke=None, retry_failed: bool = False) -> dict:
    """Sequential, resumable pilot with reservations retained after unknown-cost failures."""
    from app.agent.cli import main as answer_cli
    rubric.require_comparison_ready(observed_hashes)
    planned_routes = {"openai/gpt-5-mini": "azure", "openai/gpt-5.4": "azure",
                      "qwen/qwen3.5-122b-a10b": "siliconflow/fp8"}
    if planned_routes.get(config.get("model")) != config.get("generation_provider_tag"):
        raise ValueError("configuration is outside the planned model routes")
    questions = config.get("question_ids", [])
    if not questions or len(set(questions)) != len(questions) or not set(questions).issubset(
        {item.id for item in rubric.questions}
    ):
        raise ValueError("configuration must select frozen question IDs")
    cap = Decimal(str(config["max_usd"]))
    if not cap.is_finite() or cap <= 0:
        raise ValueError("configuration needs a finite positive spending cap")
    directory = directory.resolve()
    if not directory.is_relative_to(Path(".local").resolve()):
        raise ValueError("pilot reports must stay under .local")
    manifest_path = directory / "pilot.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else
                {"configuration": config, "rubric_version": rubric.version,
                 "source_revisions": observed_hashes, "questions": {}, "status": "running"})
    fingerprint = rubric_fingerprint(rubric)
    from app.agent.harness import load_prompt, prompt_family
    prompt_fingerprint = hashlib.sha256(load_prompt(prompt_family(config.get("prompt_version"))[0]).encode()).hexdigest()
    manifest.setdefault("rubric_sha256", fingerprint)
    manifest.setdefault("prompt_sha256", prompt_fingerprint)
    if (manifest["configuration"] != config or manifest["rubric_version"] != rubric.version
            or manifest["source_revisions"] != observed_hashes
            or manifest["rubric_sha256"] != fingerprint or manifest["prompt_sha256"] != prompt_fingerprint):
        raise ValueError("resume configuration or frozen revisions changed")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger.setdefault("base_stage5_usd", ledger["stage5_estimated_usd_upper_bound"])
    ledger.setdefault("attempts", [])

    def used() -> Decimal:
        return Decimal(str(ledger["base_stage5_usd"])) + sum(
            (Decimal(item["cost_upper_bound"]) for item in ledger["attempts"]), Decimal(0))

    def save_ledger() -> None:
        ledger["stage5_estimated_usd_upper_bound"] = float(used())
        ledger["cumulative_estimated_usd_upper_bound"] = float(used() + Decimal(str(ledger["prior_stage4_usd"])))
        _save_json(ledger_path, ledger)

    for question_id in questions:
        previous = manifest["questions"].get(question_id, [])
        if previous and previous[-1]["status"] == "reserved" and Path(previous[-1]["report"]).exists():
            retained = json.loads(Path(previous[-1]["report"]).read_text(encoding="utf-8"))
            if retained.get("configuration"):
                rubric.require_comparison_ready(retained["configuration"]["source_revisions"])
            previous[-1]["status"] = "completed" if retained.get("status") == "completed" else "failed"
            matches = [item for item in ledger["attempts"] if item["report"] == previous[-1]["report"]]
            if len(matches) != 1:
                raise ValueError("interrupted attempt has an ambiguous spending reservation")
            matches[0]["status"] = previous[-1]["status"]
            if retained.get("usage_complete") and retained.get("estimated_usd") is not None:
                cost = Decimal(retained["estimated_usd"])
                if not cost.is_finite() or not 0 <= cost <= cap:
                    raise ValueError("retained attempt has invalid spend")
                matches[0]["cost_upper_bound"] = str(cost)
            save_ledger()
            _save_json(manifest_path, manifest)
        if previous and previous[-1]["status"] == "completed":
            retained = json.loads(Path(previous[-1]["report"]).read_text(encoding="utf-8"))
            rubric.require_comparison_ready(retained["configuration"]["source_revisions"])
            continue
        if previous and not retry_failed:
            raise ValueError("previous attempt was unfinished or failed; use --retry-failed to retain and retry it")
        if (used() + cap > Decimal(str(ledger["stage5_ceiling_usd"])) or
            used() + cap + Decimal(str(ledger["prior_stage4_usd"])) > Decimal(str(ledger["cumulative_ceiling_usd"]))):
            raise ValueError("remaining Stage 5 or cumulative budget cannot cover this attempt")
        report_path = directory / question_id / f"attempt-{len(previous) + 1}.json"
        reservation = {"report": str(report_path), "question_id": question_id,
                       "status": "reserved", "cost_upper_bound": str(cap)}
        ledger["attempts"].append(reservation)
        save_ledger()
        entry = {"report": str(report_path), "status": "reserved"}
        manifest["questions"].setdefault(question_id, []).append(entry)
        _save_json(manifest_path, manifest)
        question = next(item.question for item in rubric.questions if item.id == question_id)
        args = [question, "--live", "--embedding-provider-tag", "perplexity/int8",
                "--generation-provider-tag", config["generation_provider_tag"],
                "--model", config["model"], "--reasoning-effort", config["reasoning_effort"],
                "--max-embedding-usd-per-million", "0.004", "--max-generation-usd-per-million",
                str(config["max_generation_usd_per_million"]), "--max-usd", str(cap),
                "--report", str(report_path)]
        if "input_byte_budget" in config:
            args.extend(["--input-byte-budget", str(config["input_byte_budget"])])
        if "budget_profile" in config:
            args.extend(["--budget-profile", config["budget_profile"]])
        if "request_budget" in config:
            args.extend(["--request-budget", str(config["request_budget"])])
        with contextlib.redirect_stdout(io.StringIO()):
            code = (invoke or answer_cli)(args)
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {"status": "failed"}
        entry["status"] = "completed" if code == 0 and report["status"] == "completed" else "failed"
        reservation["status"] = entry["status"]
        if report.get("usage_complete") and report.get("estimated_usd") is not None:
            cost = Decimal(report["estimated_usd"])
            if not cost.is_finite() or cost < 0 or cost > cap:
                raise ValueError("reported spend is invalid or exceeds reservation")
            reservation["cost_upper_bound"] = str(cost)
        save_ledger()
        manifest["status"] = "running" if entry["status"] == "completed" else "failed"
        _save_json(manifest_path, manifest)
        print(json.dumps({"question_id": question_id, "status": entry["status"],
                          "stage5_estimated_usd_upper_bound": float(used())}), flush=True)
        if entry["status"] != "completed":
            return manifest
    manifest["status"] = "attempts_finished_review_pending"
    _save_json(manifest_path, manifest)
    return manifest


def review_template(rubric: AnswerRubric, question_id: str) -> dict:
    if rubric.version != 3:
        raise ValueError("historical review template only supports rubric version 3")
    question = next((item for item in rubric.questions if item.id == question_id), None)
    if question is None or rubric.status != "frozen":
        raise ValueError("review requires a frozen question")
    return {
        "rubric_version": rubric.version, "question_id": question.id,
        "answerability": question.answerability,
        "facets": [{"id": f"{question.id}.F{index}", "expected": facet,
                    "rating": None, "answer_excerpt": "", "reason": ""}
                   for index, facet in enumerate(question.essential_facets, 1)],
        "examples": {"rating": None, "reason": ""},
        "qualifications": {"rating": None, "reason": ""},
        "claims": None, "inference_failures": None, "attribution_failures": None,
        "usefulness": None, "usefulness_reason": "",
    }


def summarize_review(rubric: AnswerRubric, review: dict) -> dict:
    question_id = review.get("question_id")
    template = review_template(rubric, question_id)
    if review.get("rubric_version") != rubric.version:
        raise ValueError("review uses a different rubric version")
    if review.get("answerability") != template["answerability"]:
        raise ValueError("review changed question answerability")
    expected = {item["id"]: item["expected"] for item in template["facets"]}
    facets = review.get("facets", [])
    if len(facets) != len(expected) or {item.get("id") for item in facets} != set(expected):
        raise ValueError("review must grade each frozen facet exactly once")
    counts = dict.fromkeys(("covered", "partial", "missing", "unreviewed"), 0)
    for item in facets:
        if item.get("expected") != expected[item["id"]]:
            raise ValueError("review changed a frozen facet")
        rating = item.get("rating")
        if rating not in (None, "covered", "partial", "missing"):
            raise ValueError("invalid facet rating")
        if rating == "covered" and not item.get("answer_excerpt", "").strip():
            raise ValueError("covered facet needs answer evidence")
        if rating in ("partial", "missing") and not item.get("reason", "").strip():
            raise ValueError("partial or missing facet needs a reason")
        counts[rating or "unreviewed"] += 1
    checks = {}
    for name in ("examples", "qualifications"):
        check = review.get(name, {})
        rating = check.get("rating")
        if rating not in (None, "pass", "partial", "fail"):
            raise ValueError("invalid quality check rating")
        if rating is not None and not check.get("reason", "").strip():
            raise ValueError("quality rating needs evidence or a reason")
        checks[name] = rating or "unreviewed"
    claims = review.get("claims")
    if claims is not None and not isinstance(claims, list):
        raise ValueError("claims must be a reviewed list or null")
    if claims == [] and template["answerability"] != "unsupported":
        raise ValueError("answerable response needs material claim review")
    grounding = {"supported": 0, "unsupported": 0, "unclear": 0}
    for claim in claims or []:
        if claim.get("support") not in grounding or not claim.get("claim") or not claim.get("reason"):
            raise ValueError("claim review needs a claim, support decision, and reason")
        if claim["support"] == "supported" and not claim.get("citation_ids"):
            raise ValueError("supported source claim needs citation references")
        grounding[claim["support"]] += 1
    failures = {}
    for name in ("inference_failures", "attribution_failures"):
        values = review.get(name)
        if values is not None and not isinstance(values, list):
            raise ValueError("failure review must be a list or null")
        failures[name] = None if values is None else len(values)
    usefulness = review.get("usefulness")
    if usefulness not in (None, "useful", "partly useful", "unhelpful"):
        raise ValueError("invalid user usefulness judgment")
    reviewed = (counts["unreviewed"] == 0 and all(value != "unreviewed" for value in checks.values())
                and claims is not None and all(value is not None for value in failures.values())
                and usefulness is not None)
    return {"question_id": question_id, "rubric_version": rubric.version,
            "review_status": "reviewed" if reviewed else "pending",
            "facet_coverage": counts, "claim_support": grounding if claims is not None else "unreviewed",
            **checks, **failures, "usefulness": usefulness or "unreviewed",
            "quality_gate": reviewed and counts["partial"] == counts["missing"] == 0
                and all(value == "pass" for value in checks.values())
                and grounding["unsupported"] == grounding["unclear"] == 0
                and all(value == 0 for value in failures.values()) and usefulness == "useful"}


def _private_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to((Path.cwd() / ".local").resolve()) or resolved.exists():
        raise ValueError("choose a new report path under .local/")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or score a frozen answer pilot review")
    parser.add_argument("action", choices=("make-review", "summarize", "run"))
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--question-id")
    parser.add_argument("--rubric", type=Path, default=Path("evals/answer_questions.yaml"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--budget-ledger", type=Path, default=Path(".local/stage5-budget.json"))
    args = parser.parse_args(argv)
    rubric = load_rubric(args.rubric)
    data = json.loads(args.input.read_text(encoding="utf-8"))
    if args.action == "run":
        if not args.live:
            parser.error("pilot execution requires explicit --live")
        from app.storage.loader import connect_runtime
        with connect_runtime() as conn, conn.transaction():
            conn.execute("set transaction read only")
            observed = dict(conn.execute("""select r.document_id,r.source_sha256 from knowledge.runs r
                join knowledge.active_runs ar on ar.run_id=r.run_id
                join knowledge.corpus_state state on state.profile_id=ar.profile_id""").fetchall())
        result = run_configuration(rubric, data, args.output, args.budget_ledger, observed,
                                   retry_failed=args.retry_failed)
        return 0 if result["status"] == "attempts_finished_review_pending" else 1
    if args.action == "make-review":
        if data.get("status") != "completed":
            raise ValueError("incomplete model attempt cannot enter answer grading")
        rubric.require_comparison_ready(data["configuration"]["source_revisions"])
        result = review_template(rubric, args.question_id)
        result["answer_report"] = str(args.input.resolve())
    else:
        result = summarize_review(rubric, data)
    _private_output(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"action": args.action, "question_id": result["question_id"], "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
