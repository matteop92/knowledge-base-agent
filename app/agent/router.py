"""Routes every question to one of three paths with one Jev call: catalog, narrowed or plain.

- `catalog`: a count or list about the collection itself, answered from a typed plan applied to
  the loaded sources (`app.agent.catalog`).
- `narrowed`: a question about what the sources say that explicitly limits them by type, folder,
  period or "the newest one", answered by the normal loop over only those sources.
- `plain`: everything else, and the default whenever the router is unsure or anything fails.

The wording lives in `prompts/question_router_v1.json` (a versioned spec, hashed into receipts).
Jev reads dates and numbers poorly, so code reads them (`app.agent.periods`). Every value the
model picks must exist in the vault, and a plan that fails validation becomes plain.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.agent.catalog import CatalogPlan, PlanError, build_plan, vocabulary, words
from app.agent.periods import Period, read_limit, read_period
from app.agent.properties import Source
from app.models.decisions import Choice, decide

log = logging.getLogger("knowledge.agent.router")

SPEC_PATH = Path(__file__).resolve().parents[2] / "prompts" / "question_router_v1.json"
SPEC_TEXT = SPEC_PATH.read_text(encoding="utf-8")
SPEC = json.loads(SPEC_TEXT)
SPEC_VERSION = SPEC["version"]
SPEC_SHA256 = hashlib.sha256(SPEC_TEXT.encode("utf-8")).hexdigest()
ROUTE_ORDER = ("plain", "narrowed", "catalog")  # Jev favours the first option, so plain leads
MAX_OPTIONS = 255  # a Choice takes at most this many options, "none" included
QUESTION_CHARS = 1000


TYPE_NOUNS = {"highlights": "book highlights", "book": "book notes", "social_post": "social posts"}


@dataclass
class Narrowing:
    types: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    period: Period | None = None
    latest: int | None = None  # keep only the newest N by added date

    def is_empty(self) -> bool:
        return not (self.types or self.folders or self.period or self.latest)

    def record(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for name in ("types", "folders", "latest"):
            if getattr(self, name):
                data[name] = getattr(self, name)
        if self.period:
            data["period"] = {"start": self.period.start, "end": self.period.end, "field": self.period.field}
        return data


    def noun(self, count: int = 2) -> str:
        """What the scope holds: "notes" for one type, else "sources"."""
        if len(self.types) == 1:
            plural = TYPE_NOUNS.get(self.types[0], self.types[0] + "s")
            return plural if count != 1 else plural.removesuffix("s")
        return "source" if count == 1 else "sources"

    def _where(self) -> str:
        where = " ".join(part for part in (" ".join("in " + folder for folder in self.folders),
                                           self.period.label if self.period else "") if part)
        return where

    def scope_line(self, count: int, from_date: bool) -> str:
        """The sentence the host puts first in a narrowed answer, e.g. "Searched 12 notes from September 2026."."""
        if self.latest:
            line = (f"Searched your newest {self.noun(1)} by date added." if count == 1
                    else f"Searched your {count} newest {self.noun(count)} by date added.")
        else:
            line = f"Searched {count} {self.noun(count)}" + (f" {self._where()}" if self._where() else "") + "."
        if from_date and (self.latest or (self.period and self.period.field == "added")):
            line += " Notes from the first bulk load use their own date as the date added."
        return line

    def widened_line(self) -> str:
        what = "newest " + self.noun() if self.latest else self.noun()
        return (f"No {what}" + (f" {self._where()}" if self._where() else "") +
                " matched; searched everything instead.")


@dataclass
class RouteDecision:
    route: str = "plain"
    probabilities: dict[str, float] = field(default_factory=dict)
    plan: CatalogPlan | None = None
    narrowing: Narrowing | None = None
    called: bool = False  # false when no router call was made (or it failed)

    def record(self, matched: list[str] | None = None) -> dict[str, Any]:
        """The routing record stored in a receipt."""
        data: dict[str, Any] = {"route": self.route, "called": self.called, "spec": SPEC_VERSION,
                                "spec_sha256": SPEC_SHA256, "probabilities": self.probabilities}
        if self.plan:
            data["plan"] = self.plan.record()
        if self.narrowing:
            data["narrowing"] = self.narrowing.record()
        if matched is not None:
            data["matched"] = matched
        return data


def shortlist(values: list[str], question: str, cap: int = MAX_OPTIONS - 1) -> list[str]:
    """At most `cap` values: all of them, or those sharing the most words with the question first."""
    if len(values) <= cap:
        return values
    asked = set(words(question))
    ranked = sorted(values, key=lambda value: (-len(asked & set(words(value))), value))
    return sorted(ranked[:cap])


def _options(values: list[str], describe: dict[str, str] | None = None) -> dict[str, str]:
    return {**{value: (describe or {}).get(value, value) for value in values}, "none": SPEC["none"]}


def build_questions(vocab: dict[str, list[str]], question: str,
                    order: tuple[str, ...] = ROUTE_ORDER) -> dict[str, dict[str, Any]]:
    """Every router question for one call; a value question is left out when the vault has no values."""
    narrowing, catalog = SPEC["narrowing"], SPEC["catalog"]
    questions: dict[str, dict[str, Any]] = {
        "route": {"type": "choice", "instructions": SPEC["route"]["instructions"],
                  "criteria": {name: SPEC["route"]["options"][name] for name in order}},
        "by_type": {"type": "noul", "instructions": narrowing["by_type"]},
        "by_date": {"type": "noul", "instructions": narrowing["by_date"]},
        "by_folder": {"type": "noul", "instructions": narrowing["by_folder"]},
        "by_latest": {"type": "noul", "instructions": narrowing["by_latest"]},
        "op": {"type": "choice", "instructions": catalog["op"], "criteria": dict(catalog["ops"])},
        "person_role": {"type": "choice", "instructions": catalog["person_role"], "criteria": dict(catalog["roles"])},
        "sort": {"type": "choice", "instructions": catalog["sort"], "criteria": dict(catalog["sorts"])},
        "single": {"type": "noul", "instructions": catalog["single"]},
        "cat_by_type": {"type": "noul", "instructions": catalog["by_type"]},
        "cat_by_date": {"type": "noul", "instructions": catalog["by_date"]},
        "cat_by_folder": {"type": "noul", "instructions": catalog["by_folder"]},
        "by_person": {"type": "noul", "instructions": catalog["by_person"]},
        "by_tag": {"type": "noul", "instructions": catalog["by_tag"]},
        "by_title": {"type": "noul", "instructions": catalog["by_title"]},
    }
    for name, instructions, values, describe in (
            ("type_value", narrowing["type_value"], vocab["types"], narrowing["types"]),
            ("folder_value", narrowing["folder_value"], vocab["folders"], None),
            ("person_value", catalog["person_value"], shortlist(vocab["people"], question), None),
            ("tag_value", catalog["tag_value"], shortlist(vocab["tags"], question), None),
            ("title_value", catalog["title_value"], shortlist(vocab["titles"], question), None)):
        if values:
            questions[name] = {"type": "choice", "instructions": instructions, "criteria": _options(values, describe)}
    return questions


def _chosen(answers: dict[str, Any], name: str) -> str | None:
    answer = answers.get(name)
    return answer.choice if isinstance(answer, Choice) and answer.choice != "none" else None


def _yes(answers: dict[str, Any], name: str) -> bool:
    value = answers.get(name)
    return isinstance(value, float) and value >= SPEC["thresholds"]["flag"]


def _flagged(answers: dict[str, Any], flag: str, value: str) -> str | None:
    return _chosen(answers, value) if _yes(answers, flag) else None


def _narrowing(answers: dict[str, Any], question: str, today: date | None) -> Narrowing:
    narrowing = Narrowing()
    latest = _yes(answers, "by_latest")
    # "My most recent article" names a kind of source without saying "only", so a latest question
    # takes the kind the model chose even when the explicit-limit question was not a yes.
    kind = _chosen(answers, "type_value") if latest else _flagged(answers, "by_type", "type_value")
    folder = _flagged(answers, "by_folder", "folder_value")
    narrowing.types = [kind] if kind else []
    narrowing.folders = [folder] if folder else []
    if _yes(answers, "by_date"):
        narrowing.period = read_period(question, today)
    # A stated period and "the newest" do not combine: the period wins.
    if latest and not narrowing.period:
        narrowing.latest = read_limit(question) or 1
    return narrowing


def _plan(answers: dict[str, Any], question: str, vocab: dict[str, list[str]], today: date | None) -> CatalogPlan:
    raw: dict[str, Any] = {"op": _chosen(answers, "op") or "list", "sort": _chosen(answers, "sort") or "none"}
    # A catalog question asks about a kind of source ("how many articles"); it does not say "only".
    kind = _flagged(answers, "cat_by_type", "type_value")
    folder = _flagged(answers, "cat_by_folder", "folder_value")
    period = read_period(question, today) if _yes(answers, "cat_by_date") else None
    if kind:
        raw["types"] = [kind]
    if folder:
        raw["folders"] = [folder]
    if period:
        raw["period"] = period
    for flag, value, key in (("by_person", "person_value", "person"), ("by_tag", "tag_value", "tag"),
                             ("by_title", "title_value", "title")):
        chosen = _flagged(answers, flag, value)
        if chosen:
            raw[key] = chosen
    if raw.get("person"):
        raw["role"] = _chosen(answers, "person_role") or "any"
    limit = 1 if _yes(answers, "single") else read_limit(question)
    if limit:
        raw["limit"] = limit
    return build_plan(raw, vocab)


def interpret(answers: dict[str, Any], question: str, vocab: dict[str, list[str]],
              today: date | None = None) -> RouteDecision:
    """The decision for one set of Jev answers: plain unless the route clears its threshold and
    its filters validate."""
    route = answers.get("route")
    if not isinstance(route, Choice):
        return RouteDecision(called=True)
    decision = RouteDecision(probabilities=route.probabilities, called=True)
    threshold = SPEC["thresholds"].get(route.choice)
    if route.choice == "plain" or threshold is None or route.probabilities.get(route.choice, 0) < threshold:
        return decision
    try:
        if route.choice == "catalog":
            decision.plan = _plan(answers, question, vocab, today)
        else:
            narrowing = _narrowing(answers, question, today)
            if narrowing.is_empty():
                return decision  # "narrowed" without anything to narrow by
            decision.narrowing = narrowing
    except PlanError as exc:
        log.warning("router plan rejected error=%s", exc)
        return decision
    decision.route = route.choice
    return decision


def route_question(question: str, sources: list[Source], *, client, today: date | None = None,
                   order: tuple[str, ...] = ROUTE_ORDER) -> RouteDecision:
    """One Jev call for the question. Any failure (route check, call, unusable answers) is plain."""
    vocab = vocabulary(sources)
    text = question[:QUESTION_CHARS]
    try:
        answers = decide(client, {"question": text}, build_questions(vocab, text, order))
    except Exception as exc:  # noqa: BLE001 - routing is optional: any failure means the normal path
        log.warning("question router skipped error=%s", type(exc).__name__)
        return RouteDecision()
    if answers is None:
        return RouteDecision()
    return interpret(answers, text, vocab, today)

