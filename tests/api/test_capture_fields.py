"""The Add card's type list and type-ahead matching, run under Node (the module has no DOM)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path("app/web")

PRELUDE = """
const f = require("./app/web/capture-fields.js");
const TAGS = [{ name: "marketing", count: 14 }, { name: "market-research", count: 3 }, { name: "farming", count: 5 },
  { name: "supermarket", count: 1 }, { name: "smart-markets", count: 2 }, { name: "mar-a", count: 1 }, { name: "mar-b", count: 1 },
  { name: "mar-c", count: 1 }, { name: "mar-d", count: 1 }, { name: "ai", count: 9 }];
const PEOPLE = [{ name: "Naval Ravikant", count: 4 }, { name: "Paul Graham", count: 11 }, { name: "Ada Stone", count: 2 }];
const names = (rows) => rows.map((row) => row.text);
"""


def run(body: str):
    out = subprocess.run(["node", "-e", PRELUDE + body], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "needs node")
class CaptureFieldsTests(unittest.TestCase):
    def test_tags_list_prefix_matches_first_then_contains_at_most_six(self) -> None:
        out = run("""const r = f.suggestTags("mar", TAGS, []);
          console.log(JSON.stringify({ rows: names(r.rows), first: r.rows[0], create: r.create }));""")
        self.assertEqual(out["rows"], ["marketing", "market-research", "mar-a", "mar-b", "mar-c", "mar-d"])
        self.assertEqual(len(out["rows"]), 6)  # supermarket and smart-markets (contains) fall past the limit
        self.assertEqual(out["first"], {"text": "marketing", "count": 14, "at": 0, "length": 3})
        self.assertEqual(out["create"], "mar")

    def test_contains_matches_follow_the_prefix_ones(self) -> None:
        out = run("""console.log(JSON.stringify(names(f.suggestTags("market", TAGS.slice(0, 5), []).rows)));""")
        self.assertEqual(out, ["marketing", "market-research", "supermarket", "smart-markets"])

    def test_an_exact_existing_tag_offers_no_create_row(self) -> None:
        out = run("""console.log(JSON.stringify({ exact: f.suggestTags("marketing", TAGS, []).create, caps: f.suggestTags("AI", TAGS, []).create,
          onNote: f.suggestTags("brand-new", TAGS, ["brand-new"]).create, fresh: f.suggestTags("brand-new", TAGS, []).create }));""")
        self.assertEqual(out, {"exact": None, "caps": None, "onNote": None, "fresh": "brand-new"})

    def test_typed_tag_is_normalised_for_matching_and_creation(self) -> None:
        out = run("""const r = f.suggestTags("Mar ", TAGS, []);
          console.log(JSON.stringify({ create: r.create, first: r.rows[0].text, hash: f.normalizeTag("#AI"), spaced: f.normalizeTag("  big   idea ") }));""")
        self.assertEqual(out["create"], "mar")
        self.assertEqual(out["first"], "marketing")
        self.assertEqual(out["hash"], {"tag": "ai", "valid": True})
        self.assertEqual(out["spaced"], {"tag": "big idea", "valid": True})

    def test_invalid_tag_text_offers_no_create_row(self) -> None:
        out = run("""console.log(JSON.stringify({ bang: f.suggestTags("#!", TAGS, []), tooLong: f.suggestTags("a".repeat(41), TAGS, []).create,
          norm: f.normalizeTag("#!") }));""")
        self.assertEqual(out["bang"], {"rows": [], "create": None})
        self.assertIsNone(out["tooLong"])
        self.assertEqual(out["norm"], {"tag": "!", "valid": False})

    def test_an_empty_tag_field_lists_nothing(self) -> None:
        out = run("""console.log(JSON.stringify([f.suggestTags("", TAGS, []), f.suggestTags("   ", TAGS, []), f.suggestTags("#", TAGS, [])]));""")
        self.assertEqual(out, [{"rows": [], "create": None}] * 3)

    def test_tags_already_on_the_note_are_not_offered_again(self) -> None:
        out = run("""console.log(JSON.stringify(names(f.suggestTags("mar", TAGS, ["Marketing"]).rows).slice(0, 2)));""")
        self.assertEqual(out, ["market-research", "mar-a"])

    def test_author_nav_suggests_naval_ravikant_and_offers_add(self) -> None:
        out = run("""const r = f.suggestPeople("nav", PEOPLE, []);
          console.log(JSON.stringify({ rows: r.rows, create: r.create, inside: names(f.suggestPeople("rav", PEOPLE, []).rows),
            exact: f.suggestPeople("paul graham", PEOPLE, []).create, taken: f.suggestPeople("Ada Stone", PEOPLE.slice(2), ["Ada Stone"]),
            tidy: f.suggestPeople("  New   Name ", PEOPLE, []).create, empty: f.suggestPeople("", PEOPLE, []),
            long: f.suggestPeople("x".repeat(121), PEOPLE, []).create }));""")
        self.assertEqual(out["rows"], [{"text": "Naval Ravikant", "count": 4, "at": 0, "length": 3}])
        self.assertEqual(out["create"], "nav")
        self.assertEqual(out["inside"], ["Naval Ravikant"])
        self.assertIsNone(out["exact"])
        self.assertEqual(out["taken"], {"rows": [], "create": None})
        self.assertEqual(out["tidy"], "New Name")
        self.assertEqual(out["empty"], {"rows": [], "create": None})
        self.assertIsNone(out["long"])

    def test_the_type_list_has_the_ten_names_and_no_create_row(self) -> None:
        out = run("""console.log(JSON.stringify({ rows: f.typeRows(), types: f.TYPES }));""")
        self.assertEqual([row["text"] for row in out["rows"]], ["Article", "Book", "Book highlights", "Meeting", "Note", "Other", "Post",
                                                              "Quote", "Reference", "Transcript"])
        self.assertTrue(all(row["count"] == 0 for row in out["rows"]))
        self.assertEqual(len(out["types"]), 10)

    def test_labels_map_to_the_stored_values(self) -> None:
        out = run("""console.log(JSON.stringify(Object.fromEntries(f.TYPES.map((t) => [t.label, [f.typeValue(t.label), f.typeLabel(t.value)]]))));""")
        self.assertEqual({label: pair[0] for label, pair in out.items()}, {
            "Article": "article", "Book": "book", "Book highlights": "highlights", "Meeting": "meeting", "Note": "note",
            "Other": "other", "Post": "social_post", "Quote": "quote", "Reference": "reference", "Transcript": "transcript"})
        self.assertTrue(all(pair[1] == label for label, pair in out.items()))
        self.assertEqual(run("""console.log(JSON.stringify([f.typeValue("Nope"), f.typeLabel("nope")]));"""), ["", ""])

    def test_the_stored_values_are_the_classifiers_one_list_of_note_types(self) -> None:
        from app.ingestion.properties import DEFAULT_SOURCE_TYPES
        stored = {entry["value"] for entry in run("""console.log(JSON.stringify(f.TYPES));""")}
        self.assertEqual(stored, set(DEFAULT_SOURCE_TYPES))

    def test_the_module_is_loaded_by_the_page_and_the_offline_shell(self) -> None:
        self.assertIn('"/static/capture-fields.js"', (WEB / "sw.js").read_text(encoding="utf-8"))
        self.assertIn("/static/capture-fields.js", (WEB / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
