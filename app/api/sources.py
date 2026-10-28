"""The Sources page: one light row per indexed source, and one source's reading payload.

Read-only. Card facts (type, platform, people, dates) are derived at read time from stored run metadata by
`app.ingestion.card_facts`; nothing is written back to the vault or the index.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable

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
# A link target may hold one level of brackets (a Wikipedia file name); the page's link pattern reads it the same way.
TARGET = r"\((?:[^()\s]|\([^()\s]*\))*(?:\s+\"[^\"]*\")?\)"
IMAGE = re.compile(r"!\[[^\]]*\]" + TARGET + r"|!\[\[[^\]|]+\.(?:png|jpe?g|gif|webp|svg|bmp|avif)(?:\|[^\]]*)?\]\]",
                   re.IGNORECASE)
EMPTY_LINK = re.compile(r"\[\s*\]" + TARGET)
EMBED = re.compile(r"!(?=\[\[)")  # an embedded note that is not an image reads as a plain wiki link

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


def reading_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Blocks as the reader draws them. A personal note rides on the highlight before it."""
    result: list[dict[str, Any]] = []
    for block in blocks:
        text = _block_text(block)
        if not text or block.get("kind") == "separator":
            continue
        note = block.get("content_role") == "personal_note"
        previous = result[-1] if result else None
        if note and previous and previous["kind"] == "highlight" and previous["personal_note"] is None:
            previous["personal_note"] = text
            continue
        result.append({"kind": block.get("kind"), "text": text, "speaker": block.get("speaker"),
                       "page_reference": block.get("page_reference"), "heading_path": block.get("heading_path"),
                       "personal_note": text if note else None})
    return result


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
        relative_path, metadata, blocks, run_id, sha, root_alias, added = found
        metadata = metadata if isinstance(metadata, dict) else {}
        source = to_source(document_id, str(run_id), sha, root_alias, relative_path, metadata, 0, None, added_at=added)
        header = self._header(source, metadata)
        header["source_url"] = metadata.get("source_url")
        header["body"] = reading_blocks(blocks if isinstance(blocks, list) else [])
        return header
