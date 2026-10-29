"""Consistent properties for every indexed file, filling only what its frontmatter lacks.

One cheap zero-retention model call per new or changed file returns a fixed schema.
Tags must come from the live list in the knowledge.tags table (seeded from config/tags.yaml); at most one new tag is
kept aside as a proposal for review. Properties are metadata for labels and
filtering, never chunk text, so embeddings are unchanged.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import yaml

from app.ingestion.contracts import ParsedDocument
from app.models.client import ModelClient, ModelSpec, ModelUsage
from app.ingestion.card_facts import PLATFORM_WORDS
from app.models.routes import price_usd
from app.tag_aliases import aliases


log = logging.getLogger("knowledge.properties")
PROMPT_VERSION = "properties_v4"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"
DEFAULT_MODEL = "openai/gpt-6-luna"
MODEL_VERSION = "accepted-2026-10-17"
TAGS_PATH = Path(__file__).resolve().parents[2] / "config" / "tags.yaml"
INPUT_CHARS = 8000  # about 2,000 tokens of the note
MAX_OUTPUT_TOKENS = 400
MAX_TAGS = 3  # per note, from the approved list
MAX_SUGGESTED_TAGS = 5  # free suggestions while drafting the list
FIELDS = ("source_type", "tags", "people", "date", "description", "language")
DEFAULT_SOURCE_TYPES = ("transcript", "book", "highlights", "article", "note", "social_post",
                        "meeting", "reference", "other")
TAG = re.compile(r"^[a-z0-9][a-z0-9 &/-]{0,39}$")


class PropertiesError(ValueError):
    """The model reply does not fit the properties schema."""


@dataclass(frozen=True)
class TagList:
    tags: tuple[str, ...]
    source_types: tuple[str, ...] = DEFAULT_SOURCE_TYPES
    approved: bool = False
    # One line per tag saying when it applies; the model sees these so overlapping tags are used consistently.
    definitions: tuple[tuple[str, str], ...] = ()
    # Tags the user rejected: never applied and never suggested again.
    blocked: tuple[str, ...] = ()
    # Slugs of names found in the notes (publications, shows, channels, authors, guests): never tags.
    entities: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path = TAGS_PATH) -> TagList | None:
        if not path.is_file():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = data.get("tags") or []
        tags = tuple(str(item["name"] if isinstance(item, dict) else item).strip().lower() for item in items)
        definitions = tuple((str(item["name"]).strip().lower(), str(item["when"]).strip())
                            for item in items if isinstance(item, dict) and item.get("when"))
        types = tuple(str(item).strip() for item in data.get("source_types") or DEFAULT_SOURCE_TYPES)
        blocked = tuple(str(item).strip().lower() for item in data.get("blocked") or [])
        return cls(tags=tuple(dict.fromkeys(tag for tag in tags if tag and tag not in blocked)),
                   source_types=types, approved=data.get("approved") is True, definitions=definitions,
                   blocked=blocked)


    @classmethod
    def load_from_database(cls, conn, path: Path = TAGS_PATH) -> TagList | None:
        """The live list from the tags table, seeded once from the yaml file; the file alone if the table fails."""
        base = cls.load(path)
        if base is None:
            return None
        try:
            _seed_tags(conn, base)
            rows = conn.execute("select name, status, rule from knowledge.tags order by position").fetchall()
        except psycopg.Error as exc:
            log.warning("tag table unavailable, using the file error=%s", type(exc).__name__)
            return base
        approved = [row for row in rows if row[1] == "approved"]
        blocked = tuple(row[0] for row in rows if row[1] == "blocked")
        # Whatever the owner writes in a note's own tags is allowed too; a tag that is not a topic is left out.
        try:
            entities = entity_names(conn)
            written = [tag for tag in note_tags(conn) if tag not in blocked and tag not in aliases() and TAG.match(tag)
                       and _is_topic(tag, [], base.source_types, entities)]
        except psycopg.Error as exc:
            log.warning("note tags unavailable error=%s", type(exc).__name__)
            entities, written = (), []
        names = tuple(dict.fromkeys([row[0] for row in approved if row[0] not in aliases()] + written))
        return cls(tags=names, source_types=base.source_types, approved=base.approved,
                   definitions=tuple((row[0], row[2]) for row in approved if row[2]), blocked=blocked, entities=entities)


NOTE_TAGS_SQL = """select distinct lower(btrim(tag)) from knowledge.corpus_state state
    join knowledge.active_runs a on a.profile_id=state.profile_id
    join knowledge.runs r on r.run_id=a.run_id,
    jsonb_array_elements_text(case when jsonb_typeof(r.parsed->'metadata'->'tags')='array'
        then r.parsed->'metadata'->'tags' else '[]'::jsonb end) tag
    order by 1"""


def note_tags(conn) -> list[str]:
    """Every tag the owner has written in a note's own tags, lower case."""
    return [row[0] for row in conn.execute(NOTE_TAGS_SQL).fetchall() if row[0]]


