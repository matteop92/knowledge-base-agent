"""Questions about the collection itself, answered from stored source properties.

"How many sources have author Ada Stone?" or "which notes are tagged productivity?" are
counts and lists over each source's properties, not questions about what the notes say.
Searching chunk text for them is slow and miscounts (a source is several chunks), so a
router sends them here: one query loads every active source's properties, a filter picks
the matches, and the answer is written from the matches, with each listed source cited by
its first passage. Every other question goes to the answer loop unchanged.

Properties follow the search labels (`app.storage.search.source_label`): the file's own
frontmatter wins and generated properties fill only what it leaves out. A source's type is
its generated type, or else its folder (Articles, Transcripts, Books, Book Highlights, Notes).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from app.agent.records import AnswerResult
from app.models.client import ModelClient, ModelSpec
from app.storage.records import FusedHit, SearchHit, SourcePassage
from app.storage.search import LABEL_METADATA, _profile, source_label

ROUTER_MODEL = "openai/gpt-6-luna"
ROUTER_PROMPT_VERSION = "property_router_v1"
ROUTER_PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / f"{ROUTER_PROMPT_VERSION}.md").read_text(
    encoding="utf-8")
ROUTER_MAX_OUTPUT_TOKENS = 300
ROUTER_VOCABULARY_CHARS = 16_000  # people, tags and types sent to the router, so its cost has a bound
# Worst-case router input, in characters, used to bound its cost before the call.
ROUTER_INPUT_CHARS = len(ROUTER_PROMPT) + ROUTER_VOCABULARY_CHARS + 1500
FOLDER_TYPES = {"articles": "article", "transcripts": "transcript", "books": "book",
                "book highlights": "highlights", "notes": "note"}
MAX_CITED = 12  # listed sources cited by their first passage; the rest are named only
FILTER_FIELDS = ("authors", "guests", "people", "tags", "types")
_list = {"type": "array", "items": {"type": "string"}}
ROUTER_SCHEMA = {"type": "object", "additionalProperties": False,
                 "required": ["route", "count", *FILTER_FIELDS, "date", "title"],
                 "properties": {"route": {"type": "string", "enum": ["properties", "search"]},
                                "count": {"type": "boolean"}, **dict.fromkeys(FILTER_FIELDS, _list),
                                "date": {"type": ["string", "null"]}, "title": {"type": ["string", "null"]}}}


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


@dataclass
class PropertyQuery:
    count: bool = False
    authors: list[str] = field(default_factory=list)
    guests: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    date: str | None = None
    title: str | None = None

    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in (*FILTER_FIELDS, "date", "title"))

    def record(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in ("count", *FILTER_FIELDS, "date", "title")
                if getattr(self, name)}


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


def to_source(document_id: str, run_id: str, sha: str, root_alias: str, relative_path: str,
              metadata: dict, chunks: int, first_chunk: dict | None) -> Source:
    title, people, tags = source_label(metadata, relative_path)
    frontmatter = metadata.get("frontmatter") if isinstance(metadata.get("frontmatter"), dict) else {}
    generated = metadata.get("generated") if isinstance(metadata.get("generated"), dict) else {}
    # Same order as the tag classifier; the generated date exists only when the frontmatter has none.
    date = next((str(value)[:10] for value in (metadata.get("published"), metadata.get("date"),
                                               metadata.get("created"), generated.get("date")) if value), None)
    return Source(document_id=document_id, run_id=run_id, source_sha256=sha, root_alias=root_alias,
                  relative_path=relative_path, title=title, authors=_names(metadata.get("authors")),
                  guests=list(dict.fromkeys(_names(metadata.get("guests")) + _names(frontmatter.get("guest")))),
                  people=people, tags=tags, type=source_type(generated, relative_path, frontmatter), date=date,
                  chunks=chunks, first_chunk=first_chunk)


def load_sources(conn) -> list[Source]:
    """Every active source in the selected profile, with its properties, in one query."""
    with conn.cursor() as cur:
        profile = _profile(cur, None, None)
        cur.execute(f"""select r.document_id, r.run_id, r.source_sha256, d.root_alias, d.relative_path,
                {LABEL_METADATA},
                (select count(*) from knowledge.chunks c where c.run_id=r.run_id
                   and c.variant=p.variant and c.chunking_version=p.chunking_version),
                (select jsonb_build_object('chunk_id', c.chunk_id, 'source_spans', c.source_spans)
                   from knowledge.chunks c where c.run_id=r.run_id
                   and c.variant=p.variant and c.chunking_version=p.chunking_version
                   order by (c.source_spans->0->>'start_char')::int, c.chunk_id limit 1)
            from knowledge.active_runs ar
            join knowledge.runs r on r.run_id=ar.run_id and r.document_id=ar.document_id
            join knowledge.documents d on d.document_id=r.document_id
            join knowledge.profiles p on p.profile_id=ar.profile_id
            join knowledge.corpus_state state on state.profile_id=ar.profile_id
            where ar.profile_id=%s order by d.relative_path""", (profile[0],))
        rows = cur.fetchall()
    return [to_source(*row[:5], row[5] if isinstance(row[5], dict) else {}, int(row[6]), row[7]) for row in rows]


def _fold(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


def _words(value: str) -> list[str]:
    return re.findall(r"\w+", value.casefold())


def matches(sources: list[Source], query: PropertyQuery) -> list[Source]:
    """Sources meeting every stated filter; within one filter any listed value matches."""
    def has(values: list[str], wanted: list[str]) -> bool:
        folded = {_fold(value) for value in values}
        return not wanted or any(_fold(item) in folded for item in wanted)

    found = [source for source in sources
             if has(source.authors, query.authors) and has(source.guests, query.guests)
             and has(source.people, query.people) and has(source.tags, query.tags)
             and has([source.type or ""], query.types)
             and (not query.date or (source.date or "").startswith(query.date))]
    if query.title:
        phrase = _words(query.title)
        def score(source: Source) -> int:
            title = _words(source.title)
            return sum(word in title for word in phrase)
        best = max((score(source) for source in found), default=0)
        found = [source for source in found if best and score(source) == best]
    return found


def vocabulary(sources: list[Source]) -> dict[str, list[str]]:
    """The values that exist, trimmed to the router's character allowance (types, then tags, then people)."""
    found = {"types": sorted({source.type for source in sources if source.type}),
             "tags": sorted({tag for source in sources for tag in source.tags}),
             "people": sorted({name for source in sources for name in source.people + source.authors + source.guests})}
    room = ROUTER_VOCABULARY_CHARS
    for name, values in found.items():
        kept = []
        for value in values:
            if room - len(value) - 3 < 0:
                break
            room -= len(value) + 3
            kept.append(value)
        found[name] = kept
    return found


