from __future__ import annotations

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
        (self.root / "Career").mkdir()
        (self.root / "Career/private.md").write_text("# Private\n", encoding="utf-8")
        (self.root / "Brand New Folder").mkdir()
        (self.root / ".obsidian").mkdir()
        (self.root / "loose note.md").write_text("# Loose\n", encoding="utf-8")

    def test_everything_outside_the_allowlist_is_removed_and_knowledge_is_untouched(self) -> None:
        outside, loose = allowlist.enforce(self.root, ["Knowledge"], delete=True)
        self.assertEqual((outside, loose), (["Brand New Folder", "Career"], 1))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), [".obsidian", "Knowledge"])
        self.assertTrue((self.root / "Knowledge/Notes/kept.md").is_file())

    def test_folder_names_match_without_regard_to_case(self) -> None:
        outside, _ = allowlist.enforce(self.root, ["knowledge"], delete=False)
        self.assertNotIn("Knowledge", outside)

    def test_without_permission_to_delete_nothing_is_touched(self) -> None:
        outside, loose = allowlist.enforce(self.root, ["Knowledge"], delete=False)
        self.assertEqual((outside, loose), (["Brand New Folder", "Career"], 1))
        self.assertEqual(len(list(self.root.iterdir())), 5)

    def test_an_empty_allowlist_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            allowlist.enforce(self.root, [], delete=True)
        self.assertTrue((self.root / "Career/private.md").is_file())

    def test_skip_list_keeps_the_baseline_and_remembers_new_folders(self) -> None:
        baseline, extra = Path(self.temp.name) / "baseline.txt", Path(self.temp.name) / "extra.txt"
        baseline.write_text("Career\nTasks\n", encoding="utf-8")
        names = allowlist.merged_exclusions(baseline, extra, ["Brand New Folder", "career"])
        self.assertEqual(names, ["Career", "Tasks", "Brand New Folder"])
        self.assertEqual(extra.read_text(encoding="utf-8").split(), ["Brand", "New", "Folder"])
        self.assertEqual(allowlist.merged_exclusions(baseline, extra, [])[-1], "Brand New Folder")

    def run_main(self, config_output: str, mode: str | None, returncode=0):
        baseline, extra = Path(self.temp.name) / "baseline.txt", Path(self.temp.name) / "extra.txt"
        baseline.write_text("Tasks\n", encoding="utf-8")
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
        self.assertFalse((self.root / "Career").exists())
        update = [command for command in calls if "--excluded-folders" in command][0]
        self.assertEqual(update[update.index("--excluded-folders") + 1], "Tasks,Brand New Folder,Career")

    def test_two_way_sync_never_deletes_but_still_stops_syncing_new_folders(self) -> None:
        for config, mode in (("mode: bidirectional\n", None), ("mode: pull-only\n", "bidirectional"), ("", None)):
            with self.subTest(config=config, mode=mode):
                code, calls = self.run_main(config, mode)
                self.assertEqual(code, 0)
                self.assertTrue((self.root / "Career/private.md").is_file())
                self.assertTrue(any("--excluded-folders" in command for command in calls))

    def test_a_failed_skip_list_update_is_reported(self) -> None:
        code, _ = self.run_main("mode: pull-only\n", None, returncode=1)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
