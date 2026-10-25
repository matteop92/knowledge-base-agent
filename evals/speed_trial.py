"""Time the web app's answer engine on a private set of questions, with a spending ceiling.

Each question runs through `OpenRouterAnswerEngine`, the code path the web app uses, and
writes one private receipt under `.local/` with the answer, the actions, per-request token
counts and times, the cost, and two times: the receipt's own elapsed seconds and the wall
time of the whole engine call (route checks and any follow-up rewrite included).

The question set is a JSON list kept under `.local/` because the questions are about
private notes: `{"id", "type", "question"}` plus, for a follow-up, `prior_id` (an earlier
question in the same repeat) or `prior_turn_id` (a stored hosted turn). `--config` is a
JSON object of `AnswerSettings` overrides. Every run needs `--max-usd`; before each answer
the run checks that what it has spent plus `--reserve-usd` stays under the ceiling, and the
running total is added to the shared ledger after each answer.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from evals.answer_review import private_output


def percentile(values: list[float], share: float) -> float | None:
    """Nearest-rank percentile, so a reported p95 is a time some answer actually took."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * share // 1))
    return ordered[int(rank) - 1]


def model_seconds(receipt: dict[str, Any]) -> float:
    """Time inside generation requests: the part of an answer that does not depend on where the host runs."""
    return sum(entry.get("seconds") or 0 for entry in receipt["model_usage"]
               if entry.get("output_tokens") is not None)


