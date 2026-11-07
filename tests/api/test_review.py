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
        cls.pool.teardown()
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

    # Picks -----------------------------------------------------------------------------------
    ARTICLE = "Knowledge/Articles/a.md"

    def article(self, body: str = "First idea here. Second idea there.\n\nA **bold** claim, a [link](https://example.org/x) and more.\n") -> str:
        return self.pool.add(self.ARTICLE, f"# Title\n\n{body}")

    def detail(self, document: str) -> dict:
        response = self.client.get(f"/api/sources/{document}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def block_of(self, document: str, containing: str) -> int:
        return next(item["block"] for item in self.detail(document)["body"] if containing in item["text"])

    def pick(self, document: str, words: str, containing: str | None = None, occurrence: int = 1, block: int | None = None):
        block = block if block is not None else self.block_of(document, containing or words)
        return self.client.post("/api/review/picks", json={"source_id": document, "block": block, "text": words,
                                                           "occurrence": occurrence})

    def source_text(self, document: str) -> str:
        return self.admin.execute("""select r.source_text from knowledge.runs r join knowledge.active_runs a on a.run_id=r.run_id
            where r.document_id=%s""", (document,)).fetchone()[0]

    def live(self) -> list[tuple]:
        return self.admin.execute("select pick_id, start_char, end_char, exact from knowledge.feed_picks where removed_at is null order by start_char").fetchall()

    def test_a_selection_is_stored_with_offsets_that_slice_to_the_words(self) -> None:
        doc = self.article()
        response = self.pick(doc, "Second idea there.")
        self.assertEqual(response.status_code, 200, response.text)
        [(pick_id, start, end, exact)] = self.live()
        self.assertEqual((exact, self.source_text(doc)[start:end]), ("Second idea there.", "Second idea there."))
        self.assertEqual(response.json()["id"], str(pick_id))

    def test_a_repeated_phrase_anchors_to_the_requested_occurrence(self) -> None:
        doc = self.article("Echo and echo and echo again.\n")
        self.assertEqual(self.pick(doc, "echo", containing="Echo", occurrence=2).status_code, 200)
        [(_, start, _, _)] = self.live()
        self.assertEqual(start, self.source_text(doc).index("echo and echo again") + len("echo and "))

    def test_a_selection_over_bold_and_link_text_includes_the_markers_and_reports_shown_positions(self) -> None:
        doc = self.article()
        self.assertEqual(self.pick(doc, "bold claim, a link", containing="claim").status_code, 200)
        [(_, _, _, exact)] = self.live()
        self.assertEqual(exact, "bold** claim, a [link")
        [pick] = self.detail(doc)["picks"]
        item = next(item for item in self.detail(doc)["body"] if "bold" in item["text"])
        # The page shows "A bold claim, a link and more." (the markers are drawn as elements).
        self.assertEqual((pick["block"], pick["text"], pick["start"], pick["end"]), (item["block"], "bold claim, a link", 2, 20))

    def test_a_selection_in_a_highlights_note_anchors_in_the_note_block(self) -> None:
        doc = self.pool.add(HIGHLIGHTS, highlights_file(("Alpha thought.", "my own take on it")))
        item = next(i for i in self.detail(doc)["body"] if i["kind"] == "highlight")
        self.assertNotEqual(item["block"], item["note_block"])
        self.assertEqual(self.pick(doc, "own take", block=item["note_block"]).status_code, 200)
        [pick] = self.detail(doc)["picks"]
        self.assertEqual((pick["block"], pick["text"]), (item["note_block"], "own take"))
        self.assertEqual(item["personal_note"][pick["start"]:pick["end"]], "own take")

    def test_words_that_are_not_there_are_refused_and_store_nothing(self) -> None:
        doc = self.article()
        block = self.block_of(doc, "Second idea")
        self.assertEqual(self.pick(doc, "No such words", block=block).status_code, 400)
        self.assertEqual(self.pick(doc, "Second idea there.", block=block, occurrence=2).status_code, 400)
        self.assertEqual(self.pick(doc, "Second idea there.", block=9999).status_code, 400)
        self.assertEqual(self.live(), [])

    def test_removing_a_pick_is_harmless_to_repeat(self) -> None:
        doc = self.article()
        pick_id = self.pick(doc, "First idea here.").json()["id"]
        for _ in range(2):
            self.assertEqual(self.client.request("DELETE", f"/api/review/picks/{pick_id}", json={}).status_code, 200)
        self.assertEqual((self.live(), self.detail(doc)["picks"]), ([], []))
        unknown = "00000000-0000-0000-0000-000000000000"
        self.assertEqual(self.client.request("DELETE", f"/api/review/picks/{unknown}", json={}).status_code, 404)

    def test_a_selection_that_overlaps_a_pick_merges_with_it_and_one_inside_it_changes_nothing(self) -> None:
        doc = self.article("Alpha beta gamma delta.\n")
        first = self.pick(doc, "Alpha beta").json()
        second = self.pick(doc, "beta gamma").json()
        [(pick_id, _, _, exact)] = self.live()
        self.assertEqual((exact, str(pick_id), second["text"]), ("Alpha beta gamma", second["id"], "Alpha beta gamma"))
        self.assertNotEqual(first["id"], second["id"])
        inside = self.pick(doc, "beta").json()
        self.assertEqual(inside["id"], second["id"])
        self.assertEqual(len(self.live()), 1)

    def test_ae1_a_new_pick_enters_the_shuffle_at_its_hashed_place(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.", "Delta.")
        doc = self.article()
        self.assertEqual(self.pick(doc, "First idea here.").status_code, 200)
        passages = self.next(5)
        keys = [p["passage_key"] for p in passages]
        self.assertEqual(keys, sorted(keys, key=lambda key: rank(1, key)))
        picked = next(p for p in passages if p["source_id"] == doc)
        self.assertEqual((picked["text"], picked["note"]), ("First idea here.", None))

    def test_a_pick_in_the_feed_shows_the_words_without_markers(self) -> None:
        doc = self.article()
        self.pick(doc, "bold claim, a link", containing="claim")
        [picked] = [p for p in self.next(5) if p["source_id"] == doc]
        self.assertEqual(picked["text"], "bold claim, a link")

    def test_ae4_a_pick_follows_its_words_through_an_edit_and_goes_when_they_are_deleted(self) -> None:
        doc = self.article("Intro line with a tpyo.\n\nLife is just the way it is.\n")
        pick_id = self.pick(doc, "Life is just the way it is").json()["id"]
        self.article("Intro line with a typo.\n\nLife is just the way it is.\n")  # a new run, a different hash
        [pick] = self.detail(doc)["picks"]
        self.assertEqual((pick["id"], pick["text"]), (pick_id, "Life is just the way it is"))
        [(_, start, end, _)] = self.live()
        self.assertEqual(self.source_text(doc)[start:end], "Life is just the way it is")
        self.assertEqual([p["text"] for p in self.next(5) if p["source_id"] == doc], ["Life is just the way it is"])
        self.article("Intro line with a typo.\n\nSomething else now.\n")
        self.assertEqual(self.detail(doc)["picks"], [])
        self.assertEqual([p for p in self.next(5) if p["source_id"] == doc], [])
        self.assertEqual(self.live(), [])

    def test_the_reader_detail_carries_block_indexes(self) -> None:
        doc = self.article()
        items = self.detail(doc)["body"]
        self.assertTrue(all(isinstance(item["block"], int) for item in items))
        self.assertEqual(self.detail(doc)["picks"], [])

    def test_pick_routes_need_a_session_and_a_known_source(self) -> None:
        other = TestClient(self.app)
        body = {"source_id": "00000000-0000-0000-0000-000000000000", "block": 0, "text": "x"}
        self.assertEqual(other.post("/api/review/picks", json=body).status_code, 401)
        self.assertEqual(self.client.post("/api/review/picks", json=body).status_code, 404)
        self.assertEqual(self.client.post("/api/review/picks", json={**body, "source_id": "nope"}).status_code, 404)

    def test_pick_positions_count_utf16_units_like_the_page(self) -> None:
        shown = "\U0001F600 hello world now"
        doc = self.article(shown + "\n")
        self.assertEqual(self.pick(doc, "world", containing="hello").status_code, 200)
        [pick] = self.detail(doc)["picks"]
        units = shown.encode("utf-16-le")
        self.assertEqual(units[pick["start"] * 2:pick["end"] * 2].decode("utf-16-le"), "world")

    def test_a_passage_with_nothing_to_draw_is_skipped_and_logged_so_the_feed_moves_on(self) -> None:
        image_only = self.pool.add("Knowledge/Notes/img.md", "---\ntype: social_post\n---\n![a](https://example.org/a.png)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        self.highlights("Alpha.", "Beta.")
        keys = {p["passage_key"] for p in self.next(5)}
        self.assertEqual(len(keys), 2)
        self.assertNotIn(image_only, {p["source_id"] for p in self.next(5)})
        for _ in range(2):
            self.next(5)  # never blank, never blocks
        logged = self.admin.execute("select count(*) from knowledge.feed_shown where document_id=%s", (image_only,)).fetchone()[0]
        self.assertLessEqual(logged, 2)

    def test_reanchoring_picks_the_repeat_whose_surroundings_still_match(self) -> None:
        from app.api.review import _reanchored
        text = "Echo here. Something else. Echo here. And more."
        self.assertEqual(_reanchored(text, "Echo here.", "", " And more.", 0), text.index("Echo here.", 5))
        self.assertEqual(_reanchored(text, "Echo here.", "", " Something else.", 40), 0)
        # No surroundings match: the repeat nearest to where the pick was.
        self.assertEqual(_reanchored(text, "Echo here.", "zzz", "zzz", 30), text.index("Echo here.", 5))
        self.assertIsNone(_reanchored(text, "Gone entirely.", "", "", 0))

    def test_a_repeated_pick_follows_its_own_occurrence_through_an_edit(self) -> None:
        doc = self.article("Echo here. Middle.\n\nEcho here. Tail.\n")
        self.pick(doc, "Echo here.", containing="Echo here. Tail", occurrence=1)
        self.article("A new line first.\n\nEcho here. Middle.\n\nEcho here. Tail.\n")
        self.detail(doc)  # reading the source is what re-anchors its picks
        [(_, start, _, _)] = self.live()
        self.assertTrue(self.source_text(doc)[start:].startswith("Echo here. Tail."))

    def test_picks_inside_a_list_item_and_a_quote_exclude_the_markers(self) -> None:
        doc = self.article("- first point here\n- second point here\n\n> a quoted line to keep\n")
        self.assertEqual(self.pick(doc, "second point", containing="second point").status_code, 200)
        self.assertEqual(self.pick(doc, "quoted line", containing="quoted line").status_code, 200)
        exact = [row[3] for row in self.live()]
        self.assertEqual(exact, ["second point", "quoted line"])
        for _, start, end, text in self.live():
            self.assertEqual(self.source_text(doc)[start:end], text)

    def test_a_pick_in_a_file_saved_with_windows_line_endings_anchors_exactly(self) -> None:
        doc = self.pool.add(self.ARTICLE, "# Title\r\n\r\nFirst paragraph.\r\n\r\nSecond paragraph here.\r\n")
        self.assertEqual(self.pick(doc, "paragraph here", containing="Second").status_code, 200)
        [(_, start, end, exact)] = self.live()
        self.assertEqual((exact, self.source_text(doc)[start:end]), ("paragraph here", "paragraph here"))
        [pick] = self.detail(doc)["picks"]
        self.assertEqual(pick["text"], "paragraph here")

    def test_code_and_table_blocks_cannot_be_picked(self) -> None:
        doc = self.article("```\nsome code line\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nPlain text.\n")
        blocks = self.admin.execute("""select jsonb_array_elements(r.parsed->'blocks')->>'kind' from knowledge.runs r
            join knowledge.active_runs a on a.run_id=r.run_id where r.document_id=%s""", (doc,)).fetchall()
        kinds = [row[0] for row in blocks]
        for kind in ("code_block", "table"):
            self.assertEqual(self.pick(doc, "x", block=kinds.index(kind)).status_code, 400, kind)
        self.assertEqual(self.live(), [])

    def test_delete_with_a_bad_id_is_404_and_needs_a_session(self) -> None:
        self.assertEqual(self.client.request("DELETE", "/api/review/picks/not-a-uuid", json={}).status_code, 404)
        self.assertEqual(TestClient(self.app).request("DELETE", "/api/review/picks/00000000-0000-0000-0000-000000000000", json={}).status_code, 401)

    def test_a_pick_in_a_highlights_text_and_one_in_its_note_are_separate_passages(self) -> None:
        doc = self.pool.add(HIGHLIGHTS, highlights_file(("Alpha thought here.", "my own take on it")))
        item = next(i for i in self.detail(doc)["body"] if i["kind"] == "highlight")
        self.assertEqual(self.pick(doc, "own take", block=item["note_block"]).status_code, 200)
        self.assertEqual(self.pick(doc, "thought here", block=item["block"]).status_code, 200)
        texts = sorted(p["text"] for p in self.next(5))
        self.assertEqual(texts, sorted(["Alpha thought here.", "own take", "thought here"]))

    def test_a_note_labelled_note_location_is_split_from_its_highlight_and_the_quote_marks_go(self) -> None:
        text = ("---\ntitle: T\nauthor: A\n---\n# T\n\n**Highlight p. 1**\n> people have limited capacity.\n> They settle for enough.\n\n"
                "**Note - Location 34**\n\nHow can this be done?\n\nVia tests\n")
        doc = self.pool.add(HIGHLIGHTS, text)
        [passage] = [p for p in self.next(5) if p["source_id"] == doc]
        self.assertEqual(passage["text"], "people have limited capacity.\nThey settle for enough.")
        self.assertEqual(passage["note"], "How can this be done?\n\nVia tests")

    def test_a_highlight_that_is_only_a_note_label_is_left_alone(self) -> None:
        from app.api.review import _quote_and_note
        self.assertEqual(_quote_and_note("**Note - Location 3**\n\nOnly this.", None)[0], "**Note - Location 3**\n\nOnly this.")
        self.assertEqual(_quote_and_note("Quote.", "mine"), ("Quote.", "mine"))
        self.assertEqual(_quote_and_note("Quote.\n\n**Note**\n\nMore.", "mine"), ("Quote.", "mine\n\nMore."))

    # Marks -----------------------------------------------------------------------------------
    def mark(self, key: str, mark: str):
        return self.client.put(f"/api/review/marks/{key}", json={"mark": mark})

    def set_every(self, every: int) -> None:
        self.admin.execute("update knowledge.feed_settings set again_every=%s", (every,))
        self.addCleanup(self.admin.execute, "update knowledge.feed_settings set again_every=10")

    def marked(self, key: str, mark: str) -> None:
        self.assertEqual(self.mark(key, mark).status_code, 200)

    def serve_log(self, passage: dict) -> None:
        body = {"passage_key": passage["passage_key"], "round": passage["round"], "again": passage["again"]}
        self.assertEqual(self.client.post("/api/review/shown", json=body).status_code, 200)

    def test_done_leaves_the_shuffle_and_clearing_brings_it_back(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.")
        [first, second, third] = self.order(1)
        self.marked(second, "done")
        self.assertEqual([p["passage_key"] for p in self.next(5)], [first, third])
        self.assertEqual(self.client.request("DELETE", f"/api/review/marks/{second}", json={}).status_code, 200)
        self.assertEqual([p["passage_key"] for p in self.next(5)], [first, second, third])

    def test_an_again_passage_comes_only_through_the_interleave_and_is_flagged(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.", "Delta.")
        again = self.order(1)[0]
        self.marked(again, "again")
        passages = self.next(5)
        self.assertEqual(len(passages), 3, "three shuffle passages; the Again one is not one of them")
        self.assertNotIn(again, [p["passage_key"] for p in passages])
        self.assertTrue(all(p["mark"] is None and p["again"] is False for p in passages))

    def test_with_every_five_the_fifth_passage_served_is_the_again_one(self) -> None:
        self.highlights(*[f"Thought {n}." for n in range(9)])
        again = self.order(1)[0]
        self.marked(again, "again")
        self.set_every(5)
        passages = self.next(5)
        self.assertEqual([p["again"] for p in passages], [False] * 4 + [True])
        self.assertEqual((passages[4]["passage_key"], passages[4]["mark"]), (again, "again"))

    def test_held_passages_count_toward_the_gap_and_no_again_passage_repeats(self) -> None:
        self.highlights(*[f"Thought {n}." for n in range(10)])
        self.marked(self.order(1)[0], "again")
        self.set_every(3)
        batch_one = self.next(2)
        self.assertEqual([p["again"] for p in batch_one], [False, False])
        batch_two = self.next(2, held=[p["passage_key"] for p in batch_one])
        self.assertEqual([p["again"] for p in batch_two], [True, False])
        held = [p["passage_key"] for p in batch_one + batch_two]
        batch_three = self.next(2, held=held)
        self.assertEqual([p["again"] for p in batch_three], [False, False], "the only Again passage is held")

    def test_two_again_passages_take_turns(self) -> None:
        self.highlights(*[f"Thought {n}." for n in range(12)])
        first, second = self.order(1)[:2]
        self.marked(first, "again")
        self.marked(second, "again")
        self.set_every(2)
        [_, a1, _, a2, _] = self.next(5)
        self.assertEqual([a1["again"], a2["again"]], [True, True])
        self.assertNotEqual(a1["passage_key"], a2["passage_key"])
        self.serve_log(a1)
        self.serve_log(a2)
        self.serve_log(a1)  # a1 is served last now
        later = [p for p in self.next(5) if p["again"]]
        self.assertEqual(later[0]["passage_key"], a2["passage_key"], "the one served longest ago goes first")

    def test_a_single_again_passage_returns_every_fifth_slot(self) -> None:
        self.highlights(*[f"Thought {n}." for n in range(14)])
        self.marked(self.order(1)[0], "again")
        self.set_every(5)
        slots = []
        for _ in range(2):
            batch = self.next(5)
            slots += [p["again"] for p in batch]
            for passage in batch:
                self.serve_log(passage)
        self.assertEqual(slots, ([False] * 4 + [True]) * 2)

    def test_logging_an_again_serve_updates_the_mark_not_the_shown_log(self) -> None:
        self.highlights("Alpha.", "Beta.")
        key = self.order(1)[0]
        self.marked(key, "again")
        body = {"passage_key": key, "round": 1, "again": True}
        self.assertEqual(self.client.post("/api/review/shown", json=body).status_code, 200)
        self.assertEqual(self.logged(), [])
        served = self.admin.execute("select last_served_at from knowledge.feed_marks where passage_key=%s", (key,)).fetchone()[0]
        self.assertIsNotNone(served)
        self.assertEqual(self.client.post("/api/review/shown", json={**body, "round": 9}).status_code, 200, "no round check")

    def test_when_no_shuffle_passage_exists_the_again_passages_are_served_anyway(self) -> None:
        self.highlights("Alpha.", "Beta.")
        first, second = self.keys()
        self.marked(first, "again")
        self.marked(second, "again")
        self.assertEqual(sorted(p["passage_key"] for p in self.next(5)), sorted([first, second]))
        self.marked(first, "done")
        self.marked(second, "done")
        self.assertEqual(self.next(5), [])

    def test_bad_marks_and_settings_are_refused(self) -> None:
        self.highlights("Alpha.")
        [key] = self.keys()
        self.assertEqual(self.mark("f" * 32, "done").status_code, 404)
        self.assertEqual(self.mark("not-a-key", "done").status_code, 400)
        self.assertEqual(self.mark(key, "later").status_code, 422)
        self.assertEqual(self.client.request("DELETE", f"/api/review/marks/{'f' * 32}", json={}).status_code, 200, "clearing nothing is fine")
        self.assertEqual(self.client.put("/api/review/settings", json={"every": 4}).status_code, 422)
        self.assertEqual(self.client.get("/api/review/marks/sometimes").status_code, 404)
        other = TestClient(self.app)
        self.assertEqual(other.put(f"/api/review/marks/{key}", json={"mark": "done"}).status_code, 401)
        self.assertEqual(other.get("/api/review/settings").status_code, 401)

    def test_lists_come_newest_mark_first_with_counts_and_skip_passages_that_left(self) -> None:
        self.highlights("Alpha.", "Beta.", "Gamma.", "Delta.")
        a, b, c, d = self.keys()
        for key, mark in ((a, "again"), (b, "again"), (c, "done"), (d, "again")):
            self.marked(key, mark)
        self.marked(a, "done")
        self.marked(a, "again")  # a new mark moves it to the top
        listed = self.client.get("/api/review/marks/again").json()
        self.assertEqual([p["passage_key"] for p in listed["passages"]], [a, d, b])
        self.assertEqual(listed["count"], 3)
        self.assertTrue(all(p["mark"] == "again" and p["text"] and p["marked_at"] for p in listed["passages"]))
        self.assertEqual(self.client.get("/api/review/settings").json(), {"every": 10, "again": 3, "done": 1})
        self.admin.execute("update knowledge.feed_marks set passage_key=%s where passage_key=%s", ("c" * 32, b))
        self.assertEqual([p["passage_key"] for p in self.client.get("/api/review/marks/again").json()["passages"]], [a, d])
        self.assertEqual(self.client.get("/api/review/settings").json()["again"], 2)

    def test_switching_a_mark_moves_the_passage_between_lists(self) -> None:
        self.highlights("Alpha.")
        [key] = self.keys()
        self.marked(key, "again")
        self.marked(key, "done")
        self.assertEqual(self.client.get("/api/review/marks/again").json()["count"], 0)
        self.assertEqual([p["passage_key"] for p in self.client.get("/api/review/marks/done").json()["passages"]], [key])

    def test_the_how_often_setting_is_saved(self) -> None:
        self.set_every(10)
        self.assertEqual(self.client.put("/api/review/settings", json={"every": 3}).json()["every"], 3)
        self.assertEqual(self.client.get("/api/review/settings").json()["every"], 3)
