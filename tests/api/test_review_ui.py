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

    def test_a_part_is_sized_from_the_passage_box_so_long_passages_split_into_more_parts(self) -> None:
        out = run("partsForOnePassageSize")
        self.assertEqual((out["long140"]["budget"], out["long140Wide"]["budget"]), (68, 119))
        self.assertEqual(len(out["long140"]["parts"]), 3, "was 2 at the old 110-word budget")
        self.assertEqual(len(out["long140Wide"]["parts"]), 2)
        self.assertEqual(out["quote30"]["parts"], [30])
        self.assertFalse(out["quote30"]["bars"])
        self.assertTrue(out["long140"]["bars"])
        self.assertFalse(any(case["short"] for case in out.values()), "one size for every passage: no is-short class")

    def test_a_passage_and_its_note_are_checked_together_against_the_budget(self) -> None:
        out = run("partsForOnePassageSize")["withNote"]
        self.assertEqual(out["parts"], [60, 0])  # 60 words and a 60-word note do not fit one part of 68

    def test_one_long_sentence_is_broken_at_word_boundaries_to_fit(self) -> None:
        out = run("partsForOnePassageSize")
        for case in ("oneSentence", "noPunctuation"):
            parts, budget = out[case]["parts"], out[case]["budget"]
            self.assertGreater(len(parts), 1, case)
            self.assertLessEqual(max(parts), -(-budget * 115 // 100), (case, parts))

    def test_the_two_circles_fill_clear_and_switch_and_carry_their_names_and_keys(self) -> None:
        out = run("marksFillTapAgainClearsAndSwitches")
        self.assertEqual(out["initial"], ["false", "false"])
        self.assertEqual(out["afterAgain"], ["false", "true"])
        self.assertEqual(out["afterClear"], ["false", "false"])
        self.assertEqual(out["afterSwitch"], ["true", "false"])
        key = "0" * 31 + "1"
        self.assertEqual(out["writes"], [["PUT", key, {"mark": "again"}], ["DELETE", key, None], ["PUT", key, {"mark": "again"}],
                                         ["PUT", key, {"mark": "done"}]])
        self.assertEqual((out["labels"], out["keys"]), (["Done", "Show again"], ["X", "B"]))
        self.assertEqual(out["titles"], ["Done (X)", "Show again (B)"])

    def test_a_mark_set_on_one_part_stays_filled_on_the_others(self) -> None:
        out = run("aMarkSetOnOnePartShowsOnTheOthers")
        self.assertGreater(out["parts"], 2)
        self.assertEqual(set(out["pressed"]), {"true"})

    def test_x_and_b_mark_and_are_ignored_while_typing_and_the_arrows_still_turn_parts(self) -> None:
        out = run("keysXAndBMarkOnDesktopAndAreIgnoredWhileTyping")
        key = "0" * 31 + "1"
        self.assertEqual(out["writes"], [["PUT", {"mark": "done"}], ["PUT", {"mark": "again"}]])
        self.assertEqual(out["part"], 1)
        self.assertEqual(out["pressed"], ["false", "true"])

    def test_a_failed_save_puts_the_fill_back_and_says_so(self) -> None:
        out = run("aFailedSaveGivesTheFillBack")
        self.assertEqual((out["during"], out["after"]), ("true", "false"))
        self.assertEqual(out["notices"], ["The mark was not saved. Try again."])

    def test_circles_never_show_the_ask_pill_the_passage_does_and_an_again_serve_starts_filled(self) -> None:
        out = run("circlesNeverShowThePillButThePassageDoesAndAnAgainServeStartsFilled")
        self.assertEqual(out["filled"], "true")
        self.assertTrue(out["afterCircle"], "the pill stays hidden after a circle tap")
        self.assertEqual(out["afterPassage"], {"hidden": False, "ask": "open"})
        self.assertIsNone(out["afterHide"])
        self.assertEqual(out["shown"], [{"passage_key": "0" * 31 + "1", "round": 1, "again": True}])

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

    def test_each_source_type_colours_its_screen_and_names_itself_and_its_source(self) -> None:
        out = run("typesStyleTheScreen")
        self.assertEqual(out["classes"], [["is-highlights"], ["is-quote"], ["is-note"], ["is-post"], ["is-note"]])
        self.assertEqual(out["kickers"], ["Highlights", "Quote", "Note", "Post", "Note"])
        self.assertEqual(out["source"], ["Title 1", "Ada Stone"])
        self.assertEqual((out["type"], out["tone"]), ("post", "light"))

    def test_a_wheel_flick_flips_one_passage_even_with_trailing_inertia(self) -> None:
        out = run("wheelFlipsOnePassagePerGesture")
        self.assertEqual(out, {"prevented": True, "flipped": True, "extra": False, "second": True})

    def test_loading_more_never_moves_the_pages_under_the_reader(self) -> None:
        out = run("tailStaysPutWhileFetching")
        self.assertTrue(out["sameTail"])
        self.assertEqual(out["order"][-1], "tail")
        self.assertEqual(out["order"].count("tail"), 1)


if __name__ == "__main__":
    unittest.main()
