"""The Review pool view and its two tables, against a disposable database; set KNOWLEDGE_TEST_DATABASE_URL to run."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source
from tests.storage.database import apply_migrations

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


@unittest.skipUnless(os.getenv("KNOWLEDGE_TEST_DATABASE_URL"), "set disposable KNOWLEDGE_TEST_DATABASE_URL")
class ReviewPoolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True)
        apply_migrations(cls.admin.cursor())
        cls.admin.execute("""insert into knowledge.embedding_specs (spec_id, provider, model, model_version, dimensions,
            distance_metric, input_recipe_version, chunking_version, payload)
            values (%s,'fixture','synthetic','1',3,'cosine','raw-v1','c1','{}') on conflict do nothing""", (SPEC,))
        cls.admin.execute("""insert into knowledge.profiles (profile_id, variant, chunking_version, spec_id, text_config)
            values (%s,'fixed_raw','c1',%s,'simple') on conflict do nothing""", (PROFILE, SPEC))
        cls.conn = psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True)
        cls.conn.execute("set role knowledge_runtime")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.admin.close()

    def setUp(self) -> None:
        self.admin.execute("update knowledge.corpus_state set profile_id=null where singleton=true")
        self.admin.execute("truncate knowledge.documents cascade")
        self.admin.execute("update knowledge.corpus_state set profile_id=%s where singleton=true", (PROFILE,))
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.documents: dict[str, str] = {}

    def add(self, path: str, text: str) -> str:
        """Parse a file with the real parsers and publish it as the active run of a new document."""
        base = Path(self.folder.name)
        source = base / "vault" / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(text.encode("utf-8"))
        parsed = route_source(capture_source(base / "vault", path, base / "runs"))
        document = str(uuid.uuid5(uuid.NAMESPACE_URL, path))
        self.admin.execute("insert into knowledge.documents values (%s,'primary',%s) on conflict do nothing", (document, path))
        self.publish(document, parsed)
        self.documents[path] = document
        return document

    def publish(self, document: str, parsed) -> str:
        run_id = f"run-{uuid.uuid4().hex}"
        raw = parsed.source_text.encode("utf-8")
        self.admin.execute("""insert into knowledge.runs (run_id, document_id, source_sha256, source_bytes, source_text,
            parsed, parser_version, manifest) values (%s,%s,%s,%s,%s,%s,%s,'{}')""",
                           (run_id, document, hashlib.sha256(raw).hexdigest(), raw, parsed.source_text,
                            Jsonb(parsed.model_dump(mode="json")), parsed.parser_version))
        self.admin.execute("""insert into knowledge.active_runs (profile_id, document_id, run_id, selection)
            values (%s,%s,%s,'{}') on conflict (profile_id, document_id) do update set run_id=excluded.run_id""",
                           (PROFILE, document, run_id))
        return run_id

    def rows(self, document: str | None = None):
        sql = "select kind, origin, text, note, passage_key, type, title, author, locator, start_char, end_char from knowledge.feed_passages"
        if document:
            return self.conn.execute(sql + " where document_id=%s order by start_char", (document,)).fetchall()
        return self.conn.execute(sql + " order by passage_key").fetchall()

    def test_each_highlight_is_a_row_and_its_note_travels_with_it(self) -> None:
        doc = self.add("Knowledge/Book Highlights/Book.md",
                       highlights_file(("Alpha thought.", None), ("Beta thought.", "my own take"), ("Gamma thought.", None)))
        rows = self.rows(doc)
        self.assertEqual([(r[0], r[2], r[3]) for r in rows],
                         [("highlight", "Alpha thought.", None), ("highlight", "Beta thought.", "my own take"),
                          ("highlight", "Gamma thought.", None)])
        self.assertEqual((rows[0][5], rows[0][6], rows[0][7], rows[0][8]), ("highlights", "Book Notes", "Ada Stone", "p. 1"))

    def test_whole_quotes_and_short_notes_are_one_row_each_whatever_a_quote_weighs(self) -> None:
        quote = self.add("Knowledge/Notes/q.md", f"---\ntype: quote\n---\n{words(600)}\n")
        note = self.add("Knowledge/Notes/n.md", f"# A note\n\n{words(120)}\n\nSecond paragraph.\n")
        self.assertEqual([(r[0], len(r[2].split())) for r in self.rows(quote)], [("quote", 600)])
        found = self.rows(note)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "note")
        self.assertTrue(found[0][2].startswith(words(2)), "the leading title heading is not part of the passage")
        self.assertTrue(found[0][2].strip().endswith("Second paragraph."))

    def test_a_social_post_is_a_post(self) -> None:
        post = self.add("Knowledge/Notes/p.md", "---\ntype: social_post\n---\nShort post.\n")
        self.assertEqual([(r[0], r[5]) for r in self.rows(post)], [("post", "social_post")])

    def test_a_long_note_or_one_with_two_headings_is_left_out(self) -> None:
        long = self.add("Knowledge/Notes/long.md", f"{words(450)}\n")
        limit = self.add("Knowledge/Notes/limit.md", f"{words(400)}\n")
        two = self.add("Knowledge/Notes/two.md", f"# One\n\nShort text.\n\n## Two\n\nMore text.\n")
        self.assertEqual(self.rows(long), [])
        self.assertEqual(len(self.rows(limit)), 1)
        self.assertEqual(self.rows(two), [])

    def test_articles_transcripts_and_books_have_no_auto_rows(self) -> None:
        for path in ("Knowledge/Articles/a.md", "Knowledge/Books/b.md"):
            self.add(path, f"# Title\n\n{words(50)}\n")
        self.add("Knowledge/Transcripts/t.md", "**Ada:** Hello there.\n\n**Ben:** Hi.\n\n**Ada:** Fine.\n")
        self.assertEqual(self.rows(), [])

    def test_the_same_words_in_two_files_are_two_rows(self) -> None:
        a = self.add("Knowledge/Book Highlights/A.md", highlights_file(("Shared thought.", None)))
        b = self.add("Knowledge/Book Highlights/B.md", highlights_file(("Shared thought.", None)))
        keys = {self.rows(a)[0][4], self.rows(b)[0][4]}
        self.assertEqual(len(keys), 2)

    def pick(self, document: str, exact: str, *, sha: str | None = None) -> str:
        text, current = self.conn.execute("""select r.source_text, r.source_sha256 from knowledge.runs r
            join knowledge.active_runs a on a.run_id=r.run_id where r.document_id=%s""", (document,)).fetchone()
        start = text.index(exact)
        return self.conn.execute("""insert into knowledge.feed_picks (document_id, source_sha256, start_char, end_char, exact)
            values (%s,%s,%s,%s,%s) returning pick_id""", (document, sha or current, start, start + len(exact), exact)).fetchone()[0]

    def test_a_pick_on_an_article_is_a_row_and_a_removed_or_stale_pick_is_not(self) -> None:
        article = self.add("Knowledge/Articles/a.md", f"# Title\n\nFirst idea here. Second idea there.\n\n{words(30)}\n")
        live = self.pick(article, "Second idea there.")
        self.assertEqual([(r[0], r[1], r[2]) for r in self.rows(article)], [("pick", "picked", "Second idea there.")])
        self.conn.execute("update knowledge.feed_picks set removed_at=now() where pick_id=%s", (live,))
        self.assertEqual(self.rows(article), [])
        self.pick(article, "First idea here.", sha="0" * 64)
        self.assertEqual(self.rows(article), [])

    def test_a_pick_that_covers_exactly_a_highlight_collapses_into_it(self) -> None:
        doc = self.add("Knowledge/Book Highlights/Book.md", highlights_file(("Alpha thought.", "a note")))
        self.pick(doc, "Alpha thought.")
        rows = self.rows(doc)
        self.assertEqual([(r[0], r[1], r[3]) for r in rows], [("highlight", "auto", "a note")])

    def test_a_new_run_replaces_the_old_runs_rows(self) -> None:
        path = "Knowledge/Book Highlights/Book.md"
        doc = self.add(path, highlights_file(("Old thought.", None)))
        base = Path(self.folder.name)
        (base / "vault" / path).write_bytes(highlights_file(("New thought.", None)).encode("utf-8"))
        self.publish(doc, route_source(capture_source(base / "vault", path, base / "runs2")))
        self.assertEqual([r[2] for r in self.rows(doc)], ["New thought."])

    def test_a_round_logs_a_passage_once(self) -> None:
        doc = self.add("Knowledge/Book Highlights/Book.md", highlights_file(("Alpha thought.", None)))
        key = self.rows(doc)[0][4]
        insert = "insert into knowledge.feed_shown (round, passage_key, document_id) values (%s,%s,%s)"
        self.conn.execute(insert, (1, key, doc))
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.conn.execute(insert, (1, key, doc))
        self.conn.execute(insert, (2, key, doc))

    def test_only_the_runtime_role_can_read_and_write(self) -> None:
        for role in ("anon", "authenticated"):
            with psycopg.connect(os.environ["KNOWLEDGE_TEST_DATABASE_URL"], autocommit=True) as other:
                other.execute(f"set role {role}")
                for relation in ("feed_passages", "feed_picks", "feed_shown"):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        other.execute(f"select 1 from knowledge.{relation}")
        self.conn.execute("select 1 from knowledge.feed_passages")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.conn.execute("delete from knowledge.feed_shown")


if __name__ == "__main__":
    unittest.main()
