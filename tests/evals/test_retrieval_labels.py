import tempfile
import unittest
from pathlib import Path

from app.storage import StorageError
from evals.retrieval_trial import checked_questions

TEXT = "alpha beta gamma delta"
LABELS = """document_id: doc-1
source_sha256: sha-1
source_title: One
questions:
- id: q1
  question: What comes first?
  expected: supported
  must_find:
  - start_char: 0
    end_char: 5
    quote: alpha
"""


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        pass

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)


def labels_in(directory, text=LABELS):
    (Path(directory) / "questions_one.yaml").write_text(text, encoding="utf-8")
    return Path(directory)


class LabelTests(unittest.TestCase):
    def test_labels_may_cover_a_subset_of_the_index(self):
        rows = [("doc-1", "sha-1", TEXT), ("doc-2", "sha-2", "another note")]
        with tempfile.TemporaryDirectory() as directory:
            questions = checked_questions(FakeConnection(rows), labels_in(directory))
        self.assertEqual([q["document_id"] for q in questions], ["doc-1"])

    def test_a_changed_revision_or_missing_note_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = labels_in(directory)
            for rows in ([("doc-1", "other-sha", TEXT)], [("doc-2", "sha-1", TEXT)]):
                with self.subTest(rows=rows), self.assertRaises(StorageError):
                    checked_questions(FakeConnection(rows), folder)

    def test_a_quote_that_no_longer_matches_the_text_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = labels_in(directory, LABELS.replace("alpha", "alpHa"))
            with self.assertRaises(StorageError):
                checked_questions(FakeConnection([("doc-1", "sha-1", TEXT)]), folder)


if __name__ == "__main__":
    unittest.main()
