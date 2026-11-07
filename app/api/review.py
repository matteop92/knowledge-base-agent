"""Review: one passage at a time from the indexed vault, in a seeded shuffle with no daily limit.

The pool is the `knowledge.feed_passages` view (nothing is copied). A round orders the pool by
md5(round || passage_key); the round is the highest one in the shown log and its position is the highest rank shown
in it. The next passage is the first one ranked after the position, and a new round starts when none is left.

Marked passages (Done and Again) leave that shuffle. Done ones never come back; Again ones come back through an
interleave: one slot in `again_every` (10, 5, 3 or 2) serves the Again passage served longest ago.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Callable

from app.api.conversations import ConversationError
from app.api.sources import EMBED, EMPTY_LINK, IMAGE, block_display, reading_blocks

MAX_COUNT = 5
MAX_HELD = 30
KEY = re.compile(r"^[0-9a-f]{32}$")
SKIPPED_KINDS = ("image", "table")
UNPICKABLE_KINDS = ("code_block", "table", "asset_reference", "separator")
CONTEXT_CHARS = 40  # what a pick keeps on each side of its words, to choose between repeats when it is re-anchored

ROUND_SQL = "select coalesce(max(round), 1) from knowledge.feed_shown"
POSITION_SQL = "select coalesce(max(md5(round::text || passage_key)), '') from knowledge.feed_shown where round=%s"
NEXT_SQL = """select passage_key, document_id, kind, start_char, end_char, text, note, type, title, author, locator
    from knowledge.feed_passages
    where md5(%(round)s::text || passage_key) > %(position)s
      and passage_key not in (select passage_key from knowledge.feed_marks)
    order by md5(%(round)s::text || passage_key)
    limit %(limit)s"""
PASSAGE_COLUMNS = "p.passage_key, p.document_id, p.kind, p.start_char, p.end_char, p.text, p.note, p.type, p.title, p.author, p.locator"
EVERY_SQL = "select again_every from knowledge.feed_settings"
# Shuffle passages shown since an Again passage was last served or marked (the interleave's gap counter starts from it, so
# a fresh mark waits its turn instead of coming straight back on a vault with a long shown log).
SINCE_SERVE_SQL = """select count(*) from knowledge.feed_shown
    where shown_at > coalesce((select greatest(max(last_served_at), max(marked_at)) from knowledge.feed_marks where mark = 'again'),
                              '-infinity'::timestamptz)"""
AGAIN_SQL = f"""select {PASSAGE_COLUMNS}
    from knowledge.feed_marks m join knowledge.feed_passages p on p.passage_key = m.passage_key
    where m.mark = 'again' and not (m.passage_key = any(%s::text[]))
    order by m.last_served_at asc nulls first, m.marked_at, m.passage_key"""
HELD_AGAIN_SQL = "select passage_key from knowledge.feed_marks where mark = 'again' and passage_key = any(%s::text[])"
LIST_SQL = f"""select {PASSAGE_COLUMNS}, m.marked_at
    from knowledge.feed_marks m join knowledge.feed_passages p on p.passage_key = m.passage_key
    where m.mark = %s order by m.marked_at desc, m.passage_key"""
COUNTS_SQL = """select m.mark, count(*) from knowledge.feed_marks m
    join knowledge.feed_passages p on p.passage_key = m.passage_key group by m.mark"""
MARKS = ("again", "done")
EVERY_STEPS = (10, 5, 3, 2)
RUN_SQL = """select r.source_sha256, r.source_text, coalesce(r.parsed->'blocks', '[]'::jsonb)
    from knowledge.active_runs a
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where r.document_id=%s"""
STALE_SQL = """select k.pick_id, k.start_char, k.exact, k.prefix, k.suffix, r.source_sha256, r.source_text
    from knowledge.feed_picks k
    join knowledge.active_runs a on a.document_id=k.document_id
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where k.removed_at is null and k.source_sha256 <> r.source_sha256"""
LIVE_SQL = """select pick_id, start_char, end_char from knowledge.feed_picks
    where document_id=%s and source_sha256=%s and removed_at is null order by start_char"""
BLOCKS_SQL = """select r.document_id, coalesce(r.parsed->'blocks', '[]'::jsonb)
    from knowledge.active_runs a
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where r.document_id = any(%s::text[])"""


def _without_jit(conn) -> None:
    """The pool view's estimated cost is high on a small vault, which makes Postgres compile it (over a second per
    query) for no gain; these queries run once and are short."""
    conn.execute("set jit = off")


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


# Some exports label the reader's own note "**Note - Location 34**" instead of "**My note**", so the parser keeps it inside
# the highlight. Review shows the quote and the note apart.
NOTE_LABEL = re.compile(r"^[ \t]*\*\*[ \t]*Note\b[^\n]*\*\*[ \t]*$", re.MULTILINE)


def _quote_and_note(text: str, note: str | None) -> tuple[str, str | None]:
    label = NOTE_LABEL.search(text)
    if label and text[:label.start()].strip():
        text, inline_note = text[:label.start()], text[label.end():].strip()
        note = "\n\n".join(part for part in (note, inline_note) if part) or None
    return text.strip(), note


def _highlight(blocks: list[dict[str, Any]], start: int) -> tuple[str, str | None]:
    """A highlight's text and its own note, cleaned by the reader's pipeline."""
    for index, block in enumerate(blocks):
        if block.get("kind") == "highlight" and _span(block, "content_span") and _span(block, "content_span")[0] == start:
            note = next((other for other in blocks[index + 1:] if other.get("content_role") == "personal_note"
                         and other.get("group_id") == block.get("group_id")), None)
            items = reading_blocks([block] + ([note] if note else []))
            return _quote_and_note(items[0]["text"], items[0]["personal_note"]) if items else ("", None)
    return "", None


def _whole(blocks: list[dict[str, Any]], start: int, end: int) -> str:
    """The text items of the blocks inside a whole source's span, as paragraphs."""
    inside = [block for block in blocks if (span := _span(block, "span")) and start <= span[0] and span[1] <= end]
    return "\n\n".join(item["text"] for item in reading_blocks(inside) if item["kind"] not in SKIPPED_KINDS and item.get("text"))


