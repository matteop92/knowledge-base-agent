"""Sources and their properties, and the answer to a catalog question.

"How many sources have author Ada Stone?" or "what is the latest note I uploaded?" are
counts and lists over each source's properties, not questions about what the notes say.
Searching chunk text for them is slow and miscounts (a source is several chunks), so the
question router (`app.agent.router`) sends them here: one query loads every active source's
properties, a validated plan (`app.agent.catalog`) picks the matches, and the answer is
written from the matches, led by how the question was read, with each listed source cited by
its first passage.

Properties follow the search labels (`app.storage.search.source_label`): the file's own
frontmatter wins and generated properties fill only what it leaves out. A source's type is
its generated type, or else its folder (Articles, Transcripts, Books, Book Highlights, Notes).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from app.agent.records import AnswerResult
from app.storage.records import FusedHit, SearchHit, SourcePassage
from app.storage.search import source_label

FOLDER_TYPES = {"articles": "article", "transcripts": "transcript", "books": "book",
                "book highlights": "highlights", "notes": "note"}
MAX_CITED = 12  # listed sources cited by their first passage; the rest are named only


@dataclass(frozen=True)
class Source:
    document_id: str
    run_id: str
    source_sha256: str
    root_alias: str
    relative_path: str
    title: str
    authors: list[str]
    guests: list[str]
    people: list[str]
    tags: list[str]
    type: str | None
    date: str | None
    chunks: int
    first_chunk: dict[str, Any] | None  # chunk_id and source_spans of the earliest chunk
    folder: str = ""
    added_at: datetime | None = None  # when the source first entered the index (see `added_date`)
    updated_at: datetime | None = None  # when its active version was loaded
    added_from_date: bool = False  # added_at was taken from the note's own date (first bulk load)


def _names(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [item.removeprefix("[[").removesuffix("]]").strip() for item in values
            if isinstance(item, str) and item.strip()]


def source_type(generated: dict, relative_path: str, frontmatter: dict | None = None) -> str | None:
    frontmatter = frontmatter or {}
    for value in (frontmatter.get("type"), frontmatter.get("source_type"), generated.get("source_type")):
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    folder = PurePosixPath(relative_path).parent.name.lower()
    return FOLDER_TYPES.get(folder)


FILENAME_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
# Everything first loaded at or before this instant came in with the first bulk load of the vault,
# so its load time says nothing about when the note was added (the loads settled by 2026-10-18 10:18 UTC).
BULK_LOAD_CUTOFF = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def _day(value: object) -> datetime | None:
    match = re.match(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    try:
        return datetime.strptime(match.group(), "%Y-%m-%d").replace(tzinfo=timezone.utc) if match else None
    except ValueError:
        return None


def added_date(first_loaded: datetime | None, metadata: dict, relative_path: str) -> tuple[datetime | None, bool]:
    """When a source was added, and whether the note's own date stood in for the load time.

    A note first loaded in the bulk load takes its created date, else a date in its filename,
    else its load time. Any later note keeps its load time.
    """
    if first_loaded is None or first_loaded > BULK_LOAD_CUTOFF:
        return first_loaded, False
    named = FILENAME_DATE.search(PurePosixPath(relative_path).name)
    own = _day(metadata.get("created")) or (_day(named.group()) if named else None)
    return (own, True) if own else (first_loaded, False)


def to_source(document_id: str, run_id: str, sha: str, root_alias: str, relative_path: str,
              metadata: dict, chunks: int, first_chunk: dict | None, *,
              added_at: datetime | None = None, updated_at: datetime | None = None) -> Source:
    title, people, tags = source_label(metadata, relative_path)
    frontmatter = metadata.get("frontmatter") if isinstance(metadata.get("frontmatter"), dict) else {}
    generated = metadata.get("generated") if isinstance(metadata.get("generated"), dict) else {}
    # Same order as the tag classifier; the generated date exists only when the frontmatter has none.
    date = next((str(value)[:10] for value in (metadata.get("published"), metadata.get("date"),
                                               metadata.get("created"), generated.get("date")) if value), None)
    added = added_date(added_at, metadata, relative_path)
    return Source(document_id=document_id, run_id=run_id, source_sha256=sha, root_alias=root_alias,
                  relative_path=relative_path, title=title, authors=_names(metadata.get("authors")),
                  guests=list(dict.fromkeys(_names(metadata.get("guests")) + _names(frontmatter.get("guest")))),
                  people=people, tags=tags, type=source_type(generated, relative_path, frontmatter), date=date,
                  chunks=chunks, first_chunk=first_chunk, folder=PurePosixPath(relative_path).parent.as_posix(),
                  added_at=added[0], updated_at=updated_at, added_from_date=added[1])


def to_sources(rows: list[dict]) -> list[Source]:
    """Sources from `app.storage.search.load_sources` rows."""
    return [to_source(row["document_id"], row["run_id"], row["source_sha256"], row["root_alias"],
                      row["relative_path"], row["metadata"], row["chunks"], row["first_chunk"],
                      added_at=row.get("added_at"), updated_at=row.get("updated_at")) for row in rows]


def _line(source: Source, citation: str | None) -> str:
    details = [", ".join(source.authors) or ", ".join(source.people)]
    if source.guests:
        details.append("guest " + ", ".join(source.guests))
    details += [source.type or "", source.date or ""]
    shown = " · ".join(item for item in details if item)
    cite = f" [{citation}]" if citation else ""
    # The title leads in bold; the detail line under it carries the people, type and date.
    return f"- **{source.title}**" + (f"\n  {shown}{cite}" if shown else cite)


SORT_PHRASES = {"added_newest": "when it was added, newest first", "added_oldest": "when it was added, oldest first",
                "dated_newest": "its own date, newest first", "dated_oldest": "its own date, oldest first"}


def describe(plan) -> str:
    """The plan's filters as a phrase that follows "sources", for the reading sentence and the answer."""
    parts = []
    if plan.types:
        parts.append("of type " + " or ".join(plan.types))
    if plan.folders:
        parts.append("in " + " or ".join(plan.folders))
    if plan.person:
        parts.append({"author": f"with author {plan.person}", "guest": f"with {plan.person} as a guest",
                      "any": f"naming {plan.person}"}[plan.role])
    if plan.tag:
        parts.append(f"tagged {plan.tag}")
    if plan.period:
        parts.append(f"{'added' if plan.period.field == 'added' else 'dated'} {plan.period.label.removeprefix('from ')}")
    if plan.title:
        parts.append(f"whose title matches “{plan.title}”")
    return " and ".join(parts)


