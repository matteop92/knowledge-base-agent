import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ingestion.run_manifest import RunManifest
from app.ingestion.router import route_source
from app.ingestion.source_reader import capture_source
from app.models.client import ModelSpec, ModelUsage


class RunManifestTests(unittest.TestCase):
    def test_versions_and_failed_variants_remain_inspectable(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "Knowledge"
        path = root / "Notes" / "idea.md"
        path.parent.mkdir(parents=True)
        path.write_text("A note.", encoding="utf-8")
        capture = capture_source(root, "Notes/idea.md", Path(temp.name) / "runs")
        document = route_source(capture)
        manifest = RunManifest.for_document(document)
        first_model = ModelSpec(provider="openrouter", model_id="open/model-a", model_version="v1")
        second_model = ModelSpec(provider="openrouter", model_id="open/model-b", model_version="v1")
        first = manifest.begin(
            "context", {"prompt_version": "context-v1", "chunking_version": "structural-v1"},
            first_model,
        )
        manifest.fail(first.artifact_id, "timeout")
        self.assertEqual(manifest.artifacts[first.artifact_id].status, "failed")
        self.assertIsNone(manifest.artifacts[first.artifact_id].path)
        retry = manifest.begin("context", first.version_inputs, first_model)
        self.assertEqual(retry.attempts, 2)
        manifest.succeed(retry.artifact_id, "artifacts/context/v1.json", ModelUsage(input_tokens=5))
        second = manifest.begin("context", first.version_inputs, second_model)
        self.assertNotEqual(first.artifact_id, second.artifact_id)
        self.assertEqual(manifest.artifacts[first.artifact_id].status, "ready")
        self.assertEqual(manifest.artifacts[second.artifact_id].status, "pending")
        manifest.save(capture.run_dir)
        self.assertEqual(RunManifest.load(capture.run_dir), manifest)

    def test_save_retries_transient_windows_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "note.md"
            source.write_text("A note.", encoding="utf-8")
            capture = capture_source(root, "note.md", root / "runs")
            manifest = RunManifest.for_document(route_source(capture))
            original_replace = Path.replace
            calls = 0

            def briefly_locked(path, target):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("locked")
                return original_replace(path, target)

            with patch.object(Path, "replace", briefly_locked), patch("time.sleep"):
                manifest.save(capture.run_dir)
            self.assertEqual(calls, 2)
            self.assertEqual(RunManifest.load(capture.run_dir), manifest)


if __name__ == "__main__":
    unittest.main()
