import tempfile
import unittest
from pathlib import Path
from shutil import copyfile

import yaml

from app.ingestion.chunking import fixed_raw_chunks, structural_raw_chunks
from app.ingestion.eval_labels import EvalLabelError
from app.ingestion.eval_mapping import map_eval_labels
from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source


class EvalMappingTests(unittest.TestCase):
    def test_other_markdown_categories_have_checked_fixture_labels(self):
        cases = [
            ("bracket_transcript", "Transcripts"),
            ("highlights", "Book Highlights"),
            ("book", "Books"),
            ("article", "Articles"),
            ("note", "Notes"),
        ]
        with tempfile.TemporaryDirectory() as base:
            root = Path(base) / "knowledge"
            root.mkdir()
            for name, folder in cases:
                with self.subTest(category=name):
                    relative = f"{folder}/sample_{name}.md"
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    copyfile(Path("sources/fixtures") / f"sample_{name}.md", target)
                    document = route_source(capture_source(root, relative, Path(base) / "runs"))
                    mappings = map_eval_labels(
                        Path("evals") / f"fixture_{name}.yaml", document,
                        [fixed_raw_chunks(document), structural_raw_chunks(document)],
                    )
                    self.assertEqual([item.labeled_span_count for item in mappings], [1, 1])

    def test_overlap_maps_once_per_source_label(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base) / "knowledge"
            root.mkdir()
            text = " ".join(["a"] * 750 + ["unique evidence"] + ["b"] * 450) + "\n"
            (root / "note.md").write_text(text, encoding="utf-8")
            document = route_source(capture_source(root, "note.md", Path(base) / "runs"))
            fixed = fixed_raw_chunks(document)
            structural = structural_raw_chunks(document)
            quote = "unique evidence"
            start = document.source_text.index(quote)
            label_path = Path(base) / "labels.yaml"
            labels = {
                "document_id": document.document_id,
                "source_sha256": document.source_sha256,
                "questions": [{
                    "id": "q1", "question": "What is the evidence?", "expected": "supported",
                    "must_find": [{"start_char": start, "end_char": start + len(quote), "quote": quote}],
                }],
            }
            label_path.write_text(yaml.safe_dump(labels), encoding="utf-8")
            mappings = map_eval_labels(label_path, document, [fixed, structural])
            self.assertEqual([item.labeled_span_count for item in mappings], [1, 1])
            self.assertEqual(len(mappings[0].mappings), 1)
            self.assertEqual(len(mappings[0].mappings[0].chunk_ids), 2)
            labels["source_sha256"] = "0" * 64
            label_path.write_text(yaml.safe_dump(labels), encoding="utf-8")
            with self.assertRaisesRegex(EvalLabelError, "source_sha256"):
                map_eval_labels(label_path, document, [fixed])


if __name__ == "__main__":
    unittest.main()
