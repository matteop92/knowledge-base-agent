"""Turn exported thumbs-down turns into evals, run them with repeat runs and compare two versions.

An eval set is a YAML file under `.local/` (it names real notes and holds real questions): a list
under `evals:` of `{id, question, check, approved}`. `check.kind` is `route`, `result_set` or
`attribution` (see `evals/feedback_checks.py`); every check is run by code. Only approved entries run.

    python -m evals.feedback_eval promote --export .local/feedback/export.json --set .local/feedback/new.yaml --turn <id>
    python -m evals.feedback_eval import-routes --questions .local/router-experiment/questions.json --set .local/feedback/routes.yaml
    python -m evals.feedback_eval run --set .local/feedback/seed.yaml --output .local/feedback/runs/v15 \\
        --config '{"prompt_version": "answer_agent_v15"}' --repeats 3 --max-usd 1 --ledger .local/feedback/ledger.json --live
    python -m evals.feedback_eval compare .local/feedback/runs/v13/summary.json .local/feedback/runs/v15/summary.json

A seed eval passes only when all of its runs pass. Every run needs `--max-usd`; before each call the
run checks that what it has spent plus `--reserve-usd` stays under the ceiling and stops otherwise,
marking the summary incomplete.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import yaml

from evals.answer_review import private_output
from evals.feedback_checks import CheckResult, check_attribution, check_result_set, check_route, validate_check


def private_input(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to((Path.cwd() / ".local").resolve()) or not resolved.is_file():
        raise ValueError("choose an existing file under .local/")
    return resolved


def load_set(path: Path) -> list[dict]:
    """The entries of an eval set; drafts may carry an unfinished check, approved entries may not."""
    data = yaml.safe_load(private_input(path).read_text(encoding="utf-8")) or {}
    entries = data.get("evals") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("an eval set needs an `evals:` list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("question"):
            raise ValueError("every eval needs an id and a question")
        if entry["id"] in seen:
            raise ValueError(f"duplicate eval id {entry['id']}")
        seen.add(entry["id"])
        if entry.get("approved") is True:
            validate_check(entry.get("check"))
    return entries


def write_set(path: Path, entries: list[dict]) -> Path:
    target = private_output(path)
    target.write_text(yaml.safe_dump({"evals": entries}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return target


def promote(export: Path, target: Path, turn_ids: list[str]) -> Path:
    """Copy chosen exported turns into a new set as drafts (`approved: false`). Never overwrites a set."""
    turns = {turn["turn_id"]: turn for turn in json.loads(private_input(export).read_text(encoding="utf-8"))["turns"]}
    unknown = [turn_id for turn_id in turn_ids if turn_id not in turns]
    if unknown:
        raise ValueError(f"turn not in the export: {unknown[0]}")
    entries = [{"id": f"feedback-{turn_id[:8]}", "question": turns[turn_id]["question"], "source_turn": turn_id,
                "note": turns[turn_id].get("note"), "check": {"kind": "TODO"}, "approved": False}
               for turn_id in turn_ids]
    return write_set(target, entries)


def import_routes(questions: Path, target: Path) -> Path:
    """The router experiment's labelled questions as an approved route set."""
    entries = []
    for index, item in enumerate(json.loads(private_input(questions).read_text(encoding="utf-8")), 1):
        check: dict[str, Any] = {"kind": "route", "route": item["route"]}
        if item["route"] == "narrowed":
            for key, field in (("type", "types"), ("folder", "folders")):
                if item.get(key):
                    check[field] = item[key]
            check["period"] = bool(item.get("date"))
        entries.append({"id": f"route-{index:03d}", "question": item["q"], "check": check, "approved": True})
    return write_set(target, entries)


Answer = Callable[[str], "tuple[dict, float]"]  # question -> (receipt, cost in USD)
Route = Callable[[str], "tuple[dict, float]"]  # question -> (routing record, cost in USD)


def run_check(entry: dict, receipt: dict, routing: dict | None, lookup) -> CheckResult:
    check = entry["check"]
    if check["kind"] == "route":
        return check_route(check, routing)
    if check["kind"] == "result_set":
        return check_result_set(check, receipt.get("routing"))
    return check_attribution(check, receipt, lookup)


