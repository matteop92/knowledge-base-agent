import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from evals.export_feedback import export_feedback


class FakeConn:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None):
        self.queries.append(sql)
        return self

    def fetchall(self):
        return self.rows


def row(turn, receipt, note="wrong"):
    return (turn, "c1", "Chat", 1, f"question {turn}", "done", note,
            datetime(2026, 10, 26, tzinfo=timezone.utc), receipt)


class ExportFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.old = Path.cwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        Path(".local").mkdir()

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def run_export(self, rows, name="feedback/out.json"):
        conn = FakeConn(rows)
        path = export_feedback(lambda: conn, Path(".local") / name)
        return conn, json.loads(path.read_text(encoding="utf-8"))

    def test_exports_question_note_and_receipt(self):
        _, data = self.run_export([row("t1", {"answer": "a"}), row("t2", {"answer": "b"}, note="bad")])
        self.assertEqual([turn["turn_id"] for turn in data["turns"]], ["t1", "t2"])
        self.assertEqual(data["turns"][1]["note"], "bad")
        self.assertEqual(data["turns"][0]["receipt"], {"answer": "a"})
        self.assertEqual(data["warnings"], [])

    def test_query_joins_from_feedback_so_other_turns_are_not_exported(self):
        conn, _ = self.run_export([])
        self.assertIn("from knowledge.feedback", conn.queries[-1])
        self.assertIn("set transaction read only", conn.queries[0])

    def test_missing_receipt_is_listed_with_a_warning(self):
        _, data = self.run_export([row("t9", None)])
        self.assertEqual(len(data["turns"]), 1)
        self.assertIn("t9", data["warnings"][0])
        self.assertEqual(data["turns"][0]["warnings"], data["warnings"])

    def test_bad_path_is_refused_before_any_query(self):
        def forbidden():
            raise AssertionError("queried")

        with self.assertRaises(ValueError):
            export_feedback(forbidden, Path(self.tmp.name) / "elsewhere.json")
        existing = Path(".local/exists.json")
        existing.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            export_feedback(forbidden, existing)

    def test_empty_table_writes_zero_turns(self):
        _, data = self.run_export([])
        self.assertEqual(data["turns"], [])


if __name__ == "__main__":
    unittest.main()
