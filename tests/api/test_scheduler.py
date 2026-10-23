from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from app.api.scheduler import IndexScheduler
from app.ingestion.vault_sync import VaultSettings, parse_folders


@contextmanager
def fake_connection():
    yield object()


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "vault"
        self.root.mkdir()
        self.vault = VaultSettings(self.root, "primary", parse_folders("Knowledge"), Path(self.temp.name) / "runs")
        self.calls: list[dict] = []

    def note(self, relative="Knowledge/Notes/a.md") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# A\n\nText.\n", encoding="utf-8")

    def scheduler(self, pass_fn=None, **kwargs) -> IndexScheduler:
        def default(vault, **options):
            self.calls.append(options)
            return {"files_seen": 1, "published": 0, "retired": 0}
        return IndexScheduler(self.vault, fake_connection, pass_fn=pass_fn or default, tag_list=lambda conn: None, **kwargs)

    def test_a_pass_gets_the_tag_list_read_through_its_connection(self) -> None:
        seen: list = []
        calls: list = []

        def pass_fn(vault, **kwargs):
            calls.append(kwargs)
            return {}
        scheduler = IndexScheduler(self.vault, fake_connection, pass_fn=pass_fn,
                                   tag_list=lambda conn: seen.append(conn) or "the-list")
        self.note()
        scheduler.tick()
        self.assertEqual((len(seen), calls[0]["tag_list"]), (1, "the-list"))

    def test_no_pass_runs_before_the_first_sync_has_produced_notes(self) -> None:
        scheduler = self.scheduler()
        self.assertFalse(scheduler.tick())
        self.assertEqual(scheduler.status()["status"], "waiting for the first vault sync")
        (self.root / "Knowledge").mkdir()
        self.assertFalse(scheduler.tick())
        self.assertEqual(self.calls, [])
        self.note()
        self.assertTrue(scheduler.tick())
        self.assertEqual(len(self.calls), 1)

    def test_a_capture_waits_for_a_running_pass_then_runs_its_own(self) -> None:
        self.note()
        scheduler = self.scheduler()
        scheduler._lock.acquire()
        threading.Timer(0.2, scheduler._lock.release).start()
        self.assertTrue(scheduler.tick(wait=5))
        self.assertEqual(len(self.calls), 1)

    def test_a_capture_gives_up_when_the_running_pass_outlasts_its_wait(self) -> None:
        self.note()
        scheduler = self.scheduler()
        scheduler._lock.acquire()
        try:
            self.assertFalse(scheduler.tick(wait=0.1))
        finally:
            scheduler._lock.release()
        self.assertEqual(self.calls, [])

    def test_the_first_pass_after_a_start_retires_nothing(self) -> None:
        self.note()
        scheduler = self.scheduler()
        scheduler.tick()
        scheduler.tick()
        self.assertEqual([call["retire"] for call in self.calls], [False, True])

    def test_a_failed_first_pass_keeps_the_hold_on_retirement(self) -> None:
        self.note()
        attempts = []

        def flaky(vault, **options):
            attempts.append(options["retire"])
            if len(attempts) == 1:
                raise RuntimeError("x")
            return {}
        scheduler = self.scheduler(pass_fn=flaky)
        scheduler.tick()
        scheduler.tick()
        scheduler.tick()
        self.assertEqual(attempts, [False, False, True])

    def test_an_unreadable_vault_is_recorded_and_the_next_tick_still_runs(self) -> None:
        self.note()
        scheduler = self.scheduler()
        real = scheduler.vault_ready
        scheduler.vault_ready = lambda: (_ for _ in ()).throw(OSError("x"))
        self.assertFalse(scheduler.tick())
        self.assertEqual((scheduler.status()["status"], scheduler.status()["last_error"]), ("failed", "OSError"))
        scheduler.vault_ready = real
        self.assertTrue(scheduler.tick())

    def test_a_tick_is_skipped_while_the_previous_pass_still_runs(self) -> None:
        self.note()
        entered, release = threading.Event(), threading.Event()

        def slow(vault, **options):
            entered.set()
            release.wait(5)
            return {}
        scheduler = self.scheduler(pass_fn=slow)
        worker = threading.Thread(target=scheduler.tick)
        worker.start()
        self.assertTrue(entered.wait(5))
        self.assertFalse(scheduler.tick())
        self.assertIn("still running", scheduler.status()["status"])
        release.set()
        worker.join(5)

    def test_a_failed_pass_is_recorded_without_its_message_and_the_next_tick_runs(self) -> None:
        self.note()
        attempts = []

        def flaky(vault, **options):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("secret note text in the message")
            return {"published": 2, "spent_usd": 0.001}
        scheduler = self.scheduler(pass_fn=flaky)
        scheduler.tick()
        status = scheduler.status()
        self.assertEqual((status["status"], status["last_error"]), ("failed", "RuntimeError"))
        self.assertNotIn("secret", str(status))
        scheduler.tick()
        status = scheduler.status()
        self.assertEqual((status["status"], status["last_result"]["published"], status["last_error"]), ("ok", 2, None))

    def test_the_background_thread_runs_passes_and_stops(self) -> None:
        self.note()
        scheduler = self.scheduler(interval_seconds=0.05, first_delay_seconds=0)
        scheduler.start()
        deadline = 5
        while len(self.calls) < 2 and deadline > 0:
            threading.Event().wait(0.05)
            deadline -= 0.05
        scheduler.stop()
        self.assertGreaterEqual(len(self.calls), 2)


@unittest.skipUnless(__import__("importlib").util.find_spec("fastapi"), "needs fastapi")
class HealthTests(unittest.TestCase):
    def test_health_reports_the_index_state(self) -> None:
        import os
        from fastapi.testclient import TestClient
        from app.api.main import create_app
        from tests.api.test_api import FakeAuth, FakeEngine, settings

        if not os.getenv("KNOWLEDGE_TEST_DATABASE_URL"):
            self.skipTest("needs a disposable KNOWLEDGE_TEST_DATABASE_URL")
        import psycopg
        from tests.storage.database import apply_migrations

        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as setup:
            apply_migrations(setup.cursor())

        def connect():
            conn = psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True)
            conn.execute("set role knowledge_runtime")
            return conn

        class Stub:
            def start(self): pass
            def stop(self): pass
            def status(self): return {"enabled": True, "status": "ok", "last_result": {"published": 1}}

        app = create_app(settings(), connect=connect, auth=FakeAuth(), engine=FakeEngine(), indexer=Stub())
        with TestClient(app) as client:
            client.post("/auth/verify", json={"email": "user@example.com", "code": "111111"})
            self.assertEqual(client.get("/health").json()["index"]["last_result"], {"published": 1})


if __name__ == "__main__":
    unittest.main()
