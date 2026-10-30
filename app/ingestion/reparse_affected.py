"""One-off: re-index only the notes the fence- and table-aware Markdown parser reads differently.

`vault_sync` decides by content hash, so a parser change alone re-indexes nothing. This reads the stored source text of
the active runs, keeps the notes that hold a fence line or a table, parses each with the current parser and compares its
blocks with the stored ones. The default run is read-only and reports the count. `--live` then re-indexes just the
notes whose blocks differ, through the normal load path (a new run with embeddings, under `--max-usd`).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.env import load_env_file
from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source
from app.ingestion.vault_sync import VaultSyncError, run_pass, settings_from_env
from app.storage.loader import connect_runtime

# A coarse filter; the comparison below is the exact test. A fence line, or a `|` row followed by a `---` row.
CANDIDATES_SQL = r"""select d.document_id, d.relative_path, d.root_alias, r.source_bytes,
        coalesce(r.parsed->'blocks', '[]'::jsonb)
    from knowledge.active_runs a
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.documents d on d.document_id=r.document_id
    join knowledge.corpus_state s on s.profile_id=a.profile_id
    where r.source_text ~ '(^|\n)[ \t]*(```|~~~)'
       or r.source_text ~ '(^|\n)[ \t]*\|[^\n]*\n[ \t]*\|?[ \t]*:?-+:?[ \t]*\|'
    order by d.relative_path"""
ACTIVE_COUNT_SQL = """select count(*) from knowledge.active_runs a
    join knowledge.corpus_state s on s.profile_id=a.profile_id"""


def block_shape(block: dict[str, Any]) -> tuple:
    span = block["span"]
    return (block["kind"], span["start_char"], span["end_char"], block.get("heading_path"))


def read_differently(relative_path: str, root_alias: str, source_bytes: bytes, stored: list[dict[str, Any]]) -> bool:
    """Whether the current parser cuts the note's text into other blocks (or headings) than the stored run has."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "vault"
        path = root / relative_path
        path.parent.mkdir(parents=True)
        path.write_bytes(bytes(source_bytes))
        parsed = route_source(capture_source(root, relative_path, Path(temp) / "runs", root_alias))
    return [block_shape(block) for block in stored] != [
        block_shape(block.model_dump(mode="json")) for block in parsed.blocks]


def affected_documents(conn) -> dict[str, Any]:
    active = conn.execute(ACTIVE_COUNT_SQL).fetchone()[0]
    rows = conn.execute(CANDIDATES_SQL).fetchall()
    affected = [(document_id, relative_path) for document_id, relative_path, alias, source_bytes, blocks in rows
                if read_differently(relative_path, alias, source_bytes, blocks)]
    return {"active_notes": active, "with_fence_or_table": len(rows), "read_differently": len(affected),
            "documents": affected}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-index the notes the fence- and table-aware parser reads differently")
    parser.add_argument("--live", action="store_true", help="re-index the affected notes (embeds them; needs --max-usd)")
    parser.add_argument("--max-usd", type=float)
    parser.add_argument("--list", action="store_true", help="print the affected notes' paths (private; do not commit)")
    parser.add_argument("--source-root")
    parser.add_argument("--folders")
    parser.add_argument("--runtime-dir")
    args = parser.parse_args(argv)
    load_env_file()
    try:
        with connect_runtime() as conn:
            found = affected_documents(conn)
            documents = found.pop("documents")
            ids = frozenset(document_id for document_id, _ in documents)
            report = dict(found)
            if args.list:
                report["paths"] = [path for _, path in documents]
            if ids:
                # A dry pass (no model calls, no writes) reports the cost; --live runs the pass for these notes only.
                report["pass"] = run_pass(settings_from_env(args), conn=conn, live=args.live, max_usd=args.max_usd,
                                          retire=False, only=ids)
    except VaultSyncError as exc:
        print(f"reparse stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
