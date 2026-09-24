import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from app.ingestion.router import choose_route, route_source
from app.ingestion.source_reader import capture_source


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Knowledge"
        self.root.mkdir()
        self.runtime = Path(self.temp.name) / "runs"

    def capture(self, relative: str, text: str):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return capture_source(self.root, relative, self.runtime)

    def test_routes_observed_structures_and_explicit_override(self) -> None:
        cases = [
            ("Transcripts/first.md", "**Host:** Hi.\n", "transcript_bold"),
            (
                "Transcripts/second.md",
                Path("sources/fixtures/sample_bracket_transcript.md").read_text(encoding="utf-8"),
                "transcript_bracket",
            ),
            (
                "Book Highlights/book.md",
                Path("sources/fixtures/sample_highlights.md").read_text(encoding="utf-8"),
                "highlights",
            ),
            ("Books/book.md", "## STEP 1: Decide\n\n**STEP 1:** Begin.\n", "book"),
            ("Articles/article.md", "**Tip 1: Notice**\n\nSay what you mean.\n", "article"),
            ("Notes/note.md", "An unheaded thought.\n", "note"),
        ]
        for relative, text, expected in cases:
            with self.subTest(relative=relative):
                parsed = route_source(self.capture(relative, text))
                self.assertEqual(choose_route(self.capture(relative, text))[0], expected)
                self.assertEqual(parsed.source_kind, "transcript" if expected.startswith("transcript") else expected)
                self.assertTrue(parsed.route_reason)
        capture = self.capture("Misc/unknown.md", "A plain thought.\n")
        fallback = route_source(capture)
        self.assertEqual(fallback.source_kind, "generic")
        self.assertEqual(fallback.structural_confidence, "reduced")
        self.assertEqual(fallback.diagnostics[0].code, "generic_fallback")
        forced = route_source(capture, override="note")
        self.assertEqual(forced.source_kind, "note")
        self.assertIn("explicit", forced.route_reason)

    @unittest.skipUnless(os.getenv("KNOWLEDGE_SOURCE_ROOT"), "set KNOWLEDGE_SOURCE_ROOT for vault acceptance")
    def test_read_only_vault_examples_have_full_coverage(self) -> None:
        root = Path(os.environ["KNOWLEDGE_SOURCE_ROOT"])
        paths = sorted(root.rglob("*.md"))
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(path=path.relative_to(root).as_posix()):
                before = hashlib.sha256(path.read_bytes()).hexdigest()
                capture = capture_source(root, path.relative_to(root).as_posix(), self.runtime)
                parsed = route_source(capture)
                self.assertEqual(parsed.source_sha256, before)
                self.assertFalse(
                    [item for item in parsed.excluded_spans if item.reason == "unclassified"]
                )
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
                self.assertGreater(len(parsed.blocks), 0)


if __name__ == "__main__":
    unittest.main()
