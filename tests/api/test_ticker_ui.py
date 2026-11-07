"""The Ask page's band of recently added notes (app/web/ticker.js), run under Node against a small fake DOM."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

HARNESS = "tests/api/ticker_ui_harness.js"


def run(scenario: str):
    done = subprocess.run(["node", HARNESS, scenario], capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node")
class TickerTests(unittest.TestCase):
    def test_one_button_per_source_with_the_icon_for_its_type_and_platform(self) -> None:
        out = run("mixed")
        self.assertEqual(out["api"], [["GET", "/api/sources/recent"]])
        self.assertFalse(out["hidden"])
        icons = {item["title"]: item["icon"] for item in out["items"]}
        self.assertEqual(icons, {
            "A post on X": "xlogo", "A LinkedIn post": "linkedin", "A Reddit thread": "reddit", "A post on the web": "globe",
            "An article": "newspaper", "A book": "book", "Some highlights": "book-open", "A note": "pencil-simple",
            "A quote": "ph-quotes", "A talk": "mic", "A new kind": "pencil-simple",
        })
        self.assertEqual(len(out["items"]), 11)
        for item in out["items"]:
            self.assertEqual(item["type"], "button")
            self.assertEqual(item["label"], f"Open {item['title']}")
            self.assertIsNone(item["tabindex"])

    def test_the_author_is_left_out_when_there_is_none_and_the_dot_always_ends_the_item(self) -> None:
        items = {item["title"]: item for item in run("mixed")["items"]}
        self.assertEqual(items["A post on X"]["author"], "Ada Stone")
        self.assertIsNone(items["A Reddit thread"]["author"])
        self.assertIsNone(items["A note"]["author"])
        self.assertEqual(items["A post on X"]["parts"], ["svg.ticker-icon", "span.ticker-title", "span.ticker-author", "span.ticker-dot"])
        self.assertEqual(items["A note"]["parts"], ["svg.ticker-icon", "span.ticker-title", "span.ticker-dot"])

    def test_each_title_is_a_real_item_plus_one_aria_hidden_copy_when_the_items_do_not_fit(self) -> None:
        out = run("mixed")
        self.assertEqual(out["titlesInDom"], [2] * 11)
        self.assertEqual(out["copies"], [{"hidden": None, "buttons": 11}, {"hidden": "true", "buttons": 11}])
        self.assertEqual(out["copyTabindexes"], ["-1"], "Tab skips the repeated copy")
        self.assertTrue(out["scrolling"])

    def test_the_scroll_speed_is_about_40_pixels_a_second_from_the_measured_width(self) -> None:
        self.assertEqual(run("speed")["duration"], "50s")  # 2000 px of items

    def test_items_that_all_fit_are_not_repeated_or_animated(self) -> None:
        out = run("fits")
        self.assertFalse(out["hidden"])
        self.assertFalse(out["scrolling"])
        self.assertEqual(out["copies"], [{"hidden": None, "buttons": 3}])

    def test_reduced_motion_scrolls_by_hand_so_nothing_is_repeated_or_animated(self) -> None:
        out = run("reducedMotion")
        self.assertFalse(out["hidden"])
        self.assertFalse(out["scrolling"])
        self.assertEqual(out["copies"], [{"hidden": None, "buttons": 11}])

    def test_a_band_that_is_not_on_screen_does_not_decide_to_scroll(self) -> None:
        self.assertFalse(run("notOnScreen")["scrolling"])

    def test_an_empty_list_or_a_failure_leaves_the_band_hidden(self) -> None:
        out = run("emptyAndFailure")
        self.assertFalse(out["shown"])
        self.assertTrue(out["empty"])
        self.assertTrue(out["failed"])
        self.assertTrue(out["malformed"])

    def test_clicking_an_item_opens_that_source_even_on_the_repeated_copy(self) -> None:
        self.assertEqual(run("click"), ["s5", "s8"])

    def test_the_newest_load_wins(self) -> None:
        self.assertEqual(run("newestLoadWins"), 2)