def route_model(question: str, sources: list[Source], *, client: ModelClient, model: ModelSpec) -> PropertyQuery | None:
    """The router model's reading of the question, or None for the answer loop.

    Values outside the vocabulary are dropped. A rule-based router was tried and kept as
    trial code only: on 24 new phrasings it routed 18 correctly against 23 for the model,
    and it once built a wrong filter (see docs/evaluation.md).
    """
    vocab = vocabulary(sources)
    input_text = json.dumps({"question": question[:1000], "vocabulary": vocab}, ensure_ascii=False,
                            separators=(",", ":"))
    result = client.generate_json(model=model, system_prompt=ROUTER_PROMPT, input_text=input_text,
                                  schema_name=ROUTER_PROMPT_VERSION, json_schema=ROUTER_SCHEMA,
                                  max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS)
    data = result.data
    if data.get("route") != "properties":
        return None
    allowed = {"authors": vocab["people"], "guests": vocab["people"], "people": vocab["people"],
               "tags": vocab["tags"], "types": vocab["types"]}
    query = PropertyQuery(count=bool(data.get("count")))
    for name in FILTER_FIELDS:
        known = {_fold(value): value for value in allowed[name]}
        given = [value for value in data.get(name) or [] if isinstance(value, str) and value.strip()]
        # A value outside the vocabulary would be dropped and silently widen the count to every source.
        if any(_fold(value) not in known for value in given):
            return None
        setattr(query, name, [known[_fold(value)] for value in given])
    date = data.get("date")
    query.date = date if isinstance(date, str) and re.fullmatch(r"\d{4}(-\d{2})?", date) else None
    title = data.get("title")
    query.title = title.strip() if isinstance(title, str) and title.strip() else None
    if query.is_empty() and not query.count:
        return None
    return query


def describe(query: PropertyQuery) -> str:
    parts = []
    if query.authors:
        parts.append("with author " + " or ".join(query.authors))
    if query.guests:
        parts.append("with " + " or ".join(query.guests) + " as a guest")
    if query.people:
        parts.append("naming " + " or ".join(query.people))
    if query.tags:
        parts.append("tagged " + " or ".join(query.tags))
    if query.types:
        parts.append("of type " + " or ".join(query.types))
    if query.date:
        parts.append(f"dated {query.date}")
    if query.title:
        parts.append(f"whose title matches “{query.title}”")
    return " and ".join(parts)


def _line(source: Source, citation: str | None) -> str:
    details = [", ".join(source.authors) or ", ".join(source.people)]
    if source.guests:
        details.append("guest " + ", ".join(source.guests))
    details += [source.type or "", source.date or ""]
    shown = "; ".join(item for item in details if item)
    return f"- *{source.title}*" + (f" — {shown}" if shown else "") + (f" [{citation}]" if citation else "")


def answer_properties(query: PropertyQuery, sources: list[Source], *, retriever,
                      started: float, model_calls: int, route: dict[str, Any]) -> AnswerResult:
    """Write the count or list from the matches and cite each listed source by its first passage."""
    found = matches(sources, query)
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
            passage = retriever.read(FusedHit(rank=index, rrf_score=0, channels=["properties"], hit=hit), 0)
            citation = f"E{index}.0"
            citations[citation] = passage
        lines.append(_line(source, citation))
    described = describe(query) or "in total"
    noun = "source" if len(found) == 1 else "sources"
    if not found:
        answer = f"None of your {len(sources)} sources is {described}."
    elif query.count:
        answer = f"You have {len(found)} {noun} {described}:\n" + "\n".join(lines)
    else:
        answer = f"{len(found)} {noun} {described}:\n" + "\n".join(lines)
    answer += ("\n\nCounted from the stored properties of all your indexed sources: a note's own frontmatter "
               "first, generated properties where it has none.")
    return AnswerResult(answer=answer, completeness="complete", citations=citations, gaps=[],
                        actions=[{"action": "properties", "route": route, "query": query.record(),
                                  "matched": [source.document_id for source in found]}],
                        model_calls=model_calls, inspected=dict(citations), needs={},
                        elapsed_seconds=time.monotonic() - started)
