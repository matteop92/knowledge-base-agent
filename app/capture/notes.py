"""Captured text and Markdown become new notes in the vault's existing Knowledge folders.

The properties classifier decides what kind of note it is, and the kind picks the folder: YouTube to
Transcripts, full articles to Articles, book highlights to Book Highlights, everything else (tweets,
LinkedIn posts, short text, anything uncertain) to Notes. Nothing is ever written to Books.

Safety: the server only ever creates new files here. Every write uses exclusive create, so an existing
note can never be replaced, and nothing in this module edits or deletes a note. Captured text is content,
never instructions; it reaches a model only as the text to classify.
"""

from __future__ import annotations

import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.ingestion.contracts import ParsedDocument
from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source

FOLDER_BY_TYPE = {"transcript": "Transcripts", "article": "Articles", "highlights": "Book Highlights"}
DEFAULT_FOLDER = "Notes"
CAPTURE_FOLDERS = ("Transcripts", "Articles", "Book Highlights", "Notes")
MAX_TEXT_BYTES = 2_000_000
MAX_TITLE_CHARS = 200
SLUG_CHARS = 60
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class CaptureError(ValueError):
    """A capture that cannot be saved; the message is safe to show the user."""


@dataclass
class Draft:
    kind: str  # text | markdown | screenshot | youtube
    body: str
    title: str
    # Properties the input already sets (a Markdown file's own frontmatter); these always win.
    own: dict[str, Any] = field(default_factory=dict)
    url: str | None = None
    tags: list[str] = field(default_factory=list)


def clean_title(value: str | None) -> str | None:
    text = " ".join((value or "").split())[:MAX_TITLE_CHARS]
    return text or None


def first_line_title(body: str) -> str:
    for line in body.splitlines():
        text = line.strip().lstrip("#").strip()
        if any(char.isalnum() for char in text):  # skip rules such as "---"
            return clean_title(text[:80]) or "Capture"
    return "Capture"


def clean_tags(tags: list[str] | None) -> list[str]:
    cleaned = [" ".join(str(tag).split()).lstrip("#").strip().lower()[:40] for tag in tags or []]
    return list(dict.fromkeys(tag for tag in cleaned if tag))[:10]


def draft_from_text(text: str, *, title: str | None = None, url: str | None = None,
                    tags: list[str] | None = None) -> Draft:
    body = text.strip()
    if not body:
        raise CaptureError("there is no text to save")
    if len(body.encode("utf-8")) > MAX_TEXT_BYTES:
        raise CaptureError("the text is too long to save")
    return Draft(kind="text", body=body, title=clean_title(title) or first_line_title(body),
                 url=url, tags=clean_tags(tags))


def draft_from_markdown(markdown: str, *, title: str | None = None, url: str | None = None,
                        tags: list[str] | None = None) -> Draft:
    """A Markdown file keeps its own properties and its text; missing properties are added later."""
    text = markdown.lstrip("﻿")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise CaptureError("the file is too large to save")
    own: dict[str, Any] = {}
    match = FRONTMATTER.match(text)
    if match:
        try:
            loaded = yaml.safe_load(match.group(1) or "") or {}
        except yaml.YAMLError:
            raise CaptureError("the file's properties block is not valid YAML") from None
        if not isinstance(loaded, dict):
            raise CaptureError("the file's properties block is not valid YAML")
        own = {str(key): value for key, value in loaded.items()}
        text = text[match.end():]
    body = text.strip()
    if not body:
        raise CaptureError("the file has no text to save")
    own_title = own.get("title") if isinstance(own.get("title"), str) else None
    return Draft(kind="markdown", body=body, title=clean_title(own_title) or clean_title(title) or first_line_title(body),
                 own=own, url=url, tags=clean_tags(tags))


def folder_for(kind: str, source_type: str | None) -> str:
    if kind == "youtube":
        return "Transcripts"
    return FOLDER_BY_TYPE.get(source_type or "", DEFAULT_FOLDER)


def slugify(title: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:SLUG_CHARS].strip("-")
    return slug or "capture"


