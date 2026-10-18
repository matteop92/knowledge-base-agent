"""Conversations and turns in the private knowledge schema."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from psycopg.types.json import Jsonb

TITLE_CHARS = 80
INTERRUPTED = "interrupted: the server restarted while answering"


class ConversationError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _title(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rstrip() + "…"


class ConversationStore:
    def __init__(self, connect: Callable[[], Any], running_limit: timedelta) -> None:
        self.connect = connect
        # A running turn older than the answer time limit plus a minute no longer blocks the conversation.
        self.running_limit = running_limit + timedelta(minutes=1)

    def list(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("""select conversation_id, title, last_activity_at from knowledge.conversations
                order by last_activity_at desc limit 200""").fetchall()
        return [{"id": str(row[0]), "title": row[1], "last_activity_at": row[2].isoformat()} for row in rows]

    def get(self, conversation_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("select conversation_id, title from knowledge.conversations where conversation_id=%s",
                               (conversation_id,)).fetchone()
            if row is None:
                raise ConversationError(404, "conversation not found")
            turns = conn.execute("""select t.turn_id from knowledge.turns t
                where t.conversation_id=%s order by t.position""", (conversation_id,)).fetchall()
        return {"id": str(row[0]), "title": row[1], "turns": [self.turn(str(turn[0])) for turn in turns]}

    def start_turn(self, conversation_id: str | None, question: str) -> tuple[str, str]:
        """Create the conversation if needed and a running turn; one running turn per conversation."""
        question = question.strip()
        if not question:
            raise ConversationError(422, "question must not be empty")
        if len(question) > 4000:
            raise ConversationError(422, "question is too long")
        with self.connect() as conn, conn.transaction():
            if conversation_id is None:
                conversation_id = str(conn.execute(
                    "insert into knowledge.conversations (title) values (%s) returning conversation_id",
                    (_title(question),)).fetchone()[0])
            elif conn.execute("select 1 from knowledge.conversations where conversation_id=%s for update",
                              (conversation_id,)).fetchone() is None:
                raise ConversationError(404, "conversation not found")
            cutoff = datetime.now(timezone.utc) - self.running_limit
            if conn.execute("""select 1 from knowledge.turns where conversation_id=%s and status='running'
                    and created_at > %s""", (conversation_id, cutoff)).fetchone():
                raise ConversationError(409, "an answer is still running in this conversation")
            position = conn.execute("select coalesce(max(position), 0) + 1 from knowledge.turns where conversation_id=%s",
                                    (conversation_id,)).fetchone()[0]
            turn_id = str(conn.execute("""insert into knowledge.turns (conversation_id, position, question)
                values (%s, %s, %s) returning turn_id""", (conversation_id, position, question)).fetchone()[0])
            conn.execute("update knowledge.conversations set last_activity_at=now() where conversation_id=%s",
                         (conversation_id,))
        return conversation_id, turn_id

    def finish_turn(self, turn_id: str, *, status: str, receipt: dict[str, Any] | None, cost_usd: float,
                    error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute("""update knowledge.turns set status=%s, receipt=%s, cost_usd=%s, error=%s, finished_at=now()
                where turn_id=%s and status='running'""",
                         (status, Jsonb(receipt) if receipt is not None else None, round(cost_usd, 8), error, turn_id))

    def interrupt_running(self) -> int:
        """Fail running turns past the time limit; no live server can still be answering them."""
        cutoff = datetime.now(timezone.utc) - self.running_limit
        with self.connect() as conn:
            return conn.execute("""update knowledge.turns set status='failed', error=%s, finished_at=now()
                where status='running' and created_at < %s""", (INTERRUPTED, cutoff)).rowcount

    def running_count(self) -> int:
        cutoff = datetime.now(timezone.utc) - self.running_limit
        with self.connect() as conn:
            return conn.execute("select count(*) from knowledge.turns where status='running' and created_at >= %s",
                                (cutoff,)).fetchone()[0]

    def turn(self, turn_id: str) -> dict[str, Any]:
        self.interrupt_running()
        with self.connect() as conn:
            row = conn.execute("""select t.turn_id, t.conversation_id, t.position, t.question, t.status, t.receipt,
                    t.cost_usd, t.error, t.created_at, t.finished_at, f.verdict, f.note
                from knowledge.turns t left join knowledge.feedback f on f.turn_id=t.turn_id
                where t.turn_id=%s""", (turn_id,)).fetchone()
        if row is None:
            raise ConversationError(404, "turn not found")
        return {"id": str(row[0]), "conversation_id": str(row[1]), "position": row[2], "question": row[3],
                "status": row[4], "receipt": row[5], "cost_usd": float(row[6]), "error": row[7],
                "created_at": row[8].isoformat(), "finished_at": row[9].isoformat() if row[9] else None,
                "feedback": {"verdict": row[10], "note": row[11]} if row[10] else None}

    def set_feedback(self, turn_id: str, note: str | None) -> None:
        note = (note or "").strip()[:2000] or None
        with self.connect() as conn:
            if conn.execute("select 1 from knowledge.turns where turn_id=%s", (turn_id,)).fetchone() is None:
                raise ConversationError(404, "turn not found")
            conn.execute("""insert into knowledge.feedback (turn_id, verdict, note) values (%s, 'down', %s)
                on conflict (turn_id) do update set note=excluded.note""", (turn_id, note))

    def clear_feedback(self, turn_id: str) -> None:
        with self.connect() as conn:
            conn.execute("delete from knowledge.feedback where turn_id=%s", (turn_id,))
