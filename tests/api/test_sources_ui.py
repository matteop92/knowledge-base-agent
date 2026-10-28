"""The Sources page's list, bar, phone rows, filter sheet and reader, run under Node against a small fake DOM."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path("app/web")
HARNESS = "tests/api/sources_ui_harness.js"


def run(scenario: str):
    done = subprocess.run(["node", HARNESS, scenario], capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node")
class DesktopListTests(unittest.TestCase):
    def test_one_card_per_type_with_the_kicker_text(self) -> None:
        cards = run("sevenCards")
        self.assertEqual([card["kicker"] for card in cards], ["Book · 2015", "86 highlights", "Post", "Note", "Transcript",
                                                              "Article · example.org", "Quote"])
        self.assertEqual([card["cls"].split()[1] for card in cards],
                         ["s-cover", "s-cover", "s-post", "s-note", "s-transcript", "s-article", "s-quote"])
        self.assertEqual(cards[0]["cls"].split()[2], "cover-0")  # a book cover is always the one colour
        self.assertEqual(cards[2]["label"], "Post title")  # the title is the accessible name

    def test_a_post_with_no_platform_shows_only_the_type_and_the_date(self) -> None:
        card = run("postWithNoPlatform")
        self.assertEqual(card, {"top": "Post20 Oct 2026", "dot": False, "mark": False, "author": False, "voices": False})

    def test_voices_for_podcasts_author_for_notes_and_no_preview_text(self) -> None:
        out = run("voicesAndPreviews")
        self.assertEqual((out["transcript"], out["note"], out["noteVoices"]), ("Ben Hart × Ada Stone", "Ben Hart", False))
        self.assertEqual(out["authorOnly"], "Ben Hart")
        self.assertFalse(out["previews"])

    def test_an_active_tag_is_a_solid_pill_and_other_tags_stay_light(self) -> None:
        out = run("activeTagPill")
        self.assertEqual(out["post"], [["x", True], ["z", False]])
        self.assertEqual(out["article"], [["x", True]])  # an article shows only a matched tag
        self.assertEqual(out["results"], "2 sources")

    def test_sixty_cards_then_show_all_and_a_filter_shows_every_match(self) -> None:
        out = run("cap")
        self.assertEqual(out["before"], {"cards": 60, "more": False, "label": "Show all 80 sources"})
        self.assertEqual(out["after"], {"cards": 80, "more": True})
        self.assertEqual(out["filtered"], {"cards": 80, "more": True})

    def test_no_match_shows_the_empty_copy_and_no_list(self) -> None:
        out = run("zeroMatches")
        self.assertEqual((out["cards"], out["empty"]), (0, False))
        self.assertEqual((out["title"], out["sub"]), ("No sources match these filters.", "Remove a filter, or try a shorter search."))

    def test_enter_adds_a_token_backspace_removes_it_and_the_input_names_its_active_option(self) -> None:
        out = run("keyboard")
        self.assertEqual((out["active"], out["expanded"]), ("sg-0", "true"))
        self.assertEqual(out["afterEnter"], {"tokens": 1, "value": "", "closed": True})
        self.assertEqual(out["afterBackspace"], 0)
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertRegex(page, r'id="sources-result" class="sources-result" aria-live="polite"')
        self.assertRegex(page, r'id="sources-input"[^>]*role="combobox"[^>]*aria-controls="sources-suggest"')

    def test_no_author_means_no_byline_and_no_empty_line(self) -> None:
        for card in run("noAuthor"):
            self.assertFalse(card["byline"])
        self.assertNotIn("Unknown", json.dumps(run("noAuthor")))

    def test_a_quote_shows_its_text_with_the_mark_and_a_post_shows_no_body(self) -> None:
        out = run("quoteCard")
        self.assertEqual((out["mark"], out["attribution"]), ("Be the change.", "Ada Stone"))
        self.assertNotIn("ignored", out["postText"])

    def test_podcast_marks_use_the_microphone_with_the_show_or_the_word_podcast(self) -> None:
        self.assertEqual(run("podcastMarks"), [{"mark": "mic", "name": "Show Alpha"}, {"mark": "mic", "name": "Podcast"}])

    def test_a_book_leaves_the_app(self) -> None:
        out = run("booksLeaveTheApp")
        self.assertEqual((out["tag"], out["out"], out["pushes"]), ("a", True, 0))
        self.assertTrue(out["href"].startswith("obsidian://"))


@unittest.skipUnless(shutil.which("node"), "needs node")
class PhoneTests(unittest.TestCase):
    def test_every_row_has_one_layout_and_a_thumbnail_per_type_and_no_previews_or_tags(self) -> None:
        out = run("phoneRows")
        self.assertEqual({row[0] for row in out["rows"]}, {"s-row"})
        self.assertEqual([row[1] for row in out["rows"]],
                         ["s-thumb s-thumb-book cover-0", "s-thumb s-thumb-book cover-7", "s-thumb s-thumb-post",
                          "s-thumb s-thumb-note", "s-thumb s-thumb-transcript", "s-thumb s-thumb-article", "s-thumb s-thumb-quote"])
        self.assertFalse(any(row[2] or row[3] for row in out["rows"]))

    def test_the_filters_button_shows_a_badge_only_with_active_filters(self) -> None:
        out = run("phoneRows")
        self.assertEqual((out["badge0"], out["aria0"]), (True, "Filters"))  # no badge without filters
        self.assertEqual((out["aria1"], out["badge"]), ("Filters, 1 active", "1"))

    def test_the_sheet_updates_live_and_clear_all_resets_it(self) -> None:
        out = run("filterSheet")
        self.assertEqual(out["open"], {"sheets": ["filters"], "show": "Show 7 sources"})
        self.assertNotEqual(out["live"], "Show 7 sources")
        self.assertEqual(out["types"], ["Date added", "Type", "Tags", "People"])
        self.assertEqual((out["afterClear"], out["badge"]), ("Show 7 sources", True))

    def test_the_sheet_and_header_follow_the_documented_patterns(self) -> None:
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.sources-pin \{[^}]*position: sticky")
        self.assertIn('.sheet[data-kind="filters"]', css)
        script = (WEB / "sources.js").read_text(encoding="utf-8")
        self.assertIn('openSheet("filters"', script)
        self.assertNotIn("history.", script[script.index("function fillFilters"):script.index("function openFilters")])


@unittest.skipUnless(shutil.which("node"), "needs node")
class ReaderTests(unittest.TestCase):
    def test_a_transcript_turn_has_a_speaker_label_and_the_guest_is_green(self) -> None:
        out = run("readerTranscript")
        self.assertEqual(out["labels"], [["Ben Hart", False], ["Ada Stone", True]])
        self.assertEqual((out["reader"], out["list"], out["pushes"], out["focus"]), (False, True, 1, True))
        self.assertEqual(out["title"], "Transcript title")

    def test_there_are_no_counters_and_no_next_or_previous_controls(self) -> None:
        self.assertFalse(run("readerTranscript")["counters"])
        page = (WEB / "index.html").read_text(encoding="utf-8")
        reader = page[page.index('id="source-reader"'):page.index("</section>", page.index('id="source-reader"'))]
        self.assertNotRegex(reader.lower(), r"next|previous|\bof \d")

    def test_the_header_draws_from_the_list_row_and_a_missing_source_offers_retry_and_back(self) -> None:
        out = run("readerLoadingAndFailure")
        self.assertEqual(out["header"], "Note title")
        self.assertEqual(out["buttons"], ["Retry", "Back to Sources"])
        self.assertEqual(out["reloaded"], 2)  # the list refreshes after a 404

    def test_highlights_show_their_location_and_the_note_under_the_highlight(self) -> None:
        self.assertEqual(run("readerHighlights"), {"loc": "Page 12", "note": "Try it."})

    def test_a_source_without_a_description_has_no_lead_and_no_in_short_box(self) -> None:
        out = run("noDescription")
        self.assertEqual(out["bare"], {"lead": True, "inshort": True, "rule": True})
        self.assertEqual(out["full"], {"lead": False, "inshort": False, "text": "In short."})

    def test_back_returns_to_the_list_position_and_the_card_and_adds_no_stray_entries(self) -> None:
        out = run("backContract")
        self.assertEqual(out["afterButton"], {"backs": 1, "list": False, "scroll": 420, "focus": True})
        self.assertEqual(out["pushes"], 3)  # one entry per opening
        self.assertEqual(out["backsAfterLeave"], 2)  # a tab switch went back once
        self.assertEqual(out["backsAfterPhoneBack"], 2)  # the phone's own Back adds none
        self.assertTrue(out["readerHidden"])

    def test_ask_about_this_scopes_a_new_question_and_the_phone_box_sends_its_text(self) -> None:
        out = run("askAbout")
        self.assertEqual(out["first"]["scopes"], [{"id": "id-4", "title": "Note title"}])
        self.assertEqual((out["first"]["asked"], out["first"]["backs"]), ([], 1))
        self.assertEqual(out["second"]["asked"], ["what is it?"])
        self.assertEqual(len(out["second"]["scopes"]), 2)
        self.assertEqual(out["hint"], "Ask about this note…")


class SourceSurfaceTests(unittest.TestCase):
    def test_the_scope_chip_sends_the_source_and_every_new_conversation_clears_it(self) -> None:
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("source_id: state.scope.id", app)
        for name in ("function newQuestion", "async function openConversation"):
            body = app[app.index(name):]
            self.assertIn("setScope(null)", body[:body.index("\n}\n")])
        self.assertEqual(app.count('addEventListener("popstate"'), 1)
        self.assertIn("window.sourcesScreen.popped()", app)

    def test_the_reader_owns_one_history_entry_per_opening_and_goes_back_once(self) -> None:
        script = (WEB / "sources.js").read_text(encoding="utf-8")
        self.assertEqual(script.count("history.pushState"), 1)
        self.assertEqual(script.count("history.back()"), 2)  # closeReader and leave; popped never goes back
        popped = script[script.index("function popped"):script.index("function askAbout")]
        self.assertNotIn("history.", popped)

    def test_server_text_is_inserted_as_text_and_ids_exist(self) -> None:
        script = (WEB / "sources.js").read_text(encoding="utf-8")
        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(unsafe, script)
        self.assertIn("/static/sources.js", (WEB / "sw.js").read_text(encoding="utf-8"))
        page = (WEB / "index.html").read_text(encoding="utf-8")
        for element_id in sorted(set(re.findall(r'\$\("([a-z-]+)"\)', script))):
            self.assertIn(f'id="{element_id}"', page, element_id)


if __name__ == "__main__":
    unittest.main()
