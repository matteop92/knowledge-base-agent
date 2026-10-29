"""Suggested tags: the classifier's proposals for tags not on the list, and one-tap approve or block.

A tag decision only writes a row in knowledge.tags. The classifier reads that table on its next run, so only
notes added or changed from then on are affected; stored properties of existing notes are never touched.
"""

from __future__ import annotations

from typing import Any, Callable

from app.api.answers import obsidian_link
from app.agent.properties import to_sources
from app.api.conversations import ConversationError
from app.ingestion.properties import TAG, ensure_tags_seeded
from app.tag_aliases import aliases
from app.storage.search import load_sources, source_label

SUGGESTIONS_SQL = """select p.proposed_tag, r.document_id, d.relative_path, r.parsed->'metadata', p.properties
    from knowledge.corpus_state state
    join knowledge.active_runs a on a.profile_id=state.profile_id
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.documents d on d.document_id=r.document_id
    join knowledge.document_properties p on p.document_id=r.document_id and p.source_sha256=r.source_sha256
    where p.status='ready' and p.proposed_tag is not null
      and not exists (select 1 from knowledge.tags t where t.name=p.proposed_tag)
    order by p.proposed_tag, d.relative_path"""

APPROVED_SQL = "select name from knowledge.tags where status='approved' order by position"

# Notes whose current revision was loaded in the last seven days, newest first.
RECENT_SQL = """select d.relative_path, r.parsed->'metadata', p.properties
    from knowledge.corpus_state state
    join knowledge.active_runs a on a.profile_id=state.profile_id
    join knowledge.runs r on r.run_id=a.run_id
    join knowledge.documents d on d.document_id=r.document_id
    left join knowledge.document_properties p on p.document_id=r.document_id and p.source_sha256=r.source_sha256
        and p.status='ready'
    where r.loaded_at >= now() - interval '7 days'
    order by r.loaded_at desc
    limit 3"""


def _kind(metadata: dict[str, Any], properties: dict[str, Any]) -> str | None:
    frontmatter = metadata.get("frontmatter") if isinstance(metadata.get("frontmatter"), dict) else {}
    kind = frontmatter.get("type") or frontmatter.get("source_type") or properties.get("source_type")
    return kind if isinstance(kind, str) else None


def _note(row, vault_name: str) -> dict[str, Any]:
    _, document_id, relative_path, metadata, properties = row
    metadata = metadata if isinstance(metadata, dict) else {}
    properties = properties if isinstance(properties, dict) else {}
    title, people, _ = source_label({**metadata, "generated": properties}, relative_path)
    kind = _kind(metadata, properties)
    description = metadata.get("description") or properties.get("description")
    return {"document_id": document_id, "title": title, "people": people,
            "source_type": kind,
            "description": description if isinstance(description, str) else None,
            "obsidian_url": obsidian_link(vault_name, relative_path)}


class TagStore:
    def __init__(self, connect: Callable[[], Any], vault_name: str) -> None:
        self.connect, self.vault_name = connect, vault_name

    def suggestions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            ensure_tags_seeded(conn)
            rows = conn.execute(SUGGESTIONS_SQL).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(_note(row, self.vault_name))
        ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
        return [{"name": name, "count": len(notes), "notes": notes} for name, notes in ordered]

    def approved(self) -> list[str]:
        return [tag["name"] for tag in self.in_use()]

    def in_use(self) -> list[dict[str, Any]]:
        """The one tag list: approved tags plus every tag on a note, with how many notes carry each (0 if none yet).

        The agent may use any of them; it can only suggest new ones, which wait in Settings for approval."""
        with self.connect() as conn:
            ensure_tags_seeded(conn)
            names = {row[0] for row in conn.execute(APPROVED_SQL).fetchall() if row[0] not in aliases()}
            sources = to_sources(load_sources(conn))
            blocked = {row[0] for row in conn.execute("select name from knowledge.tags where status='blocked'").fetchall()}
        counts = {name: 0 for name in names}
        for source in sources:
            for tag in set(source.tags):
                if tag not in blocked:
                    counts[tag] = counts.get(tag, 0) + 1
        return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]

    def starters(self) -> list[dict[str, str]]:
        """Up to three questions, one per note added or changed this week."""
        with self.connect() as conn:
            rows = conn.execute(RECENT_SQL).fetchall()
        starters = []
        for relative_path, metadata, properties in rows:
            metadata = metadata if isinstance(metadata, dict) else {}
            properties = properties if isinstance(properties, dict) else {}
            title, _, _ = source_label({**metadata, "generated": properties}, relative_path)
            starters.append({"title": title, "question": f"What do my notes say about {title}?",
                             "source_type": _kind(metadata, properties)})
        return starters

    def decide(self, name: str, status: str) -> None:
        if status not in ("approved", "blocked"):
            raise ConversationError(400, "unknown decision")
        name = name.strip().lower()
        if not TAG.match(name):
            raise ConversationError(400, "that is not a valid tag name")
        with self.connect() as conn:
            ensure_tags_seeded(conn)
            known = conn.execute("""select 1 from knowledge.tags where name=%s union all
                select 1 from knowledge.document_properties where proposed_tag=%s limit 1""", (name, name)).fetchone()
            if known is None:
                raise ConversationError(404, "tag not found")
            conn.execute("""insert into knowledge.tags (name, status) values (%s, %s)
                on conflict (name) do update set status=excluded.status, decided_at=now()""", (name, status))
