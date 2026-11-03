"""Build Review test data: files parsed by the real parsers and published as a document's active run."""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path

from psycopg.types.json import Jsonb

from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source

PROFILE = "review-pool"
SPEC = "e" * 64


def highlights_file(*items: tuple[str, str | None]) -> str:
    parts = ["---\ntitle: Book Notes\nauthor: Ada Stone\n---\n# Book Notes\n"]
    for index, (text, note) in enumerate(items, 1):
        parts.append(f"**Highlight p. {index}**\n{text}\n")
        if note:
            parts.append(f"**My note**\n{note}\n")
    return "\n".join(parts)


def words(count: int, stem: str = "word") -> str:
    return " ".join(f"{stem}{n}" for n in range(count))


class PoolBuilder:
    """Needs an autocommit connection that may write every table (not the runtime role)."""

    def __init__(self, admin) -> None:
        self.admin = admin
        admin.execute("""insert into knowledge.embedding_specs (spec_id, provider, model, model_version, dimensions,
            distance_metric, input_recipe_version, chunking_version, payload)
            values (%s,'fixture','synthetic','1',3,'cosine','raw-v1','c1','{}') on conflict do nothing""", (SPEC,))
        admin.execute("""insert into knowledge.profiles (profile_id, variant, chunking_version, spec_id, text_config)
            values (%s,'fixed_raw','c1',%s,'simple') on conflict do nothing""", (PROFILE, SPEC))

    def reset(self) -> None:
        self.admin.execute("update knowledge.corpus_state set profile_id=null where singleton=true")
        self.admin.execute("truncate knowledge.documents cascade")
        self.admin.execute("update knowledge.corpus_state set profile_id=%s where singleton=true", (PROFILE,))
        self.folder = tempfile.TemporaryDirectory()
        self.revision = 0

    def close(self) -> None:
        self.folder.cleanup()

    def teardown(self) -> None:
        """Leave the shared test database as found: no documents, no active profile, and none of this fixture's rows."""
        self.admin.execute("update knowledge.corpus_state set profile_id=null where singleton=true")
        self.admin.execute("truncate knowledge.documents cascade")
        self.admin.execute("delete from knowledge.profiles where profile_id=%s", (PROFILE,))
        self.admin.execute("delete from knowledge.embedding_specs where spec_id=%s", (SPEC,))

    def add(self, path: str, text: str) -> str:
        """Parse a file and publish it as the active run of its document (a new run if it was added before)."""
        base = Path(self.folder.name)
        source = base / "vault" / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(text.encode("utf-8"))
        self.revision += 1
        parsed = route_source(capture_source(base / "vault", path, base / f"runs{self.revision}"))
        document = str(uuid.uuid5(uuid.NAMESPACE_URL, path))
        self.admin.execute("insert into knowledge.documents values (%s,'primary',%s) on conflict do nothing", (document, path))
        raw = parsed.source_text.encode("utf-8")
        run_id = f"run-{uuid.uuid4().hex}"
        self.admin.execute("""insert into knowledge.runs (run_id, document_id, source_sha256, source_bytes, source_text,
            parsed, parser_version, manifest) values (%s,%s,%s,%s,%s,%s,%s,'{}')""",
                           (run_id, document, hashlib.sha256(raw).hexdigest(), raw, parsed.source_text,
                            Jsonb(parsed.model_dump(mode="json")), parsed.parser_version))
        self.admin.execute("""insert into knowledge.active_runs (profile_id, document_id, run_id, selection)
            values (%s,%s,%s,'{}') on conflict (profile_id, document_id) do update set run_id=excluded.run_id""",
                           (PROFILE, document, run_id))
        return document
