"""Settings > Review (the how-often control, the Again and Done lists) run under Node against a small fake DOM."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import unittest

HARNESS = "tests/api/review_settings_harness.js"
KEY = lambda n: str(n).zfill(32)  # noqa: E731


def run(scenario: str):
    done = subprocess.run(["node", HARNESS, scenario], capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node")
class ReviewSettingsScreenTests(unittest.TestCase):
    def test_the_tab_shows_the_saved_level_with_its_word_number_and_dots_and_stops_at_the_ends(self) -> None:
        out = run("tabShowsLevelWordNumberAndDots")
        self.assertEqual(out["10"]["word"], "Normal")
        self.assertEqual((out["10"]["less"], out["10"]["more"]), (True, False), "minus is disabled at Normal")
        self.assertEqual(out["10"]["dots"], [True, False, False, False])
        self.assertEqual((out["5"]["word"], out["5"]["dots"]), ("Sometimes", [True, True, False, False]))
        self.assertEqual((out["3"]["word"], out["3"]["n"], out["3"]["short"], out["3"]["label"]), ("Often", "3", "3", "Level 3 of 4"))
        self.assertEqual((out["2"]["word"], out["2"]["less"], out["2"]["more"]), ("Frequently", False, True))
        self.assertEqual(out["2"]["counts"], ["3", "1"])

    def test_a_stepper_saves_at_once_and_the_caption_follows_and_a_failed_save_goes_back(self) -> None:
        out = run("steppersSaveAndFailureReverts")
        self.assertEqual(out["optimistic"], "Often")
        self.assertEqual(out["saved"], [{"every": 5}, {"every": 3}])
        self.assertEqual((out["final"], out["caption"]), ("Often", "3"))
        self.assertEqual((out["during"], out["afterFail"]), ("Normal", "Sometimes"))
        self.assertIn("Could not save", out["message"])

    def test_a_row_opens_its_list_newest_first_and_an_empty_list_says_so(self) -> None:
        out = run("listsOpenNewestFirstWithTheSubLineAndAnEmptyOne")
        again = out["again"]
        self.assertEqual(again["title"], "Again")
        self.assertEqual(again["sub"], "2 passages, newest first. They come back about 1 in 5.")
        self.assertEqual([row.startswith(text) for row, text in zip(again["rows"], ("Passage 3.", "Passage 2."))], [True, True])
        self.assertEqual((again["mainHidden"], again["listHidden"], again["isList"]), (True, False, True))
        self.assertEqual(again["moves"], ["Move to Done", "Move to Done"])
        self.assertEqual(out["afterBack"], {"main": False})
        done = out["done"]
        self.assertEqual((done["title"], done["sub"], done["rows"]), ("Done", "0 passages, newest first.", 0))
        self.assertFalse(done["empty"], "the empty line shows")
        page = (pathlib.Path("app/web/index.html")).read_text(encoding="utf-8")
        self.assertIn(">Nothing here yet.</p>", page)

    def test_move_to_done_moves_the_row_and_both_counts_update(self) -> None:
        out = run("moveToDoneMovesTheRowAndBothCounts")
        self.assertEqual(out["writes"], [[KEY(3), {"mark": "done"}]])
        self.assertEqual(out["rows"], 1)
        self.assertEqual(out["sub"], "1 passage, newest first. They come back about 1 in 10.")
        self.assertEqual(out["counts"], ["1", "2"], "the counts as the server gave them after the move: reloaded on return")

    def test_a_row_or_read_opens_the_passage_in_review_with_the_whole_list_and_back_returns_to_the_list(self) -> None:
        out = run("readOpensTheListPassageInReviewAndBackComesBack")
        self.assertEqual(out["opened"], {"entered": [["again", 3, 1]], "views": ["review"]})
        self.assertEqual(out["entered"], [["again", 1], ["again", 2]])
        self.assertEqual(out["views"], ["review", "review", "settings"])
        self.assertFalse(out["listAgain"], "the list page is showing again")
        self.assertEqual(out["title"], "Again")
        self.assertGreaterEqual(out["gets"], 2, "the list is read again on return")

    def test_the_tabs_swap_panels_and_the_review_tab_loads_its_settings(self) -> None:
        out = run("tabsSwitchPanelsAndTheReviewTabLoads")
        self.assertEqual(out["onReview"], {"tags": [True, None], "review": [False, "page"]})
        self.assertEqual(out["word"], "Often")
        self.assertEqual(out["onTags"], {"tags": [False, "page"], "review": [True, None]})

    def test_the_every_passage_done_link_opens_settings_review_done(self) -> None:
        out = run("everyPassageDoneLinkOpensTheDoneListFromReview")
        self.assertEqual(out["before"]["views"], ["settings"])
        self.assertEqual((out["title"], out["listHidden"], out["reviewPanel"]), ("Done", False, False))


if __name__ == "__main__":
    unittest.main()