def run_evals(entries: list[dict], *, answer: Answer, route: Route, repeats: int, max_usd: Decimal,
              reserve_usd: Decimal, lookup=None, save: Callable[[str, int, dict], None] | None = None) -> dict:
    """Run every approved eval `repeats` times; stop before a call that could pass the ceiling."""
    approved = [entry for entry in entries if entry.get("approved") is True]
    results: dict[str, list[dict]] = {entry["id"]: [] for entry in approved}
    spent, incomplete = Decimal(0), False
    for repeat in range(1, repeats + 1):
        for entry in approved:
            if spent + reserve_usd > max_usd:
                incomplete = True
                continue
            kind = entry["check"]["kind"]
            receipt: dict = {}
            try:
                if kind == "route":
                    routing, cost = route(entry["question"])
                else:
                    receipt, cost = answer(entry["question"])
                    routing = receipt.get("routing")
                outcome = run_check(entry, receipt, routing, lookup)
            except Exception as exc:  # noqa: BLE001 - a failed call is a failed run, and its cost is unknown
                cost, outcome, routing = float(reserve_usd), CheckResult(False, f"error: {type(exc).__name__}"), None
            spent += Decimal(str(cost))
            results[entry["id"]].append({"repeat": repeat, "passed": outcome.passed, "detail": outcome.detail,
                                         "cost_usd": cost})
            if save:
                save(entry["id"], repeat, receipt or {"routing": routing})
    evals = {}
    for entry in approved:
        runs = results[entry["id"]]
        passes = sum(item["passed"] for item in runs)
        evals[entry["id"]] = {"kind": entry["check"]["kind"], "runs": len(runs), "passes": passes,
                              "pass_rate": round(passes / len(runs), 3) if runs else None,
                              "all_runs_pass": bool(runs) and passes == len(runs) == repeats,
                              "details": [item["detail"] for item in runs if not item["passed"]]}
    return {"spent_usd": str(spent), "max_usd": str(max_usd), "complete": not incomplete, "repeats": repeats,
            "skipped_unapproved": len(entries) - len(approved), "evals": evals,
            "route_checks_passed": sum(item["passes"] for item in evals.values() if item["kind"] == "route"),
            "route_checks_total": sum(item["runs"] for item in evals.values() if item["kind"] == "route")}


def compare(first: dict, second: dict, names: tuple[str, str] = ("A", "B")) -> list[str]:
    """One line per eval id in either summary, with both pass rates."""
    ids = list(dict.fromkeys([*first["evals"], *second["evals"]]))

    def rate(summary: dict, eval_id: str) -> str:
        item = summary["evals"].get(eval_id)
        return f"{item['passes']}/{item['runs']}" if item else "-"

    lines = [f"{'eval':<28}{names[0]:>8}{names[1]:>8}"]
    lines += [f"{eval_id:<28}{rate(first, eval_id):>8}{rate(second, eval_id):>8}" for eval_id in ids]
    for name, summary in zip(names, (first, second)):
        if not summary.get("complete", True):
            lines.append(f"{name} is incomplete: the spend ceiling stopped it")
    return lines


