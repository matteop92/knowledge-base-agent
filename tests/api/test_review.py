"""Review routes against a disposable database: the shuffle, rounds, the shown log and the picks."""

from __future__ import annotations

import hashlib
import itertools
import os
import unittest

import psycopg

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

from tests.api.test_api import EMAIL, FakeAuth, FakeEngine, settings
from tests.storage.database import apply_migrations
from tests.storage.review_fixture import PoolBuilder, highlights_file

URL = os.getenv("KNOWLEDGE_TEST_DATABASE_URL")
HIGHLIGHTS = "Knowledge/Book Highlights/Book.md"


def rank(round_: int, key: str) -> str:
    return hashlib.md5(f"{round_}{key}".encode()).hexdigest()


@unittest.skipUnless(TestClient and URL, "needs fastapi and a disposable KNOWLEDGE_TEST_DATABASE_URL")
class ReviewApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = psycopg.connect(URL, autocommit=True)
        apply_migrations(cls.admin.cursor())
        cls.pool = PoolBuilder(cls.admin)

    @classmethod
    def tearDownClass(cls):
        cls.admin.close()

    def connect(self):
        conn = psycopg.connect(URL, autocommit=True)
        conn.execute("set role knowledge_runtime")
        return conn

    def setUp(self) -> None:
        from app.api.main import create_app
        self.pool.reset()
        self.addCleanup(self.pool.close)
        self.fresh = itertools.count()
        self.admin.execute("truncate knowledge.sessions")
        self.app = create_app(settings(), connect=self.connect, auth=FakeAuth(), engine=FakeEngine())
        self.client = TestClient(self.app)
        self.assertEqual(self.client.post("/auth/verify", json={"email": EMAIL, "code": "111111"}).status_code, 200)

    # Helpers ---------------------------------------------------------------------------------
    def highlights(self, *texts: str, path: str = HIGHLIGHTS) -> str:
        return self.pool.add(path, highlights_file(*[(text, None) for text in texts]))

    def keys(self) -> list[str]:
        return [row[0] for row in self.admin.execute("select passage_key from knowledge.feed_passages").fetchall()]

    def order(self, round_: int) -> list[str]:
        return sorted(self.keys(), key=lambda key: rank(round_, key))

    def log(self, round_: int, keys: list[str]) -> None:
        for key in keys:
            self.admin.execute("""insert into knowledge.feed_shown (round, passage_key, document_id)
                select %s, passage_key, document_id from knowledge.feed_passages where passage_key=%s""", (round_, key))

    def next(self, count: int = 3, held: list[str] | None = None):
        response = self.client.get("/api/review/next", params={"count": count, "held": ",".join(held or [])})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["passages"]

    def shown(self, key: str, round_: int):
        return self.client.post("/api/review/shown", json={"passage_key": key, "round": round_})

    def logged(self) -> list[tuple[int, str]]:
        return self.admin.execute("select round, passage_key from knowledge.feed_shown order by seq").fetchall()

    # Order and history -----------------------------------------------------------------------
    def test_one_at_a_time_walks_the_whole_pool_once_then_starts_round_two(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.", "Delta.")
        seen = []
        for _ in range(4):
            [passage] = self.next(1)
            self.assertEqual(passage["round"], 1)
            seen.append(passage["passage_key"])
            self.assertEqual(self.shown(passage["passage_key"], 1).status_code, 200)
        self.assertEqual(seen, self.order(1))
        [again] = self.next(1)
        self.assertEqual((again["round"], again["passage_key"]), (2, self.order(2)[0]))

    def test_ae3_with_nothing_left_after_the_position_the_next_round_starts_and_logs_there(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.")
        self.log(3, self.keys())
        passages = self.next(2)
        self.assertEqual([(p["round"], p["passage_key"]) for p in passages], [(4, key) for key in self.order(4)[:2]])
        self.assertEqual(self.shown(passages[0]["passage_key"], 4).status_code, 200)
        self.assertEqual(self.logged()[-1], (4, passages[0]["passage_key"]))

    def new_passage_ranked(self, round_: int, position: str, after: bool) -> str:
        """Add a file whose one highlight ranks after (or before) the position, and return its key."""
        for _ in range(200):
            attempt = next(self.fresh)
            before = set(self.keys())
            self.highlights(f"Fresh thought {attempt}.", path=f"Knowledge/Book Highlights/New{attempt}.md")
            [key] = set(self.keys()) - before
            if (rank(round_, key) > position) == after:
                return key
        raise AssertionError("no candidate found")

    def test_ae1_a_new_passage_ranked_after_the_position_comes_later_this_round_and_one_before_it_waits(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.", "Delta.", "Epsilon.", "Zeta.")
        first = self.order(1)[:3]
        self.log(1, first)
        position = rank(1, first[-1])
        later = self.new_passage_ranked(1, position, after=True)
        self.assertIn(later, [p["passage_key"] for p in self.next(5)])
        waiting = self.new_passage_ranked(1, position, after=False)
        self.assertNotIn(waiting, [p["passage_key"] for p in self.next(5)])
        self.log(1, [key for key in self.keys() if rank(1, key) > position])
        [opener] = self.next(1)
        self.assertEqual(opener["round"], 2)
        everything = self.next(5) + self.next(5, held=[p["passage_key"] for p in self.next(5)])
        self.assertIn(waiting, [p["passage_key"] for p in everything], "it comes in the next round")

    def test_only_held_passages_left_returns_nothing_and_does_not_start_a_round(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.")
        order = self.order(1)
        self.log(1, order[:1])
        self.assertEqual(self.next(3, held=order[1:]), [])
        [passage] = self.next(1, held=order[1:2])
        self.assertEqual((passage["round"], passage["passage_key"]), (1, order[2]))

    def test_a_held_round_three_passage_still_logs_in_round_three_after_round_four_started(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.")
        order = self.order(3)
        self.log(3, order[:1])
        self.assertEqual(self.shown(order[0], 4).status_code, 200)
        self.assertEqual(self.shown(order[1], 3).status_code, 200)
        self.assertEqual(self.logged()[-2:], [(4, order[0]), (3, order[1])])

    def test_two_calls_with_the_first_ones_keys_held_return_different_passages(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.", "Delta.", "Epsilon.")
        first = self.next(2)
        second = self.next(2, held=[p["passage_key"] for p in first])
        self.assertEqual(len({p["passage_key"] for p in first + second}), 4)

    def test_bad_held_lists_are_refused(self) -> None:
        self.highlights("Alpha.")
        for held in ("not-a-key", ",".join(["a" * 32] * 31), "A" * 32):
            self.assertEqual(self.client.get("/api/review/next", params={"held": held}).status_code, 400, held)

    def test_shown_twice_logs_once_and_an_unknown_key_is_404(self) -> None:
        self.highlights("Alpha.")
        [key] = self.keys()
        self.assertEqual(self.shown(key, 1).status_code, 200)
        self.assertEqual(self.shown(key, 1).status_code, 200)
        self.assertEqual(self.logged(), [(1, key)])
        self.assertEqual(self.shown("f" * 32, 1).status_code, 404)
        self.assertEqual(self.shown(key, 9).status_code, 400)
        self.assertEqual(len(self.logged()), 1)

    def test_an_empty_pool_returns_an_empty_list(self) -> None:
        self.assertEqual(self.next(), [])

    def test_count_is_clamped(self) -> None:
        self.highlights(*[f"Thought {n}." for n in range(8)])
        self.assertEqual(len(self.next(50)), 5)
        self.assertEqual(len(self.next(0)), 1)

    def test_routes_need_a_session(self) -> None:
        other = TestClient(self.app)
        self.assertEqual(other.get("/api/review/next").status_code, 401)
        self.assertEqual(other.post("/api/review/shown", json={"passage_key": "a" * 32, "round": 1}).status_code, 401)

    # What a passage carries ------------------------------------------------------------------
    def test_passages_carry_clean_text_note_and_source(self) -> None:
        doc = self.pool.add(HIGHLIGHTS, highlights_file(("A **bold** thought.", "my own take")))
        note = self.pool.add("Knowledge/Notes/n.md", "---\ntitle: Orchard\nauthor: Ben Hart\n---\n# Orchard\n\n"
                             "![pic](https://example.org/a.png)\n\nFirst paragraph.\n\n"
                             "Second [link](https://example.org).\n")
        found = {p["source_id"]: p for p in self.next(5)}
        highlight = found[doc]
        self.assertEqual((highlight["text"], highlight["note"], highlight["title"], highlight["author"],
                          highlight["locator"], highlight["type"]),
                         ("A **bold** thought.", "my own take", "Book Notes", "Ada Stone", "p. 1", "highlights"))
        self.assertEqual(found[note]["text"], "First paragraph.\n\nSecond [link](https://example.org).")
        self.assertEqual((found[note]["author"], found[note]["note"]), ("Ben Hart", None))
