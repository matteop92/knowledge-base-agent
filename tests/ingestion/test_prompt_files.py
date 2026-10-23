import re
import unittest
from pathlib import Path

from app.ingestion.summary import read_prompt

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


class PromptFilesTest(unittest.TestCase):
    def test_archived_summary_prompt_is_still_readable(self) -> None:
        self.assertTrue(read_prompt("document_summary_v17").strip())

    def test_current_prompt_is_read_from_the_main_folder(self) -> None:
        self.assertEqual(read_prompt("section_summary_v1"), (PROMPTS / "section_summary_v1.md").read_text(encoding="utf-8"))

    def test_unknown_prompt_is_an_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_prompt("no_such_prompt_v1")

    def test_every_prompt_named_in_the_changelog_exists(self) -> None:
        text = (PROMPTS / "README.md").read_text(encoding="utf-8")
        for name in set(re.findall(r"`([a-z_]+_v\d+)\.md`", text)):
            self.assertTrue((PROMPTS / f"{name}.md").is_file(), name)


if __name__ == "__main__":
    unittest.main()
