"""Captures run as background jobs: a POST stores a running job and the client polls it.

A job classifies the captured text, writes it as a new note into the right Knowledge folder, then asks
the indexer to run at once so the note is searchable within a minute. Two-way sync carries the note to
Matt's devices, so capture is refused until the server's sync is two-way.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from app.api.conversations import ConversationError
from app.api.settings import Settings
from app.capture.notes import CaptureError, Draft, folder_for, note_properties, parse_draft, render, write_note
from app.ingestion.source_reader import document_id_for
from app.storage.spend import SpendCapError, check_daily_cap, record_spend

log = logging.getLogger("knowledge.api.capture")

INDEX_WAIT_SECONDS = 30
# A running job older than this no longer counts as running: the server restarted while it ran.
JOB_LIMIT = timedelta(minutes=5)
INTERRUPTED = "interrupted: the server restarted while saving"
JOB_SELECT = "select job_id, kind, status, vault_path, title, indexed, error, cost_usd, created_at, finished_at from knowledge.capture_jobs"


def _job_dict(row) -> dict[str, Any]:
    return {"id": str(row[0]), "kind": row[1], "status": row[2], "vault_path": row[3], "title": row[4],
            "indexed": row[5], "error": row[6], "cost_usd": float(row[7]), "created_at": row[8].isoformat(),
            "finished_at": row[9].isoformat() if row[9] else None}


@dataclass
class Classification:
    properties: dict[str, Any]
    cost_usd: float


class Classifier(Protocol):
    def worst_case_usd(self, draft: Draft, today: date) -> float: ...

    def classify(self, draft: Draft, today: date) -> Classification: ...


class OpenRouterClassifier:
    """The indexer's properties classifier on its pinned zero-retention route."""

    def worst_case_usd(self, draft: Draft, today: date) -> float:
        from app.ingestion.properties import TagList, worst_case_usd

        tag_list = TagList.load()
        return worst_case_usd(parse_draft(draft, today), tag_list) if tag_list else 0.0

    def classify(self, draft: Draft, today: date) -> Classification:
        from app.ingestion.properties import TagList, classify_properties, model_spec, worst_case_usd
        from app.ingestion.vault_sync import _classifier_input_bytes, _measured_usd, _openrouter_client, _usage_mark

        tag_list = TagList.load()
        if tag_list is None:
            return Classification({}, 0.0)
        parsed = parse_draft(draft, today)
        model = model_spec()
        worst = worst_case_usd(parsed, tag_list, model)
        client = _openrouter_client([model.model_id], requests=1, input_bytes=_classifier_input_bytes(parsed, tag_list),
                                    structured={model.model_id})
        mark = _usage_mark(client)
        try:
            result = classify_properties(parsed, tag_list, client, model)
        except Exception:
            raise CaptureCostError(_measured_usd(client, mark, worst)) from None
        return Classification(result.properties, _measured_usd(client, mark, worst))


@dataclass
class ScreenshotInput:
    """A screenshot waiting for its text to be extracted."""
    image: str
    title: str | None = None
    tags: list[str] | None = None
    kind: str = "screenshot"


class Extractor(Protocol):
    def worst_case_usd(self, image: str) -> float: ...

    def extract(self, item: ScreenshotInput) -> tuple[Draft, float]: ...


class OpenRouterExtractor:
    """The vision model on its pinned zero-retention route."""

    def worst_case_usd(self, image: str) -> float:
        from app.capture.screenshot import worst_case_usd

        return worst_case_usd(image)

    def extract(self, item: ScreenshotInput) -> tuple[Draft, float]:
        from app.capture.screenshot import extract_screenshot, input_bytes, model_spec, worst_case_usd
        from app.ingestion.vault_sync import _measured_usd, _openrouter_client, _usage_mark

        model = model_spec()
        client = _openrouter_client([model.model_id], requests=1, input_bytes=input_bytes(item.image),
                                    structured={model.model_id})
        mark, worst = _usage_mark(client), worst_case_usd(item.image, model)
        try:
            draft = extract_screenshot(item.image, client, model, title=item.title, tags=item.tags)
        except CaptureError as exc:
            exc.cost_usd = _measured_usd(client, mark, worst)
            raise
        except Exception:
            raise CaptureCostError(_measured_usd(client, mark, worst)) from None
        return draft, _measured_usd(client, mark, worst)


class CaptureCostError(Exception):
    """Classification failed after it may have been billed; carries what it cost."""

    def __init__(self, cost_usd: float) -> None:
        super().__init__("classification failed")
        self.cost_usd = cost_usd


