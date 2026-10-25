"""Grade saved answer receipts with the model grader against the frozen checklist.

`--dry-run` measures every grader input and prints the worst-case token use without a
model call; with OPENROUTER_API_KEY set it also reads the pinned route's current price
(a free metadata request) and prints the worst-case cost. `--live` makes the calls,
one per answer and repeat, and writes one private grade receipt for each under `.local/`.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.models.client import ModelClientError, ModelSpec
from app.models.openrouter import OpenRouterClient, completion_limit
from evals.answer_grader import CHECKLIST_PROMPT, GRADE_SCHEMA_V3, grade_answer, prepare_evidence
from evals.answer_review import private_output, load_receipts
from evals.answer_rubric import Checklist, load_rubric

CHECKLIST = Path("evals/answer_questions_v4_restructured.yaml")
SNAPSHOTS = ".local/ingestion-runs/*/source.md"
EXPECTED_OUTPUT_TOKENS = 6000


def gold_sources(checklist: Checklist, pattern: str = SNAPSHOTS) -> dict[str, bytes]:
    """Captured source snapshots whose bytes match the checklist's pinned revisions."""
    wanted = {revision.source_sha256: revision.document_id for revision in checklist.source_revisions.values()}
    found = {}
    for path in glob.glob(pattern):
        raw = Path(path).read_bytes()
        document_id = wanted.get(hashlib.sha256(raw).hexdigest())
        if document_id is not None:
            found[document_id] = raw
    if len(found) != len(wanted):
        raise ValueError("a pinned source snapshot is missing under .local/ingestion-runs/")
    return found


def receipts_from_judgments(path: str) -> list[str]:
    """The `QID=path` receipts a review export was made from (labels are relative to .local/)."""
    review = json.loads(Path(path).read_text(encoding="utf-8"))
    return [f"{item['question_id']}=.local/{item['label']}" for item in review["reviews"]]


def completion_bound(model_id: str, max_output_tokens: int, reasoning_effort: str | None) -> int:
    """The completion-token ceiling the OpenRouter adapter sends for one grader call."""
    return completion_limit(model_id, "answer_grader_v4", max_output_tokens, reasoning_effort)[1]


def _price(pricing: object, key: str) -> Decimal:
    try:
        value = Decimal(str(pricing[key]))  # type: ignore[index]
    except (KeyError, TypeError, InvalidOperation):
        raise ModelClientError(f"route has no valid {key} price") from None
    if not value.is_finite() or value < 0:
        raise ModelClientError(f"route has no valid {key} price")
    return value


