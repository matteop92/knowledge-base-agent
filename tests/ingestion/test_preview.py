import json
import tempfile
import unittest
from pathlib import Path

import yaml

from app.ingestion.contracts import ParsedTranscript
from app.ingestion.eval_labels import EvalLabelError, validate_eval_labels
from app.ingestion.preview import preview_source
from app.ingestion.transcript_parser import TranscriptParseError


class PreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "vault"
        self.root.mkdir()
        self.runtime = self.base / "runs"
        self.source = self.root / "episode.md"
        self.source.write_bytes(Path("sources/fixtures/sample_transcript.md").read_bytes())

    def test_preview_writes_validated_record_without_changing_source(self) -> None:
        before = self.source.read_bytes()
        summary = preview_source(self.root, "episode.md", self.runtime)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(summary["turn_count"], 2)
        self.assertEqual(summary["speakers"], ["Host", "Guest"])
        self.assertNotIn("I wanted to learn", json.dumps(summary))
        restored = ParsedTranscript.model_validate_json(
            Path(summary["preview_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(restored.source_sha256, summary["source_sha256"])

    def test_invalid_parse_has_no_successful_preview(self) -> None:
        self.source.write_text("No speaker labels here.", encoding="utf-8")
        with self.assertRaises(TranscriptParseError):
            preview_source(self.root, "episode.md", self.runtime)
        self.assertEqual(list(self.runtime.glob("*/parsed.json")), [])

    def test_eval_labels_are_bound_to_exact_source_revision(self) -> None:
        summary = preview_source(self.root, "episode.md", self.runtime)
        record = ParsedTranscript.model_validate_json(
            Path(summary["preview_path"]).read_text(encoding="utf-8")
        )
        quote = "We spoke to ten people first."
        start = record.source_text.index(quote)
        labels = {
            "document_id": record.document_id,
            "source_sha256": record.source_sha256,
            "questions": [
                {
                    "id": "q1",
                    "question": "How many people did the guest speak to first?",
                    "expected": "supported",
                    "must_find": [{"start_char": start, "end_char": start + len(quote), "quote": quote}],
                },
                {
                    "id": "q2",
                    "question": "How many paid ads did they buy?",
                    "expected": "unsupported",
                    "must_find": [],
                },
            ],
        }
        label_path = self.base / "labels.yaml"
        label_path.write_text(yaml.safe_dump(labels), encoding="utf-8")
        self.assertEqual(validate_eval_labels(label_path, record), 2)
        labels["source_sha256"] = "0" * 64
        label_path.write_text(yaml.safe_dump(labels), encoding="utf-8")
        with self.assertRaisesRegex(EvalLabelError, "source_sha256"):
            validate_eval_labels(label_path, record)
        labels["source_sha256"] = record.source_sha256
        labels["questions"][0]["must_find"][0]["quote"] = "invented evidence"
        label_path.write_text(yaml.safe_dump(labels), encoding="utf-8")
        with self.assertRaisesRegex(EvalLabelError, "exact source text"):
            validate_eval_labels(label_path, record)


if __name__ == "__main__":
    unittest.main()