class CaptureJobs:
    def __init__(self, connect: Callable[[], Any], settings: Settings, classifier: Classifier | None = None,
                 indexer=None, today: Callable[[], date] = date.today, extractor: Extractor | None = None) -> None:
        self.connect, self.settings, self.indexer, self.today = connect, settings, indexer, today
        self.classifier = classifier or OpenRouterClassifier()
        self.extractor = extractor or OpenRouterExtractor()
        self._threads: list[threading.Thread] = []

    @property
    def enabled(self) -> bool:
        return self.settings.sync_enabled and self.settings.sync_mode == "bidirectional"

    def _worst_case(self, item: Draft | ScreenshotInput) -> float:
        if isinstance(item, ScreenshotInput):
            # The classifier reads at most a fixed slice of the text, so a short stand-in bounds it.
            return self.extractor.worst_case_usd(item.image) + self.classifier.worst_case_usd(
                Draft(kind="screenshot", body="x" * 8000, title="x"), self.today())
        return self.classifier.worst_case_usd(item, self.today())

    def start(self, item: Draft | ScreenshotInput) -> str:
        if not self.enabled:
            raise ConversationError(503, "capture needs two-way sync, which is not switched on yet")
        with self.connect() as conn:
            check_daily_cap(conn, self._worst_case(item), self.settings.daily_cap_usd)
            job_id = str(conn.execute("insert into knowledge.capture_jobs (kind, title) values (%s, %s) returning job_id",
                                      (item.kind, item.title)).fetchone()[0])
        thread = threading.Thread(target=self._run, args=(job_id, item), daemon=True, name=f"capture-{job_id}")
        self._threads = [item for item in self._threads if item.is_alive()] + [thread]
        thread.start()
        return job_id

    def wait(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        for thread in list(self._threads):
            thread.join(max(0, deadline - time.monotonic()))

    def job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(JOB_SELECT + " where job_id=%s", (job_id,)).fetchone()
        if row is None:
            raise ConversationError(404, "capture not found")
        return _job_dict(row)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(JOB_SELECT + " order by created_at desc limit %s", (limit,)).fetchall()
        return [_job_dict(row) for row in rows]

    def expire_stale(self) -> int:
        cutoff = datetime.now(timezone.utc) - JOB_LIMIT
        with self.connect() as conn:
            rows = conn.execute("""update knowledge.capture_jobs set status='failed', error=%s, finished_at=now()
                where status='running' and created_at < %s returning job_id""", (INTERRUPTED, cutoff)).fetchall()
        return len(rows)

    def _finish(self, job_id: str, *, status: str, vault_path: str | None = None, indexed: bool = False,
                error: str | None = None, spend: dict[str, float] | None = None, title: str | None = None) -> None:
        spend = {source: usd for source, usd in (spend or {}).items() if usd}
        with self.connect() as conn:
            for source, usd in spend.items():
                record_spend(conn, source, usd, job_id)
            conn.execute("""update knowledge.capture_jobs set status=%s, vault_path=%s, indexed=%s, error=%s,
                cost_usd=%s, title=coalesce(%s, title), finished_at=now() where job_id=%s and status='running'""",
                         (status, vault_path, indexed, error, round(sum(spend.values()), 8), title, job_id))

    def _run(self, job_id: str, item: Draft | ScreenshotInput) -> None:
        started = time.monotonic()
        spend: dict[str, float] = {}
        title, stage = None, "properties"
        try:
            today = self.today()
            with self.connect() as conn:
                check_daily_cap(conn, self._worst_case(item), self.settings.daily_cap_usd)
            if isinstance(item, ScreenshotInput):
                stage = "vision"
                try:
                    draft, spend["vision"] = self.extractor.extract(item)
                except CaptureError as exc:
                    spend["vision"] = getattr(exc, "cost_usd", 0.0)
                    raise
                stage = "properties"
            else:
                draft = item
            title = draft.title
            classified = self.classifier.classify(draft, today)
            spend["properties"] = classified.cost_usd
            properties = note_properties(draft, classified.properties, today)
            folder = folder_for(draft.kind, classified.properties.get("source_type"))
            vault_path = write_note(Path(self.settings.vault_root), self.settings.capture_folder, folder, draft.title,
                                    render(draft, properties), today)
        except CaptureError as exc:
            self._finish(job_id, status="failed", error=str(exc), spend=spend, title=title)
            return
        except SpendCapError:
            self._finish(job_id, status="failed", error="the daily spending cap was reached", spend=spend, title=title)
            return
        except CaptureCostError as exc:
            log.warning("capture model call failed job=%s", job_id)
            spend[stage] = exc.cost_usd
            self._finish(job_id, status="failed", error="the note could not be read or classified; try again",
                         spend=spend, title=title)
            return
        except Exception as exc:  # noqa: BLE001 - the job must always leave the running state
            log.warning("capture failed job=%s error=%s", job_id, type(exc).__name__)
            self._finish(job_id, status="failed", error="the note could not be saved; try again", spend=spend, title=title)
            return
        # The note is saved; indexing it now is a bonus, and the next scheduled pass picks it up otherwise.
        indexed = False
        try:
            indexed = self._index_now(vault_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("capture indexing failed job=%s error=%s", job_id, type(exc).__name__)
        self._finish(job_id, status="done", vault_path=vault_path, indexed=indexed, spend=spend, title=title)
        # Ids and timing only; never captured text or titles.
        log.info("capture finished job=%s seconds=%.1f indexed=%s", job_id, time.monotonic() - started, indexed)

    def _index_now(self, vault_path: str) -> bool:
        if self.indexer is None or not self.indexer.tick(wait=INDEX_WAIT_SECONDS):
            return False
        from app.ingestion.vault_sync import embedding_spec, profile_id_for

        document_id = document_id_for(self.indexer.vault.root_alias, vault_path)
        with self.connect() as conn:
            return conn.execute("""select 1 from knowledge.active_runs where document_id=%s and profile_id=%s""",
                                (document_id, profile_id_for(embedding_spec()))).fetchone() is not None
