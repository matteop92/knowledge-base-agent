"""API tests against a disposable database; set KNOWLEDGE_TEST_DATABASE_URL to run them."""

from __future__ import annotations

import logging
import os
import threading
import unittest
from decimal import Decimal

import psycopg

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - fastapi is an optional server dependency
    TestClient = None

from app.agent.records import AnswerLimits, AnswerResult
from app.api.auth import AuthError
from app.api.answers import AnswerFailed, AnswerOutcome, obsidian_link
from app.api.settings import Settings
from app.storage.records import SourcePassage

USER = "11111111-1111-1111-1111-111111111111"
EMAIL = "matt@example.com"


def settings() -> Settings:
    return Settings(database_url=os.getenv("KNOWLEDGE_TEST_DATABASE_URL", ""), supabase_url="https://example.supabase.co",
                    supabase_publishable_key="publishable", allowed_user_id=USER, allowed_email=EMAIL,
                    vault_name="New Vault", cookie_secure=False, daily_cap_usd=Decimal("2"))


class FakeAuth:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_code(self, email: str) -> None:
        self.sent.append(email)

    def verify_code(self, email: str, code: str) -> str:
        if code == "111111":
            return USER
        if code == "222222":
            raise AuthError(403, "not authorised")
        raise AuthError(401, "code expired or wrong")


def passage(text="The pear is ripe.") -> SourcePassage:
    return SourcePassage(document_id="doc-1", run_id="run-1", source_sha256="a" * 64, root_alias="primary",
                         relative_path="Knowledge/Notes/pear tree.md", chunk_id="c1", start_char=0,
                         end_char=len(text), text=text)


class FakeEngine:
    def __init__(self) -> None:
        self.limits = AnswerLimits(max_seconds=5)
        self.mode = "done"
        self.calls = 0
        self.priors: list = []
        self.release = threading.Event()
        self.release.set()

    def worst_case_usd(self) -> Decimal:
        return Decimal("0.30")

    def answer(self, question: str, prior=None) -> AnswerOutcome:
        self.calls += 1
        self.priors.append(prior or [])
        self.release.wait(5)
        if self.mode == "error":
            raise RuntimeError("model failed")
        if self.mode == "failed_after_billing":
            raise AnswerFailed(0.05) from RuntimeError(f"provider echoed: {question}")
        if self.mode == "gaps":
            result = AnswerResult(answer="Partly.", completeness="partial", citations={"E1.1": passage()},
                                  gaps=["No source covers winter."], actions=[], model_calls=3, termination="exhausted")
        else:
            result = AnswerResult(answer="The pear is ripe [E1.1].", completeness="complete",
                                  citations={"E1.1": passage()}, inspected={"E1.1": passage()}, gaps=[], actions=[],
                                  model_calls=2)
        return AnswerOutcome(result, 0.02)


@unittest.skipUnless(TestClient and os.getenv("KNOWLEDGE_TEST_DATABASE_URL"),
                     "needs fastapi and a disposable KNOWLEDGE_TEST_DATABASE_URL")
