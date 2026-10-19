"""Per-conversation memory: recent turns, a standalone search query, and carried evidence.

A follow-up resends the last few turns as text, rewrites the follow-up into a query that
stands on its own, and carries forward passages earlier turns read so they can be cited
again without a new read. A carried passage whose document has been republished since is
dropped, and the model is told which sources changed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.client import ModelClient, ModelClientError, ModelSpec
from app.storage.records import SourcePassage

REWRITE_MODEL = "openai/gpt-6-luna"
REWRITE_PROMPT_VERSION = "followup_rewrite_v1"
REWRITE_PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / f"{REWRITE_PROMPT_VERSION}.md").read_text(
    encoding="utf-8")
REWRITE_MAX_OUTPUT_TOKENS = 200
REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["query"],
                  "properties": {"query": {"type": "string"}}}
RECENT_TURNS = 3  # turns resent as text
PRIOR_TURNS = 6  # turns whose read passages may be carried
ANSWER_CHARS = 1500  # of each resent answer
QUESTION_CHARS = 1000
# Worst-case rewrite input, in characters, used to bound its cost before the call.
REWRITE_INPUT_CHARS = len(REWRITE_PROMPT) + RECENT_TURNS * (QUESTION_CHARS + ANSWER_CHARS) + QUESTION_CHARS + 500
MAX_CARRIED_PASSAGES = 24
MAX_CARRIED_BYTES = 24_000
CITATION_MARK = re.compile(r"\s?\[E\d+\.\d+(?:@\d+)?\]")
PASSAGE_ID = re.compile(r"^E\d+(\.\d+(?:@\d+)?)$")
REVISION_FIELDS = ("document_id", "run_id", "chunk_id", "source_sha256", "root_alias", "relative_path",
                   "start_char", "end_char", "text")


@dataclass(frozen=True)
class PriorTurn:
    question: str
    answer: str
    passages: dict[str, SourcePassage]  # citation id in that turn -> passage
    titles: dict[str, str]  # document id -> source label


@dataclass
class Memory:
    turns: list[dict[str, str]]
    carried: dict[str, SourcePassage] = field(default_factory=dict)
    chunk_titles: dict[str, str] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)
    dropped_over_cap: int = 0

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_titles)

    def record(self) -> dict[str, Any]:
        return {"turns": len(self.turns), "carried": len(self.carried), "changed": self.changed,
                "dropped_over_cap": self.dropped_over_cap}


def prior_turn_from_receipt(question: str, receipt: dict[str, Any]) -> PriorTurn:
    """Rebuild a finished turn from its stored receipt; passages without revision ids are not carried."""
    passages, titles = {}, {}
    for ref, item in (receipt.get("inspected") or {}).items():
        if not all(item.get(name) is not None for name in REVISION_FIELDS):
            continue
        passages[ref] = SourcePassage(**{name: item[name] for name in REVISION_FIELDS})
        titles[item["document_id"]] = item.get("title") or item["relative_path"]
    return PriorTurn(question=question, answer=receipt.get("answer") or "", passages=passages, titles=titles)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_memory(turns: list[PriorTurn], *, active: set[tuple[str, str]]) -> Memory:
    """Carry passages from the newest turns first, renumbered E1, E2... per chunk, within the caps."""
    memory = Memory(turns=[{"question": _clip(item.question, QUESTION_CHARS),
                            "answer": _clip(CITATION_MARK.sub("", item.answer), ANSWER_CHARS)}
                           for item in turns[-RECENT_TURNS:]])
    kept: list[tuple[str, SourcePassage, str]] = []  # (span suffix, passage, title)
    seen: set[tuple[str, str, int]] = set()
    used_bytes = 0
    for prior in reversed(turns[-PRIOR_TURNS:]):
        for ref, item in prior.passages.items():
            key = (item.run_id, item.chunk_id, item.start_char)
            match = PASSAGE_ID.match(ref)
            if key in seen or match is None:
                continue
            seen.add(key)
            title = prior.titles.get(item.document_id) or item.relative_path
            if (item.document_id, item.run_id) not in active:
                if title not in memory.changed:
                    memory.changed.append(title)
                continue
            size = len(item.text.encode("utf-8"))
            if len(kept) >= MAX_CARRIED_PASSAGES or used_bytes + size > MAX_CARRIED_BYTES:
                memory.dropped_over_cap += 1
                continue
            used_bytes += size
            kept.append((match.group(1), item, title))
    chunks: dict[tuple[str, str], str] = {}
    for suffix, item, title in kept:
        chunk_ref = chunks.setdefault((item.run_id, item.chunk_id), f"E{len(chunks) + 1}")
        memory.chunk_titles.setdefault(chunk_ref, title)
        memory.carried[chunk_ref + suffix] = item
    return memory


def standalone_query(question: str, recent: list[dict[str, str]], *, client: ModelClient, model: ModelSpec) -> str:
    """Rewrite a follow-up into a self-contained search query; a first question passes through unchanged."""
    if not recent:
        return question
    input_text = json.dumps({"conversation": recent, "follow_up": _clip(question, QUESTION_CHARS)},
                            ensure_ascii=False, separators=(",", ":"))
    try:
        result = client.generate_json(model=model, system_prompt=REWRITE_PROMPT, input_text=input_text,
                                      schema_name="followup_rewrite_v1", json_schema=REWRITE_SCHEMA,
                                      max_output_tokens=REWRITE_MAX_OUTPUT_TOKENS)
    except ModelClientError:
        # The answer can still search with the follow-up itself and use the carried evidence.
        return question
    query = result.data.get("query") if isinstance(result.data, dict) else None
    return " ".join(query.split())[:QUESTION_CHARS] if isinstance(query, str) and query.strip() else question
