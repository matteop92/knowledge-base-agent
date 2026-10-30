"""The Sources page: one light row per indexed source, and one source's reading payload.

Read-only. Card facts (type, platform, people, dates) are derived at read time from stored run metadata by
`app.ingestion.card_facts`; nothing is written back to the vault or the index.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable, NamedTuple

from app.agent.properties import to_source, to_sources
from app.api.answers import obsidian_link
from app.api.conversations import ConversationError
from app.ingestion.card_facts import card_facts
from app.storage.search import LABEL_METADATA, load_sources

# Blocks of the active run in reading order, with the label metadata and path of its document.
DETAIL_SQL = f"""select d.relative_path, {LABEL_METADATA}, coalesce(r.parsed->'blocks', '[]'::jsonb),
        r.run_id, r.source_sha256, d.root_alias,
        least((select min(r2.loaded_at) from knowledge.runs r2 where r2.document_id=r.document_id),
              (select min(cj.created_at) from knowledge.capture_jobs cj
                 where cj.vault_path=d.relative_path and cj.status='done')),
        r.source_text
    from knowledge.active_runs a
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.documents d on d.document_id=r.document_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where r.document_id=%s"""

# The text of quote sources, which the cards show in full.
QUOTE_TEXT_SQL = """select r.document_id, string_agg(b->>'raw_text', E'\\n\\n' order by (b->>'index')::int)
    from knowledge.runs r cross join lateral jsonb_array_elements(coalesce(r.parsed->'blocks', '[]'::jsonb)) b
    where r.run_id = any(%s) and b->>'kind' in ('block_quote', 'paragraph')
      and coalesce(b->>'content_role', 'source')='source'
    group by r.document_id"""

# Markdown markers the reader draws as structure, and images, which the reader leaves out (an image wrapped in a link
# leaves an empty link behind, which goes too). Links, bold and italic stay; the page draws them as elements.
MARKERS = {"heading": re.compile(r"^#{1,6}[ \t]+|[ \t]+#+[ \t]*$"),
           "list_item": re.compile(r"^[ \t]*[-*+][ \t]+(?:\[[ xX]\][ \t]+)?"),
           "block_quote": re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)}
# A link target may hold one level of brackets (a Wikipedia file name); the page's link pattern reads it the same way.
TARGET = r"\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"[^\"]*\")?\)"
IMAGE = re.compile(r"!\[[^\]]*\]" + TARGET + r"|!\[\[[^\]|]+\.(?:png|jpe?g|gif|webp|svg|bmp|avif)(?:\|[^\]]*)?\]\]",
                   re.IGNORECASE)
EMPTY_LINK = re.compile(r"\[\s*\]" + TARGET)
EMBED = re.compile(r"!(?=\[\[)")  # an embedded note that is not an image reads as a plain wiki link
FENCE_OPEN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*([\w+-]*)")
TABLE_RULE_CELL = re.compile(r":?-+:?")

ACTIVE_SQL = """select 1 from knowledge.active_runs a
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where r.document_id=%s"""


def _block_text(block: dict[str, Any]) -> str:
    """The block's text without its markers (a speaker label, a highlight header, `##`, `- `, `>`) or images."""
    text = str(block.get("raw_text") or "")
    span, content = block.get("span"), block.get("content_span")
    if isinstance(span, dict) and isinstance(content, dict):
        start = int(content.get("start_char", 0)) - int(span.get("start_char", 0))
        end = int(content.get("end_char", 0)) - int(span.get("start_char", 0))
        if 0 <= start < end <= len(text):
            text = text[start:end]
    marker = MARKERS.get(block.get("kind"))
    if marker:
        text = marker.sub("", text.strip())
    return EMBED.sub("", EMPTY_LINK.sub("", IMAGE.sub("", text))).strip()


class Fence(NamedTuple):
    start: int
    end: int
    language: str
    code: str


