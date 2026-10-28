"""Export thumbs-down turns to a private review file under `.local/`.

Each exported turn carries the question, the thumbs-down note, the answer receipt and the
IDs needed to find it again. A turn whose receipt is missing is still listed, with a
warning. The query is read-only and the output path must be new and under `.local/`.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from evals.answer_review import private_output

QUERY = """select f.turn_id, t.conversation_id, c.title, t.position, t.question, t.status, f.note,
    f.created_at, t.receipt
  from knowledge.feedback f
  join knowledge.turns t on t.turn_id = f.turn_id
  join knowledge.conversations c on c.conversation_id = t.conversation_id
  order by f.created_at"""


def turn_entry(row: tuple) -> dict[str, Any]:
    turn_id, conversation_id, title, position, question, status, note, created_at, receipt = row
    warnings = [] if receipt else [f"turn {turn_id} has no receipt"]
    return {"turn_id": str(turn_id), "conversation_id": str(conversation_id), "conversation_title": title,
            "position": position, "question": question, "status": status, "note": note,
            "created_at": created_at.isoformat() if created_at else None, "receipt": receipt,
            "warnings": warnings}


def export_feedback(connect: Callable, output: Path) -> Path:
    """Write every thumbs-down turn to a new private file; the path is checked before any query."""
    target = private_output(output)
    with connect() as conn:
        with conn.transaction():
            conn.execute("set transaction read only")
            rows = conn.execute(QUERY).fetchall()
    turns = [turn_entry(row) for row in rows]
    data = {"exported_at": datetime.now(timezone.utc).isoformat(), "turns": turns,
            "warnings": [warning for turn in turns for warning in turn["warnings"]]}
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new file under .local/feedback/")
    args = parser.parse_args(argv)
    from app.storage.loader import connect_runtime

    target = export_feedback(connect_runtime, args.output)
    data = json.loads(target.read_text(encoding="utf-8"))
    for warning in data["warnings"]:
        print(f"warning: {warning}")
    print(f"{len(data['turns'])} turn(s) written to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