class ApiTests(unittest.TestCase):
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
        from app.api.main import create_app
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("truncate knowledge.conversations, knowledge.sessions, knowledge.spend cascade")
        self.auth, self.engine = FakeAuth(), FakeEngine()
        self.app = create_app(settings(), connect=self.connect, auth=self.auth, engine=self.engine)
        self.client = TestClient(self.app)

    def sign_in(self, client=None):
        client = client or self.client
        response = client.post("/auth/verify", json={"email": EMAIL, "code": "111111"})
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def ask(self, question="Is the pear ripe?", conversation=None):
        path = f"/api/conversations/{conversation}/turns" if conversation else "/api/conversations"
        return self.client.post(path, json={"question": question})

    def finished(self, turn_id):
        self.app.state.jobs.wait()
        return self.client.get(f"/api/turns/{turn_id}").json()

    # Auth ------------------------------------------------------------------------------------
    def test_api_routes_need_a_session_and_public_routes_do_not(self):
        for path in ("/api/conversations", "/health", "/auth/me"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        self.assertEqual(self.ask().status_code, 401)

    def test_session_cookie_is_http_only_and_sign_out_everywhere_ends_all_sessions(self):
        response = self.client.post("/auth/verify", json={"email": EMAIL, "code": "111111"})
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        other = self.sign_in(TestClient(self.app))
        self.assertEqual(self.client.get("/auth/me").status_code, 200)
        self.client.post("/auth/sign-out-everywhere", json={})
        self.assertEqual(other.get("/auth/me").status_code, 401)

    def test_expired_session_is_refused(self):
        self.sign_in()
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("update knowledge.sessions set expires_at = now() - interval '1 minute'")
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_form_posts_are_refused(self):
        self.sign_in()
        response = self.client.post("/api/conversations", data={"question": "x"})
        self.assertEqual(response.status_code, 415)

    # Asking ----------------------------------------------------------------------------------
    def test_asking_creates_a_running_turn_that_finishes_with_citations(self):
        self.sign_in()
        self.engine.release.clear()
        started = self.ask().json()
        self.assertEqual(self.client.get(f"/api/turns/{started['turn_id']}").json()["status"], "running")
        self.engine.release.set()
        turn = self.finished(started["turn_id"])
        self.assertEqual(turn["status"], "done")
        citation = turn["receipt"]["citations"]["E1.1"]
        self.assertEqual(citation["text"], "The pear is ripe.")
        self.assertEqual(citation["obsidian_url"], obsidian_link("New Vault", "Knowledge/Notes/pear tree.md"))
        self.assertIn("vault=New%20Vault&file=Knowledge%2FNotes%2Fpear%20tree.md", citation["obsidian_url"])
        listing = self.client.get("/api/conversations").json()["conversations"]
        self.assertEqual(listing[0]["title"], "Is the pear ripe?")
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            self.assertEqual(conn.execute("select source, estimated_usd from knowledge.spend").fetchall(),
                             [("answer", Decimal("0.02000000"))])

    def test_a_harness_stop_is_done_with_gaps(self):
        self.sign_in()
        self.engine.mode = "gaps"
        turn = self.finished(self.ask().json()["turn_id"])
        self.assertEqual((turn["status"], turn["receipt"]["gaps"]), ("done_with_gaps", ["No source covers winter."]))

    def test_a_model_error_marks_the_turn_failed_with_a_readable_message(self):
        self.sign_in()
        self.engine.mode = "error"
        turn = self.finished(self.ask().json()["turn_id"])
        self.assertEqual(turn["status"], "failed")
        self.assertIn("try again", turn["error"])

    def test_a_second_ask_while_one_runs_is_refused_and_follow_ups_continue_the_conversation(self):
        self.sign_in()
        self.engine.release.clear()
        started = self.ask().json()
        second = self.ask("Another?", started["conversation_id"])
        self.assertEqual(second.status_code, 409)
        self.engine.release.set()
        self.app.state.jobs.wait()
        follow_up = self.ask("And the apple?", started["conversation_id"])
        self.assertEqual(follow_up.status_code, 202)
        self.app.state.jobs.wait()
        conversation = self.client.get(f"/api/conversations/{started['conversation_id']}").json()
        self.assertEqual([turn["position"] for turn in conversation["turns"]], [1, 2])

    def test_a_follow_up_receives_earlier_finished_turns_with_their_read_passages(self):
        self.sign_in()
        first = self.ask().json()
        turn = self.finished(first["turn_id"])
        stored = turn["receipt"]["inspected"]["E1.1"]
        self.assertEqual((stored["run_id"], stored["chunk_id"]), ("run-1", "c1"))
        self.engine.mode = "error"
        self.finished(self.ask("Why?", first["conversation_id"]).json()["turn_id"])
        self.engine.mode = "done"
        self.finished(self.ask("And the apple?", first["conversation_id"]).json()["turn_id"])
        self.assertEqual(self.engine.priors[0], [])
        prior = self.engine.priors[2]
        # The failed turn has no answer to build on, so only the first turn is passed.
        self.assertEqual([item.question for item in prior], ["Is the pear ripe?"])
        self.assertEqual(prior[0].passages["E1.1"].run_id, "run-1")
        self.assertEqual(prior[0].titles, {"doc-1": "Knowledge/Notes/pear tree.md"})

    def test_running_turns_are_interrupted_on_startup(self):
        from app.api.main import create_app
        self.sign_in()
        self.engine.release.clear()
        started = self.ask().json()
        fresh = self.ask("A newer question?").json()
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            # Older than the answer time limit: no live server can still be answering it.
            conn.execute("update knowledge.turns set created_at = now() - interval '10 minutes' where turn_id=%s",
                         (started["turn_id"],))
        with TestClient(create_app(settings(), connect=self.connect, auth=self.auth, engine=FakeEngine())) as restarted:
            self.sign_in(restarted)
            turn = restarted.get(f"/api/turns/{started['turn_id']}").json()
            self.assertEqual(turn["status"], "failed")
            self.assertIn("interrupted", turn["error"])
            # A turn another server may still be answering during a deploy is left alone.
            self.assertEqual(restarted.get(f"/api/turns/{fresh['turn_id']}").json()["status"], "running")
            self.assertEqual(restarted.post(f"/api/conversations/{started['conversation_id']}/turns",
                                            json={"question": "Again?"}).status_code, 202)
        self.engine.release.set()

    def test_running_answers_count_against_the_daily_cap(self):
        self.sign_in()
        self.engine.release.clear()
        # Worst case is $0.30 each and the cap is $2, so six may run at once and the seventh is refused.
        codes = [self.ask(f"Question {index}?").status_code for index in range(7)]
        self.engine.release.set()
        self.app.state.jobs.wait()
        self.assertEqual(codes, [202] * 6 + [429])

    def test_an_answer_budget_over_the_per_answer_limit_stops_the_server_starting(self):
        from dataclasses import replace
        from app.api.main import create_app
        from app.api.settings import AnswerSettings
        strict = replace(settings(), answer=AnswerSettings(max_usd_per_answer=Decimal("0.10")))
        with self.assertRaises(RuntimeError):
            create_app(strict, connect=self.connect, auth=self.auth, engine=FakeEngine())

    def test_a_renewed_session_refreshes_the_cookie(self):
        self.sign_in()
        self.assertNotIn("set-cookie", self.client.get("/auth/me").headers)
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("update knowledge.sessions set last_used_at = now() - interval '2 hours'")
        self.assertIn("max-age=2592000", self.client.get("/auth/me").headers["set-cookie"].lower())

    def test_daily_cap_refuses_before_any_model_call(self):
        self.sign_in()
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("insert into knowledge.spend (source, estimated_usd) values ('indexing', 1.8)")
        response = self.ask()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.engine.calls, 0)

    def test_thumbs_down_is_stored_and_can_be_removed(self):
        self.sign_in()
        turn_id = self.ask().json()["turn_id"]
        self.finished(turn_id)
        self.client.post(f"/api/turns/{turn_id}/feedback", json={"note": "Missed the apple note"})
        self.assertEqual(self.client.get(f"/api/turns/{turn_id}").json()["feedback"],
                         {"verdict": "down", "note": "Missed the apple note"})
        self.client.request("DELETE", f"/api/turns/{turn_id}/feedback", json={})
        self.assertIsNone(self.client.get(f"/api/turns/{turn_id}").json()["feedback"])

    def test_a_failed_answer_logs_no_question_text_and_still_records_its_spend(self):
        self.sign_in()
        self.engine.mode = "failed_after_billing"
        with self.assertLogs("knowledge", level=logging.INFO) as captured:
            turn = self.finished(self.ask("Secret orchard question?").json()["turn_id"])
        self.assertEqual(turn["status"], "failed")
        self.assertNotIn("Secret orchard", "\n".join(captured.output))
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            self.assertEqual(conn.execute("select source, estimated_usd from knowledge.spend").fetchall(),
                             [("answer", Decimal("0.05000000"))])

    def test_an_expired_running_turn_is_charged_its_worst_case(self):
        self.sign_in()
        self.engine.release.clear()
        started = self.ask().json()
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            conn.execute("update knowledge.turns set created_at = now() - interval '10 minutes' where turn_id=%s",
                         (started["turn_id"],))
        self.assertEqual(self.client.get(f"/api/turns/{started['turn_id']}").json()["status"], "failed")
        with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as conn:
            self.assertEqual(conn.execute("select estimated_usd from knowledge.spend where job_id=%s",
                                          (started["turn_id"],)).fetchone()[0], Decimal("0.30000000"))
        self.engine.release.set()

    def test_logs_never_contain_question_or_answer_text(self):
        self.sign_in()
        with self.assertLogs("knowledge", level=logging.INFO) as captured:
            self.finished(self.ask("Secret orchard question?").json()["turn_id"])
        text = "\n".join(captured.output)
        self.assertNotIn("Secret orchard", text)
        self.assertNotIn("pear is ripe", text)


if __name__ == "__main__":
    unittest.main()