def _seed_tags(conn, base: TagList) -> None:
    """Fill an empty tags table from the yaml file, all or nothing. Does nothing once the table has any row."""
    with conn.transaction():
        # One seeder at a time: a second one waits, then finds the table filled and does nothing.
        conn.execute("select pg_advisory_xact_lock(hashtext('knowledge.tags'))")
        if conn.execute("select count(*) from knowledge.tags").fetchone()[0]:
            return
        rules = dict(base.definitions)
        for name in base.tags:
            conn.execute("insert into knowledge.tags (name, status, rule) values (%s, 'approved', %s)",
                         (name, rules.get(name)))
        for name in base.blocked:
            conn.execute("insert into knowledge.tags (name, status) values (%s, 'blocked')", (name,))


def ensure_tags_seeded(conn, path: Path = TAGS_PATH) -> None:
    """Make sure the tags table holds the seed before anything adds a row to it, or that row would hide the seed."""
    base = TagList.load(path)
    if base is not None:
        _seed_tags(conn, base)


def model_spec() -> ModelSpec:
    return ModelSpec(provider="openrouter", model_id=os.getenv("KNOWLEDGE_PROPERTIES_MODEL", DEFAULT_MODEL),
                     model_version=MODEL_VERSION)


def existing_properties(parsed: ParsedDocument) -> dict[str, Any]:
    """Properties the file already sets; these are never overwritten.

    Tags are not here: every note gets list tags next to its own tags, which stay in the file untouched.
    """
    metadata, frontmatter = parsed.metadata, parsed.metadata.frontmatter
    found: dict[str, Any] = {}
    kind = frontmatter.get("type") or frontmatter.get("source_type")
    if isinstance(kind, str) and kind.strip():
        found["source_type"] = kind.strip()
    if metadata.authors or metadata.guests:
        found["people"] = list(dict.fromkeys([*metadata.authors, *metadata.guests]))
    when = metadata.published or metadata.date or metadata.created
    if when is not None:
        found["date"] = when.isoformat()
    if metadata.description:
        found["description"] = metadata.description
    language = frontmatter.get("language") or frontmatter.get("lang")
    if isinstance(language, str) and language.strip():
        found["language"] = language.strip()
    return found


def missing_fields(parsed: ParsedDocument) -> list[str]:
    present = existing_properties(parsed)
    return [name for name in FIELDS if name not in present]


def json_schema(tag_list: TagList) -> dict[str, Any]:
    tag_item: dict[str, Any] = {"type": "string"}
    if tag_list.tags:
        tag_item["enum"] = list(tag_list.tags)
    return {
        "type": "object", "additionalProperties": False,
        "required": ["source_type", "tags", "proposed_tag", "people", "date", "description", "language"],
        "properties": {
            "source_type": {"type": "string", "enum": list(tag_list.source_types)},
            "tags": {"type": "array", "items": tag_item, "maxItems": MAX_TAGS if tag_list.tags else MAX_SUGGESTED_TAGS},
            "proposed_tag": {"type": ["string", "null"]},
            "people": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
            "date": {"type": ["string", "null"]},
            "description": {"type": "string"},
            "language": {"type": "string"},
        },
    }


def _tag_lines(tag_list: TagList) -> list[str]:
    """Every approved tag, with its rule when it has one."""
    if not tag_list.tags:
        return ["(none yet)"]
    rules = dict(tag_list.definitions)
    return [f"- {name}: {rules[name]}" if name in rules else f"- {name}" for name in tag_list.tags]


