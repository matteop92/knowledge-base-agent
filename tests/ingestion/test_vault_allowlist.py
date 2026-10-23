from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ingestion import vault_allowlist as allowlist


class Result:
    def __init__(self, stdout="", returncode=0) -> None:
        self.stdout, self.returncode = stdout, returncode


class AllowlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "vault"
        (self.root / "Knowledge/Notes").mkdir(parents=True)
        (self.root / "Knowledge/Notes/kept.md").write_text("# Kept\n", encoding="utf-8")
        (self.root / "Cabinet").mkdir()
        (self.root / "Cabinet/private.md").write_text("# Private\n", encoding="utf-8")
        (self.root / "Brand New Folder").mkdir()
        (self.root / ".obsidian").mkdir()
        (self.root / "loose note.md").write_text("# Loose\n", encoding="utf-8")

    def test_everything_outside_the_allowlist_is_removed_and_knowledge_is_untouched(self) -> None:
        outside, loose = allowlist.enforce(self.root, ["Knowledge"], delete=True)
        self.assertEqual((outside, loose), (["Brand New Folder", "Cabinet"], 1))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [".obsidian", "Knowledge"])
        self.assertTrue((self.root / "Knowledge/Notes/kept.md").is_file())

    def test_folder_names_match_without_regard_to_case(self) -> None:
        outside, _ = allowlist.enforce(self.root, ["knowledge"], delete=False)
        self.assertNotIn("Knowledge", outside)

    def test_without_permission_to_delete_nothing_is_touched(self) -> None:
        outside, loose = allowlist.enforce(self.root, ["Knowledge"], delete=False)
        self.assertEqual((outside, loose), (["Brand New Folder", "Cabinet"], 1))
        self.assertEqual(len(list(self.root.iterdir())), 5)

    def test_an_empty_allowlist_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            allowlist.enforce(self.root, [], delete=True)
        self.assertTrue((self.root / "Cabinet/private.md").is_file())

    def test_skip_list_keeps_the_baseline_and_remembers_new_folders(self) -> None:
        baseline, extra = Path(self.temp.name) / "baseline.txt", Path(self.temp.name) / "extra.txt"
        baseline.write_text("Cabinet\nTray\n", encoding="utf-8")
        names = allowlist.merged_exclusions(baseline, extra, ["Brand New Folder", "cabinet"])
        # Each spelling on disk is listed exactly, so the tool skips the folder as it is named.
        self.assertEqual(names, ["Cabinet", "Tray", "Brand New Folder", "cabinet"])
        self.assertEqual(extra.read_text(encoding="utf-8").splitlines(), ["Brand New Folder", "cabinet"])
        self.assertEqual(allowlist.merged_exclusions(baseline, extra, [])[-2:], ["Brand New Folder", "cabinet"])

    def run_main(self, config_output: str, mode: str | None, returncode=0):
        baseline, extra = Path(self.temp.name) / "baseline.txt", Path(self.temp.name) / "extra.txt"
        baseline.write_text("Tray\n", encoding="utf-8")
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return Result(config_output if "--excluded-folders" not in command else "", returncode)

        env = {"KNOWLEDGE_INCLUDED_FOLDERS": "Knowledge"}
        if mode:
            env["KNOWLEDGE_SYNC_MODE"] = mode
        with patch.dict(os.environ, env, clear=False), patch("subprocess.run", fake_run):
            code = allowlist.main(["--root", str(self.root), "--baseline", str(baseline), "--extra", str(extra)])
        return code, calls

    def test_main_deletes_and_updates_the_skip_list_only_in_pull_only_mode(self) -> None:
        code, calls = self.run_main("mode: pull-only\n", None)
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "Cabinet").exists())
        update = [command for command in calls if "--excluded-folders" in command][0]
        self.assertEqual(update[update.index("--excluded-folders") + 1], "Tray,Brand New Folder,Cabinet")

    def test_two_way_sync_never_deletes_but_still_stops_syncing_new_folders(self) -> None:
        for config, mode in (("mode: bidirectional\n", None), ("mode: pull-only\n", "bidirectional"), ("", None)):
            with self.subTest(config=config, mode=mode):
                code, calls = self.run_main(config, mode)
                self.assertEqual(code, 0)
                self.assertTrue((self.root / "Cabinet/private.md").is_file())
                self.assertTrue(any("--excluded-folders" in command for command in calls))

    def sync_settings(self, ignore_folders) -> Path:
        """A sync settings folder like the tool's: one vault folder holding config.json."""
        folder = Path(self.temp.name) / "sync"
        (folder / "vault-id").mkdir(parents=True, exist_ok=True)
        (folder / "vault-id/config.json").write_text(json.dumps(
            {"syncMode": "bidirectional", "ignoreFolders": ignore_folders}), encoding="utf-8")
        return folder

    def run_two_way(self, settings: Path, update_code=0):
        def run(command, **kwargs):
            return Result("", update_code) if "--excluded-folders" in command else Result("mode: bidirectional\n", 0)
        with patch.dict(os.environ, {"KNOWLEDGE_SYNC_MODE": "bidirectional"}):
            return self.main_with(run, "--sync-settings", str(settings))

    def test_two_way_sync_deletes_only_folders_the_tool_skips(self) -> None:
        # The tool's settings skip Cabinet but not the brand new folder yet.
        self.assertEqual(self.run_two_way(self.sync_settings(["Tray", "Cabinet"])), 0)
        self.assertFalse((self.root / "Cabinet").exists())
        self.assertTrue((self.root / "Brand New Folder").is_dir())
        self.assertTrue((self.root / "loose note.md").is_file())
        self.assertTrue((self.root / "Knowledge/Notes/kept.md").is_file())

    def test_skip_list_works_without_a_baseline_file(self) -> None:
        # The server runs without --baseline; a new folder must still reach the skip list.
        extra = Path(self.temp.name) / "extra.txt"
        extra.write_text("Cabinet\n", encoding="utf-8")
        self.assertEqual(allowlist.merged_exclusions(None, extra, ["Brand New Folder"]), ["Cabinet", "Brand New Folder"])
        self.assertEqual(extra.read_text(encoding="utf-8").splitlines(), ["Cabinet", "Brand New Folder"])

    def test_main_without_a_baseline_updates_the_skip_list_and_cleans_up(self) -> None:
        extra = Path(self.temp.name) / "e.txt"
        extra.write_text("Tray\n", encoding="utf-8")
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return Result("" if "--excluded-folders" in command else "mode: pull-only\n", 0)
        with patch("subprocess.run", run):
            self.assertEqual(allowlist.main(["--root", str(self.root), "--extra", str(extra)]), 0)
        update = [command for command in calls if "--excluded-folders" in command][0]
        self.assertEqual(update[update.index("--excluded-folders") + 1], "Tray,Brand New Folder,Cabinet")
        self.assertFalse((self.root / "Cabinet").exists())

    def test_two_way_sync_never_deletes_a_folder_whose_name_differs_in_case(self) -> None:
        self.assertEqual(self.run_two_way(self.sync_settings(["cabinet", "brand new folder"])), 0)
        self.assertTrue((self.root / "Cabinet/private.md").is_file())
        self.assertTrue((self.root / "Brand New Folder").is_dir())

    def test_two_way_sync_without_a_readable_settings_file_deletes_nothing(self) -> None:
        settings = self.sync_settings(["Cabinet", "Brand New Folder"])
        (settings / "vault-id/config.json").write_text("{not json", encoding="utf-8")
        for folder in (settings, Path(self.temp.name) / "missing"):
            with self.subTest(folder=folder):
                self.assertEqual(self.run_two_way(folder), 0)
                self.assertTrue((self.root / "Cabinet/private.md").is_file())
                self.assertTrue((self.root / "Brand New Folder").is_dir())

    def test_a_skip_list_naming_an_allowed_folder_never_deletes_it(self) -> None:
        self.assertEqual(self.run_two_way(self.sync_settings(["Knowledge", "Cabinet"])), 0)
        self.assertTrue((self.root / "Knowledge/Notes/kept.md").is_file())

    def test_a_failed_skip_list_update_in_two_way_sync_deletes_nothing(self) -> None:
        self.assertEqual(self.run_two_way(self.sync_settings(["Cabinet"]), update_code=1), 1)
        self.assertTrue((self.root / "Cabinet/private.md").is_file())

    def test_a_failed_skip_list_update_is_reported(self) -> None:
        code, _ = self.run_main("mode: pull-only\n", None, returncode=1)
        self.assertEqual(code, 1)

    def main_with(self, run, *extra_args):
        baseline, extra = Path(self.temp.name) / "b.txt", Path(self.temp.name) / "e.txt"
        with patch("subprocess.run", run):
            return allowlist.main(["--root", str(self.root), "--baseline", str(baseline), "--extra", str(extra), *extra_args])

    def test_a_failed_skip_list_update_deletes_nothing(self) -> None:
        def run(command, **kwargs):
            return Result("", 1) if "--excluded-folders" in command else Result("mode: pull-only\n", 0)
        self.assertEqual(self.main_with(run), 1)
        self.assertTrue((self.root / "Cabinet/private.md").is_file())

    def test_a_folder_name_with_a_trailing_slash_still_protects_knowledge(self) -> None:
        with patch.dict(os.environ, {"KNOWLEDGE_INCLUDED_FOLDERS": "Knowledge/"}):
            self.assertEqual(self.main_with(lambda *a, **k: Result("mode: pull-only\n", 0)), 0)
        self.assertTrue((self.root / "Knowledge/Notes/kept.md").is_file())

    def test_a_mistyped_allowlist_changes_nothing(self) -> None:
        with patch.dict(os.environ, {"KNOWLEDGE_INCLUDED_FOLDERS": "Knowlege"}):
            self.assertEqual(self.main_with(lambda *a, **k: Result("mode: pull-only\n", 0)), 1)
        self.assertTrue((self.root / "Cabinet/private.md").is_file())
        self.assertTrue((self.root / "Knowledge/Notes/kept.md").is_file())

    def test_an_allowed_folder_is_dropped_from_the_skip_list(self) -> None:
        baseline, extra = Path(self.temp.name) / "b.txt", Path(self.temp.name) / "e.txt"
        extra.write_text("Knowledge\n", encoding="utf-8")
        self.assertNotIn("Knowledge", allowlist.merged_exclusions(baseline, extra, [], ["Knowledge"]))

    def test_check_reports_without_deleting(self) -> None:
        self.assertEqual(self.main_with(lambda *a, **k: Result("mode: pull-only\n", 0), "--check"), 0)
        self.assertTrue((self.root / "Cabinet/private.md").is_file())

    def test_the_pull_only_check_fails_safe(self) -> None:
        for output, code in (("pull-only", 1), ("pull-only bidirectional", 0), ("", 0), ("mirror-remote", 0)):
            with self.subTest(output=output, code=code), patch("subprocess.run", lambda *a, **k: Result(output, code)):
                self.assertNotEqual(allowlist.tool_mode(self.root), "pull-only")
        with patch("subprocess.run", side_effect=OSError):
            self.assertIsNone(allowlist.tool_mode(self.root))

    def test_a_link_is_removed_but_what_it_points_to_is_not(self) -> None:
        target = Path(self.temp.name) / "elsewhere"
        target.mkdir()
        (target / "keep.md").write_text("# Keep\n", encoding="utf-8")
        link = self.root / "Linked"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted here")
        allowlist.enforce(self.root, ["Knowledge"], delete=False)
        self.assertTrue(link.is_symlink())
        allowlist.enforce(self.root, ["Knowledge"], delete=True)
        self.assertFalse(link.is_symlink())
        self.assertTrue((target / "keep.md").is_file())


if __name__ == "__main__":
    unittest.main()