def _fences(source_text: str) -> list[Fence]:
    """Fenced code regions of the note as character offsets. The parser reads a fence line by line, so the reader finds
    the fences again in the stored text. An unclosed fence runs to the end."""
    found: list[Fence] = []
    offset, opened, body = 0, None, []
    for line in source_text.splitlines(keepends=True):
        if opened is None:
            match = FENCE_OPEN.match(line)
            if match and not (match.group(1)[0] == "`" and "`" in line[match.end():]):  # "```x```" is inline code
                opened, body = (offset, match.group(1), match.group(2)), []
        else:
            closing = line.strip()
            if closing.startswith(opened[1]) and not closing.strip(opened[1][0]):
                found.append(Fence(opened[0], offset + len(line), opened[2], "".join(body)))
                opened = None
            else:
                body.append(line)
        offset += len(line)
    if opened is not None:
        found.append(Fence(opened[0], len(source_text), opened[2], "".join(body)))
    return found


def _cut(block: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    """The part of a block between two character offsets, as a block of its own."""
    base = int(block["span"]["start_char"])
    return {**{key: value for key, value in block.items() if key != "content_span"},
            "raw_text": str(block.get("raw_text") or "")[start - base:end - base],
            "span": {"start_char": start, "end_char": end}}


def _around_fences(block: dict[str, Any], fences: list[Fence]) -> list[dict[str, Any] | Fence]:
    """The block with fenced code cut out, in reading order. A paragraph can run into a fence on either side."""
    span = block.get("span")
    if not fences or not isinstance(span, dict) or not isinstance(span.get("start_char"), int):
        return [block]
    start, end = span["start_char"], span["end_char"]
    parts: list[dict[str, Any] | Fence] = []
    cursor, touched = start, False
    for fence in fences:
        if fence.end <= start or fence.start >= end:
            continue
        touched = True
        if fence.start > cursor:
            parts.append(_cut(block, cursor, fence.start))
        if fence.start >= start:
            parts.append(fence)
        cursor = max(cursor, min(fence.end, end))
    if not touched:
        return [block]
    if cursor < end:
        parts.append(_cut(block, cursor, end))
    return parts


def _is_table_rule(line: str) -> bool:
    """The `| --- | :-: |` row under a table header. Checked cell by cell so a hostile line cannot make a regex backtrack."""
    return all(TABLE_RULE_CELL.fullmatch(cell.strip()) for cell in line.strip().strip("|").split("|"))


def _table_cells(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip())
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _around_tables(text: str) -> list[str | list[list[str]]]:
    """A paragraph's text with its Markdown tables (a header row, a `---` row, then rows) as lists of rows."""
    lines = text.splitlines()
    parts: list[str | list[list[str]]] = []
    plain: list[str] = []
    index = 0
    while index < len(lines):
        if (index + 1 < len(lines) and lines[index].strip().startswith("|") and _is_table_rule(lines[index + 1])):
            stop = index + 2
            while stop < len(lines) and lines[stop].strip().startswith("|"):
                stop += 1
            if plain:
                parts.append("\n".join(plain))
                plain = []
            parts.append([_table_cells(lines[index])] + [_table_cells(line) for line in lines[index + 2:stop]])
            index = stop
        else:
            plain.append(lines[index])
            index += 1
    if plain:
        parts.append("\n".join(plain))
    return parts


def reading_blocks(blocks: list[dict[str, Any]], source_text: str = "") -> list[dict[str, Any]]:
    """Blocks as the reader draws them. A personal note rides on the highlight before it; the "Front Matter" heading is left out.
    Fenced code (a diagram when the language is `mermaid`) and Markdown tables come out as blocks of their own."""
    result: list[dict[str, Any]] = []
    fences = _fences(source_text)
    for original in blocks:
        for part in _around_fences(original, fences):
            if isinstance(part, Fence):
                if part.code.strip():
                    result.append({"kind": "code", "text": part.code.replace("\r\n", "\n").rstrip(),
                                   "language": part.language.lower()})
                continue
            _append_block(result, part)
    return result


def _append_block(result: list[dict[str, Any]], block: dict[str, Any]) -> None:
    text = _block_text(block)
    if not text or block.get("kind") == "separator":
        return
    if block.get("kind") == "heading" and text.casefold() == "front matter":  # every book highlights export opens with it
        return
    if block.get("kind") == "paragraph":
        parts = _around_tables(text)
        if len(parts) > 1 or not isinstance(parts[0], str):
            for part in parts:
                if isinstance(part, str):
                    if part.strip():
                        result.append(_reading_block(block, part.strip()))
                else:
                    result.append({"kind": "table", "text": "", "rows": part})
            return
    note = block.get("content_role") == "personal_note"
    previous = result[-1] if result else None
    if note and previous and previous["kind"] == "highlight" and previous["personal_note"] is None:
        previous["personal_note"] = text
        return
    result.append(_reading_block(block, text))


def _reading_block(block: dict[str, Any], text: str) -> dict[str, Any]:
    note = block.get("content_role") == "personal_note"
    return {"kind": block.get("kind"), "text": text, "speaker": block.get("speaker"),
            "page_reference": block.get("page_reference"), "heading_path": block.get("heading_path"),
            "personal_note": text if note else None}


def _description(metadata: dict[str, Any]) -> str | None:
    generated = metadata.get("generated") if isinstance(metadata.get("generated"), dict) else {}
    for value in (metadata.get("description"), generated.get("description")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class SourceStore:
    def __init__(self, connect: Callable[[], Any], vault_name: str) -> None:
        self.connect, self.vault_name = connect, vault_name

    def is_active(self, document_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute(ACTIVE_SQL, (document_id,)).fetchone() is not None

    def _header(self, source, metadata: dict[str, Any]) -> dict[str, Any]:
        facts = card_facts(metadata, source.relative_path, source.added_at)
        added = source.added_at.date().isoformat() if source.added_at else None
        row = {"id": source.document_id, "title": source.title, "tags": source.tags, "added": added,
               "description": _description(metadata),
               "obsidian_url": obsidian_link(self.vault_name, source.relative_path),
               "opens_in_obsidian": facts["type"] == "book", **facts}
        row["date"] = row.pop("card_date", added)
        return row

    def list(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = load_sources(conn)
            sources = to_sources(rows)
            facts = [card_facts(row["metadata"], row["relative_path"], source.added_at)
                     for row, source in zip(rows, sources)]
            quote_runs = [row["run_id"] for row, fact in zip(rows, facts) if fact["type"] == "quote"]
            quotes = dict(conn.execute(QUOTE_TEXT_SQL, (quote_runs,)).fetchall()) if quote_runs else {}
        result = []
        for row, source, fact in zip(rows, sources, facts):
            item = self._header(source, row["metadata"])
            if fact["type"] == "quote" and quotes.get(source.document_id):
                item["quote_text"] = quotes[source.document_id].strip()
            result.append(item)
        result.sort(key=lambda item: (item["added"] or "", item["title"].casefold()), reverse=True)
        return {"sources": result, "vault_name": self.vault_name}

    def detail(self, document_id: str) -> dict[str, Any]:
        try:
            document_id = str(uuid.UUID(document_id))
        except ValueError:
            raise ConversationError(404, "source not found") from None
        with self.connect() as conn:
            found = conn.execute(DETAIL_SQL, (document_id,)).fetchone()
            if found is None:
                raise ConversationError(404, "source not found")
        relative_path, metadata, blocks, run_id, sha, root_alias, added, source_text = found
        metadata = metadata if isinstance(metadata, dict) else {}
        source = to_source(document_id, str(run_id), sha, root_alias, relative_path, metadata, 0, None, added_at=added)
        header = self._header(source, metadata)
        header["source_url"] = metadata.get("source_url")
        header["body"] = reading_blocks(blocks if isinstance(blocks, list) else [], source_text or "")
        return header