def reading(plan, shown: int, from_date: bool) -> str:
    """How the question was read, e.g. "Sorted by when it was added, newest first; showing 1."."""
    described = describe(plan)
    text = f"{'Counted' if plan.op == 'count' else 'Listed'} sources {described}." if described else (
        "Counted all sources." if plan.op == "count" else "Listed all sources.")
    if plan.sort != "none":
        text += f" Sorted by {SORT_PHRASES[plan.sort]}"
        text += f"; showing {shown}." if plan.limit else "."
    elif plan.limit:
        text += f" Showing {shown}."
    if from_date and (plan.sort.startswith("added") or (plan.period and plan.period.field == "added")):
        text += " Notes from the first bulk load use their own date as the date added."
    return text


def answer_catalog(plan, sources: list[Source], *, retriever, started: float, model_calls: int,
                   route: dict[str, Any]) -> AnswerResult:
    """Write the count or list from the plan's matches, led by how the question was read, and cite each
    listed source by its first passage."""
    from app.agent.catalog import apply_plan

    found = apply_plan(sources, plan)
    citations: dict[str, SourcePassage] = {}
    lines = []
    for index, source in enumerate(found, 1):
        citation = None
        if index <= MAX_CITED and source.first_chunk:
            hit = SearchHit(rank=index, score=0, document_id=source.document_id, run_id=source.run_id,
                            source_sha256=source.source_sha256, root_alias=source.root_alias,
                            relative_path=source.relative_path, chunk_id=source.first_chunk["chunk_id"],
                            source_spans=source.first_chunk["source_spans"], raw_text="", title=source.title,
                            people=source.people)
            passage = retriever.read(FusedHit(rank=index, rrf_score=0, channels=["catalog"], hit=hit), 0)
            citation = f"E{index}.0"
            citations[citation] = passage
        lines.append(_line(source, citation))
    described = describe(plan) or "in total"
    noun = "source" if len(found) == 1 else "sources"
    lead = reading(plan, len(found), any(source.added_from_date for source in found))
    if not found:
        answer = f"{lead}\n\nNone of your {len(sources)} sources is {described}."
    elif plan.op == "count":
        answer = f"{lead}\n\nYou have {len(found)} {noun} {described}:\n" + "\n".join(lines)
    else:
        answer = f"{lead}\n\n{len(found)} {noun} {described}:\n" + "\n".join(lines)
    answer += ("\n\nCounted from the stored properties of all your indexed sources: a note's own frontmatter "
               "first, generated properties where it has none.")
    return AnswerResult(answer=answer, completeness="complete", citations=citations, gaps=[],
                        actions=[{"action": "catalog", "route": route, "plan": plan.record(),
                                  "matched": [source.document_id for source in found]}],
                        model_calls=model_calls, inspected=dict(citations), needs={},
                        elapsed_seconds=time.monotonic() - started)
