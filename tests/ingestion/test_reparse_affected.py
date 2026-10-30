"""The re-index tool for notes a parser change reads differently, against the real parser and a fake connection."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.ingestion.reparse_affected import affected_documents, main, read_differently
from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source

FENCED = "# Title\n\nIntro.\n\n```python\n# not a heading\nprint(1)\n```\n\n## After\n\nBody.\n"
PLAIN = "# Title\n\nJust prose.\n\n- one\n- two\n"


def stored_blocks(relative_path: str, text: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "vault"
        path = root / relative_path
        path.parent.mkdir(parents=True)
        path.write_text(text, encoding="utf-8", newline="")
        parsed = route_source(capture_source(root, relative_path, Path(temp) / "runs", "primary"))
    return [block.model_dump(mode="json") for block in parsed.blocks]


def as_old_parser(blocks: list[dict]) -> list[dict]:
    """What the line-based parser stored: a fence's lines as paragraphs."""
    return [{**block, "kind": "paragraph"} if block["kind"] == "code_block" else block for block in blocks]


class Rows:
    def __init__(self, rows) -> None:
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, rows) -> None:
        self.rows = rows

    def execute(self, sql, params=()):
        return Rows([(len(self.rows),)]) if sql.lstrip().startswith("select count(*)") else Rows(self.rows)


class ReadDifferentlyTests(unittest.TestCase):
    def test_a_note_the_current_parser_already_stored_reads_the_same(self) -> None:
        for text in (FENCED, PLAIN):
            with self.subTest(text=text[:12]):
                self.assertFalse(read_differently("Notes/n.md", "primary", text.encode(), stored_blocks("Notes/n.md", text)))

    def test_a_note_stored_by_the_old_parser_reads_differently_only_when_it_holds_a_fence(self) -> None:
        fenced = as_old_parser(stored_blocks("Notes/n.md", FENCED))
        self.assertTrue(read_differently("Notes/n.md", "primary", FENCED.encode(), fenced))
        plain = as_old_parser(stored_blocks("Notes/p.md", PLAIN))
        self.assertFalse(read_differently("Notes/p.md", "primary", PLAIN.encode(), plain))

    def test_a_changed_heading_path_counts_as_a_difference(self) -> None:
        stored = stored_blocks("Notes/n.md", FENCED)
        stored[-1] = {**stored[-1], "heading_path": ["Title"]}
        self.assertTrue(read_differently("Notes/n.md", "primary", FENCED.encode(), stored))


class AffectedDocumentsTests(unittest.TestCase):
    def test_every_active_note_is_compared_and_only_the_different_ones_are_listed(self) -> None:
        rows = [("id-a", "Notes/a.md", "primary", FENCED.encode(), stored_blocks("Notes/a.md", FENCED)),
                ("id-b", "Notes/b.md", "primary", FENCED.encode(), as_old_parser(stored_blocks("Notes/b.md", FENCED))),
                ("id-c", "Notes/c.md", "primary", PLAIN.encode(), stored_blocks("Notes/c.md", PLAIN))]
        self.assertEqual(affected_documents(Conn(rows)), {"active_notes": 3, "checked": 3, "read_differently": 1,
                                                           "documents": [("id-b", "Notes/b.md")]})


class MainTests(unittest.TestCase):
    def run_main(self, argv, found, pass_report=None):
        calls = []

        def fake_pass(settings, **kwargs):
            calls.append(kwargs)
            return pass_report or {"stopped": None, "ingest_failures": 0}

        patches = [mock.patch("app.ingestion.reparse_affected.load_env_file"),
                   mock.patch("app.ingestion.reparse_affected.connect_runtime"),
                   mock.patch("app.ingestion.reparse_affected.settings_from_env", return_value=object()),
                   mock.patch("app.ingestion.reparse_affected.affected_documents", return_value=dict(found)),
                   mock.patch("app.ingestion.reparse_affected.run_pass", side_effect=fake_pass)]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(argv)
        return code, calls, out.getvalue(), err.getvalue()

    FOUND = {"active_notes": 5, "checked": 5, "read_differently": 1, "documents": [("id-b", "Notes/b.md")]}

    def test_the_default_run_is_a_dry_run_for_just_the_affected_notes_and_retires_nothing(self) -> None:
        code, calls, out, _ = self.run_main([], self.FOUND)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["live"])
        self.assertFalse(calls[0]["retire"])
        self.assertEqual(calls[0]["only"], frozenset({"id-b"}))
        self.assertNotIn("Notes/b.md", out)  # paths are private and only printed with --list

    def test_list_prints_the_paths(self) -> None:
        self.assertIn("Notes/b.md", self.run_main(["--list"], self.FOUND)[2])

    def test_live_forwards_the_cap_and_fails_when_the_pass_stopped_or_a_note_failed(self) -> None:
        code, calls, _, _ = self.run_main(["--live", "--max-usd", "0.5"], self.FOUND)
        self.assertEqual((code, calls[0]["live"], calls[0]["max_usd"]), (0, True, 0.5))
        for report in ({"stopped": "pass spend limit reached", "ingest_failures": 0}, {"stopped": None, "ingest_failures": 1}):
            with self.subTest(report=report):
                code, _, _, err = self.run_main(["--live", "--max-usd", "0.5"], self.FOUND, report)
                self.assertEqual(code, 1)
                self.assertIn("incomplete", err)

    def test_no_affected_note_means_no_pass(self) -> None:
        found = {"active_notes": 5, "checked": 5, "read_differently": 0, "documents": []}
        code, calls, out, _ = self.run_main(["--live", "--max-usd", "0.5"], found)
        self.assertEqual((code, calls), (0, []))
        self.assertIn('"read_differently": 0', out)


if __name__ == "__main__":
    unittest.main()
