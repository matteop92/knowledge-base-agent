"""The Sources page's matching rules, counts and suggestions, run under Node (the module has no DOM)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path("app/web")

PRELUDE = """
const f = require("./app/web/source-filter.js");
const NOW = new Date(2026, 9, 28, 12);
const row = (id, type, title, authors, guests, tags, added) => ({ id, type, title, authors, guests, tags, added });
const SOURCES = [
  row(1, "book", "Craft of Pears", ["Ada Stone"], [], [], "2026-10-27"),
  row(2, "transcript", "Orchard talk", ["Ben Hart"], ["Ada Stone"], ["farming", "markets"], "2026-10-25"),
  row(3, "note", "Market notes", [], [], ["markets", "maps"], "2026-09-01"),
  row(4, "note", "Map making", [], [], ["maps", "markets", "maple"], "2025-03-04"),
  row(5, "article", "Weather and pears", ["Cy Lane"], [], ["farming"], "2025-11-30"),
];
const ids = (state) => f.filtered(SOURCES, state, NOW).map((s) => s.id);
const make = (patch) => ({ ...f.empty(), ...patch });
"""


def run(body: str):
    out = subprocess.run(["node", "-e", PRELUDE + body], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "needs node")
class SourceFilterTests(unittest.TestCase):
    def test_tags_all_people_any_types_any_titles_all(self) -> None:
        out = run("""console.log(JSON.stringify({
          tags: ids(make({ tags: ["markets", "maps"] })),
          people: ids(make({ people: ["Ben Hart", "Cy Lane"] })),
          types: ids(make({ types: ["note", "article"] })),
          titles: ids(make({ titles: ["pears", "weather"] })),
        }));""")
        self.assertEqual(out, {"tags": [3, 4], "people": [2, 5], "types": [3, 4, 5], "titles": [5]})

    def test_people_covers_guests_and_authors(self) -> None:
        out = run("""console.log(JSON.stringify(ids(make({ people: ["Ada Stone"] }))));""")
        self.assertEqual(out, [1, 2])

    def test_date_presets_years_and_ranges(self) -> None:
        out = run("""console.log(JSON.stringify({
          week: ids(make({ date: { preset: "week" } })), month: ids(make({ date: { preset: "month" } })),
          year: ids(make({ date: { preset: "year" } })), y2025: ids(make({ date: { year: "2025" } })),
          range: ids(make({ date: { from: "2025-03-05", to: "2025-12-31" } })),
        }));""")
        self.assertEqual(out, {"week": [1, 2], "month": [1, 2], "year": [1, 2, 3, 5], "y2025": [4, 5], "range": [5]})

    def test_menu_counts_ignore_their_own_filter_but_respect_the_others(self) -> None:
        out = run("""const c = f.counts(make({ tags: ["farming"], types: ["transcript"] }), SOURCES, NOW);
          console.log(JSON.stringify({ tags: c.tags.map((r) => [r.value, r.count]), types: c.types.map((r) => [r.value, r.count]),
            dates: c.dates.map((r) => [r.key, r.count]) }));""")
        # Tags: only transcripts count (type is applied); farming stays listed because it is selected.
        self.assertEqual(out["tags"], [["farming", 1], ["markets", 1]])
        # Types: only sources tagged farming count (tag is applied, type itself is cleared).
        self.assertEqual(out["types"], [["article", 1], ["transcript", 1]])
        self.assertEqual(out["dates"][0], ["any", 1])

    def test_tag_rows_count_what_would_remain_with_every_selected_tag_applied(self) -> None:
        out = run("""const c = f.counts(make({ tags: ["markets", "maps"] }), SOURCES, NOW);
          console.log(JSON.stringify(c.tags.map((r) => [r.value, r.count])));""")
        # Sources 3 and 4 carry both tags; "maple" is only on 4, "farming" on neither of them.
        self.assertEqual(sorted(out), [["maple", 1], ["maps", 2], ["markets", 2]])

    def test_suggestions_are_capped_sorted_by_count_and_hide_zero_rows(self) -> None:
        out = run("""const groups = f.suggest("ma", make(), SOURCES, NOW);
          console.log(JSON.stringify(groups.map((g) => [g.kind, g.items.map((i) => [i.value, i.count])])));""")
        kinds = [group[0] for group in out]
        self.assertEqual(kinds[-1], "title")
        tags = dict(out)["tag"]
        self.assertEqual(tags, [["markets", 3], ["maps", 2], ["maple", 1]])
        self.assertTrue(all(count > 0 for kind, items in out if kind != "title" for _, count in items))

    def test_a_year_and_past_offer_date_suggestions_with_counts(self) -> None:
        out = run("""const year = f.suggest("2025", make(), SOURCES, NOW).find((g) => g.kind === "date");
          const past = f.suggest("past", make(), SOURCES, NOW).find((g) => g.kind === "date");
          console.log(JSON.stringify({ year: year.items.map((i) => [i.label, i.count]), past: past.items.map((i) => [i.label, i.count]) }));""")
        self.assertEqual(out["year"], [["Added in 2025", 2]])
        self.assertEqual([label for label, _ in out["past"]], ["Added past week", "Added past 30 days", "Added past year"])

    def test_selected_values_are_not_suggested_again_and_titles_always_come_last(self) -> None:
        out = run("""const groups = f.suggest("markets", make({ tags: ["markets"] }), SOURCES, NOW);
          console.log(JSON.stringify(groups.map((g) => g.kind)));""")
        self.assertEqual(out, ["title"])

    def test_tokens_follow_the_drawn_order_and_backspace_removes_the_last(self) -> None:
        out = run("""let s = make({ titles: ["pear"], tags: ["maps"], types: ["note"], people: ["Ada Stone"], date: { preset: "week" } });
          const order = f.tokens(s).map((t) => t.key + " " + t.text);
          s = f.removeLast(s);
          console.log(JSON.stringify({ order, afterBackspace: f.tokens(s).map((t) => t.key), action: f.keyAction("Backspace", { empty: true, count: 0, index: -1 }) }));""")
        self.assertEqual(out["order"], ["Type notes", "Tag maps", "Person Ada Stone", "Added past week", "Title “pear”"])
        self.assertEqual(out["afterBackspace"], ["Type", "Tag", "Person", "Added"])
        self.assertEqual(out["action"], {"type": "remove-last"})

    def test_keys_move_add_and_close(self) -> None:
        out = run("""console.log(JSON.stringify([
          f.keyAction("ArrowDown", { empty: false, count: 3, index: 2 }), f.keyAction("ArrowUp", { empty: false, count: 3, index: 0 }),
          f.keyAction("Enter", { empty: false, count: 3, index: 1 }), f.keyAction("Escape", { empty: false, count: 0, index: -1 }),
          f.keyAction("Backspace", { empty: false, count: 0, index: -1 })]));""")
        self.assertEqual([item["type"] for item in out], ["move", "move", "add", "close", "none"])
        self.assertEqual((out[0]["index"], out[1]["index"], out[2]["index"]), (0, 2, 1))

    def test_clearing_resets_every_key(self) -> None:
        out = run("""console.log(JSON.stringify(f.tokens(f.empty()).length));""")
        self.assertEqual(out, 0)

    def test_the_module_is_in_the_offline_shell_list(self) -> None:
        worker = (WEB / "sw.js").read_text(encoding="utf-8")
        self.assertIn('"/static/source-filter.js"', worker)
        self.assertRegex((WEB / "index.html").read_text(encoding="utf-8"), re.escape("/static/source-filter.js"))


if __name__ == "__main__":
    unittest.main()
