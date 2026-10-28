from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

WEB = Path("app/web")
# Every font size the app uses, by role (AGENTS.md "Web app type scale"); 40 and 44 are the quote mark on a cited passage. A new size is a deliberate change to this set.
TYPE_SCALE = {10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15, 16, 17, 17.5, 18, 18.5, 19, 24, 26, 27, 30, 32, 36, 40, 44, 46}


class ShellSourceTests(unittest.TestCase):
    def test_untrusted_text_is_never_inserted_as_html(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")
        for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(unsafe, script)
        self.assertIn('url.startsWith("obsidian://")', script)

    def test_capture_and_worker_scripts_never_insert_html(self) -> None:
        for name in ("capture.js", "settings.js", "sources.js", "source-filter.js", "sw.js"):
            script = (WEB / name).read_text(encoding="utf-8")
            for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
                with self.subTest(name=name, unsafe=unsafe):
                    self.assertNotIn(unsafe, script)

    def test_the_app_is_a_share_target_for_screenshots_text_and_markdown(self) -> None:
        import json
        share = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))["share_target"]
        self.assertEqual((share["action"], share["method"], share["enctype"]), ("/share", "POST", "multipart/form-data"))
        self.assertIn("image/png", share["params"]["files"][0]["accept"])
        self.assertIn(".md", share["params"]["files"][0]["accept"])

    def test_page_has_no_inline_script(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertNotIn("onclick=", page)

    def test_page_has_no_inline_styles_and_scripts_set_none(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(page, r"\sstyle=")
        for name in ("app.js", "capture.js", "settings.js", "sources.js"):
            script = (WEB / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertNotIn('setAttribute("style"', script)
                self.assertNotIn("cssText", script)

    def test_shell_cache_is_bumped_and_the_sidebar_choice_is_remembered(self) -> None:
        worker = (WEB / "sw.js").read_text(encoding="utf-8")
        self.assertIn('const CACHE = "ka-shell-v22"', worker)
        # A deploy must reach an installed app: never answer from the browser's own HTTP cache.
        self.assertIn('fetch(event.request, { cache: "no-cache" })', worker)
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertEqual(script.count("SIDEBAR_KEY"), 4)  # declaration, read at start, write on collapse and on expand

    def test_sign_out_clears_the_add_box_and_a_shared_item_opens_add(self) -> None:
        capture = (WEB / "capture.js").read_text(encoding="utf-8")
        reset = capture[capture.index("function reset()"):capture.index("async function openGap")]
        for field in ("capture-text", "capture-title", "capture-tags", "capture-file", "capture-preview", "capture-list"):
            self.assertIn(field, reset)
        self.assertIn('await setView("add");', capture[capture.index("async function takeShared"):])
        self.assertIn('await cache.delete("/share/meta")', capture)

    def test_sign_out_only_reports_success_after_the_server_answers(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")
        body = script[script.index("async function signOut"):script.index("function autosize")]
        self.assertNotIn(".catch(() => {})", body)
        self.assertIn("Could not sign out", body)
        self.assertLess(body.index("await api("), body.index("showSignIn("))
        self.assertEqual((WEB / "index.html").read_text(encoding="utf-8").count("sign-out-message"), 1)

    def test_every_element_the_scripts_look_up_exists_in_the_page(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "capture.js", "settings.js", "sources.js"):
            script = (WEB / name).read_text(encoding="utf-8")
            for element_id in sorted(set(re.findall(r'\$\("([a-z-]+)"\)', script))):
                with self.subTest(name=name, element_id=element_id):
                    self.assertIn(f'id="{element_id}"', page)

    def test_styles_use_only_local_fonts_and_no_remote_imports(self) -> None:
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.assertNotIn("@import", css)
        self.assertNotIn("http", css)
        for url in re.findall(r"url\(([^)]+)\)", css):
            self.assertTrue(url.strip("'\" ").startswith("/static/fonts/"), url)
        self.assertIn("Newsreader", css)
        self.assertIn("Geist", css)

    def test_every_linked_static_file_is_in_the_offline_shell_list(self) -> None:
        worker = (WEB / "sw.js").read_text(encoding="utf-8")
        shell = re.search(r"const SHELL = \[(.*?)\];", worker, re.S).group(1)
        listed = set(re.findall(r'"([^"]+)"', shell))
        page = (WEB / "index.html").read_text(encoding="utf-8")
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        linked = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', page)) | set(re.findall(r"url\((/static/[^)]+)\)", css))
        self.assertTrue(linked)
        self.assertEqual(linked - listed, set())
        for path in linked:
            self.assertTrue((WEB / path.removeprefix("/static/")).is_file(), path)

    def test_navigation_is_three_tabs_with_names_and_one_back_handler(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        # One sidebar serves both widths (a rail on phones), so each tab appears once.
        self.assertEqual(len(re.findall(r'data-tab="ask"', page)), 1)
        self.assertEqual(len(re.findall(r'data-tab="add"', page)), 1)
        self.assertEqual(len(re.findall(r'data-tab="sources"', page)), 1)
        self.assertEqual(len(re.findall(r'id="nav-button"', page)), 1)  # the one icon at the top left on phones
        self.assertNotIn('class="tabbar"', page)
        # Sign-out options live in the account menu opened from the circle at the foot of the sidebar;
        # There is no separate New question button: Ask starts a fresh question.
        self.assertRegex(page, r'id="account-button"[^>]*aria-expanded="false"')
        self.assertNotIn('id="new-question"', page)
        self.assertNotIn("new-pill", page)
        self.assertIn('if (tab.dataset.tab === "ask") newQuestion();', script)
        self.assertEqual(script.count('addEventListener("popstate"'), 1)
        # The back-button contract: opening pushes one history entry, closing goes back once, and only
        # the popstate handler tears the sheet down (so a double tap cannot pop two entries).
        self.assertIn("history.pushState", script)
        self.assertEqual(script.count("history.back()"), 2)  # one for a sheet, one for the phone drawer
        self.assertIn("if (sheet.closing) return;", script)
        self.assertEqual(script.count("teardownSheet()"), 3)  # definition, the popstate handler, the no-Back fallback
        self.assertNotIn("capture-close", page)
        self.assertNotIn('id="sidebar-add"', page)
        # Every icon-only button needs an accessible name.
        for match in re.finditer(r"<button([^>]*)>\s*<svg[^>]*>.*?</svg>\s*</button>", page, re.S):
            self.assertIn("aria-label", match.group(1))
        # The collapsed sidebar hides text labels, so its tab and action buttons keep their own names.
        for match in re.finditer(r"<button([^>]*class=\"side-item[^>]*)>", page):
            self.assertIn("aria-label", match.group(1))


class ReleaseBTests(unittest.TestCase):
    def test_run_problem_messages_match_what_the_agent_writes(self) -> None:
        # A run problem is not a knowledge gap; the browser recognises it by the agent's own wording.
        harness = Path("app/agent/harness.py").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        for message in re.findall(r'stopped\("([^"]+)"', harness):
            with self.subTest(message=message):
                self.assertIn(f'"{message}"', script)
        self.assertIn('startsWith("Model stopped:")', script)
        self.assertIn("Unresolved need", script)  # the internal code prefix is stripped

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_gap_clean_up_runs_as_written(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8").replace("\r", "")
        problems = re.search(r"const RUN_PROBLEMS = .*?\];", script, re.S).group(0)
        function = re.search(r"function splitGaps.*?\n}\n", script, re.S).group(0)
        probe = problems + function + """
console.log(JSON.stringify(splitGaps([
  "Unresolved need N2: Find advice on hiring", "No note covers X.", "N4: Short one", "Unresolved need N3:",
  "Investigation budget ended before a valid grounded answer.", "Model stopped: timeout."])));"""
        out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True).stdout
        import json
        result = json.loads(out)
        self.assertEqual(result["gaps"], ["Find advice on hiring", "No note covers X.", "Short one"])
        self.assertEqual(result["problems"], ["Investigation budget ended before a valid grounded answer.", "Model stopped: timeout."])

    def test_click_outside_reads_the_event_path_not_the_live_dom(self) -> None:
        # A source number inside the panel is replaced when it is clicked; the outside-click check must still see it.
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("composedPath()", script)
        self.assertNotIn('event.target.closest("#sheet', script)

    def test_notices_live_outside_the_ask_tab_so_the_add_tab_shows_them(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertLess(page.index('id="banner"'), page.index('id="view-ask"'))
        self.assertLess(page.index('id="status"'), page.index('id="view-ask"'))
        self.assertNotIn('$("composer").before', script)
        self.assertNotIn('announce(error.message, true)', script)

    def test_every_finished_answer_keeps_its_feedback_button(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('aria-label="This answer was not good"', page)
        # The answer row is shown whether or not there are sources or gaps.
        row = script[script.index("function renderRow"):script.index("// Draws the answer")]
        self.assertIn('.answer-row").hidden = false', row)
        self.assertIn("Answered with gaps", script)  # kept for screen readers

    def test_add_tab_detects_kinds_and_has_a_visible_attach_button(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        capture = (WEB / "capture.js").read_text(encoding="utf-8")
        for label in ("YouTube video", "Web link", '"Text"', "Screenshot", "Markdown file", "Text file"):
            with self.subTest(label=label):
                self.assertIn(label, capture)
        self.assertIn('id="capture-attach"', page)
        self.assertRegex(page, r'<details id="capture-more"')
        self.assertRegex(page, r'id="capture-save"[^>]*disabled')


def _luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class ReleaseCTests(unittest.TestCase):
    def test_text_colours_meet_wcag_aa_on_every_ground_they_sit_on(self) -> None:
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        tokens = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;", css))
        grounds = ("ground", "sidebar", "surface", "paper", "chip", "pill", "gap-bg", "row-active")
        for ink in ("ink", "ink-soft", "muted", "accent", "danger"):
            for ground in grounds:
                with self.subTest(ink=ink, ground=ground):
                    self.assertGreaterEqual(_contrast(tokens[ink], tokens[ground]), 4.5)
        # Placeholders sit on the paper box and on the grey fields.
        for ground in ("paper", "ground"):
            with self.subTest(ink="placeholder", ground=ground):
                self.assertGreaterEqual(_contrast(tokens["placeholder"], tokens[ground]), 4.5)
        # State is shown by fill alone: the accent fill must read against the chip and pill it replaces.
        for ground in ("chip", "pill", "gap-bg"):
            with self.subTest(fill="accent", ground=ground):
                self.assertGreaterEqual(_contrast(tokens["accent"], tokens[ground]), 3)
        # The phone toast.
        for ink in ("toast-ink", "toast-sub", "toast-action"):
            with self.subTest(ink=ink, ground="toast"):
                self.assertGreaterEqual(_contrast(tokens[ink], tokens["toast"]), 4.5)
        self.assertGreaterEqual(_contrast(tokens["accent-ink"], tokens["accent"]), 4.5)
        self.assertGreaterEqual(_contrast(tokens["warn-ink"], tokens["warn"]), 4.5)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_source_numbering_runs_as_written(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8").replace("\r", "")
        function = "".join(re.search(rf"function {name}.*?\n}}\n", script, re.S).group(0)
                           for name in ("appendEmphasis", "appendBlocks", "renderAnswer"))
        probe = """
const make = (tag) => ({ tag, children: [], attrs: {}, className: "", textContent: "",
  append(...n) { this.children.push(...n); }, replaceChildren(...n) { this.children = n; },
  setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {} });
const document = { createElement: make, createTextNode: (text) => ({ text }) };
""" + function + """
const container = make("div");
const element = { querySelector: () => container };
const receipt = {
  answer: ["A [E1.1] and B [E2.1] again A [E1.2].", "Unknown [E9.9] stays text. A twice [E1.1]."].join(String.fromCharCode(10, 10)),
  citations: {
    "E1.1": { relative_path: "Knowledge/a.md", title: "Note A", text: "first", obsidian_url: "obsidian://open?x=1" },
    "E1.2": { relative_path: "Knowledge/a.md", title: "Note A", text: "second" },
    "E2.1": { relative_path: "Other/b.md", text: "bee" },
  },
};
const sources = renderAnswer(element, receipt, "t1");
const markers = container.children.map((p) => p.children.filter((c) => c.className === "marker").map((c) => c.textContent));
const texts = container.children.map((p) => p.children.filter((c) => c.text !== undefined).map((c) => c.text).join(""));
console.log(JSON.stringify({ sources, markers, texts }));"""
        out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True).stdout
        import json
        result = json.loads(out)
        # Passages from one file share one number, in order of first appearance.
        self.assertEqual([(s["number"], s["title"], s["folder"]) for s in result["sources"]],
                         [(1, "Note A", "Knowledge"), (2, "Other/b.md", "Other")])
        self.assertEqual(result["sources"][0]["passages"], ["first", "second"])
        self.assertEqual(result["markers"], [["1.1", "2", "1.2"], ["1.1"]])
        self.assertIn("Unknown [E9.9] stays text.", result["texts"][1])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_answer_lists_and_emphasis_are_drawn_as_elements(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8").replace("\r", "")
        function = "".join(re.search(rf"function {name}.*?\n}}\n", script, re.S).group(0)
                           for name in ("appendEmphasis", "appendBlocks", "renderAnswer"))
        probe = """
const make = (tag) => ({ tag, children: [], attrs: {}, className: "", textContent: "",
  append(...n) { this.children.push(...n); }, replaceChildren(...n) { this.children = n; },
  setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {} });
const document = { createElement: make, createTextNode: (text) => ({ text }) };
""" + function + """
const container = make("div");
const nl = String.fromCharCode(10);
const receipt = { answer: ["You have 2 sources:", "- *Title A*", "  Ada; note [E1.0]", "- **Title B**", "", "Closing *line*. [E1.0][E1.0] [E1.0]", "", "1. First", "", "2. Second"].join(nl),
  citations: { "E1.0": { relative_path: "K/a.md", title: "A", text: "t" } } };
renderAnswer({ querySelector: () => container }, receipt, "t1");
const shape = (n) => n.text !== undefined ? n.text : n.textContent ? [n.tag, n.textContent] : [n.tag, ...n.children.map(shape)];
console.log(JSON.stringify(container.children.map(shape)));"""
        out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True).stdout
        import json
        self.assertEqual(json.loads(out), [
            ["p", "You have 2 sources:"],
            ["ul", ["li", ["em", "Title A"], ["span", "Ada; note ", ["button", "1"]]], ["li", ["strong", "Title B"]]],
            ["p", "Closing ", ["em", "line"], ". ", ["button", "1"], " ", ["button", "1"]],
            ["ol", ["li", "First"], ["li", "Second"]],
        ])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_cited_passage_keeps_dash_lists_and_wrapped_italics(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8").replace("\r", "")
        function = "".join(re.search(rf"function {name}.*?\n}}\n", script, re.S).group(0)
                           for name in ("appendEmphasis", "appendBlocks"))
        probe = """
const make = (tag) => ({ tag, children: [], attrs: {}, className: "", textContent: "", append(...n) { this.children.push(...n); },
  setAttribute(k, v) { this.attrs[k] = v; } });
const document = { createElement: make, createTextNode: (text) => ({ text }) };
""" + function + """
const box = make("blockquote");
const nl = String.fromCharCode(10);
appendBlocks(box, ["- Is it true?", "- *Am I willing to learn", "    about this?*", "", "**Tim:** done", "", "Math 2 * 3 * 4", "", "3. Third", "", "4. Fourth"].join(nl), appendEmphasis, false);
const shape = (n) => n.text !== undefined ? n.text : n.textContent ? [n.tag, n.textContent] : [n.tag, ...n.children.map(shape)];
console.log(JSON.stringify({ shape: box.children.map(shape), numbers: box.children[3].children.map((li) => li.attrs["data-n"]) }));"""
        out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True).stdout
        import json
        result = json.loads(out)
        self.assertEqual(result["shape"], [
            ["ul", ["li", "Is it true?"], ["li", ["em", "Am I willing to learn about this?"]]],
            ["p", ["strong", "Tim:"], " done"],
            ["p", "Math 2 * 3 * 4"],
            ["ol", ["li", "Third"], ["li", "Fourth"]],
        ])
        # A numbered list keeps the numbers its text gave it, and lone asterisks stay as written.
        self.assertEqual(result["numbers"], ["3", "4"])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_add_box_classification_runs_as_written(self) -> None:
        capture = (WEB / "capture.js").read_text(encoding="utf-8").replace("\r", "")
        youtube = re.search(r"const YOUTUBE = .*?;", capture).group(0)
        function = re.search(r"function classify.*?\n  }\n", capture, re.S).group(0)
        probe = youtube + "let fileLabel = null; let kind = 'text'; const $ = () => ({ value: kind });" + function + """
const out = {};
for (const text of ["https://youtu.be/abc", "https://www.youtube.com/watch?v=1", "see https://youtu.be/abc",
                    "https://example.com/page", "https://example.com a b", "plain words"]) out[text] = classify(text);
fileLabel = "Markdown file"; kind = "markdown"; out.md = classify("# Title");
kind = "text"; out.txt = classify("some text"); out.ytInFile = classify("https://youtu.be/abc");
console.log(JSON.stringify(out));"""
        import json
        out = json.loads(subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, {
            "https://youtu.be/abc": "youtube", "https://www.youtube.com/watch?v=1": "youtube",
            "see https://youtu.be/abc": "text", "https://example.com/page": "link",
            "https://example.com a b": "text", "plain words": "text",
            "md": "markdown", "txt": "textfile", "ytInFile": "youtube"})

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_phone_drawer_takes_one_history_entry_and_back_closes_it(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8").replace("\r", "")
        function = re.search(r"function setNavOpen.*?\n}\n", script, re.S).group(0)
        probe = """
const log = [];
const el = () => ({ hidden: false, inert: false, contains: () => false, setAttribute() {}, focus() {} });
const els = {}; const $ = (id) => (els[id] ||= el());
const document = { activeElement: null, querySelector: () => el(), body: { classList: { toggle() {} } } };
const phone = { matches: true };
const history = { pushState: () => log.push("push"), back: () => log.push("back") };
const navState = { open: false, historyOpen: true, pushed: false };
function setAccountOpen() {} function syncHistory() {}
""" + function + """
setNavOpen(true); setNavOpen(true);   // opening twice still pushes once
setNavOpen(false); setNavOpen(false); // closing by hand goes back once
setNavOpen(true); setNavOpen(false, true); // Back already happened: no second Back
phone.matches = false; setNavOpen(true); setNavOpen(false); // desktop never touches history
console.log(JSON.stringify(log));"""
        import json
        out = json.loads(subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, ["push", "back", "push"])
        self.assertIn("else if (navState.open) setNavOpen(false, true);", script)


def _rules(css: str, *names: str) -> str:
    """The bodies of every rule whose selector list mentions one of `names`."""
    return " ".join(body for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", css) if any(name in selector for name in names))


class PaperRefinementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.css = (WEB / "styles.css").read_text(encoding="utf-8")
        self.app = (WEB / "app.js").read_text(encoding="utf-8").replace("\r", "")
        self.capture = (WEB / "capture.js").read_text(encoding="utf-8").replace("\r", "")
        self.page = (WEB / "index.html").read_text(encoding="utf-8")

    def _node(self, probe: str):
        import json
        return json.loads(subprocess.run(["node", "-e", probe], capture_output=True, text=True, encoding="utf-8", check=True).stdout)

    def _top_level(self, *names: str) -> str:
        # Top-level functions of app.js, copied out so Node runs them exactly as shipped.
        return "\n".join(re.search(rf"function {name}\(.*?\n}}\n", self.app, re.S).group(0) for name in names)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_history_groups_by_calendar_day_and_skips_empty_groups(self) -> None:
        probe = self._top_level("dayStart", "daysAgo", "ageGroup", "groupByAge") + """
const now = new Date(2026, 9, 21, 15, 0);
const at = (y, m, d, h) => new Date(y, m, d, h, 0).toISOString();
const item = (id, iso) => ({ id, last_activity_at: iso });
const summary = (groups) => groups.map((g) => g.label + ":" + g.items.map((i) => i.id).join(","));
console.log(JSON.stringify({
  mixed: summary(groupByAge([item("a", at(2026, 9, 21, 9)), item("b", at(2026, 9, 20, 23)), item("c", at(2026, 9, 16, 12)),
                             item("d", at(2026, 9, 13, 12)), item("e", "not a date")], now)),
  midnight: summary(groupByAge([item("a", at(2026, 9, 21, 0)), item("b", at(2026, 9, 20, 23))], now)),
  onlyEarlier: summary(groupByAge([item("a", at(2026, 8, 1, 12))], now)),
  none: groupByAge([], now),
}));"""
        out = self._node(probe)
        self.assertEqual(out["mixed"], ["Today:a", "This week:b,c", "Earlier:d,e"])
        self.assertEqual(out["midnight"], ["Today:a", "This week:b"])
        self.assertEqual(out["onlyEarlier"], ["Earlier:a"])
        self.assertEqual(out["none"], [])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_turn_time_says_today_yesterday_then_the_weekday(self) -> None:
        probe = self._top_level("dayStart", "daysAgo", "formatTurnTime") + """
const now = new Date(2026, 9, 21, 15, 0);
const f = (y, m, d) => formatTurnTime(new Date(y, m, d, 9, 41).toISOString(), now);
console.log(JSON.stringify({ today: f(2026, 9, 21), yesterday: f(2026, 9, 20), week: f(2026, 9, 18), old: f(2026, 9, 1), bad: formatTurnTime("x", now) }));"""
        out = self._node(probe)
        self.assertTrue(out["today"].startswith("Today, "))
        self.assertTrue(out["yesterday"].startswith("Yesterday, "))
        self.assertRegex(out["week"], r"^\w+, ")
        self.assertNotRegex(out["week"], r"^(Today|Yesterday)")
        self.assertRegex(out["old"], r"^[\w ]+, ")
        self.assertEqual(out["bad"], "")

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_stepping_stops_at_the_ends_and_swipes_ignore_the_screen_edge(self) -> None:
        probe = self._top_level("stepIndex", "swipeStep") + """
console.log(JSON.stringify({
  steps: [stepIndex(0, -1, 3), stepIndex(0, 1, 3), stepIndex(2, 1, 3), stepIndex(2, -1, 3), stepIndex(0, 1, 1)],
  swipes: [swipeStep(-60, 5, 200, 390), swipeStep(60, 5, 200, 390), swipeStep(20, 0, 200, 390), swipeStep(-60, 80, 200, 390),
           swipeStep(-60, 0, 10, 390), swipeStep(60, 0, 380, 390)],
}));"""
        out = self._node(probe)
        self.assertEqual(out["steps"], [-1, 1, -1, 1, -1])
        self.assertEqual(out["swipes"], [1, -1, 0, 0, 0, 0])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_recent_times_and_tag_chips_run_as_written(self) -> None:
        age = re.search(r"function formatAge.*?\n  }\n", self.capture, re.S).group(0)
        tags = re.search(r"function mergeTags.*?\n  }\n", self.capture, re.S).group(0)
        probe = "const MAX_TAGS = 10;\n" + age + tags + """
const now = new Date(2026, 9, 21, 15, 0, 0);
const ago = (ms) => new Date(now.getTime() - ms).toISOString();
const MIN = 60000, HOUR = 60 * MIN, DAY = 24 * HOUR;
console.log(JSON.stringify({
  ages: [ago(20000), ago(12 * MIN), ago(61 * MIN), ago(26 * HOUR), ago(6 * DAY), ago(9 * DAY), ago(-5 * MIN), "x"].map((iso) => formatAge(iso, now)),
  merged: [mergeTags([], "a, b"), mergeTags([], " A,a "), mergeTags([], ",,,"), mergeTags(["x"], "X, y\\nz"), mergeTags([], "a".repeat(60)), mergeTags([], "1,2,3,4,5,6,7,8,9,10,11")],
}));"""
        out = self._node(probe)
        self.assertEqual(out["ages"][:5], ["now", "12 min", "1 h", "1 d", "6 d"])
        self.assertRegex(out["ages"][5], r"\w")  # a date from seven days on
        self.assertEqual(out["ages"][6:], ["now", ""])
        self.assertEqual(out["merged"][0], {"tags": ["a", "b"], "duplicates": [], "full": False})
        self.assertEqual(out["merged"][1], {"tags": ["A"], "duplicates": ["A"], "full": False})
        self.assertEqual(out["merged"][2], {"tags": [], "duplicates": [], "full": False})
        self.assertEqual(out["merged"][3], {"tags": ["x", "y", "z"], "duplicates": ["x"], "full": False})
        self.assertEqual(out["merged"][4]["tags"], ["a" * 60])
        # The server takes ten tags; the eleventh is left out and the field says so.
        self.assertEqual((len(out["merged"][5]["tags"]), out["merged"][5]["full"]), (10, True))

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_active_fills_follow_the_open_source_gaps_or_tag_and_clear_on_close(self) -> None:
        probe = """
class El {
  constructor(text) { this.on = new Set(); this.attrs = {}; this.textContent = text || ""; this.dataset = {}; }
  get classList() { return { add: (c) => this.on.add(c), remove: (c) => this.on.delete(c) }; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
}
const markers = ["1", "2", "1.2"].map((t) => { const e = new El(t); e.attrs["data-source"] = t.split(".")[0]; return e; });
const numbers = ["1", "2", "3"].map((t) => new El(t));
const chip = new El("2");
const rows = ["a", "b"].map((name) => Object.assign(new El(""), { dataset: { tag: name } }));
const turn = { querySelector: () => chip, querySelectorAll: () => [...markers, ...numbers] };
const everything = [...markers, ...numbers, chip, ...rows];
const document = {
  querySelectorAll: (selector) => selector === ".is-active" ? everything.filter((e) => e.on.has("is-active")) : selector === ".tag-row" ? rows : [],
  querySelector: () => turn,
};
const CSS = { escape: (x) => x };
const sheet = { mark: null };
""" + self._top_level("syncActive") + """
const lit = () => everything.filter((e) => e.on.has("is-active")).map((e) => everything.indexOf(e));
const out = {};
sheet.mark = { kind: "source", id: "t", index: 0 }; syncActive(); out.firstSource = lit();
sheet.mark = { kind: "source", id: "t", index: 1 }; syncActive(); out.stepped = lit();
sheet.mark = { kind: "gaps", id: "t" }; syncActive(); out.gaps = lit();
sheet.mark = { kind: "tag", name: "b" }; syncActive(); out.tag = lit(); out.current = rows[1].attrs["aria-current"] ?? null;
sheet.mark = null; syncActive(); out.closed = lit(); out.currentGone = rows[1].attrs["aria-current"] ?? null;
console.log(JSON.stringify(out));"""
        out = self._node(probe)
        # indexes: markers 0-2, numbers 3-5, chip 6, rows 7-8
        self.assertEqual(out["firstSource"], [0, 2, 3])  # every marker for source 1, and its number
        self.assertEqual(out["stepped"], [1, 4])  # stepping moves the fill; nothing stale is left
        self.assertEqual(out["gaps"], [6])
        self.assertEqual((out["tag"], out["current"]), ([8], "true"))
        self.assertEqual((out["closed"], out["currentGone"]), ([], None))

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_source_stepping_only_runs_for_an_open_source_sheet_and_stops_at_the_ends(self) -> None:
        probe = self._top_level("stepIndex", "stepSource") + """
const log = [];
const sheet = { open: true, closing: false, name: "source", mark: { kind: "source", id: "t", index: 1 } };
const turnData = new Map([["t", { sources: [{ number: 1, title: "A" }, { number: 2, title: "B" }, { number: 3, title: "C" }] }]]);
function openSheet(name, opener, fill) { log.push("open:" + name); }
function fillSource() {}
function announce(text) { log.push(text); }
const out = {};
out.middle = [stepSource(1), log.splice(0)];
sheet.mark.index = 2; out.end = [stepSource(1), log.splice(0)];
sheet.mark.index = 0; out.start = [stepSource(-1), log.splice(0)];
sheet.name = "gaps"; out.wrong = [stepSource(1), log.splice(0)];
sheet.name = "source"; sheet.closing = true; out.closing = [stepSource(-1), log.splice(0)];
sheet.closing = false; sheet.open = false; out.closed = [stepSource(-1), log.splice(0)];
console.log(JSON.stringify(out));"""
        out = self._node(probe)
        self.assertEqual(out["middle"], [True, ["open:source", "Source 3 of 3: C"]])
        for name in ("end", "start", "wrong", "closing", "closed"):
            with self.subTest(case=name):
                self.assertEqual(out[name], [False, []])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_fill_a_gap_mode_is_set_before_add_loads_and_ends_cleanly(self) -> None:
        names = ("renderMode", "leaveGap", "backToAnswer")
        functions = "\n".join(re.search(rf"(?:async )?function {name}\(.*?\n  }}\n", self.capture, re.S).group(0) for name in names)
        opener = re.search(r"async function openGap\(.*?\n  }\n", self.capture, re.S).group(0)
        probe = """
const els = {};
const $ = (id) => (els[id] ||= { textContent: "", hidden: false, focus() {} });
let gapMode = null, keepGap = false;
const state = { conversationId: "c1" };
els.turns = { children: [1] };
const seen = [];
async function setView(view) { seen.push({ view, title: $("add-title").textContent, save: $("capture-save").textContent, kept: keepGap }); }
async function openConversation(id) { seen.push({ openConversation: id }); }
""" + functions + opener + """
(async () => {
  const out = {};
  await openGap({ gap: "How anger went", question: "How do I handle anger?", conversationId: "c1" });
  out.entry = seen.splice(0);
  out.panel = [$("gap-panel-text").textContent, $("gap-panel-from").textContent, $("gap-panel").hidden, $("gap-back").hidden, $("gap-after").hidden];
  await backToAnswer();
  out.back = seen.splice(0);  // the answer is still on screen: no refetch
  state.conversationId = "other";
  await openGap({ gap: "G2", question: "Q2", conversationId: "c1" }); seen.length = 0;
  await backToAnswer();
  out.backElsewhere = seen.splice(0);
  out.after = [$("add-title").textContent, $("capture-save").textContent, $("gap-panel-text").textContent, $("gap-panel").hidden];
  await openGap({ gap: "G", question: "", conversationId: null });
  out.noQuestionFrom = [$("gap-panel-from").textContent, $("gap-panel-from").hidden];
  seen.length = 0;
  await backToAnswer();
  out.noConversation = seen.splice(0);
  console.log(JSON.stringify(out));
})();"""
        out = self._node(probe)
        # The mode is already on when Add starts loading, and Add's own load is told to keep it.
        self.assertEqual(out["entry"], [{"view": "add", "title": "Fill a gap", "save": "Save note", "kept": True}])
        self.assertEqual(out["panel"], ["How anger went", "From “How do I handle anger?”", False, False, False])
        self.assertEqual(out["back"], [{"view": "ask", "title": "Add to your notes", "save": "Save", "kept": False}])
        self.assertEqual(out["backElsewhere"], [{"openConversation": "c1"}])
        self.assertEqual(out["after"], ["Add to your notes", "Save", "", True])
        self.assertEqual(out["noQuestionFrom"], ["", True])
        self.assertEqual(out["noConversation"], [{"view": "ask", "title": "Add to your notes", "save": "Save", "kept": False}])

    def test_open_states_are_fills_never_rings_or_outlines(self) -> None:
        for names in ((".marker.is-active",), (".source-number.is-active", ".gap-chip.is-active"), (".drop-target",), (".tag-row.is-active",),
                      (".tag-approve", ".tag-block", ".chip-button"), (".tag-chip",), (".settings-tab[aria-current",)):
            body = _rules(self.css, *names)
            with self.subTest(names=names):
                self.assertTrue(body, "no rule matched: the selector was renamed")
                self.assertNotRegex(body, r"(?<![\w-])(outline|border)(-top|-bottom|-left|-right|-width|-color|-style)?\s*:")
                self.assertNotRegex(body, r"box-shadow:\s*(inset )?0 0 0 (1\.5|2)px")  # a ring, not a hairline edge or underline
        # The Add box and the composer take no extra ring while typing into the Add box.
        self.assertNotIn(".capture-form:focus-within", self.css)
        self.assertNotIn("box-shadow: 0 0 0 1.5px", self.css)

    def test_sidebar_history_foot_and_gear(self) -> None:
        self.assertIn("Add to notes", self.page)
        self.assertIn('class="history-toggle"', self.page)
        self.assertIn("function groupByAge", self.app)
        foot = self.page[self.page.index('class="sidebar-foot"'):self.page.index("</aside>")]
        self.assertIn(">Account<", foot)
        self.assertNotIn("@", foot)  # name and email are not shown: the browser does not hold them
        self.assertIn('setAttribute("aria-current", "page")', self.app[self.app.index("function applyView"):self.app.index("function setView")])

    def test_composer_hint_placeholders_and_send_state(self) -> None:
        self.assertIn("Enter to ask · Shift + Enter for a new line", self.page)
        self.assertIn('placeholder="Ask a question"', self.page)
        self.assertIn('"Ask a follow-up"', self.app)
        self.assertIn('setAttribute("aria-disabled"', self.app[self.app.index("function syncSend"):])
        self.assertIn(".send[aria-disabled=\"true\"]", self.css)

    def test_active_marks_are_owned_by_one_helper_and_stepping_adds_no_history(self) -> None:
        for name in ("function openSheet", "function teardownSheet"):
            body = self.app[self.app.index(name):]
            self.assertIn("syncActive()", body[:body.index("\n}\n")])
        step = self.app[self.app.index("function stepSource"):]
        step = step[:step.index("\n}\n")]
        self.assertNotIn("history.", step)
        self.assertNotIn("pushState", step)
        self.assertIn('"aria-disabled"', self.app[self.app.index("function fillSource"):self.app.index("function openSource")])

    def test_fill_a_gap_mode_is_wired_end_to_end(self) -> None:
        for element_id in ("gap-back", "gap-panel", "gap-after", "gap-panel-text", "gap-panel-from"):
            self.assertIn(f'id="{element_id}"', self.page)
        self.assertIn("captureScreen.openGap", self.app)
        self.assertIn("window.captureScreen = { takeShared, show, reset, openGap };", self.capture)
        reset = self.capture[self.capture.index("function reset()"):self.capture.index("async function openGap")]
        self.assertIn("leaveGap()", reset)
        self.assertIn("Save note", self.capture)
        # A saved note leaves the mode, and the gap's own text is not put into the box.
        send = self.capture[self.capture.index("async function send"):self.capture.index("const KINDS")]
        self.assertIn("leaveGap()", send)

    def test_tag_chips_never_submit_the_form_and_the_request_keeps_its_shape(self) -> None:
        self.assertIn('event.key === "Enter" || event.key === ","', self.capture)
        self.assertIn("event.preventDefault(); commitTags();", self.capture)
        self.assertIn("mergeTags(tagList, $(\"capture-tags\").value).tags", self.capture)
        self.assertIn('aria-label", `Remove tag ${tag}`', self.capture)

    def test_drag_target_clears_on_every_way_a_drag_can_end(self) -> None:
        for needle in ('addEventListener("dragend", endDrag)', 'event.key === "Escape") endDrag()', "if (!event.relatedTarget) endDrag();", "setTimeout(endDrag, DRAG_IDLE_MS)"):
            self.assertIn(needle, self.capture)
        self.assertIn('id="drop-target" class="drop-target" aria-hidden="true"', self.page)
        self.assertIn("pointer-events: none", _rules(self.css, ".drop-target"))

    def test_the_saving_toast_is_phone_only_a_live_region_and_never_takes_focus(self) -> None:
        self.assertRegex(self.page, r'id="toast" class="toast" role="status" aria-live="polite"')
        self.assertRegex(self.css, r"\.toast \{ display: none;")
        phone = self.css[self.css.index("@media (max-width: 899px)"):]
        self.assertIn(".toast { display: flex; }", phone)
        self.assertNotIn("focus", self.capture[self.capture.index("function showToast"):self.capture.index("// Fill a gap")])


@unittest.skipUnless(TestClient, "needs fastapi")
class StaticRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.api.main import create_app
        from tests.api.test_api import FakeAuth, FakeEngine, settings

        def no_database():
            raise AssertionError("static routes must not touch the database")

        self.client = TestClient(create_app(settings(), connect=no_database, auth=FakeAuth(), engine=FakeEngine()))

    def test_shell_manifest_and_worker_are_served_with_csp(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn("script-src 'self'", page.headers["content-security-policy"])
        manifest = self.client.get("/manifest.webmanifest")
        self.assertIn("application/manifest+json", manifest.headers["content-type"])
        worker = self.client.get("/sw.js")
        self.assertIn("javascript", worker.headers["content-type"])
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/icons/icon-192.png").status_code, 200)

    def test_settings_opens_from_the_gear_and_lists_suggested_tags(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertRegex(page, r'id="open-settings"[^>]*aria-label="Settings"')
        for needle in ('id="settings"', 'class="settings-tabs"', 'id="tag-list"', 'data-sheet="tags"', "/static/settings.js"):
            with self.subTest(needle=needle):
                self.assertIn(needle, page)
        self.assertIn("/static/settings.js", (WEB / "sw.js").read_text(encoding="utf-8"))
        script = (WEB / "settings.js").read_text(encoding="utf-8")
        for call in ('"GET", "/api/tags"', "/api/tags/${encodeURIComponent(name)}/${verdict}"):
            self.assertIn(call, script)
        self.assertEqual(self.client.get("/static/settings.js").status_code, 200)

    def test_starters_and_approved_tags_are_wired_to_the_server(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        for needle in ('id="starters"', 'id="capture-tag-pick"', 'id="tags-inuse"'):
            with self.subTest(needle=needle):
                self.assertIn(needle, page)
        self.assertIn('"GET", "/api/starters"', (WEB / "app.js").read_text(encoding="utf-8"))
        self.assertIn(".approved", (WEB / "capture.js").read_text(encoding="utf-8"))
        self.assertIn("data.approved", (WEB / "settings.js").read_text(encoding="utf-8"))

    def test_text_uses_only_the_agreed_type_scale(self) -> None:
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        sizes = {float(size) for size in re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", css)}
        self.assertEqual(sizes - TYPE_SCALE, set())

    def test_no_button_or_number_is_bold(self) -> None:
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            weight = re.search(r"font-weight:\s*(\d+)", body)
            if weight and int(weight.group(1)) > 500:
                names = selector.strip()
                for control in (".marker", ".source-number", ".sheet-number", ".gap-chip", "button", ".primary", ".step"):
                    with self.subTest(selector=names, control=control):
                        self.assertNotIn(control, names)

    def test_font_files_are_served_from_static(self) -> None:
        fonts = sorted((WEB / "fonts").glob("*.woff2"))
        self.assertTrue(fonts)
        for font in fonts:
            with self.subTest(font=font.name):
                self.assertEqual(self.client.get(f"/static/fonts/{font.name}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
