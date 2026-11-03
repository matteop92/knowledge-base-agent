"""The Review screen (pager, parts, shown logging, Ask hand-off, states), run under Node against a small fake DOM."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

HARNESS = "tests/api/review_ui_harness.js"


def run(scenario: str):
    done = subprocess.run(["node", HARNESS, scenario], capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node")
class ReviewScreenTests(unittest.TestCase):
    def test_paginate_splits_long_passages_into_balanced_parts_and_keeps_short_ones_whole(self) -> None:
        out = run("paginate")
        self.assertEqual(len(out["oneHundredTwentyNine"]), 2)  # 129 words at a budget of 110
        self.assertEqual(len(out["threeEightySeven"]), 4)
        self.assertLessEqual(max(out["threeEightySeven"]) - min(out["threeEightySeven"]), 6)
        for parts in out.values():
            body = [words for words in parts if words]
            self.assertGreaterEqual(min(body) / max(body), 0.35, "a stub last part is merged into the one before")

    def test_paragraphs_are_kept_whole_before_sentences_are_split(self) -> None:
        self.assertEqual(run("paragraphsFirst"), [[100], [100, 8]])

    def test_the_note_counts_toward_the_split_and_a_long_note_gets_its_own_last_part(self) -> None:
        out = run("paginate")
        self.assertEqual(len(out["withNote"]), 4)  # 330 words and a 50-word note at a budget of 110
        self.assertTrue(all(abs(words - 95) <= 12 for words in out["withNote"][:3]), out["withNote"])
        self.assertLessEqual(out["withNote"][3] + 50, 110 * 1.15)
        self.assertEqual(out["ownNote"][-1], 0)  # an empty body: the last part holds only the note

    def test_the_note_shows_on_the_last_part_only(self) -> None:
        out = run("noteOnlyOnLastPart")
        self.assertEqual(out["notes"], [False] * (out["parts"] - 1) + [True])

    def test_ae2_a_passage_on_screen_for_a_second_is_logged_and_one_never_seen_is_not(self) -> None:
        out = run("shownOnlyWhenOnScreen")
        self.assertEqual(out["timerMs"], 1000)
        self.assertEqual(out["calls"], [["POST", "/api/review/shown", {"passage_key": "0" * 31 + "1", "round": 1}]])
        self.assertEqual(out["pages"], 2)

    def test_leaving_a_passage_before_a_second_logs_nothing(self) -> None:
        self.assertEqual(run("leavingBeforeASecondLogsNothing"), {"live": 0, "posts": 0})

    def test_tapping_the_passage_toggles_the_ask_pill_and_the_pill_hands_off_the_passage(self) -> None:
        out = run("tapShowsThePillAndAsks")
        self.assertEqual((out["afterFirst"], out["afterSecond"]), (False, True))  # the pill's hidden flag
        self.assertEqual((out["asked"], out["hiddenAfterAsk"]), (["source-1"], True))

    def test_on_a_phone_the_edges_turn_parts_the_middle_asks_and_a_turn_hides_the_pill(self) -> None:
        out = run("edgesTurnPartsOnAPhone")
        self.assertGreater(out["parts"], 1)
        self.assertEqual((out["afterRight"], out["afterLeft"]), (1, 0))
        self.assertTrue(out["middleShowsPill"])
        self.assertTrue(out["hiddenAfterTurn"])

    def test_on_a_one_part_passage_the_edges_do_nothing_and_any_tap_shows_the_pill(self) -> None:
        self.assertEqual(run("oneLinePassageEdgesDoNothing"), {"part": 0, "pill": True})

    def test_the_empty_state_shows_only_when_nothing_comes_back_and_nothing_is_held(self) -> None:
        out = run("emptyAndHeldStates")
        self.assertTrue(out["emptyShown"])
        self.assertIn("Nothing to review yet", out["emptyText"])
        self.assertFalse(out["heldEmpty"])
        self.assertEqual(out["heldPages"], 2)

    def test_a_pool_smaller_than_the_buffer_fills_the_screen_without_the_empty_state(self) -> None:
        self.assertEqual(run("smallPoolFillsTheScreen"), {"stateHidden": True, "pages": 2})

    def test_a_failed_fetch_shows_retry_and_retry_fetches_again(self) -> None:
        out = run("failedThenRetry")
        self.assertIn("did not load", out["failedText"])
        self.assertTrue(out["failedText"].endswith("Retry"))
        self.assertEqual((out["retried"], out["pages"], out["stateHidden"]), (2, 1, True))

    def test_arrow_keys_turn_parts_and_move_passages_and_bars_and_arrows_are_labelled(self) -> None:
        out = run("keys")
        self.assertEqual((out["afterRight"], out["afterLeft"], out["scrolled"]), (1, 0, True))
        self.assertEqual(out["labels"][:2], ["Part 1 of 4", "Part 2 of 4"])
        self.assertEqual(out["arrows"], ["Previous part", "Next part"])

    def test_the_hint_shows_on_the_first_few_passages_only(self) -> None:
        out = run("hintShowsOnTheFirstPassagesOnly")
        self.assertEqual((out["lateHidden"], out["freshHidden"]), (True, False))
        self.assertIn("Tap the edges", out["text"])

    def test_ask_about_this_starts_a_question_limited_to_the_source_and_shows_the_passage(self) -> None:
        out = run("askHandoff")
        self.assertEqual(out["log"], ["newQuestion", ["scope", "source-7"]])
        self.assertEqual(out["shown"], {"hidden": False, "text": ["First.", "Second."], "source": "A Book · Ada Stone"})
        self.assertTrue(out["afterClear"])

    def test_the_held_list_never_passes_what_the_server_accepts(self) -> None:
        out = run("heldStaysUnderTheServerCap")
        self.assertLessEqual(out["held"], 30)

    def test_a_failed_shown_call_is_retried_and_the_passage_stays_held_until_it_lands(self) -> None:
        out = run("failedShownIsRetried")
        self.assertEqual(out["first"], {"posts": 1, "logged": False})
        self.assertEqual((out["posts"], out["logged"]), (2, True))


if __name__ == "__main__":
    unittest.main()