def _containing(blocks: list[dict[str, Any]], start: int, end: int) -> dict[str, Any] | None:
    return next((block for block in blocks if (span := _span(block, "span")) and span[0] <= start and end <= span[1]), None)


def _pick_text(blocks: list[dict[str, Any]], start: int, end: int) -> str:
    """The words a pick covers as the reader shows them, without Markdown markers."""
    block = _containing(blocks, start, end)
    return "".join(char for char, offset in block_display(block) if start <= offset < end) if block else ""


def _normalised(pairs: list[tuple[str, int]]) -> tuple[str, list[int]]:
    """Text with each run of whitespace as one space, and the index into `pairs` of every character kept."""
    chars, at = [], []
    for index, (char, _) in enumerate(pairs):
        if char.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                at.append(index)
        else:
            chars.append(char)
            at.append(index)
    return "".join(chars), at


def _nth(text: str, words: str, occurrence: int) -> int | None:
    """Where the `occurrence`th (from 1) literal match of `words` starts in `text`."""
    index = -1
    for _ in range(occurrence):
        index = text.find(words, index + 1)
        if index < 0:
            return None
    return index


def _reanchored(text: str, exact: str, prefix: str, suffix: str, near: int) -> int | None:
    """Where a pick's exact words are in a changed file; between repeats, the one whose surroundings still match,
    then the one nearest to where it was. None when the words are gone."""
    hits, index = [], text.find(exact)
    while index >= 0:
        hits.append(index)
        index = text.find(exact, index + 1)
    if not hits:
        return None
    return max(hits, key=lambda hit: ((text[max(0, hit - len(prefix)):hit] == prefix)
                                      + (text[hit + len(exact):hit + len(exact) + len(suffix)] == suffix), -abs(hit - near)))