def model_input(parsed: ParsedDocument, tag_list: TagList) -> str:
    raw = parsed.metadata.frontmatter_raw
    text = parsed.source_text
    body = (text[len(raw):] if raw and text.startswith(raw) else text).strip()[:INPUT_CHARS]
    existing = existing_properties(parsed)
    lines = [f"Path: {parsed.locator.relative_path}", f"Title: {parsed.metadata.title}",
             f"Allowed types: {', '.join(tag_list.source_types)}",
             "Approved tags:", *_tag_lines(tag_list),
             f"Existing properties: {existing or '(none)'}",
             f"The note's own tags (hints only): {', '.join(parsed.metadata.tags) or '(none)'}", "", "Text:", body]
    return "\n".join(lines)


def worst_case_usd(parsed: ParsedDocument, tag_list: TagList, model: ModelSpec | None = None) -> float:
    from app.models.openrouter import completion_limit

    model = model or model_spec()
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    input_bytes = len(prompt.encode("utf-8")) + len(model_input(parsed, tag_list).encode("utf-8"))
    _, output_tokens = completion_limit(model.model_id, "properties", MAX_OUTPUT_TOKENS, None)
    return price_usd(model.model_id, input_bytes, output_tokens)


# A tag says what a note is about. Never a tag: the kind of source, the platform it came from, or a name (a publication,
# show, channel, author or guest). Names come from the notes themselves (ENTITY_NAMES_SQL), so nothing is listed by hand.
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")


SOURCE_KIND_WORDS = {"article", "articles", "book", "books", "book-notes", "book-highlights", "highlights", "kindle-highlights",
                     "note", "notes", "podcast", "podcasts", "post", "posts", "quote", "quotes", "social", "social-post",
                     "social-media", "transcript", "transcripts", "video", "videos", "newsletter"}
PLATFORM_SLUGS = {_slug(word) for word in (*PLATFORM_WORDS, *PLATFORM_WORDS.values())}
ENTITY_NAMES_SQL = """select distinct name from (
    select r.parsed->'metadata'->'frontmatter'->>key as name
        from knowledge.active_runs a join knowledge.runs r on r.run_id=a.run_id,
        unnest(array['publication', 'show', 'channel', 'guest']) key
    union all
    select jsonb_array_elements_text(case when jsonb_typeof(r.parsed->'metadata'->key)='array'
            then r.parsed->'metadata'->key else '[]'::jsonb end)
        from knowledge.active_runs a join knowledge.runs r on r.run_id=a.run_id, unnest(array['authors', 'guests']) key
    ) names where name is not null and btrim(name) <> '' """


def entity_names(conn) -> tuple[str, ...]:
    """Publications, shows, channels, authors and guests found in the notes, as slugs."""
    return tuple(sorted({_slug(row[0]) for row in conn.execute(ENTITY_NAMES_SQL).fetchall()} - {""}))


def _is_topic(tag: str, people: list[str], source_types: tuple[str, ...], entities: tuple[str, ...] = ()) -> bool:
    slug = _slug(tag)
    named = {_slug(name) for name in people} | set(entities)
    return slug not in SOURCE_KIND_WORDS | PLATFORM_SLUGS | {_slug(kind) for kind in source_types} | named


def _clean_reply(data: dict[str, Any], tag_list: TagList) -> tuple[dict[str, Any], str | None]:
    if not isinstance(data, dict) or any(name not in data for name in FIELDS):
        raise PropertiesError("properties reply is missing fields")
    source_type = data["source_type"]
    if source_type not in tag_list.source_types:
        raise PropertiesError("properties reply has an unknown source type")
    raw_tags = data["tags"] if isinstance(data["tags"], list) else []
    tags = [str(tag).strip().lower() for tag in raw_tags if isinstance(tag, str) and tag.strip()]
    proposed = data.get("proposed_tag")
    proposed = proposed.strip().lower() if isinstance(proposed, str) and proposed.strip() else None
    if tag_list.tags:
        allowed = set(tag_list.tags)
        outside = [tag for tag in tags if tag not in allowed]
        tags = [tag for tag in tags if tag in allowed]
        if proposed is None and outside:
            proposed = outside[0]
        if proposed in allowed:
            proposed = None
    tags = list(dict.fromkeys(tags))[:MAX_TAGS if tag_list.tags else MAX_SUGGESTED_TAGS]
    tags = [tag for tag in tags if tag not in tag_list.blocked]
    people = [str(name).strip() for name in data["people"] if isinstance(name, str) and name.strip()][:10]
    if proposed is not None and (not TAG.match(proposed) or proposed in tag_list.blocked
                                 or proposed in aliases()
                                 or not _is_topic(proposed, people, tag_list.source_types, tag_list.entities)):
        proposed = None
    when = data["date"]
    if when is not None:
        try:
            when = date.fromisoformat(str(when)).isoformat()
        except ValueError:
            when = None
    description = " ".join(str(data["description"]).split())[:240]
    language = str(data["language"]).strip().lower()[:8] or None
    return ({"source_type": source_type, "tags": tags, "people": list(dict.fromkeys(people)),
             "date": when, "description": description or None, "language": language}, proposed)