def load_other_sources(path: Path | None) -> dict[str, tuple[str, str]]:
    """`{document_id: {"source_sha256", "path"}}` for notes outside the checklist; each file must match its hash."""
    if path is None:
        return {}
    found = {}
    for document_id, item in json.loads(path.read_text(encoding="utf-8")).items():
        raw = Path(item["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["source_sha256"]:
            raise ValueError("an extra source file does not match its revision")
        found[document_id] = (item["source_sha256"], raw.decode("utf-8"))
    return found


def plan(checklist: Checklist, entries: list[tuple[str, str, dict]], sources: dict[str, bytes],
         other_sources: dict[str, tuple[str, str]] | None = None) -> list[dict]:
    """Prepare every grader input up front, so a bad receipt stops the run before any call."""
    planned = []
    for question_id, label, receipt in entries:
        payload = prepare_evidence(checklist, question_id, receipt, sources, other_sources)
        # Measured the way the adapter sends it: the input is itself a JSON string inside an
        # ASCII-escaped request body, so non-ASCII characters count six bytes each.
        size = len(json.dumps({"system": CHECKLIST_PROMPT, "input": json.dumps(payload, ensure_ascii=False),
                               "schema": GRADE_SCHEMA_V3}).encode("utf-8"))
        planned.append({"question_id": question_id, "label": label, "receipt": receipt, "input_bytes": size})
    return planned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", help="QID=receipt.json paths")
    parser.add_argument("--from-judgments", action="append", default=[],
                        help="a review export; grade the receipts it was made from")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider-tag", help="pinned zero-data-retention endpoint tag")
    parser.add_argument("--reasoning-effort", choices=("minimal", "low", "medium", "high"))
    parser.add_argument("--max-output-tokens", type=int, default=8000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-usd-per-million", type=Decimal,
                        help="ceiling on the route's input and output price per million tokens")
    parser.add_argument("--max-usd", type=Decimal, help="ceiling on the run's worst-case cost")
    parser.add_argument("--output", type=Path, help="new directory under .local/ for grade receipts")
    parser.add_argument("--other-sources", type=Path,
                        help="JSON of notes outside the checklist that answers read: document id to hash and file")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.max_output_tokens < 1:
        parser.error("repeats and output tokens must be positive")
    checklist = load_rubric(CHECKLIST)
    if not isinstance(checklist, Checklist) or checklist.status != "frozen":
        parser.error("grading needs the frozen three-dimension checklist")
    inputs = list(args.inputs) + [item for path in args.from_judgments for item in receipts_from_judgments(path)]
    if not inputs:
        parser.error("no receipts to grade")
    entries = load_receipts(inputs)
    sources = gold_sources(checklist)
    other_sources = load_other_sources(args.other_sources)
    planned = plan(checklist, entries, sources, other_sources)
    calls = len(planned) * args.repeats
    # The adapter's conservative input estimate is request bytes plus 512 per request;
    # allow 2 KB more per request for the model, route and message framing.
    input_tokens = sum(item["input_bytes"] + 512 + 2048 for item in planned) * args.repeats
    output_tokens = calls * completion_bound(args.model, args.max_output_tokens, args.reasoning_effort)
    summary = {"model": args.model, "reasoning_effort": args.reasoning_effort, "answers": len(planned),
               "repeats": args.repeats, "calls": calls, "worst_case_input_tokens": input_tokens,
               "worst_case_output_tokens": output_tokens,
               # About four bytes per token for this English text and JSON; output is a
               # rough per-answer figure for a checklist reply with reasons.
               "expected_input_tokens": sum(item["input_bytes"] for item in planned) * args.repeats // 4,
               "expected_output_tokens": calls * EXPECTED_OUTPUT_TOKENS,
               "input_bytes_per_answer": {item["label"]: item["input_bytes"] for item in planned}}
    route = {"only": [args.provider_tag], "allow_fallbacks": False, "data_collection": "deny",
             "zdr": True, "require_parameters": True} if args.provider_tag else None
    # A rate-limited request is retried up to twice and each retry takes from the client's
    # allowances, though it is not billed; leave room for that. The cost cap below still
    # counts each call once, at its full input and output allowance.
    client = OpenRouterClient(allow_live=args.live, request_budget=calls * 3,
                              estimated_input_token_budget=input_tokens * 3, conservative_input_budget=True,
                              provider=route, capture_failed_outputs=True)
    endpoint = None
    if args.provider_tag:
        try:
            endpoint = client.require_zdr_route(args.model, args.provider_tag, structured=True)
        except ModelClientError as exc:
            if args.live:
                raise
            summary["route"] = f"not checked: {exc}"
    if endpoint is not None:
        prompt_price = _price(endpoint["pricing"], "prompt")
        completion_price = _price(endpoint["pricing"], "completion")
        worst = Decimal(input_tokens) * prompt_price + Decimal(output_tokens) * completion_price
        expected = (Decimal(summary["expected_input_tokens"]) * prompt_price
                    + Decimal(summary["expected_output_tokens"]) * completion_price)
        summary.update(route=endpoint, worst_case_usd=str(worst.quantize(Decimal("0.0001"))),
                       expected_usd=str(expected.quantize(Decimal("0.0001"))))
    if args.dry_run:
        print(json.dumps({"status": "dry_run", **summary}, indent=2, default=str))
        return 0

    if endpoint is None or args.max_usd is None or args.max_usd_per_million is None or args.output is None:
        parser.error("--live needs --provider-tag, --max-usd-per-million, --max-usd, and --output")
    if args.reasoning_effort and "reasoning" not in endpoint["supported_parameters"]:
        parser.error("this route does not accept a reasoning effort")
    if max(prompt_price, completion_price) * 1_000_000 > args.max_usd_per_million:
        parser.error("route price exceeds --max-usd-per-million")
    if worst > args.max_usd:
        parser.error(f"worst-case cost {worst} exceeds --max-usd")
    target = private_output(args.output)
    target.mkdir()
    model = ModelSpec(provider="openrouter", model_id=args.model, model_version="current")
    spent = Decimal(0)
    written = []
    for repeat in range(1, args.repeats + 1):
        for item in planned:
            before = len(client.billable_requests)
            result = grade_answer(checklist, item["question_id"], item["receipt"], sources,
                                  client=client, model=model, max_output_tokens=args.max_output_tokens,
                                  reasoning_effort=args.reasoning_effort, other_sources=other_sources)
            usage = [usage for _, usage in client.billable_requests[before:]]
            cost = sum(Decimal(u.input_tokens or 0) * prompt_price + Decimal(u.output_tokens or 0) * completion_price
                       for u in usage)
            spent += cost
            name = re.sub(r"[^A-Za-z0-9]+", "-", item["label"]).strip("-")
            path = target / f"{name}.repeat-{repeat}.json"
            path.write_text(json.dumps({**result, "label": item["label"], "repeat": repeat,
                                        "route": endpoint, "usage": [u.model_dump() for u in usage],
                                        "usd": str(cost)}, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(path.name)
            print(item["label"], f"repeat {repeat}", result["status"], f"${cost:.4f}")
    manifest = {**summary, "spent_usd": str(spent), "grades": written,
                "rate_limit_retries": client.rate_limit_retries,
                "failed_outputs": client.failed_outputs}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
                                          encoding="utf-8")
    print(json.dumps({"spent_usd": str(spent), "grades": len(written)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
