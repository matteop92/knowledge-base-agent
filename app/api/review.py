"""Review: one passage at a time from the indexed vault, in a seeded shuffle with no daily limit.

The pool is the `knowledge.feed_passages` view (nothing is copied). A round orders the pool by
md5(round || passage_key); the round is the highest one in the shown log and its position is the highest rank shown
in it. The next passage is the first one ranked after the position, and a new round starts when none is left.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from app.api.conversations import ConversationError
from app.api.sources import EMBED, EMPTY_LINK, IMAGE, reading_blocks

MAX_COUNT = 5
MAX_HELD = 30
KEY = re.compile(r"^[0-9a-f]{32}$")
SKIPPED_KINDS = ("image", "table")

ROUND_SQL = "select coalesce(max(round), 1) from knowledge.feed_shown"
POSITION_SQL = "select coalesce(max(md5(round::text || passage_key)), '') from knowledge.feed_shown where round=%s"
AFTER_SQL = "select count(*) from knowledge.feed_passages where md5(%s::text || passage_key) > %s"
NEXT_SQL = """select passage_key, document_id, kind, start_char, end_char, text, note, type, title, author, locator
    from knowledge.feed_passages
    where md5(%(round)s::text || passage_key) > %(position)s and passage_key <> all(%(held)s::text[])
    order by md5(%(round)s::text || passage_key)
    limit %(count)s"""
BLOCKS_SQL = """select r.document_id, coalesce(r.parsed->'blocks', '[]'::jsonb)
    from knowledge.active_runs a
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where r.document_id = any(%s::text[])"""


def _held(value: str) -> list[str]:
    keys = [key for key in (part.strip() for part in value.split(",")) if key]
    if len(keys) > MAX_HELD or not all(KEY.match(key) for key in keys):
        raise ConversationError(400, "that list of passages is not valid")
    return keys


def _clean(text: str) -> str:
    """A raw slice as the reader would draw it: images out, line endings plain."""
    return EMBED.sub("", EMPTY_LINK.sub("", IMAGE.sub("", text.replace("\r\n", "\n")))).strip()


def _span(block: dict[str, Any], name: str) -> tuple[int, int] | None:
    span = block.get(name)
    return (int(span["start_char"]), int(span["end_char"])) if isinstance(span, dict) else None


def _highlight(blocks: list[dict[str, Any]], start: int) -> tuple[str, str | None]:
    """A highlight's text and its own note, cleaned by the reader's pipeline."""
    for index, block in enumerate(blocks):
        if block.get("kind") == "highlight" and _span(block, "content_span") and _span(block, "content_span")[0] == start:
            note = next((other for other in blocks[index + 1:] if other.get("content_role") == "personal_note"
                         and other.get("group_id") == block.get("group_id")), None)
            items = reading_blocks([block] + ([note] if note else []))
            return (items[0]["text"], items[0]["personal_note"]) if items else ("", None)
    return "", None


def _whole(blocks: list[dict[str, Any]], start: int, end: int) -> str:
    """The text items of the blocks inside a whole source's span, as paragraphs."""
    inside = [block for block in blocks if (span := _span(block, "span")) and start <= span[0] and span[1] <= end]
    return "\n\n".join(item["text"] for item in reading_blocks(inside) if item["kind"] not in SKIPPED_KINDS and item.get("text"))


class ReviewStore:
    def __init__(self, connect: Callable[[], Any]) -> None:
        self.connect = connect

    def next(self, count: int, held: str = "") -> list[dict[str, Any]]:
        """Up to `count` unshown passages after the round's position, skipping keys the client already holds.

        A new round starts only when nothing is left after the position, held passages included; with only held
        passages left the list is empty and the client keeps showing what it has."""
        count = max(1, min(count, MAX_COUNT))
        held_keys = _held(held)
        with self.connect() as conn:
            round_ = conn.execute(ROUND_SQL).fetchone()[0]
            position = conn.execute(POSITION_SQL, (round_,)).fetchone()[0]
            if conn.execute(AFTER_SQL, (round_, position)).fetchone()[0] == 0:
                round_, position = round_ + 1, ""
            rows = conn.execute(NEXT_SQL, {"round": round_, "position": position, "held": held_keys, "count": count}).fetchall()
            blocks = dict(conn.execute(BLOCKS_SQL, ([row[1] for row in rows],)).fetchall()) if rows else {}
        return [self._passage(row, round_, blocks.get(row[1]) or []) for row in rows]

    def _passage(self, row, round_: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        key, document_id, kind, start, end, raw, raw_note, type_, title, author, locator = row
        note = None
        if kind == "pick":
            text = _clean(raw)
        elif kind == "highlight":
            text, note = _highlight(blocks, start)
        else:
            text = _whole(blocks, start, end)
        return {"passage_key": key, "round": round_, "text": text, "note": note, "title": title, "author": author,
                "source_id": document_id, "type": "post" if type_ == "social_post" else type_, "locator": locator}

    def shown(self, passage_key: str, round_: int) -> None:
        """Log a passage as shown in `round_`. The round may be the current one, the one before (a passage held
        across a rollover) or the one after (the first passage of a new round)."""
        if not KEY.match(passage_key):
            raise ConversationError(404, "passage not found")
        with self.connect() as conn:
            current = conn.execute(ROUND_SQL).fetchone()[0]
            if not max(1, current - 1) <= round_ <= current + 1:
                raise ConversationError(400, "that round is not current")
            found = conn.execute("select document_id from knowledge.feed_passages where passage_key=%s", (passage_key,)).fetchone()
            if found is None:
                raise ConversationError(404, "passage not found")
            conn.execute("""insert into knowledge.feed_shown (round, passage_key, document_id) values (%s,%s,%s)
                on conflict (round, passage_key) do nothing""", (round_, passage_key, found[0]))