@dataclass
class PropertiesResult:
    properties: dict[str, Any]
    proposed_tag: str | None
    model: ModelSpec | None
    usage: ModelUsage = field(default_factory=ModelUsage)
    called_model: bool = True


def classify_properties(parsed: ParsedDocument, tag_list: TagList, client: ModelClient,
                        model: ModelSpec | None = None) -> PropertiesResult:
    """Return generated values for the fields the file does not set itself."""
    missing = missing_fields(parsed)
    if not missing:
        return PropertiesResult({}, None, None, called_model=False)
    model = model or model_spec()
    result = client.generate_json(
        model=model, system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        input_text=model_input(parsed, tag_list), schema_name="properties",
        json_schema=json_schema(tag_list), max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    cleaned, proposed = _clean_reply(result.data, tag_list)
    return PropertiesResult({name: cleaned[name] for name in missing if cleaned[name] not in (None, [])},
                            proposed, model, result.usage)


def suggest_tags(parsed: ParsedDocument, client: ModelClient, model: ModelSpec | None = None) -> PropertiesResult:
    """Free tag suggestions for drafting the list; nothing is stored."""
    open_list = TagList(tags=())
    model = model or model_spec()
    result = client.generate_json(
        model=model, system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        input_text=model_input(parsed, open_list), schema_name="properties",
        json_schema=json_schema(open_list), max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    cleaned, _ = _clean_reply(result.data, open_list)
    return PropertiesResult({"tags": [tag for tag in cleaned["tags"] if TAG.match(tag)]}, None, model, result.usage)


def store_properties(conn, parsed: ParsedDocument, *, status: str, result: PropertiesResult | None = None,
                     error: str | None = None) -> None:
    from psycopg.types.json import Jsonb

    conn.execute("""insert into knowledge.document_properties
        (document_id, source_sha256, status, properties, proposed_tag, model, prompt_version, error)
        values (%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (document_id, source_sha256) do update set status=excluded.status,
          properties=excluded.properties, proposed_tag=excluded.proposed_tag, model=excluded.model,
          prompt_version=excluded.prompt_version, error=excluded.error""",
                 (parsed.document_id, parsed.source_sha256, status,
                  Jsonb(result.properties if result else {}), result.proposed_tag if result else None,
                  Jsonb(result.model.model_dump(mode="json")) if result and result.model else None,
                  PROMPT_VERSION, error))


def draft_tag_list(existing_tags: Counter, suggested_tags: Counter, path: Path = TAGS_PATH,
                   limit: int = 40) -> dict[str, Any]:
    """Write an unapproved tag list for review: tags already in use first, then model suggestions."""
    combined = Counter()
    for tag, count in existing_tags.items():
        combined[tag.strip().lower()] += count
    for tag, count in suggested_tags.items():
        combined[tag.strip().lower()] += count
    tags = [{"name": tag, "existing_uses": existing_tags.get(tag, 0), "suggested": suggested_tags.get(tag, 0)}
            for tag, _ in combined.most_common() if TAG.match(tag)][:limit]
    data = {"approved": False, "source_types": list(DEFAULT_SOURCE_TYPES), "tags": tags}
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Draft tag list. Edit the names, delete what you do not want, then set approved: true.\n"
              "# The classifier may only pick tags from this list.\n")
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return data