def speaker_lookup(connect) -> Callable[[str, int], str | None]:
    """The speaker of a passage in an old receipt, read from the stored run; raises LookupError when it is gone."""
    from app.storage.retrieval import run_turns, speaker_at

    cache: dict[str, list[dict] | None] = {}
    conn = connect()

    def lookup(run_id: str, start_char: int) -> str | None:
        if run_id not in cache:
            cache[run_id] = run_turns(conn, run_id)
        if cache[run_id] is None:
            raise LookupError(f"run {run_id} is no longer stored, so its speakers cannot be looked up")
        return speaker_at({"turns": cache[run_id]}, start_char)

    return lookup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("promote")
    p.add_argument("--export", type=Path, required=True)
    p.add_argument("--set", type=Path, required=True, help="new set file under .local/")
    p.add_argument("--turn", action="append", required=True, dest="turns")
    p = sub.add_parser("import-routes")
    p.add_argument("--questions", type=Path, required=True)
    p.add_argument("--set", type=Path, required=True, help="new set file under .local/")
    p = sub.add_parser("run")
    p.add_argument("--set", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True, help="new directory under .local/")
    p.add_argument("--config", default="{}", help="JSON object of AnswerSettings overrides")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--max-usd", type=Decimal, required=True, help="ceiling on this run's spend")
    p.add_argument("--reserve-usd", type=Decimal, default=Decimal("0.15"),
                   help="room kept for the next call before it starts")
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--live", action="store_true", required=True)
    p = sub.add_parser("compare")
    p.add_argument("summaries", nargs=2, type=Path)
    args = parser.parse_args(argv)

    if args.command == "promote":
        print(f"{len(args.turns)} draft(s) written to {promote(args.export, args.set, args.turns)}")
    elif args.command == "import-routes":
        print(f"route set written to {import_routes(args.questions, args.set)}")
    elif args.command == "compare":
        summaries = [json.loads(private_input(path).read_text(encoding="utf-8")) for path in args.summaries]
        print("\n".join(compare(*summaries, names=tuple(path.parent.name for path in args.summaries))))
    else:
        return run_command(args)
    return 0


def run_command(args) -> int:
    from app.agent.router import route_question
    from app.api.answers import OpenRouterAnswerEngine, build_receipt
    from app.api.settings import AnswerSettings
    from app.models.openrouter import OpenRouterClient
    from app.models.routes import PRIVACY
    from app.agent.properties import to_sources
    from app.storage.loader import connect_runtime
    from app.storage.search import load_sources
    from evals.speed_trial import Ledger

    entries = load_set(args.set)
    defaults = AnswerSettings()
    overrides = json.loads(args.config)
    settings = dataclasses.replace(defaults, **{key: Decimal(str(value)) if isinstance(getattr(defaults, key), Decimal)
                                                else value for key, value in overrides.items()})
    engine = OpenRouterAnswerEngine(type("Shim", (), {"answer": settings})(), connect_runtime)
    target = private_output(args.output)
    target.mkdir()
    ledger = Ledger(args.ledger, target.name)
    ledger_data = json.loads(args.ledger.read_text(encoding="utf-8"))
    if Decimal(str(ledger_data["spent_usd"])) + args.max_usd > Decimal(str(ledger_data["cap_usd"])):
        raise SystemExit("this run's ceiling would pass the task's total cap")
    sources = to_sources(load_sources(connect_runtime()))

    def answer(question: str) -> tuple[dict, float]:
        outcome = engine.answer(question, None)
        ledger.add(outcome.cost_usd, f"answer {question[:40]}")
        return build_receipt(outcome.result, {}, "vault"), outcome.cost_usd

    def route(question: str) -> tuple[dict, float]:
        client = OpenRouterClient(allow_live=True, request_budget=2, estimated_input_token_budget=200_000,
                                  conservative_input_budget=True, provider=dict(PRIVACY))
        decision = route_question(question, sources, client=client)
        cost = sum(client.decision_costs)
        ledger.add(cost, f"route {question[:40]}")
        return decision.record(), cost

    def save(eval_id: str, repeat: int, receipt: dict) -> None:
        path = target / re.sub(r"[^\w.-]", "_", eval_id) / f"r{repeat}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    summary = run_evals(entries, answer=answer, route=route, repeats=args.repeats, max_usd=args.max_usd,
                        reserve_usd=args.reserve_usd, lookup=speaker_lookup(connect_runtime), save=save)
    from app.agent.harness import PROMPT_FAMILIES, PROMPT_VERSION, load_prompt
    from app.agent.router import SPEC_SHA256, SPEC_VERSION
    import hashlib

    version = PROMPT_FAMILIES[settings.prompt_version or PROMPT_VERSION][0]
    summary |= {"config": overrides, "prompt_version": version,
                "prompt_sha256": hashlib.sha256(load_prompt(version).encode()).hexdigest(),
                "router_spec": SPEC_VERSION, "router_spec_sha256": SPEC_SHA256}
    (target / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "evals"}, indent=1))
    for eval_id, item in summary["evals"].items():
        if item["kind"] != "route" or item["passes"] != item["runs"]:
            print(f"{eval_id}: {item['passes']}/{item['runs']} {item['details'][:2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
