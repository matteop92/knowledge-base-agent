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
    out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True).stdout
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


if __name__ == "__main__":
    unittest.main()
