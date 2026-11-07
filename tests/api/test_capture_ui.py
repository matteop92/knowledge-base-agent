"""The Add card's optional row (Type, Author, Tags, Title) run under Node against a small fake DOM."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

HARNESS = "tests/api/capture_ui_harness.js"


def run(scenario: str):
    done = subprocess.run(["node", HARNESS, scenario], capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "needs node")
class CaptureRowTests(unittest.TestCase):
    def test_type_opens_the_ten_names_a_pick_shows_the_chip_and_removing_it_clears_the_request(self) -> None:
        out = run("typePickShowsTheChipAndRemovingItClearsTheRequest")
        self.assertEqual(out["opened"]["list"]["rows"], ["Article", "Book", "Book highlights", "Meeting", "Note", "Other", "Post", "Quote", "Reference", "Transcript"])
        self.assertEqual((out["opened"]["expanded"], out["opened"]["wrapShown"], out["opened"]["buttonHidden"]), ("true", True, True))
        self.assertEqual(out["opened"]["list"]["in"], "cf-input", "on desktop the list floats under the field")
        self.assertEqual(out["picked"], {"chips": ["TypePost"], "buttonHidden": True, "listShown": False})
        self.assertEqual(out["posts"][0]["type"], "social_post")  # the stored value, not the label
        self.assertEqual(out["removeLabel"], "Remove type")
        self.assertEqual(out["afterRemove"], {"chips": [], "button": True})
        self.assertNotIn("type", out["posts"][1])

    def test_author_type_ahead_suggests_people_offers_add_and_several_names_become_chips(self) -> None:
        out = run("authorsSuggestPeopleAndSeveralBecomeChips")
        self.assertFalse(out["empty"], "nothing is listed before typing")
        self.assertEqual(len(out["typedNav"]["rows"]), 3)  # Naval Ravikant, a divider, then Add
        self.assertIn("Ravikant", out["typedNav"]["rows"][0])
        self.assertIn("4 notes", out["typedNav"]["rows"][0])
        self.assertEqual(out["typedNav"]["rows"][2], "Add “nav”")
        self.assertEqual(out["wrapped"], ["Add “nav”"], "arrow up from the first row wraps to the Add row")
        self.assertEqual(out["first"], {"chips": ["AuthorNaval Ravikant"], "value": "", "stillOpen": True})
        self.assertEqual(out["second"], ["Add “Brand New”"])
        self.assertEqual(out["chipsNow"], ["AuthorNaval Ravikant", "Brand New"], "only the first chip carries the Author label")
        self.assertEqual(out["posts"][0]["authors"], ["Naval Ravikant", "Brand New"])

    def test_tags_list_nothing_before_typing_then_pick_create_dedupe_and_flash(self) -> None:
        out = run("tagsAreEmptyBeforeTypingThenPickCreateAndFlash")
        self.assertFalse(out["before"])
        self.assertEqual(len(out["rows"]), 4)  # marketing, market-research, a divider, Create
        self.assertEqual(out["rows"][3], "Create tag “mar”")
        self.assertEqual(out["picked"], ["marketing"])
        self.assertEqual(out["create"], ["Create tag “brand-new”"])
        self.assertEqual(out["invalid"], [], "text that cannot be a tag has no Create row")
        self.assertEqual(out["noCreate"], [], "a tag already on the note is neither listed nor created again")
        self.assertEqual(out["chips"], ["marketing", "brand-new", "ai", "x1", "x2"])
        self.assertEqual((out["flash"], out["flashAfter"]), (1, 0), "a repeated tag flashes its chip, then settles")

    def test_a_note_keeps_ten_tags(self) -> None:
        out = run("tenTagsIsTheLimit")
        self.assertEqual(out["chips"], 10)
        self.assertEqual((out["buttonHidden"], out["open"]), (True, False))
        self.assertIn("up to 10 tags", out["status"])

    def test_combobox_arrows_enter_and_escape(self) -> None:
        out = run("keyboardMovesTheHighlightAndEscapeClosesTheListKeepingTheText")
        self.assertEqual(out["open"], {"expanded": "true", "active": "capture-option-0", "rows": 4})
        self.assertEqual(out["moved"]["active"], "capture-option-1")
        self.assertEqual(len(out["moved"]["highlighted"]), 1)
        self.assertEqual(out["closed"], {"shown": False, "expanded": "false", "text": "mar", "descendant": None, "stopped": True, "wrapShown": True})
        self.assertTrue(out["reopened"], "an arrow key opens the list again")
        self.assertTrue(out["enterPrevented"], "Enter never sends the form")

    def test_tapping_outside_closes_the_list_keeps_the_text_and_an_empty_field_closes(self) -> None:
        out = run("tappingOutsideClosesTheListAndKeepsTheText")
        self.assertEqual(out["kept"], {"shown": False, "text": "mar", "wrapShown": True})
        self.assertTrue(out["refocused"])
        self.assertEqual(out["emptyClosed"]["inputHidden"], [True] * 4)
        self.assertEqual(out["emptyClosed"]["buttons"], [True] * 4)

    def test_save_commits_typed_text_first_sends_type_and_authors_and_the_row_returns_to_rest(self) -> None:
        out = run("saveCommitsTypedTextFirstAndTheRowReturnsToRest")
        self.assertEqual(out["before"]["chips"], ["TypeArticle", "AuthorAda Lovelace", "mar", "TitleMy title"])
        post = out["posts"][0]
        self.assertEqual((post["type"], post["authors"], post["tags"], post["title"]), ("article", ["Ada Lovelace"], ["mar"], "My title"))
        self.assertEqual(out["after"], {"chips": ["", "", "", ""], "buttons": [True] * 4, "inputHidden": [True] * 4, "collapsed": False, "listShown": False})
        self.assertEqual(out["title"], "")

    def test_nothing_set_sends_the_request_shape_it_always_had(self) -> None:
        out = run("nothingSetSendsNeitherTypeNorAuthors")
        self.assertEqual(out["keys"], ["content", "kind", "tags", "title", "url"])
        self.assertEqual(out["posts"][0]["tags"], [])
        self.assertIsNone(out["posts"][0]["title"])

    def test_on_a_phone_a_focused_field_collapses_the_card_and_closing_restores_it(self) -> None:
        out = run("onAPhoneAFocusedFieldCollapsesTheCardAndClosingRestoresIt")
        self.assertFalse(out["rest0"]["collapsed"])
        self.assertTrue(out["open"]["collapsed"])
        self.assertEqual(out["open"]["line"], "https://paulgraham.com/makersschedule.html")
        self.assertEqual(out["open"]["rest"], ["Author", "Title"], "the unset buttons sit under the list, with Save")
        self.assertEqual(out["open"]["list"]["in"], "list-slot", "the list is in flow under the row")
        self.assertEqual(out["open"]["typeChip"], ["TypeArticle"])
        self.assertEqual(out["open"]["buttonsHome"], [False, False])
        self.assertTrue(out["waiting"], "the card holds its shape for a moment after a blur, so a tap on Save or Title lands")
        self.assertEqual((out["closed"]["collapsed"], out["closed"]["line"], out["closed"]["rest"]), (False, "", []))
        self.assertEqual(out["closed"]["text"], "mar", "typed text stays when the field closes")
        self.assertEqual(out["closed"]["buttonsHome"], [True, True])
        self.assertEqual(out["closed"]["order"], ["capture-slot-type", "capture-slot-author", "capture-slot-tags", "capture-slot-title"])
        self.assertFalse(out["desktop"]["collapsed"])
        self.assertEqual(out["desktop"]["list"]["in"], "cf-input")
        self.assertEqual(out["desktop"]["rest"], [])


    def test_backspace_in_an_empty_tags_or_author_field_removes_the_last_chip(self) -> None:
        out = run("backspaceOnAnEmptyFieldRemovesTheLastChip")
        self.assertEqual(out["tagsBefore"], ["one", "two", "three"])
        self.assertEqual(out["withText"], ["one", "two", "three"], "with text in the field Backspace only edits the text")
        self.assertEqual(out["tagsAfterOne"], {"chips": ["one", "two"], "status": "Removed tag three", "open": True})
        self.assertEqual(out["tagsAfterAll"], [])
        self.assertEqual(out["tagsEmpty"], {"chips": [], "status": "Removed tag one"}, "with no chip left Backspace does nothing")
        self.assertEqual(out["authorsBefore"], ["AuthorAda Lovelace", "Alan Turing"])
        self.assertEqual(out["authorsAfterOne"], {"chips": ["AuthorAda Lovelace"], "status": "Removed author Alan Turing"})
        self.assertEqual(out["authorsAfterAll"], [])
        self.assertEqual(out["posts"][0]["tags"], [])
        self.assertNotIn("authors", out["posts"][0])

    def test_an_author_already_added_says_so_and_adds_no_chip(self) -> None:
        out = run("anAuthorAlreadyAddedIsNotAddedTwice")
        self.assertEqual(out["first"], ["AuthorPaul Graham"])
        self.assertEqual(out["rows"], [], "a name already on the note is not offered again")
        self.assertEqual(out["status"], "PAUL graham is already added")
        self.assertEqual(out["after"], {"chips": ["AuthorPaul Graham"], "value": ""})
        self.assertEqual(out["posts"][0]["authors"], ["Paul Graham"])

    def test_a_note_keeps_ten_authors_and_the_add_button_goes_until_one_is_removed(self) -> None:
        out = run("tenAuthorsIsTheLimit")
        self.assertEqual((out["chips"], out["buttonHidden"], out["open"]), (10, True, False))
        self.assertEqual(out["afterRemove"], {"chips": 9, "buttonHidden": False})
        self.assertEqual(out["posts"][0]["authors"], [f"Person {i}" for i in range(1, 10)])

    def test_an_invalid_tag_stays_in_the_field_with_a_status_and_is_not_sent(self) -> None:
        out = run("anInvalidTagStaysInTheFieldAndIsNotSent")
        self.assertEqual(out["rows"], [], "text that cannot be a tag has no Create row")
        self.assertEqual(out["typed"], {"chips": [], "value": "#!", "status": "“#!” is not a valid tag", "open": True})
        self.assertEqual(out["mixed"], {"chips": ["good"], "value": "#!", "status": "“#!” is not a valid tag"})
        self.assertEqual(out["posts"][0]["tags"], ["good"])

    def test_a_shared_title_becomes_a_title_chip_and_is_sent(self) -> None:
        out = run("aSharedTitleBecomesATitleChipAndIsSent")
        self.assertEqual(out["shown"], {"text": "Worth a read\nhttps://example.com/a", "chips": ["TitleA shared page"], "titleButtonHidden": True})
        self.assertEqual(out["posts"][0]["title"], "A shared page")
        self.assertEqual(out["posts"][0]["content"], "Worth a read\nhttps://example.com/a")

    def test_a_shared_title_is_cut_to_two_hundred_characters(self) -> None:
        self.assertEqual(run("aSharedTitleIsCutToTwoHundredCharacters")["length"], 200)


if __name__ == "__main__":
    unittest.main()
