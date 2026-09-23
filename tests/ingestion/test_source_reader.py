import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.ingestion.source_reader import SourceReadError, capture_source
from app.ingestion.transcript_parser import parse_transcript


class SourceReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "vault"
        self.root.mkdir()
        self.runtime = self.base / "runs"
        self.source = self.root / "episode.md"
        self.source.write_bytes(b"**Host:** Hello.\n")

    def test_same_locator_has_stable_identity_and_immutable_snapshots(self) -> None:
        before = self.source.read_bytes()
        first = capture_source(self.root, "episode.md", self.runtime)
        second = capture_source(self.root, "episode.md", self.runtime)
        self.assertEqual(first.manifest.document_id, second.manifest.document_id)
        self.assertEqual(first.manifest.source_sha256, second.manifest.source_sha256)
        self.assertNotEqual(first.manifest.run_id, second.manifest.run_id)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual((first.run_dir / "source.md").read_bytes(), before)
        manifest = json.loads((first.run_dir / "capture.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_sha256"], hashlib.sha256(before).hexdigest())
        self.assertNotIn(str(self.root), json.dumps(manifest))

    def test_changed_bytes_keep_document_id_and_bom_has_no_text_offset(self) -> None:
        first = capture_source(self.root, "episode.md", self.runtime)
        self.source.write_bytes(b"\xef\xbb\xbf**Host:** Changed.\n")
        second = capture_source(self.root, "episode.md", self.runtime)
        self.assertEqual(first.manifest.document_id, second.manifest.document_id)
        self.assertNotEqual(first.manifest.source_sha256, second.manifest.source_sha256)
        self.assertTrue(second.source_text.startswith("**Host:**"))
        self.assertEqual((first.run_dir / "source.md").read_bytes(), first.source_bytes)

    def test_parent_root_nested_locator_survives_root_move(self) -> None:
        transcripts = self.root / "Transcripts"
        transcripts.mkdir()
        nested = transcripts / "episode.md"
        nested.write_bytes(self.source.read_bytes())
        first = capture_source(self.root, "Transcripts/episode.md", self.runtime)
        second = capture_source(self.root, "Transcripts/episode.md", self.runtime)
        moved_root = self.base / "moved-vault"
        shutil.move(self.root, moved_root)
        moved = capture_source(moved_root, "Transcripts/episode.md", self.runtime)
        changed_locator = capture_source(moved_root, "episode.md", self.runtime)
        self.assertEqual(first.manifest.document_id, second.manifest.document_id)
        self.assertEqual(first.manifest.document_id, moved.manifest.document_id)
        self.assertNotEqual(first.manifest.document_id, changed_locator.manifest.document_id)
        self.assertEqual(first.manifest.locator.relative_path, "Transcripts/episode.md")

    def test_parser_uses_capture_after_original_changes(self) -> None:
        captured = capture_source(self.root, "episode.md", self.runtime)
        self.source.write_bytes(b"**Host:** A different version.\n")
        parsed = parse_transcript(captured)
        self.assertIn("Hello.", parsed.blocks[0].raw_text)
        self.assertNotIn("different version", parsed.source_text)

    def test_invalid_inputs_are_rejected(self) -> None:
        for locator in ("missing.md", "../episode.md", "episode.txt", ""):
            with self.subTest(locator=locator), self.assertRaises(SourceReadError):
                capture_source(self.root, locator, self.runtime)
        self.source.write_bytes(b"\xff")
        with self.assertRaisesRegex(SourceReadError, "UTF-8"):
            capture_source(self.root, "episode.md", self.runtime)
        self.source.write_bytes(b"")
        with self.assertRaisesRegex(SourceReadError, "empty"):
            capture_source(self.root, "episode.md", self.runtime)

    def test_symlink_outside_root_is_rejected_when_supported(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("**Host:** Outside.", encoding="utf-8")
        link = self.root / "link.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(SourceReadError, "inside the source root"):
            capture_source(self.root, "link.md", self.runtime)


if __name__ == "__main__":
    unittest.main()
