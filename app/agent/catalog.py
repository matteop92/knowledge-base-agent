"""The catalog: every active source with its properties, filtered and sorted in Python.

A catalog question ("how many articles do I have?", "what is the latest note I uploaded?")
is answered from a typed `CatalogPlan` that code validates against the vocabulary and applies
to the loaded list of sources. A model never writes SQL. `resolve_scope` picks the sources a
narrowed question searches (by type, folder and period, or the newest few).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent.periods import Period
from app.agent.properties import Source

OPS = ("list", "count")
ROLES = ("any", "author", "guest")
SORTS = ("none", "added_newest", "added_oldest", "dated_newest", "dated_oldest")
MAX_LIMIT = 100
PLAN_FIELDS = {"op", "types", "folders", "person", "role", "tag", "title", "period", "sort", "limit"}
VOCAB_KEYS = {"type": "types", "folder": "folders", "tag": "tags", "person": "people", "title": "titles"}


class PlanError(ValueError):
    """The plan names something outside the catalog's vocabulary or breaks a bound."""


def fold(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


def words(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold())


def vocabulary(sources: list[Source]) -> dict[str, list[str]]:
    """The values that exist in the vault, for validating a plan and for the router's options."""
    return {"types": sorted({source.type for source in sources if source.type}),
            "folders": sorted({source.folder for source in sources if source.folder}),
            "tags": sorted({tag for source in sources for tag in source.tags}),
            "people": sorted({name for source in sources for name in source.people + source.authors + source.guests}),
            "titles": sorted({source.title for source in sources})}


@dataclass
class CatalogPlan:
    op: str = "list"
    types: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    person: str | None = None
    role: str = "any"
    tag: str | None = None
    title: str | None = None
    period: Period | None = None
    sort: str = "none"
    limit: int | None = None

    def record(self) -> dict[str, Any]:
        """The plan as stored in a receipt: only what is set."""
        data: dict[str, Any] = {"op": self.op}
        for name in ("types", "folders", "person", "tag", "title", "limit"):
            if getattr(self, name):
                data[name] = getattr(self, name)
        if self.person:
            data["role"] = self.role
        if self.sort != "none":
            data["sort"] = self.sort
        if self.period:
            data["period"] = self.period.record()
        return data


def build_plan(data: dict[str, Any], vocab: dict[str, list[str]]) -> CatalogPlan:
    """A validated plan from raw values; raises PlanError for an unknown field, a value outside
    the vocabulary, a wrong type or a limit outside 1 to 100. Names are matched without regard
    to case and punctuation and come back as the vault spells them."""
    unknown = set(data) - PLAN_FIELDS
    if unknown:
        raise PlanError(f"unknown plan field: {sorted(unknown)[0]}")
    plan = CatalogPlan(op=data.get("op", "list"), role=data.get("role", "any"), sort=data.get("sort", "none"))
    if plan.op not in OPS or plan.role not in ROLES or plan.sort not in SORTS:
        raise PlanError("op, role or sort is not one of the allowed values")

    def known(kind: str, value: object) -> str:
        if not isinstance(value, str):
            raise PlanError(f"{kind} must be text")
        lookup = {fold(item): item for item in vocab[VOCAB_KEYS[kind]]}
        if fold(value) not in lookup:
            raise PlanError(f"{kind} is not in the vault: {value[:60]}")
        return lookup[fold(value)]

    for name, kind in (("types", "type"), ("folders", "folder")):
        values = data.get(name) or []
        if not isinstance(values, list):
            raise PlanError(f"{name} must be a list")
        setattr(plan, name, [known(kind, value) for value in values])
    for name in ("person", "tag", "title"):
        if data.get(name) is not None:
            setattr(plan, name, known(name, data[name]))
    if data.get("period") is not None:
        if not isinstance(data["period"], Period):
            raise PlanError("period must be read by code")
        plan.period = data["period"]
    limit = data.get("limit")
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise PlanError("limit must be a whole number from 1 to 100")
        plan.limit = limit
    return plan


def _in_period(source: Source, period: Period) -> bool:
    day = source.added_at.date().isoformat() if period.field == "added" and source.added_at else source.date
    return period.contains(day)


def _title_matches(sources: list[Source], phrase_text: str) -> list[Source]:
    """The sources whose titles share the most words with the phrase; none when no word is shared."""
    phrase = words(phrase_text)
    exact = [source for source in sources if words(source.title) == phrase]
    if exact:
        return exact

    def score(source: Source) -> int:
        title = words(source.title)
        return sum(word in title for word in phrase)

    best = max((score(source) for source in sources), default=0)
    return [source for source in sources if best and score(source) == best]


def filter_sources(sources: list[Source], plan: CatalogPlan) -> list[Source]:
    types = {fold(value) for value in plan.types}
    folders = {fold(value) for value in plan.folders}
    found = []
    for source in sources:
        if types and fold(source.type or "") not in types:
            continue
        if folders and fold(source.folder) not in folders:
            continue
        if plan.tag and fold(plan.tag) not in {fold(tag) for tag in source.tags}:
            continue
        if plan.person:
            roles = {"author": source.authors, "guest": source.guests,
                     "any": source.people + source.authors + source.guests}[plan.role]
            if fold(plan.person) not in {fold(name) for name in roles}:
                continue
        if plan.period and not _in_period(source, plan.period):
            continue
        found.append(source)
    return _title_matches(found, plan.title) if plan.title else found


def _stamp(value: datetime | None) -> float:
    return value.timestamp() if value else 0.0


def sort_sources(sources: list[Source], sort: str) -> list[Source]:
    """Ordered newest or oldest by added or own date. Ties break by updated_at (in the same
    direction), then title, then document ID, so the order never changes between calls.
    A source with no date comes last under a dated sort."""
    ordered = sorted(sources, key=lambda source: (fold(source.title), source.document_id))
    if sort == "none":
        return ordered
    newest = sort.endswith("newest")
    ordered.sort(key=lambda source: _stamp(source.updated_at), reverse=newest)
    if sort.startswith("added"):
        ordered.sort(key=lambda source: _stamp(source.added_at), reverse=newest)
        return ordered
    dated = sorted((source for source in ordered if source.date), key=lambda source: source.date, reverse=newest)
    return dated + [source for source in ordered if not source.date]


def apply_plan(sources: list[Source], plan: CatalogPlan) -> list[Source]:
    """The matching sources in order, cut to the plan's limit. A count uses the unlimited matches."""
    found = sort_sources(filter_sources(sources, plan), plan.sort)
    return found if plan.op == "count" or plan.limit is None else found[:plan.limit]


@dataclass(frozen=True)
class Scope:
    document_ids: list[str]
    added_from_date: bool  # some source's added date came from its own date (first bulk load)


def resolve_scope(sources: list[Source], *, types: list[str] | None = None, folders: list[str] | None = None,
                  period: Period | None = None, latest: int | None = None) -> Scope:
    """The document IDs a narrowed question searches. `latest` keeps the newest N by added date."""
    plan = CatalogPlan(types=types or [], folders=folders or [], period=period)
    found = sort_sources(filter_sources(sources, plan), "added_newest" if latest else "none")
    if latest:
        found = found[:latest]
    return Scope([source.document_id for source in found], any(source.added_from_date for source in found))