def summarize(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """p50 and p95 of receipt elapsed and wall time, cost and calls, overall and per type."""
    def block(items: list[dict[str, Any]]) -> dict[str, Any]:
        elapsed = [item["elapsed_seconds"] for item in items]
        wall = [item["wall_seconds"] for item in items]
        model = [model_seconds(item) for item in items]
        return {"answers": len(items), "p50_elapsed": percentile(elapsed, 0.5),
                "p95_elapsed": percentile(elapsed, 0.95), "p50_wall": percentile(wall, 0.5),
                "p95_wall": percentile(wall, 0.95), "p50_model": percentile(model, 0.5),
                "p95_model": percentile(model, 0.95),
                "mean_cost_usd": round(statistics.fmean(item["cost_usd"] for item in items), 5) if items else None,
                "mean_model_calls": round(statistics.fmean(item["model_calls"] for item in items), 2) if items else None,
                "failed": sum(item["termination"] != "completed" for item in items)}
    types = sorted({item["type"] for item in receipts})
    return {"all": block(receipts), **{name: block([item for item in receipts if item["type"] == name])
                                       for name in types}}


class Ledger:
    """The task's running spend, shared by every trial and grading run."""

    def __init__(self, path: Path, label: str) -> None:
        self.path, self.label, self.lock = path, label, threading.Lock()

    def total(self) -> Decimal:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return Decimal(str(data["spent_usd"]))

    def add(self, usd: float, note: str) -> None:
        with self.lock:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data["spent_usd"] = round(float(Decimal(str(data["spent_usd"])) + Decimal(str(usd))), 6)
            data["entries"].append({"run": self.label, "note": note, "usd": round(usd, 6),
                                    "total_usd": data["spent_usd"]})
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", type=Path, required=True, help="question set JSON under .local/")
    parser.add_argument("--config", default="{}", help="JSON object of AnswerSettings overrides")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", nargs="*", help="question ids to run (follow-ups bring their prior)")
    parser.add_argument("--max-usd", type=Decimal, required=True, help="ceiling on this run's spend")
    parser.add_argument("--reserve-usd", type=Decimal, default=Decimal("0.10"),
                        help="room kept for the next answer before it starts")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory under .local/")
    parser.add_argument("--workers", type=int, default=3, help="repeats run side by side")
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args(argv)

    from app.agent.memory import prior_turn_from_receipt
    from app.api.answers import OpenRouterAnswerEngine, build_receipt
    from app.api.settings import AnswerSettings
    from app.storage.loader import connect_runtime

    questions = json.loads(args.set.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only)
        wanted |= {item["prior_id"] for item in questions if item["id"] in wanted and item.get("prior_id")}
        questions = [item for item in questions if item["id"] in wanted]
    overrides = json.loads(args.config)
    defaults = AnswerSettings()
    # JSON has no decimals: price ceilings arrive as strings or numbers.
    settings = dataclasses.replace(defaults, **{key: Decimal(str(value)) if isinstance(getattr(defaults, key), Decimal)
                                                else value for key, value in overrides.items()})
    engine = OpenRouterAnswerEngine(type("Shim", (), {"answer": settings})(), connect_runtime)
    target = private_output(args.output)
    target.mkdir()
    ledger = Ledger(args.ledger, target.name)
    spent = Decimal(0)
    spend_lock = threading.Lock()
    stopped: list[str] = []
    receipts: list[dict[str, Any]] = []
    with connect_runtime() as conn:
        revisions = dict(conn.execute("""select r.document_id,r.source_sha256 from knowledge.runs r
            join knowledge.active_runs ar on ar.run_id=r.run_id
            join knowledge.corpus_state state on state.profile_id=ar.profile_id""").fetchall())
        stored = {}
        for item in questions:
            if item.get("prior_turn_id"):
                row = conn.execute("select question, receipt from knowledge.turns where turn_id=%s",
                                   (item["prior_turn_id"],)).fetchone()
                stored[item["id"]] = prior_turn_from_receipt(row[0], row[1])
    # Priors first, so a follow-up can use this repeat's earlier answer.
    ordered = sorted(questions, key=lambda item: bool(item.get("prior_id")))

    def run_repeat(repeat: int) -> None:
        nonlocal spent
        answers: dict[str, dict] = {}
        for item in ordered:
            with spend_lock:
                if spent + args.reserve_usd > args.max_usd or ledger.total() + args.reserve_usd > Decimal("8"):
                    stopped.append(f"{item['id']} r{repeat}")
                    continue
            prior = None
            if item.get("prior_turn_id"):
                prior = [stored[item["id"]]]
            elif item.get("prior_id"):
                earlier = answers.get(item["prior_id"])
                if earlier is None:
                    stopped.append(f"{item['id']} r{repeat} (no prior)")
                    continue
                prior = [prior_turn_from_receipt(earlier["question"], earlier["receipt"])]
            started = time.monotonic()
            error = None
            try:
                outcome = engine.answer(item["question"], prior)
                result, cost, usage = outcome.result, outcome.cost_usd, outcome.model_usage
            except Exception as exc:  # noqa: BLE001 - a failed answer is a result too
                result, cost = None, getattr(exc, "cost_usd", 0.0)
                usage = getattr(exc, "model_usage", [])
                error = type(exc.__cause__ or exc).__name__
            wall = time.monotonic() - started
            with spend_lock:
                spent += Decimal(str(cost))
            ledger.add(cost, f"{item['id']} r{repeat}")
            receipt = build_receipt(result, {}, "vault") if result is not None else {}
            record = {"id": item["id"], "type": item["type"], "repeat": repeat, "question": item["question"],
                      "elapsed_seconds": result.elapsed_seconds if result else wall, "wall_seconds": wall,
                      "model_calls": result.model_calls if result else len(usage),
                      "termination": result.termination if result else "error", "error": error,
                      "cost_usd": cost, "model_usage": usage,
                      "actions": result.actions if result else [], "receipt": receipt,
                      "route": getattr(result, "route", None) if result else None,
                      # The grader reads the answer from `result` and the revisions from `configuration`.
                      "status": "completed" if result and result.termination == "completed" else "failed",
                      "configuration": {"source_revisions": revisions, "settings": overrides},
                      "result": result.model_dump(mode="json") if result else None}
            answers[item["id"]] = record
            path = target / item["id"] / f"r{repeat}.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            with spend_lock:
                receipts.append(record)
            print(f"{item['id']} r{repeat} {record['elapsed_seconds']:.1f}s calls={record['model_calls']} "
                  f"${cost:.4f} {record['termination']}", flush=True)

    repeats = list(range(1, args.repeats + 1))
    for start in range(0, len(repeats), max(1, args.workers)):
        threads = [threading.Thread(target=run_repeat, args=(repeat,))
                   for repeat in repeats[start:start + args.workers]]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    summary = {"config": overrides, "spent_usd": str(spent), "not_run": stopped,
               "summary": summarize(receipts)}
    (target / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
