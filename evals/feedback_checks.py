"""Checks run by code for evals made from feedback: no check uses a model to judge a result.

- `route`: the router's route and narrowing against the expected ones.
- `result_set`: the document IDs a catalog answer matched against the expected IDs.
- `attribution`: stricter than the runtime check in `app.agent.attribution`. Every sentence that cites
  a passage of the given transcript said by someone other than the person the question asks about
  must name that passage's speaker, so a pronoun ("he cites X") or unnamed advice fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.agent.attribution import CITATION, named_speakers, split_sentences
from app.agent.catalog import fold

KINDS = ("route", "result_set", "attribution")
ROUTES = ("plain", "narrowed", "catalog")


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    detail: str


def validate_check(check: Any) -> dict:
    """The check as a dict, or ValueError naming what is wrong with it."""
    if not isinstance(check, dict) or check.get("kind") not in KINDS:
        raise ValueError(f"check.kind must be one of {', '.join(KINDS)}")
    kind = check["kind"]
    if kind == "route" and check.get("route") not in ROUTES:
        raise ValueError("a route check needs route: plain, narrowed or catalog")
    if kind == "result_set" and not (isinstance(check.get("ids"), list)
                                     and all(isinstance(item, str) for item in check["ids"])):
        raise ValueError("a result_set check needs a list of document ids (empty when nothing should match)")
    if kind == "attribution" and not (check.get("document") and check.get("person")):
        raise ValueError("an attribution check needs the transcript document and the person")
    return check


def check_route(check: dict, routing: dict | None) -> CheckResult:
    """`routing` is the router's decision record (`RouteDecision.record()`) or a receipt's `routing`."""
    if not routing:
        return CheckResult(False, "no routing record")
    got = routing.get("route")
    if got != check["route"]:
        return CheckResult(False, f"routed {got}, expected {check['route']}")
    if got != "narrowed":
        return CheckResult(True, f"routed {got}")
    narrowing = routing.get("narrowing") or {}
    problems = []
    for key, field in (("types", "types"), ("folders", "folders")):
        accepted = check.get(key)
        if accepted and not set(narrowing.get(field) or []) & set(accepted):
            problems.append(f"{field} {narrowing.get(field) or []} not in {accepted}")
    if "period" in check and bool(narrowing.get("period")) != bool(check["period"]):
        problems.append("period " + ("missing" if check["period"] else "unexpected"))
    if "latest" in check and bool(narrowing.get("latest")) != bool(check["latest"]):
        problems.append("latest " + ("missing" if check["latest"] else "unexpected"))
    return CheckResult(not problems, "; ".join(problems) or "routed narrowed with the expected scope")


def check_result_set(check: dict, routing: dict | None) -> CheckResult:
    matched = (routing or {}).get("matched")
    if matched is None:
        return CheckResult(False, "the answer matched no catalog result set")
    expected = list(check["ids"])
    if check.get("ordered"):
        same = matched == expected
    else:
        same = set(matched) == set(expected) and len(matched) == len(set(matched))
    if same:
        return CheckResult(True, f"{len(matched)} document(s) match")
    missing = [item for item in expected if item not in matched]
    extra = [item for item in matched if item not in expected]
    detail = f"missing {missing}" if missing else ""
    detail += (" " if detail else "") + (f"extra {extra}" if extra else "")
    if not missing and not extra:
        detail = "same documents in another order"
    return CheckResult(False, detail)


Lookup = Callable[[str, int], "str | None"]


def check_attribution(check: dict, receipt: dict, lookup: Lookup | None = None) -> CheckResult:
    """`lookup(run_id, start_char)` gives the speaker of a passage saved before passages carried one;
    it raises LookupError with a message when the run is no longer stored."""
    document, person = check["document"], fold(check["person"])
    citations = receipt.get("citations") or {}
    failures, examined = [], 0
    for sentence in split_sentences(receipt.get("answer") or ""):
        for ref in dict.fromkeys(CITATION.findall(sentence)):
            passage = citations.get(ref)
            if not passage or document not in (passage.get("document_id"), passage.get("relative_path")):
                continue
            speaker = passage.get("speaker")
            if speaker is None and lookup is not None:
                try:
                    speaker = lookup(passage["run_id"], passage["start_char"])
                except LookupError as exc:
                    return CheckResult(False, str(exc))
            if not speaker or fold(speaker) == person:
                continue
            examined += 1
            if not named_speakers(sentence, [speaker]):
                failures.append(f"{ref} was said by {speaker}, but the sentence does not name them: {sentence[:120]}")
    if failures:
        return CheckResult(False, "; ".join(failures))
    return CheckResult(True, f"{examined} passage(s) by other speakers, each named"
                       if examined else "no sentence cites another speaker of the transcript")
