from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path("app/web")


def _script() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8").replace("\r", "")


def _node(probe: str) -> dict:
    out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "needs node")
class RunsAsWrittenTests(unittest.TestCase):
    """The browser code is run under node with its collaborators stubbed, so the behaviour is checked, not the text."""

    def test_answer_poll_gives_up_after_repeated_failures_and_resets_on_success(self) -> None:
        poll = re.search(r"function poll\(turnId\).*?\n}\n", _script(), re.S).group(0)
        probe = """
const POLL_MS = 1, POLL_RETRIES = 5;
const state = { conversationId: "c1", polling: new Map() };
const queue = [];
const setTimeout = (fn) => { queue.push(fn); return queue.length; };
const clearTimeout = () => {};
let results = [];
let calls = 0;
const api = async () => { calls += 1; const next = results.shift(); if (next instanceof Error) throw next; return next; };
const log = [];
function renderTurn() {}
function announce() {}
function setRunning(value) { log.push("running:" + value); }
function notify() { log.push("notify"); }
""" + poll + """
const fail = () => Object.assign(new Error("x"), { status: 500 });
const drain = async () => { while (queue.length) await queue.shift()(); };
(async () => {
  const out = {};
  results = Array.from({ length: 20 }, fail);
  poll("t1"); await drain();
  out.giveUp = { calls, log: log.splice(0), left: state.polling.size };
  // A success in between starts the count again, so the poll survives 4 + 4 failures.
  calls = 0;
  results = [fail(), fail(), fail(), fail(), { conversation_id: "c1", status: "running" },
             fail(), fail(), fail(), fail(), { conversation_id: "c1", status: "done" }];
  poll("t2"); await drain();
  out.reset = { calls, log: log.splice(0) };
  // The user opened another conversation: a stale poll must not touch the screen.
  calls = 0;
  results = Array.from({ length: 20 }, fail);
  poll("t3"); state.conversationId = "c2"; await drain();
  out.stale = { calls, log: log.splice(0) };
  console.log(JSON.stringify(out));
})();"""
        out = _node(probe)
        self.assertEqual(out["giveUp"], {"calls": 5, "log": ["running:false", "notify"], "left": 0})
        self.assertEqual(out["reset"], {"calls": 10, "log": ["running:false"]})
        self.assertEqual(out["stale"], {"calls": 1, "log": []})

    def test_sheet_close_falls_back_by_hand_only_when_back_never_arrives(self) -> None:
        close = re.search(r"function closeSheet\(after\).*?\n}\n", _script(), re.S).group(0)
        probe = """
const BACK_FALLBACK_MS = 500;
const timers = [];
const setTimeout = (fn) => { timers.push(fn); };
let backs = 0, teardowns = 0;
const history = { back: () => { backs += 1; } };
const sheet = { open: false, closing: false, serial: 0, afterClose: [] };
function teardownSheet() { teardowns += 1; sheet.open = false; sheet.closing = false; }
""" + close + """
const out = {};
// Back never arrives: the timer closes the sheet once.
Object.assign(sheet, { open: true, closing: false, serial: 1 });
closeSheet(); closeSheet();  // a double tap still makes one Back
timers.splice(0).forEach((fn) => fn());
out.never = { backs, teardowns };
// Back arrives in time (popstate tore the sheet down): the timer does nothing.
backs = 0; teardowns = 0;
Object.assign(sheet, { open: true, closing: false, serial: 2 });
closeSheet(); teardownSheet(); teardowns = 0;
timers.splice(0).forEach((fn) => fn());
out.inTime = { backs, teardowns };
// Another sheet was opened before the timer fired: that sheet is left alone.
backs = 0; teardowns = 0;
Object.assign(sheet, { open: true, closing: false, serial: 3 });
closeSheet(); Object.assign(sheet, { open: true, closing: false, serial: 4 });
timers.splice(0).forEach((fn) => fn());
out.reopened = { backs, teardowns };
console.log(JSON.stringify(out));"""
        out = _node(probe)
        self.assertEqual(out["never"], {"backs": 1, "teardowns": 1})
        self.assertEqual(out["inTime"], {"backs": 1, "teardowns": 0})
        self.assertEqual(out["reopened"], {"backs": 1, "teardowns": 0})

    def test_gap_note_button_disables_on_first_click(self) -> None:
        fill = re.search(r"function fillGaps\(section, data\).*?\n}\n", _script(), re.S).group(0)
        probe = """
const make = (tag) => ({ tag, children: [], className: "", textContent: "", disabled: false, listeners: {},
  append(...n) { this.children.push(...n); }, replaceChildren(...n) { this.children = n; },
  addEventListener(name, fn) { this.listeners[name] = fn; } });
const document = { createElement: make };
function textElement(tag, className, text) { const el = make(tag); el.className = className; el.textContent = text; return el; }
let closes = 0;
function closeSheet(after) { closes += 1; }
const window = { captureScreen: { openWith() {} } };
""" + fill + """
const section = make("section");
fillGaps(section, { gaps: ["One gap."] });
const item = section.children[1].children[0];
const add = item.children[1];
add.listeners.click();
console.log(JSON.stringify({ disabled: add.disabled, closes }));"""
        self.assertEqual(_node(probe), {"disabled": True, "closes": 1})

    def test_popstate_closes_a_sheet_before_the_drawer(self) -> None:
        script = _script()
        handler = re.search(r'window.addEventListener\("popstate", \(\) => \{.*?\n  \}\);', script, re.S).group(0)
        probe = """
const log = [];
const sheet = { open: false };
const navState = { open: false };
function teardownSheet() { log.push("sheet"); }
function setNavOpen(open, fromBack) { log.push("nav:" + open + ":" + fromBack); }
let listener;
const window = { addEventListener: (name, fn) => { listener = fn; } };
""" + handler + """
const out = {};
sheet.open = true; navState.open = true; listener(); out.both = log.splice(0);
sheet.open = false; navState.open = true; listener(); out.drawer = log.splice(0);
navState.open = false; listener(); out.none = log.splice(0);
console.log(JSON.stringify(out));"""
        out = _node(probe)
        self.assertEqual(out["both"], ["sheet"])
        self.assertEqual(out["drawer"], ["nav:false:true"])
        self.assertEqual(out["none"], [])

    def test_recent_rows_show_state_icon_link_type_and_retry(self) -> None:
        capture = (WEB / "capture.js").read_text(encoding="utf-8").replace("\r", "")
        render = capture[capture.index("  const KINDS"):capture.index("  function pollCapture")]
        probe = """
const make = (tag) => ({ tag, children: [], dataset: {}, attrs: {}, className: "", textContent: "", title: "", href: "",
  append(...n) { this.children.push(...n); }, prepend(...n) { this.children.unshift(...n); },
  replaceChildren(...n) { this.children = n; }, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(name, fn) { this.handlers = { ...this.handlers, [name]: fn }; } });
const list = make("ul");
const document = { createElement: make, querySelector: () => null };
const CSS = { escape: (x) => x };
const $ = () => list;
const icon = (name) => ({ icon: name });
const STATUS = { running: "Saving…", indexed: "Saved and searchable", queued: "Queued" };
const retries = new Map([["f1", {}]]);
const obsidianLink = (path) => "obsidian://" + path;
const sends = [];
const retrying = new Set();
const keepRetry = () => true;
const store = () => {};
const RETRY_PREFIX = "ka-retry:";
const persisted = new Set();
let storedCopy = null;  // the kept copy; another tab may have taken it away
const readRetry = () => storedCopy;
let resets = 0;
async function send(body) { sends.push(body); return true; }
""" + render + """
const describe = (job) => {
  renderCapture(job, true);
  const row = list.children[0];
  list.children = [];
  const [state, body] = row.children;
  const meta = body.children[1];
  return { status: row.dataset.status, icon: state.children[0].icon, tip: state.title,
           title: body.children[0].textContent, meta: meta.children.map((c) => c.className + ":" + (c.textContent || c.children.map((x) => x.icon || x).join(""))) };
};
const out = ({
  done: describe({ id: "d1", kind: "text", status: "done", indexed: true, title: "Note", vault_path: "Knowledge/a.md" }),
  queued: describe({ id: "d2", kind: "markdown", status: "done", indexed: false, title: "Note", vault_path: "Knowledge/b.md" }),
  running: describe({ id: "r1", kind: "youtube", status: "running", title: "Video" }),
  failed: describe({ id: "f1", kind: "screenshot", status: "failed", error: "Could not read the image.", title: "Shot" }),
  reloaded: describe({ id: "f2", kind: "screenshot", status: "failed", error: "x", title: "Shot" }),
});
// Pressing Retry twice sends once; afterwards the failed row offers no second Retry.
(async () => {
  renderCapture({ id: "f1", kind: "screenshot", status: "failed", error: "x", title: "Shot" }, true);
  const button = list.children[0].children[1].children[1].children[1];
  const first = button.handlers.click();
  const second = button.handlers.click();
  await Promise.all([first, second]);
  out.afterClicks = { sends: sends.length, disabled: button.disabled, again: describe({ id: "f1", kind: "screenshot", status: "failed", error: "x", title: "Shot" }).meta };
  // After a reload a failed row gets its Retry back from the kept copy.
  storedCopy = { kind: "text" };
  out.restored = describe({ id: "f3", kind: "text", status: "failed", error: "x", title: "Note" }).meta;
  persisted.delete("f3");
  // Another tab already resent this one: its kept copy is gone, so this tab sends nothing and shows no Retry.
  retries.set("g1", { kind: "text" });
  persisted.add("g1");
  storedCopy = null;
  const before = sends.length;
  renderCapture({ id: "g1", kind: "text", status: "failed", error: "x", title: "Note" }, true);
  const other = list.children[0].children[1].children[1].children[1];
  await other.handlers.click();
  out.otherTab = { sent: sends.length - before, again: describe({ id: "g1", kind: "text", status: "failed", error: "x", title: "Note" }).meta };
  console.log(JSON.stringify(out));
})();"""
        out = _node(probe)
        self.assertEqual(out["done"], {"status": "done", "icon": "saved", "tip": "Saved and searchable", "title": "Note",
                                       "meta": [":See in Obsidian", "type-pill:Text"]})
        self.assertEqual(out["queued"]["tip"], "Queued")  # the "searchable soon" detail lives in the tooltip
        self.assertEqual(out["running"], {"status": "running", "icon": "saving", "tip": "Saving…", "title": "Video",
                                          "meta": ["muted:Saving…", "type-pill:YouTube"]})
        self.assertEqual(out["failed"], {"status": "failed", "icon": "failed", "tip": "Failed: Could not read the image.",
                                         "title": "Shot", "meta": ["type-pill:Screenshot", "retry-capture:retryRetry"]})
        self.assertEqual(out["reloaded"]["meta"], ["type-pill:Screenshot", "muted:Add it again"])
        self.assertEqual(out["restored"], ["type-pill:Text", "retry-capture:retryRetry"])
        self.assertEqual(out["otherTab"], {"sent": 0, "again": ["type-pill:Text", "muted:Add it again"]})
        self.assertEqual(out["afterClicks"], {"sends": 1, "disabled": True, "again": ["type-pill:Screenshot", "muted:Add it again"]})

    def test_every_icon_the_scripts_draw_exists_in_the_sprite(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "capture.js"):
            script = (WEB / name).read_text(encoding="utf-8")
            for icon_name in sorted(set(re.findall(r'icon\("([a-z-]+)"\)', script))):
                with self.subTest(name=name, icon=icon_name):
                    self.assertIn(f'id="i-{icon_name}"', page)

    def test_state_and_retry_icons_are_in_the_sprite(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        for icon_name in ("saved", "saving", "failed", "retry"):
            with self.subTest(icon=icon_name):
                self.assertIn(f'id="i-{icon_name}"', page)

    def test_failed_icon_is_not_red_and_retry_is_not_taller_than_the_pill(self) -> None:
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        failed = re.search(r"\.capture-state \{[^}]*\}", css).group(0)
        self.assertNotIn("danger", failed)
        self.assertNotIn("danger", css[css.index(".capture-item {"):css.index(".retry-capture")])
        retry = re.search(r"\.capture-meta \.retry-capture \{[^}]*\}", css).group(0)
        self.assertIn("height: 20px", retry)  # the pill is 12 px text plus 4 px above and below

    def test_failed_saves_are_kept_for_an_hour_and_forgotten_after(self) -> None:
        capture = (WEB / "capture.js").read_text(encoding="utf-8").replace(chr(13), "")
        kept = capture[capture.index("  const RETRY_PREFIX"):capture.index("  const STATUS")]
        probe = """
const disk = {};
let full = false;
const load = (key) => (key in disk ? disk[key] : null);
const store = (key, value) => { if (value === null) delete disk[key]; else if (!full) disk[key] = value; };
const localStorage = { get length() { return Object.keys(disk).length; }, key: (i) => Object.keys(disk)[i] };
let clock = 1000;
Date.now = () => clock;
""" + kept + """
const out = {};
keepRetry("a", { kind: "text", content: "first" });
keepRetry("b", { kind: "text", content: "second" });
disk["ka-draft"] = "an unsent question";
out.both = Object.keys(disk).sort();
clock += 30 * 60 * 1000;
keepRetry("c", { kind: "text", content: "third" });
clock += 40 * 60 * 1000;  // a and b are now 70 minutes old, c is 40
sweepRetries(false);
out.swept = Object.keys(disk).sort();  // expired bodies leave the disk, not just the screen
out.read = readRetry("c");
keepRetry("c", null);
out.forgotten = Object.keys(disk).sort();
disk["ka-retry:bad"] = "not json";
disk["ka-retry:old"] = JSON.stringify({ body: { kind: "text" } });  // no timestamp: never trusted
disk["ka-retry:str"] = JSON.stringify({ at: clock, body: "x" });      // body is not an object
out.poisoned = [readRetry("bad"), readRetry("old"), readRetry("str")];
out.afterPoison = Object.keys(disk).sort();
keepRetry("big", { kind: "screenshot", image: "x".repeat(2000000) });
out.big = Object.keys(disk).includes("ka-retry:big");
keepRetry("d", { kind: "text", content: "kept" });
sweepRetries(true);
out.cleared = Object.keys(disk).sort();   // sign-out clears the retries and leaves the draft alone
full = true;
keepRetry("e", { kind: "text" });
out.full = Object.keys(disk).sort();      // a full store just skips keeping
console.log(JSON.stringify(out));"""
        out = _node(probe)
        self.assertEqual(out["both"], ["ka-draft", "ka-retry:a", "ka-retry:b"])
        self.assertEqual(out["swept"], ["ka-draft", "ka-retry:c"])
        self.assertEqual(out["read"], {"kind": "text", "content": "third"})
        self.assertEqual(out["forgotten"], ["ka-draft"])
        self.assertEqual(out["poisoned"], [None, None, None])
        self.assertEqual(out["afterPoison"], ["ka-draft"])
        self.assertFalse(out["big"])
        self.assertEqual(out["cleared"], ["ka-draft"])
        self.assertEqual(out["full"], ["ka-draft"])
        # Sign-out (reset) clears every kept body, and a save answering after it writes nothing back.
        reset = capture[capture.index("function reset()"):capture.index("async function openWith")]
        self.assertIn("sweepRetries(true)", reset)
        self.assertIn("if (session !== resets) return false;", capture)


class AppNameTests(unittest.TestCase):
    def test_the_app_is_called_knowledge_agent_everywhere_it_names_itself(self) -> None:
        import json
        manifest = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual((manifest["name"], manifest["short_name"]), ("Knowledge Agent", "Knowledge Agent"))
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Knowledge Agent</title>", page)
        self.assertIn("<h1>Knowledge Agent</h1>", page)
        self.assertIn('<span class="brand label">Knowledge Agent</span>', page)
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('"Knowledge"', script)
        self.assertNotIn("- Knowledge`", script)


class PageStructureTests(unittest.TestCase):
    def test_every_closing_tag_matches_an_open_one(self) -> None:
        # A stray </div> closed the whole app grid early and pushed the main column below the sidebar.
        from html.parser import HTMLParser

        void = {"meta", "link", "input", "br", "img", "hr", "use", "path", "source"}

        class Check(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.stack: list[tuple[str, int]] = []
                self.errors: list[str] = []

            def handle_starttag(self, tag, attrs):
                if tag not in void:
                    self.stack.append((tag, self.getpos()[0]))

            def handle_startendtag(self, tag, attrs):
                pass

            def handle_endtag(self, tag):
                if tag in void:
                    return
                if not self.stack or self.stack[-1][0] != tag:
                    self.errors.append(f"</{tag}> at line {self.getpos()[0]} closes {self.stack[-1] if self.stack else None}")
                    return
                self.stack.pop()

        check = Check()
        check.feed((WEB / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(check.errors, [])
        self.assertEqual(check.stack, [])


if __name__ == "__main__":
    unittest.main()
