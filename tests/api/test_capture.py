"""Capture API tests against a disposable database; set KNOWLEDGE_TEST_DATABASE_URL to run them."""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - fastapi is an optional server dependency
    TestClient = None

from app.api.capture import CaptureCostError, Classification
from app.capture.notes import CaptureError, Draft
from tests.api.test_api import EMAIL, FakeAuth, FakeEngine, settings


class FakeClassifier:
    def __init__(self) -> None:
        self.properties = {"source_type": "article", "tags": ["leadership"], "people": ["Ann Lee"]}
        self.fail = False
        self.calls = 0

    def worst_case_usd(self, draft, today) -> float:
        return 0.01

    def classify(self, draft, today) -> Classification:
        self.calls += 1
        if self.fail:
            raise CaptureCostError(0.003)
        return Classification(dict(self.properties), 0.002)


class FakeExtractor:
    def __init__(self) -> None:
        self.result = Draft(kind="screenshot", body="Hire slowly.", title="Hiring advice",
                            own={"author": "Ann Lee", "platform": "LinkedIn"})
        self.fail: Exception | None = None

    def worst_case_usd(self, image: str) -> float:
        return 0.02

    def extract(self, item):
        if self.fail is not None:
            raise self.fail
        return self.result, 0.004


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def worst_case_usd(self, url: str) -> float:
        return 0.0

    def extract(self, item):
        self.calls.append(item.url)
        return Draft(kind="youtube", body="**Jawed:** Here we are.", title="Me at the zoo", own={"author": "jawed"},
                     url=item.url), 0.0


class FakeIndexer:
    def __init__(self, runs: bool) -> None:
        self.runs, self.waits = runs, []
        self.vault = SimpleNamespace(root_alias="primary")

    def tick(self, wait: float = 0) -> bool:
        self.waits.append(wait)
        return self.runs

    def start(self) -> None: ...

    def stop(self) -> None: ...


@unittest.skipUnless(TestClient and os.getenv("KNOWLEDGE_TEST_DATABASE_URL"),
                     "needs fastapi and a disposable KNOWLEDGE_TEST_DATABASE_URL")
class CaptureApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.storage.database import apply_migrations
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            apply_migrations(conn.cursor())

    def connect(self):
        conn = psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True)
        conn.execute("set role knowledge_runtime")
        return conn

    def setUp(self) -> None:
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("truncate knowledge.capture_jobs, knowledge.sessions, knowledge.spend cascade")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name) / "vault"
        for folder in ("Notes", "Articles", "Transcripts", "Book Highlights", "Books"):
            (self.vault / "Knowledge" / folder).mkdir(parents=True)
        self.classifier, self.indexer, self.extractor = FakeClassifier(), FakeIndexer(runs=False), FakeExtractor()
        self.transcriber = FakeTranscriber()
        self.build(sync_mode="bidirectional")

    def build(self, **changes) -> None:
        from app.api.main import create_app
        config = dataclasses.replace(settings(), sync_enabled=True, vault_root=str(self.vault), **changes)
        self.app = create_app(config, connect=self.connect, auth=FakeAuth(), engine=FakeEngine(),
                              indexer=self.indexer, classifier=self.classifier, extractor=self.extractor,
                              transcriber=self.transcriber)
        self.app.state.captures.today = lambda: date(2026, 10, 19)
        self.client = TestClient(self.app)
        response = self.client.post("/auth/verify", json={"email": EMAIL, "code": "111111"})
        self.assertEqual(response.status_code, 200, response.text)

    def capture(self, **body):
        return self.client.post("/api/captures", json={"kind": "text", "content": "Hire only when it hurts.", **body})

    def finished(self, capture_id: str) -> dict:
        self.app.state.captures.wait()
        return self.client.get(f"/api/captures/{capture_id}").json()

    def test_pasted_text_becomes_a_note_in_the_folder_its_type_picks(self):
        response = self.capture(title="On hiring", url="https://example.com/post")
        self.assertEqual(response.status_code, 202, response.text)
        job = self.finished(response.json()["capture_id"])
        self.assertEqual((job["status"], job["vault_path"]), ("done", "Knowledge/Articles/2026-10-19-on-hiring.md"))
        self.assertFalse(job["indexed"])  # the indexer was busy, so the next pass picks it up
        self.assertEqual(self.indexer.waits, [30])
        text = (self.vault / job["vault_path"]).read_text(encoding="utf-8")
        self.assertIn("url: https://example.com/post", text)
        self.assertIn("- Ann Lee", text)
        self.assertTrue(text.rstrip().endswith("Hire only when it hurts."))
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            self.assertEqual(conn.execute("select source, estimated_usd from knowledge.spend").fetchall(),
                             [("properties", Decimal("0.00200000"))])

    def test_two_captures_with_the_same_title_make_two_files(self):
        first = self.finished(self.capture(title="Same").json()["capture_id"])
        second = self.finished(self.capture(title="Same").json()["capture_id"])
        self.assertNotEqual(first["vault_path"], second["vault_path"])
        self.assertEqual(len(list((self.vault / "Knowledge/Articles").iterdir())), 2)

    def test_an_uncertain_type_goes_to_notes(self):
        self.classifier.properties = {"tags": []}
        job = self.finished(self.capture(title="Short thought").json()["capture_id"])
        self.assertEqual(job["vault_path"], "Knowledge/Notes/2026-10-19-short-thought.md")

    def test_a_markdown_file_keeps_its_properties(self):
        content = "---\ntitle: My essay\ntags: [writing]\n---\n\nBody.\n"
        job = self.finished(self.client.post("/api/captures", json={"kind": "markdown", "content": content})
                            .json()["capture_id"])
        text = (self.vault / job["vault_path"]).read_text(encoding="utf-8")
        self.assertIn("title: My essay", text)
        self.assertIn("- writing", text)
        self.assertIn("- leadership", text)

    def test_capture_is_refused_until_sync_is_two_way(self):
        self.build(sync_mode="pull-only")
        response = self.capture()
        self.assertEqual(response.status_code, 503)
        self.assertIn("two-way", response.json()["error"])
        self.assertEqual(self.classifier.calls, 0)
        self.assertFalse(self.client.get("/api/captures").json()["enabled"])

    def test_a_failed_classification_saves_nothing_and_records_its_cost(self):
        self.classifier.fail = True
        job = self.finished(self.capture().json()["capture_id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("try again", job["error"])
        self.assertEqual(list((self.vault / "Knowledge/Notes").iterdir()), [])
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            self.assertEqual(conn.execute("select estimated_usd from knowledge.spend").fetchone()[0], Decimal("0.003"))

    def test_the_daily_cap_refuses_before_any_model_call(self):
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("insert into knowledge.spend (source, estimated_usd) values ('answer', 2)")
        self.assertEqual(self.capture().status_code, 429)
        self.assertEqual(self.classifier.calls, 0)

    def test_empty_text_and_bad_links_are_refused(self):
        self.assertEqual(self.capture(content="   ").status_code, 422)
        self.assertEqual(self.capture(url="javascript:alert(1)").status_code, 422)

    def screenshot(self, image="data:image/png;base64,iVBORw0KGgo="):
        return self.client.post("/api/captures", json={"kind": "screenshot", "image": image})

    def spend(self):
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            return sorted(conn.execute("select source, estimated_usd from knowledge.spend").fetchall())

    def test_a_screenshot_becomes_a_note_with_its_author_and_no_image(self):
        self.classifier.properties = {"source_type": "social_post", "tags": ["leadership"]}
        job = self.finished(self.screenshot().json()["capture_id"])
        self.assertEqual((job["status"], job["title"]), ("done", "Hiring advice"))
        self.assertEqual(job["vault_path"], "Knowledge/Notes/2026-10-19-hiring-advice.md")
        text = (self.vault / job["vault_path"]).read_text(encoding="utf-8")
        self.assertIn("author: Ann Lee", text)
        self.assertIn("capture: screenshot", text)
        self.assertNotIn("base64", text)
        self.assertEqual([path.suffix for path in self.vault.rglob("*") if path.is_file()], [".md"])
        self.assertEqual(self.spend(), [("properties", Decimal("0.00200000")), ("vision", Decimal("0.00400000"))])

    def test_a_screenshot_must_be_an_image_data_url(self):
        for image in (None, "https://example.com/x.png", "data:image/svg+xml;base64,PHN2Zz4="):
            with self.subTest(image=image):
                self.assertEqual(self.client.post("/api/captures", json={"kind": "screenshot", "image": image})
                                 .status_code, 422)
        self.assertEqual(self.classifier.calls, 0)

    def test_an_unreadable_screenshot_fails_with_a_reason_and_records_its_cost(self):
        failure = CaptureError("no readable text was found in the screenshot")
        failure.cost_usd = 0.001
        self.extractor.fail = failure
        job = self.finished(self.screenshot().json()["capture_id"])
        self.assertEqual((job["status"], job["error"]), ("failed", "no readable text was found in the screenshot"))
        self.assertEqual(self.spend(), [("vision", Decimal("0.00100000"))])
        self.assertEqual(self.classifier.calls, 0)

    def test_a_failed_vision_call_is_charged_to_vision(self):
        self.extractor.fail = CaptureCostError(0.02)
        job = self.finished(self.screenshot().json()["capture_id"])
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.spend(), [("vision", Decimal("0.02000000"))])

    def test_a_youtube_link_becomes_a_transcript_note(self):
        self.classifier.properties = {"source_type": "note", "tags": []}
        response = self.client.post("/api/captures", json={"kind": "youtube", "content": "youtu.be/jNQXAC9IVRw"})
        job = self.finished(response.json()["capture_id"])
        self.assertEqual(self.transcriber.calls, ["https://www.youtube.com/watch?v=jNQXAC9IVRw"])
        self.assertEqual(job["vault_path"], "Knowledge/Transcripts/2026-10-19-me-at-the-zoo.md")
        text = (self.vault / job["vault_path"]).read_text(encoding="utf-8")
        self.assertIn("url: https://www.youtube.com/watch?v=jNQXAC9IVRw", text)
        self.assertEqual(self.spend(), [("properties", Decimal("0.00200000"))])

    def test_a_link_that_is_not_youtube_is_refused(self):
        response = self.client.post("/api/captures", json={"kind": "youtube", "content": "https://example.com/v"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.transcriber.calls, [])

    def test_recent_captures_are_listed_newest_first(self):
        self.finished(self.capture(title="One").json()["capture_id"])
        self.finished(self.capture(title="Two").json()["capture_id"])
        listing = self.client.get("/api/captures").json()
        self.assertEqual([item["title"] for item in listing["captures"]], ["Two", "One"])
        self.assertTrue(listing["enabled"])


if __name__ == "__main__":
    unittest.main()
