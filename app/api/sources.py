"""The Sources page: one light row per indexed source, and one source's reading payload.

Read-only. Card facts (type, platform, people, dates) are derived at read time from stored run metadata by
`app.ingestion.card_facts`; nothing is written back to the vault or the index.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Callable

from app.agent.properties import to_source, to_sources
from app.api.answers import obsidian_link
from app.api.conversations import ConversationError
from app.ingestion.card_facts import card_facts
from app.storage.search import LABEL_METADATA, load_sources

log = logging.getLogger(__name__)

# Blocks of the active run in reading order, with the label metadata and path of its document.
DETAIL_SQL = f"""select d.relative_path, {LABEL_METADATA}, coalesce(r.parsed->'blocks', '[]'::jsonb),
        r.run_id, r.source_sha256, d.root_alias,
        least((select min(r2.loaded_at) from knowledge.runs r2 where r2.document_id=r.document_id),
              (select min(cj.created_at) from knowledge.capture_jobs cj
                 where cj.vault_path=d.relative_path and cj.status='done'))
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
# A highlight exported as a Markdown quote keeps its `> ` on every line; the reader leaves it out like any quote's.
MARKERS["highlight"] = MARKERS["block_quote"]
# A link target may hold one level of brackets (a Wikipedia file name); the page's link pattern reads it the same way.
TARGET = r"\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"[^\"]*\")?\)"
IMAGE = re.compile(r"!\[[^\]]*\]" + TARGET + r"|!\[\[[^\]|]+\.(?:png|jpe?g|gif|webp|svg|bmp|avif)(?:\|[^\]]*)?\]\]",
                   re.IGNORECASE)
EMPTY_LINK = re.compile(r"\[\s*\]" + TARGET)
EMBED = re.compile(r"!(?=\[\[)")  # an embedded note that is not an image reads as a plain wiki link
IMAGE_ALT = re.compile(r"!\[([^\]]*)\]")

# One block of the active run, with that run's id, for the image route.
IMAGE_SQL = """select r.run_id, r.parsed->'blocks'->%s
    from knowledge.active_runs a
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where r.document_id=%s"""

ACTIVE_SQL = """select 1 from knowledge.active_runs a
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where r.document_id=%s"""


def _content(block: dict[str, Any]) -> str:
    """The block's raw text, cut to its content span (the code between a fence's lines, the words after a speaker label)."""
    text = str(block.get("raw_text") or "")
    span, content = block.get("span"), block.get("content_span")
    if isinstance(span, dict) and isinstance(content, dict):
        start = int(content.get("start_char", 0)) - int(span.get("start_char", 0))
        end = int(content.get("end_char", 0)) - int(span.get("start_char", 0))
        if 0 <= start < end <= len(text):
            text = text[start:end]
    return text


Pairs = list[tuple[str, int]]  # a text as (character, its offset in the source file)


def _drop(pairs: Pairs, pattern: re.Pattern[str]) -> Pairs:
    """The pairs without the text the pattern matches, so the rest keeps its source offsets."""
    gone = [False] * len(pairs)
    for found in pattern.finditer("".join(char for char, _ in pairs)):
        gone[found.start():found.end()] = [True] * (found.end() - found.start())
    return [pair for pair, drop in zip(pairs, gone) if not drop]


def _trim(pairs: Pairs) -> Pairs:
    first, last = 0, len(pairs)
    while first < last and pairs[first][0].isspace():
        first += 1
    while last > first and pairs[last - 1][0].isspace():
        last -= 1
    return pairs[first:last]


def _block_pairs(block: dict[str, Any]) -> Pairs:
    """The block's text without its markers (a speaker label, a highlight header, `##`, `- `, `>`) or images, each
    character with its offset in the source file."""
    text = str(block.get("raw_text") or "")
    span, content = block.get("span"), block.get("content_span")
    base, start, end = (int(span.get("start_char", 0)) if isinstance(span, dict) else 0), 0, len(text)
    if isinstance(span, dict) and isinstance(content, dict):
        first = int(content.get("start_char", 0)) - base
        last = int(content.get("end_char", 0)) - base
        if 0 <= first < last <= len(text):
            start, end = first, last
    pairs = [(char, base + start + offset) for offset, char in enumerate(text[start:end])]
    pairs = [pair for index, pair in enumerate(pairs)  # a line ending is one "\n", so the page and the server agree
             if not (pair[0] == "\r" and index + 1 < len(pairs) and pairs[index + 1][0] == "\n")]
    marker = MARKERS.get(block.get("kind"))
    if marker:
        pairs = _drop(_trim(pairs), marker)
    for pattern in (IMAGE, EMPTY_LINK, EMBED):
        pairs = _drop(pairs, pattern)
    return _trim(pairs)


def _block_text(block: dict[str, Any]) -> str:
    return "".join(char for char, _ in _block_pairs(block))


# The page draws these as elements (see inline() in sources.js); only the words between the markers stay on screen.
INLINE = re.compile(r'(`[^`\n]+`|\[[^\]]+\]\((?:[^()\s]|\([^()\s]*\))+(?:\s+"[^"]*")?\)|\[\[[^\]]+\]\]|\*\*(?=\S)[^*]+?(?<=\S)\*\*|\*(?=\S)[^*]+?(?<=\S)\*)')
LINK = re.compile(r'^\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)(?:\s+"[^"]*")?\)$')
WIKI = re.compile(r"^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$")
EMPHASIS = re.compile(r"^(\*\*?)([^*]+)\1$")
CODE = re.compile(r"^`[^`\n]+`$")


def _shown(text: str, offsets: list[int]) -> Pairs:
    """What the page shows for `text` (offsets: each character's offset in the source file): links, wiki links, bold,
    italic and code lose their markers. Mirrors inline() in sources.js."""
    shown: Pairs = []
    cursor = 0
    for found in INLINE.finditer(text):
        shown += list(zip(text[cursor:found.start()], offsets[cursor:found.start()]))
        run, at = found.group(0), found.start()
        link, wiki, emphasis = LINK.match(run), WIKI.match(run), EMPHASIS.match(run)
        if CODE.match(run):
            shown += list(zip(run[1:-1], offsets[at + 1:at + len(run) - 1]))
        elif link:
            first = at + link.start(1)
            shown += _shown(link.group(1), offsets[first:first + len(link.group(1))])
        elif wiki:
            group = 2 if wiki.group(2) else 1
            label = wiki.group(group)
            first = at + wiki.start(group) + (len(label) - len(label.lstrip()))
            shown += list(zip(label.strip(), offsets[first:first + len(label.strip())]))
        elif emphasis:
            first = at + emphasis.start(2)
            shown += _shown(emphasis.group(2), offsets[first:first + len(emphasis.group(2))])
        else:
            shown += list(zip(run, offsets[at:at + len(run)]))
        cursor = found.end()
    return shown + list(zip(text[cursor:], offsets[cursor:]))


def block_display(block: dict[str, Any]) -> Pairs:
    """The characters the reader shows for a text block, each with its offset in the source file."""
    pairs = _block_pairs(block)
    return _shown("".join(char for char, _ in pairs), [offset for _, offset in pairs])


def _table_cells(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip())
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _table_rows(raw_text: str) -> list[list[str]]:
    """A table block as rows of cells: the header, then the body rows (the `---` row is dropped)."""
    lines = raw_text.splitlines()
    return [_table_cells(line) for line in lines[:1] + lines[2:] if line.strip()]


def https_target(target: Any) -> str | None:
    """A stored image target as a bare https URL, or None. The parser keeps a title (`"t"`) and angle brackets attached."""
    parts = str(target or "").split()
    url = parts[0].removeprefix("<").removesuffix(">") if parts else ""
    return url if url.startswith("https://") else None


def reading_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Blocks as the reader draws them. A personal note rides on the highlight before it; the "Front Matter" heading is left out.
    Fenced code (a diagram when the language is `mermaid`) and tables come out as blocks of their own. An image on its own
    line comes out by index and alt text only; the page loads it through the image route, so its URL never leaves the server.
    Each text item carries its block's index (and a highlight's note its own), which is how a pick names where it sits."""
    result: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("kind")
        if kind == "asset_reference":
            if https_target(block.get("asset_target")):
                alt = IMAGE_ALT.match(str(block.get("raw_text") or "").strip())
                result.append({"kind": "image", "index": int(block.get("index", 0)), "alt": alt.group(1).strip() if alt else ""})
            continue
        if kind == "code_block":
            # Without a content span the fence has no body (an empty fence, or an opener on the last line).
            code = _content(block).replace("\r\n", "\n").rstrip() if block.get("content_span") else ""
            if code:
                result.append({"kind": kind, "text": code, "language": str(block.get("language") or "").lower()})
            continue
        if kind == "table":
            result.append({"kind": kind, "text": "", "rows": _table_rows(str(block.get("raw_text") or ""))})
            continue
        text = _block_text(block)
        if not text or kind == "separator":
            continue
        if kind == "heading" and text.casefold() == "front matter":  # every book highlights export opens with it
            continue
        note = block.get("content_role") == "personal_note"
        previous = result[-1] if result else None
        if note and previous and previous["kind"] == "highlight" and previous["personal_note"] is None:
            previous["personal_note"] = text
            previous["note_block"] = int(block.get("index", 0))
            continue
        result.append({"kind": kind, "text": text, "speaker": block.get("speaker"),
                       "page_reference": block.get("page_reference"), "heading_path": block.get("heading_path"),
                       "personal_note": text if note else None, "block": int(block.get("index", 0)), "note_block": None})
    return result


def _description(metadata: dict[str, Any]) -> str | None:
    generated = metadata.get("generated") if isinstance(metadata.get("generated"), dict) else {}
    for value in (metadata.get("description"), generated.get("description")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class SourceStore:
    def __init__(self, connect: Callable[[], Any], vault_name: str, picks: Any = None) -> None:
        # `picks` is the Review store, which supplies a source's picks to the reader.
        self.connect, self.vault_name, self.picks = connect, vault_name, picks

    def is_active(self, document_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute(ACTIVE_SQL, (document_id,)).fetchone() is not None

    def _header(self, source, metadata: dict[str, Any]) -> dict[str, Any]:
        facts = card_facts(metadata, source.relative_path, source.added_at)
        added = source.added_at.date().isoformat() if source.added_at else None
        row = {"id": source.document_id, "title": source.title, "tags": source.tags, "added": added,
               "added_at": source.added_at.isoformat() if source.added_at else None,
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

    def image_target(self, document_id: str, index: int, run_id: str) -> str:
        """The saved https URL of an image block in the source's active run. A run other than the active one is not
        found, so a browser cache keyed on an older parse never shows an image in the wrong place."""
        try:
            document_id = str(uuid.UUID(document_id))
        except ValueError:
            raise ConversationError(404, "image not found") from None
        if not 0 <= index < 1_000_000:
            raise ConversationError(404, "image not found")
        with self.connect() as conn:
            found = conn.execute(IMAGE_SQL, (index, document_id)).fetchone()
        block = found[1] if found and str(found[0]) == run_id else None
        target = https_target(block.get("asset_target")) if isinstance(block, dict) and block.get("kind") == "asset_reference" else None
        if target is None:
            raise ConversationError(404, "image not found")
        return target

    def detail(self, document_id: str) -> dict[str, Any]:
        try:
            document_id = str(uuid.UUID(document_id))
        except ValueError:
            raise ConversationError(404, "source not found") from None
        with self.connect() as conn:
            found = conn.execute(DETAIL_SQL, (document_id,)).fetchone()
            if found is None:
                raise ConversationError(404, "source not found")
            relative_path, metadata, blocks, run_id, sha, root_alias, added = found
            try:
                picks = self.picks.live(conn, document_id, sha, blocks) if self.picks and isinstance(blocks, list) else []
            except Exception:  # noqa: BLE001 - a source must open even when its picks cannot be read
                log.exception("picks for source %s could not be read", document_id)
                picks = []
        metadata = metadata if isinstance(metadata, dict) else {}
        source = to_source(document_id, str(run_id), sha, root_alias, relative_path, metadata, 0, None, added_at=added)
        header = self._header(source, metadata)
        header["source_url"] = metadata.get("source_url")
        header["run_id"] = str(run_id)  # ties image URLs to this parse, so the browser cache never outlives a re-index
        header["body"] = reading_blocks(blocks if isinstance(blocks, list) else [])
        header["picks"] = picks
        return header