class ReviewStore:
    def __init__(self, connect: Callable[[], Any]) -> None:
        self.connect = connect

    def next(self, count: int, held: str = "") -> list[dict[str, Any]]:
        """Up to `count` passages: unshown ones after the round's position, skipping keys the client already holds, with
        an Again passage in every `again_every`th slot.

        A new round starts only when nothing is left after the position, held passages included; with only held
        passages left the list is empty (unless an Again passage is due) and the client keeps showing what it has."""
        count = max(1, min(count, MAX_COUNT))
        held_keys = _held(held)
        with self.connect() as conn:
            _without_jit(conn)
            self.reanchor(conn)
            every = conn.execute(EVERY_SQL).fetchone()[0]
            for _ in range(3):
                round_ = conn.execute(ROUND_SQL).fetchone()[0]
                position = conn.execute(POSITION_SQL, (round_,)).fetchone()[0]
                # One query: everything ranked after the position, held keys included, so that "nothing left" (a new
                # round) is told apart from "only held ones left" (an empty list).
                ahead = conn.execute(NEXT_SQL, {"round": round_, "position": position, "limit": count + len(held_keys)}).fetchall()
                if not ahead:
                    round_, position = round_ + 1, ""
                    ahead = conn.execute(NEXT_SQL, {"round": round_, "position": position, "limit": count + len(held_keys)}).fetchall()
                shuffle = [row for row in ahead if row[0] not in held_keys][:count]
                slots = self._slots(conn, shuffle, held_keys, every, count, nothing_to_shuffle=not ahead)
                blocks = dict(conn.execute(BLOCKS_SQL, ([row[1] for row, _ in slots],)).fetchall()) if slots else {}
                passages = [self._passage(row, round_, blocks.get(row[1]) or [], "again" if again else None, again) for row, again in slots]
                # A passage with nothing to draw (only an image or a table) is logged as shown here, so it never blocks
                # the position or the next round, and the page never gets a blank screen.
                for passage in passages:
                    if not passage["text"].strip() and not passage["again"]:
                        conn.execute("""insert into knowledge.feed_shown (round, passage_key, document_id) values (%s,%s,%s)
                            on conflict (round, passage_key) do nothing""", (round_, passage["passage_key"], passage["source_id"]))
                kept = [passage for passage in passages if passage["text"].strip()]
                if kept or not passages:
                    return kept
        return []

    def _slots(self, conn, shuffle: list, held_keys: list[str], every: int, count: int, nothing_to_shuffle: bool) -> list[tuple]:
        """The batch as (row, is_again) pairs. The gap counter is the shuffle passages shown since the latest Again
        serve plus the held ones, or only the held ones after the last held Again passage."""
        again = conn.execute(AGAIN_SQL, (held_keys,)).fetchall()
        held_again = {row[0] for row in conn.execute(HELD_AGAIN_SQL, (held_keys,)).fetchall()} if held_keys else set()
        after = [index for index, key in enumerate(held_keys) if key in held_again]
        if after:
            gap = len(held_keys) - 1 - after[-1]
        else:
            gap = conn.execute(SINCE_SERVE_SQL).fetchone()[0] + len(held_keys)
        slots, queue = [], iter(shuffle)
        for _ in range(count):
            if again and (nothing_to_shuffle or gap >= every - 1):
                slots.append((again.pop(0), True))
                gap = 0
                continue
            row = next(queue, None)
            if row is None:
                break
            slots.append((row, False))
            gap += 1
        return slots

    def _passage(self, row, round_: int | None, blocks: list[dict[str, Any]], mark: str | None = None, again: bool = False) -> dict[str, Any]:
        key, document_id, kind, start, end, raw, raw_note, type_, title, author, locator = row[:11]
        note = None
        if kind == "pick":
            text = _pick_text(blocks, start, end) or _clean(raw)
        elif kind == "highlight":
            text, note = _highlight(blocks, start)
        else:
            text = _whole(blocks, start, end)
        return {"passage_key": key, "round": round_, "text": text, "note": note, "title": title, "author": author,
                "source_id": document_id, "type": "post" if type_ == "social_post" else type_, "locator": locator,
                "mark": mark, "again": again}

    def shown(self, passage_key: str, round_: int, again: bool = False) -> None:
        """Log a passage as shown in `round_`. The round may be the current one, the one before (a passage held
        across a rollover) or the one after (the first passage of a new round). An Again serve is recorded on its
        mark instead: the shown log allows one row per passage per round, and an Again passage comes back."""
        if not KEY.match(passage_key):
            raise ConversationError(404, "passage not found")
        with self.connect() as conn:
            _without_jit(conn)
            if again:
                conn.execute("update knowledge.feed_marks set last_served_at=now() where passage_key=%s and mark='again'", (passage_key,))
                return
            current = conn.execute(ROUND_SQL).fetchone()[0]
            if not max(1, current - 1) <= round_ <= current + 1:
                raise ConversationError(400, "that round is not current")
            found = conn.execute("select document_id from knowledge.feed_passages where passage_key=%s", (passage_key,)).fetchone()
            if found is None:
                raise ConversationError(404, "passage not found")
            conn.execute("""insert into knowledge.feed_shown (round, passage_key, document_id) values (%s,%s,%s)
                on conflict (round, passage_key) do nothing""", (round_, passage_key, found[0]))

    # Marks -----------------------------------------------------------------------------------
    def set_mark(self, passage_key: str, mark: str) -> dict[str, str]:
        """Mark one passage Done or Again, replacing any mark it has; the newest mark lists first."""
        if not KEY.match(passage_key):
            raise ConversationError(400, "that passage key is not valid")
        with self.connect() as conn:
            _without_jit(conn)
            found = conn.execute("select document_id from knowledge.feed_passages where passage_key=%s", (passage_key,)).fetchone()
            if found is None:
                raise ConversationError(404, "passage not found")
            conn.execute("""insert into knowledge.feed_marks (passage_key, document_id, mark) values (%s,%s,%s)
                on conflict (passage_key) do update set mark=excluded.mark, marked_at=now()""", (passage_key, found[0], mark))
        return {"passage_key": passage_key, "mark": mark}

    def clear_mark(self, passage_key: str) -> None:
        if not KEY.match(passage_key):
            raise ConversationError(400, "that passage key is not valid")
        with self.connect() as conn:
            conn.execute("delete from knowledge.feed_marks where passage_key=%s", (passage_key,))

    def marked(self, mark: str) -> dict[str, Any]:
        """Every passage with this mark, newest mark first. Marks whose passage no longer exists are skipped."""
        with self.connect() as conn:
            _without_jit(conn)
            rows = conn.execute(LIST_SQL, (mark,)).fetchall()
            blocks = dict(conn.execute(BLOCKS_SQL, ([row[1] for row in rows],)).fetchall()) if rows else {}
        passages = [{**self._passage(row, None, blocks.get(row[1]) or [], mark), "marked_at": row[11].isoformat()} for row in rows]
        return {"passages": passages, "count": len(passages)}

    def settings(self) -> dict[str, int]:
        with self.connect() as conn:
            _without_jit(conn)
            every = conn.execute(EVERY_SQL).fetchone()[0]
            counts = dict(conn.execute(COUNTS_SQL).fetchall())
        return {"every": every, "again": counts.get("again", 0), "done": counts.get("done", 0)}

    def set_every(self, every: int) -> dict[str, int]:
        if every not in EVERY_STEPS:
            raise ConversationError(400, "that is not a how-often step")
        with self.connect() as conn:
            conn.execute("update knowledge.feed_settings set again_every=%s", (every,))
        return self.settings()

    # Picks -----------------------------------------------------------------------------------
    def reanchor(self, conn) -> None:
        """Move each live pick whose file changed to where its exact words are now, or remove it when they are gone."""
        for pick_id, start, exact, prefix, suffix, sha, text in conn.execute(STALE_SQL).fetchall():
            found = _reanchored(text, exact, prefix, suffix, start)
            if found is None:
                conn.execute("update knowledge.feed_picks set removed_at=now() where pick_id=%s", (pick_id,))
                continue
            end = found + len(exact)
            conn.execute("""update knowledge.feed_picks set source_sha256=%s, start_char=%s, end_char=%s, prefix=%s, suffix=%s
                where pick_id=%s""", (sha, found, end, text[max(0, found - CONTEXT_CHARS):found], text[end:end + CONTEXT_CHARS], pick_id))

    def _described(self, picks, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Picks as the reader draws them: the block, the shown words and where in the block's shown text they sit."""
        result = []
        for pick_id, start, end in picks:
            block = _containing(blocks, start, end)
            display = block_display(block) if block else []
            inside = [index for index, (_, offset) in enumerate(display) if start <= offset < end]
            if not inside:
                continue
            # The page counts UTF-16 units, Python counts code points: an emoji before the pick is two units.
            units = lambda upto: sum(len(char.encode("utf-16-le")) // 2 for char, _ in display[:upto])  # noqa: E731
            result.append({"id": str(pick_id), "block": int(block.get("index", 0)), "start": units(inside[0]),
                           "end": units(inside[-1] + 1), "text": "".join(display[index][0] for index in inside)})
        return result

    def live(self, conn, document_id: str, sha: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The source's live picks, for the reader. `blocks` and `sha` are the active run's."""
        self.reanchor(conn)
        return self._described(conn.execute(LIVE_SQL, (document_id, sha)).fetchall(), blocks)

    def add_pick(self, source_id: str, block_index: int, words: str, occurrence: int) -> dict[str, Any]:
        """Add the words (as the reader shows them) at their `occurrence`th place in one block to Review. A pick that
        overlaps live picks replaces them with one covering both."""
        try:
            document_id = str(uuid.UUID(source_id))
        except ValueError:
            raise ConversationError(404, "source not found") from None
        with self.connect() as conn, conn.transaction():
            self.reanchor(conn)
            run = conn.execute(RUN_SQL, (document_id,)).fetchone()
            if run is None:
                raise ConversationError(404, "source not found")
            sha, source_text, blocks = run
            block = blocks[block_index] if 0 <= block_index < len(blocks) else None
            if block is None or block.get("kind") in UNPICKABLE_KINDS:
                raise ConversationError(400, "that part cannot be added to Review")
            display = block_display(block)
            shown, kept = _normalised(display)
            wanted, _ = _normalised([(char, 0) for char in words.strip()])
            found = _nth(shown, wanted, occurrence) if wanted else None
            if found is None:
                raise ConversationError(400, "those words are not in the source; select them again")
            start, end = display[kept[found]][1], display[kept[found + len(wanted) - 1]][1] + 1
            live = conn.execute("""select pick_id, start_char, end_char from knowledge.feed_picks
                where document_id=%s and source_sha256=%s and removed_at is null and start_char < %s and end_char > %s""",
                                (document_id, sha, end, start)).fetchall()
            inside = next((pick for pick in live if pick[1] <= start and end <= pick[2]), None)
            if inside is not None:
                return self._described([inside], blocks)[0]
            start, end = min([start] + [pick[1] for pick in live]), max([end] + [pick[2] for pick in live])
            if live:
                conn.execute("update knowledge.feed_picks set removed_at=now() where pick_id = any(%s::uuid[])",
                             ([pick[0] for pick in live],))
            pick_id = conn.execute("""insert into knowledge.feed_picks (document_id, source_sha256, start_char, end_char, exact, prefix, suffix)
                values (%s,%s,%s,%s,%s,%s,%s) returning pick_id""",
                                   (document_id, sha, start, end, source_text[start:end],
                                    source_text[max(0, start - CONTEXT_CHARS):start], source_text[end:end + CONTEXT_CHARS])).fetchone()[0]
            return self._described([(pick_id, start, end)], blocks)[0]

    def remove_pick(self, pick_id: str) -> None:
        with self.connect() as conn:
            found = conn.execute("update knowledge.feed_picks set removed_at=coalesce(removed_at, now()) where pick_id=%s returning 1",
                                 (pick_id,)).fetchone()
        if found is None:
            raise ConversationError(404, "pick not found")
