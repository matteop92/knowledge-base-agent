"""Index the synced vault copy every few minutes in a background thread.

Obsidian Headless keeps the vault copy up to date; this thread runs the incremental indexer over it.
Only counts and error types are recorded for /health, never note text.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.ingestion.properties import TagList
from app.ingestion.vault_sync import VaultSettings, VaultSyncError, parse_folders, run_pass

log = logging.getLogger("knowledge.api.scheduler")

SUMMARY_KEYS = ("files_seen", "to_ingest", "published", "retired", "unchanged", "skipped_failed",
                "retire_held", "properties_ready", "properties_failed", "properties_retry", "spent_usd", "stopped", "guard")


def vault_settings_from_env() -> VaultSettings:
    return VaultSettings(
        source_root=Path(os.getenv("KNOWLEDGE_SOURCE_ROOT", "/data/vault")),
        root_alias=os.getenv("KNOWLEDGE_SOURCE_ROOT_ALIAS", "primary"),
        included_folders=parse_folders(os.getenv("KNOWLEDGE_INCLUDED_FOLDERS", "Knowledge")),
        # Run snapshots live on the container's own disk; a restart clears them and any stale lock with them.
        runtime_dir=Path(os.getenv("KNOWLEDGE_RUNTIME_DIR", "/tmp/knowledge-runs")),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IndexScheduler:
    def __init__(self, vault: VaultSettings, connect: Callable[[], Any], *, interval_seconds: float = 300,
                 max_usd_per_pass: float = 0.10, first_delay_seconds: float = 30,
                 pass_fn: Callable[..., dict] = run_pass, tag_list: Callable[[], TagList | None] = TagList.load) -> None:
        self.vault, self.connect = vault, connect
        self.interval, self.first_delay = interval_seconds, first_delay_seconds
        self.max_usd, self.pass_fn, self.load_tags = max_usd_per_pass, pass_fn, tag_list
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0
        self.state: dict[str, Any] = {"enabled": True, "status": "not started", "last_started_at": None,
                                      "last_finished_at": None, "last_result": None, "last_error": None}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="index-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        state = dict(self.state)
        # The sync loop touches this file after each successful download; its age shows a stalled sync.
        marker = os.getenv("KNOWLEDGE_SYNC_MARKER", "/data/last-sync")
        try:
            state["last_sync_at"] = datetime.fromtimestamp(os.path.getmtime(marker), timezone.utc).isoformat(timespec="seconds")
        except OSError:
            state["last_sync_at"] = None
        return state

    def _loop(self) -> None:
        if self._stop.wait(self.first_delay):
            return
        while True:
            self.tick()
            if self._stop.wait(self.interval):
                return

    def vault_ready(self) -> bool:
        """The first sync has produced at least one note in an included folder."""
        root = self.vault.source_root
        if not root.is_dir():
            return False
        wanted = {name.casefold() for name in self.vault.included_folders}
        for entry in root.iterdir():
            if entry.is_dir() and entry.name.casefold() in wanted and next(entry.rglob("*.md"), None) is not None:
                return True
        return False

    def tick(self, wait: float = 0) -> bool:
        """Run one pass unless the last one is still going or the vault has not synced yet.

        `wait` lets a capture wait that many seconds for a running pass to finish before giving up.
        """
        acquired = self._lock.acquire(timeout=wait) if wait > 0 else self._lock.acquire(blocking=False)
        if not acquired:
            self.state["status"] = "skipped: the previous pass is still running"
            return False
        try:
            try:
                ready = self.vault_ready()
            except OSError as exc:
                log.warning("vault check failed error=%s", type(exc).__name__)
                self.state.update(status="failed", last_error=type(exc).__name__)
                return False
            if not ready:
                self.state["status"] = "waiting for the first vault sync"
                return False
            self.state.update(status="running", last_started_at=_now())
            started = time.monotonic()
            try:
                with self.connect() as conn:
                    report = self.pass_fn(self.vault, conn=conn, live=True, max_usd=self.max_usd,
                                          tag_list=self.load_tags(), retire=self.ticks > 0)
                self.state.update(status="ok", last_error=None,
                                  last_result={key: report[key] for key in SUMMARY_KEYS if key in report})
                # Only a finished pass ends the hold on retirement; a failed first pass leaves it in place.
                self.ticks += 1
            except Exception as exc:  # noqa: BLE001 - the next tick must still run
                log.warning("index pass failed error=%s", type(exc).__name__)
                self.state.update(status="failed", last_error=type(exc).__name__)
            finally:
                self.state["last_finished_at"] = _now()
                log.info("index pass finished status=%s seconds=%.1f", self.state["status"], time.monotonic() - started)
            return True
        finally:
            self._lock.release()
