"""app/web/prompt-text.js: a prompt's blanks, run under Node."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest


def runs(text: str) -> list[list]:
    script = f"""const p = require("./app/web/prompt-text.js");
console.log(JSON.stringify(p.runs({json.dumps(text)}).map((r) => [r.text, r.blank])));"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "needs node")
class PromptTextTests(unittest.TestCase):
    def test_braces_double_braces_and_upper_case_brackets_are_blanks(self) -> None:
        self.assertEqual(runs("For {audience}, use {{ tone }} and [TOPIC_NAME]."), [
            ["For ", False], ["{audience}", True], [", use ", False], ["{{ tone }}", True], [" and ", False],
            ["[TOPIC_NAME]", True], [".", False]])

    def test_links_json_and_lower_case_brackets_are_plain_text(self) -> None:
        for text in ("See [the docs](https://x.org).", 'Return {"a": 1}.', "A list [a, b].", "{}"):
            with self.subTest(text=text):
                self.assertEqual(runs(text), [[text, False]])

    def test_joined_runs_give_the_text_back(self) -> None:
        text = "{a} and {b}\n\n[C] end"
        self.assertEqual("".join(part for part, _ in runs(text)), text)