def note_properties(draft: Draft, generated: dict[str, Any], today: date) -> dict[str, Any]:
    """Frontmatter for the new note, using the property names the vault parser reads.

    Generated values only fill gaps: anything the input set itself is kept as it was.
    """
    props: dict[str, Any] = {"title": draft.title}
    if draft.url:
        props["url"] = draft.url
    if generated.get("people"):
        props["authors"] = generated["people"]
    if generated.get("date"):
        props["published"] = generated["date"]
    props["created"] = today.isoformat()
    tags = list(dict.fromkeys([*draft.tags, *(generated.get("tags") or [])]))
    if tags:
        props["tags"] = tags
    if generated.get("source_type"):
        props["source_type"] = generated["source_type"]
    for name in ("description", "language"):
        if generated.get(name):
            props[name] = generated[name]
    props["capture"] = draft.kind
    merged = dict(draft.own)
    if draft.kind == "youtube" and generated.get("people") and "people" not in merged:
        # `author` is the channel (the publisher); the hosts and guests in the video go in `people`, which source
        # labels and name searches read.
        channel = str(merged.get("author") or "").casefold()
        people = [name for name in generated["people"] if name.casefold() != channel]
        if people:
            merged["people"] = people
    if "tags" in merged and tags:
        own_tags = merged["tags"] if isinstance(merged["tags"], list) else [merged["tags"]]
        merged["tags"] = list(dict.fromkeys([*[str(tag) for tag in own_tags], *tags]))
    for name, value in props.items():
        # An input's own author, date or type means the generated one is not needed.
        if name == "authors" and ("author" in merged or "authors" in merged):
            continue
        if name == "published" and any(key in merged for key in ("published", "date")):
            continue
        if name == "source_type" and "type" in merged:
            continue
        merged.setdefault(name, value)
    return merged


def render(draft: Draft, properties: dict[str, Any]) -> str:
    header = yaml.safe_dump(properties, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{header}---\n\n{draft.body}\n"


def parse_draft(draft: Draft, today: date) -> ParsedDocument:
    """Parse the draft as the vault parser would, so the classifier sees what the indexer will see.

    It is parsed from a scratch folder under a neutral path, so its own text, not a folder, decides the route.
    """
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch) / "vault"
        (root / "Knowledge" / "Captured").mkdir(parents=True)
        # Without `created`, so a missing publication date still counts as missing for the classifier.
        properties = {name: value for name, value in note_properties(draft, {}, today).items() if name != "created"}
        if draft.kind == "youtube":
            # The channel is the publisher, not the people speaking; hide it so the classifier looks for the people.
            properties.pop("author", None)
            properties.pop("authors", None)
        (root / "Knowledge" / "Captured" / "draft.md").write_text(render(draft, properties), encoding="utf-8")
        captured = capture_source(root, "Knowledge/Captured/draft.md", Path(scratch) / "runs")
        return route_source(captured)


def write_note(vault_root: Path, knowledge_folder: str, folder: str, title: str, content: str,
               today: date) -> str:
    """Create a new note and return its vault-relative path; never replaces an existing file."""
    if folder not in CAPTURE_FOLDERS:
        raise CaptureError("not a capture folder")
    base = vault_root / knowledge_folder
    if not base.is_dir() or base.is_symlink():
        raise CaptureError("the Knowledge folder is missing from the server's vault copy")
    target = base / folder
    if target.is_symlink():
        raise CaptureError("the capture folder is not a plain folder")
    target.mkdir(exist_ok=True)
    if target.resolve().parent != base.resolve():
        raise CaptureError("the capture folder is not inside the Knowledge folder")
    stem = f"{today.isoformat()}-{slugify(title)}"
    for number in range(1, 100):
        path = target / (f"{stem}.md" if number == 1 else f"{stem}-{number}.md")
        try:
            # Exclusive create: fails if the file exists, so no note is ever overwritten.
            with open(path, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except FileExistsError:
            continue
        return str(PurePosixPath(knowledge_folder, folder, path.name))
    raise CaptureError("too many notes with this title today")
