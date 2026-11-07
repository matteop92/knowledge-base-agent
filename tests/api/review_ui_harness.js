"use strict";
// Runs app/web/review.js against a small fake DOM and prints what a scenario observed as JSON.
// Usage: node tests/api/review_ui_harness.js <scenario>
const fs = require("fs");
const vm = require("vm");

const { El } = require("./fake_dom");

const documentStub = { hidden: false, listeners: {}, createElement: (tag) => new El(tag),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); } };

El.document = documentStub;

function passage(n, extra = {}) {
  return { passage_key: String(n).padStart(32, "0"), round: 1, text: `Passage ${n} text.`, note: null, title: `Title ${n}`, author: "Ada Stone",
    source_id: `source-${n}`, type: "highlights", locator: null, ...extra };
}

function boot({ phone = true, replies = [] } = {}) {
  const els = {};
  for (const id of ["app", "review", "review-pager", "review-state", "review-ask", "review-live", "review-listbar", "review-list-back",
    "review-list-name", "review-list-count", "review-list-caption"]) {
    els[id] = new El("div"); els[id].id = id;
  }
  els["review-state"].hidden = true; els["review-ask"].hidden = true; els["review-listbar"].hidden = true; els["review-list-caption"].hidden = true;
  const calls = { api: [], asked: [], timers: [], notices: [], opened: [], backed: [] };
  const queue = replies.slice();
  const stored = {};
  class FakeObserver { constructor(cb) { this.cb = cb; this.watched = []; observers.push(this); } observe(el) { this.watched.push(el); } disconnect() { this.watched = []; } }
  const observers = [];
  const context = {
    console, document: documentStub, window: { reviewSettings: { openFromReview: (name) => calls.opened.push(name), backToList: (name) => calls.backed.push(name) }, readerBlocks: { inline: (tag, cls, text) => { const el = new El(tag); el.className = cls; el.textContent = text; return el; } } },
    $: (id) => { if (!els[id]) throw new Error(`no element ${id}`); return els[id]; },
    icon: (name) => { const el = new El("svg"); el.dataset.name = name; return el; },
    phone: { matches: phone, listeners: [], addEventListener(type, fn) { this.listeners.push(fn); } },
    state: { view: "review" }, sheet: { open: false },
    notify: (message) => calls.notices.push(message),
    api: async (method, path, body) => {
      calls.api.push([method, path, body || null]);
      const next = queue.shift();
      if (next instanceof Error) throw next;
      return next === undefined ? {} : next;
    },
    askAboutPassage: (p) => calls.asked.push(p),
    setTimeout: (fn, ms) => { const timer = { fn, ms, live: true }; calls.timers.push(timer); return timer; },
    clearTimeout: (timer) => { if (timer) timer.live = false; },
    IntersectionObserver: FakeObserver,
    localStorage: { getItem: (k) => (k in stored ? stored[k] : null), setItem: (k, v) => { stored[k] = v; } },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync("app/web/review.js", "utf8"), context, { filename: "review.js" });
  for (const fn of documentStub.listeners.DOMContentLoaded || []) fn();
  const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
  const screen = context.window.reviewScreen;
  const seen = (index, ratio = 0.9) => observers[0].cb([{ target: screen.pages()[index].el, isIntersecting: ratio > 0, intersectionRatio: ratio }]);
  const fire = (index) => { const timer = calls.timers.filter((t) => t.live && t.ms === 1000)[0]; if (timer) { timer.live = false; timer.fn(); } return timer; };
  return { els, calls, context, flush, screen, seen, fire, observers, stored };
}

const words = (n) => Array.from({ length: n }, (_, i) => `w${i}`).join(" ");
const sentences = (n, each) => Array.from({ length: n }, (_, i) => `${Array.from({ length: each }, (_, j) => `s${i}w${j}`).join(" ")}.`).join(" ");
const count = (text) => text.split(/\s+/).filter(Boolean).length;
const page = (app, index) => app.screen.pages()[index];
const tapAt = (el, x) => el.click({ clientX: x, target: el });

const scenarios = {
  async paginate() {
    const { screen } = boot();
    const lengths = (parts) => parts.map(count);
    return {
      oneHundredTwentyNine: lengths(screen.paginate(sentences(43, 3), 110)),
      threeEightySeven: lengths(screen.paginate(sentences(129, 3), 110)),
      stub: lengths(screen.paginate(sentences(37, 3), 110)),
      withNote: lengths(screen.paginate(sentences(110, 3), 110, 50)),
      ownNote: lengths(screen.paginate(sentences(43, 3), 110, 70)),
    };
  },
  async noteOnlyOnLastPart() {
    const app = boot({ replies: [{ passages: [passage(1, { text: sentences(110, 3), note: "my own take on this" })] }] });
    app.screen.show(); await app.flush();
    const el = page(app, 0).el;
    const notes = [];
    const parts = page(app, 0).parts.length;
    for (let i = 0; i < parts; i++) {
      notes.push(!el.querySelector(".review-note").hidden);
      el.querySelector(".review-next").click();
    }
    return { parts, notes };
  },
  async shownOnlyWhenOnScreen() {
    const app = boot({ replies: [{ passages: [passage(1), passage(2)] }, {}, {}] });
    app.screen.show(); await app.flush();
    app.seen(0);
    const timer = app.fire();
    await app.flush();
    return { timerMs: timer.ms, calls: app.calls.api.filter(([m]) => m === "POST"), pages: app.screen.pages().length };
  },
  async leavingBeforeASecondLogsNothing() {
    const app = boot({ replies: [{ passages: [passage(1), passage(2)] }] });
    app.screen.show(); await app.flush();
    app.seen(0); app.seen(0, 0);
    const timers = app.calls.timers.filter((t) => t.ms === 1000);
    return { live: timers.filter((t) => t.live).length, posts: app.calls.api.filter(([m]) => m === "POST").length };
  },
  async tapShowsThePillAndAsks() {
    const app = boot({ replies: [{ passages: [passage(1, { text: "Short one." })] }] });
    app.screen.show(); await app.flush();
    const el = page(app, 0).el; const pill = app.els["review-ask"];
    tapAt(el, 200); const afterFirst = pill.hidden;
    tapAt(el, 200); const afterSecond = pill.hidden;
    tapAt(el, 200);
    pill.click();
    return { afterFirst, afterSecond, asked: app.calls.asked.map((p) => p.source_id), hiddenAfterAsk: pill.hidden };
  },
  async edgesTurnPartsOnAPhone() {
    const app = boot({ replies: [{ passages: [passage(1, { text: sentences(110, 3) })] }] });
    app.screen.show(); await app.flush();
    const p = page(app, 0);
    const parts = p.parts.length;
    tapAt(p.el, 390); const afterRight = p.part;
    tapAt(p.el, 10); const afterLeft = p.part;
    tapAt(p.el, 200); const middle = app.els["review-ask"].hidden;
    tapAt(p.el, 390); const hiddenAfterTurn = app.els["review-ask"].hidden;
    return { parts, afterRight, afterLeft, middleShowsPill: !middle, hiddenAfterTurn };
  },
  async partsForOnePassageSize() {
    const run = async (extra, opts = {}) => {
      const app = boot({ replies: [{ passages: [passage(1, extra)] }], ...opts });
      app.screen.show(); await app.flush();
      const p = page(app, 0);
      return { parts: p.parts.map(count), budget: app.screen.budget(), bars: p.el.classList.contains("has-bars"), short: p.el.classList.contains("is-short") };
    };
    return {
      long140: await run({ text: sentences(47, 3) }),
      long140Wide: await run({ text: sentences(47, 3) }, { phone: false }),
      quote30: await run({ text: words(30), type: "quote" }),
      withNote: await run({ text: words(60), note: words(60) }),
      oneSentence: await run({ text: `${words(120)}.` }),
      noPunctuation: await run({ text: words(300) }),
    };
  },
  async oneLinePassageEdgesDoNothing() {
    const app = boot({ replies: [{ passages: [passage(1)] }] });
    app.screen.show(); await app.flush();
    const p = page(app, 0);
    tapAt(p.el, 390);
    return { part: p.part, pill: !app.els["review-ask"].hidden };
  },
  async emptyAndHeldStates() {
    const empty = boot({ replies: [{ passages: [] }] });
    empty.screen.show(); await empty.flush();
    const held = boot({ replies: [{ passages: [passage(1), passage(2)] }, { passages: [] }] });
    held.screen.show(); await held.flush();
    held.seen(0);
    return { emptyShown: !empty.els["review-state"].hidden, emptyText: empty.els["review-state"].textContent,
      heldEmpty: !held.els["review-state"].hidden, heldPages: held.screen.pages().length };
  },
  async smallPoolFillsTheScreen() {
    const app = boot({ replies: [{ passages: [passage(1), passage(2)] }] });
    app.screen.show(); await app.flush();
    return { stateHidden: app.els["review-state"].hidden, pages: app.screen.pages().length };
  },
  async failedThenRetry() {
    const fail = Object.assign(new Error("x"), { status: 500 });
    const app = boot({ replies: [fail, { passages: [passage(1)] }] });
    app.screen.show(); await app.flush();
    const failedText = app.els["review-state"].textContent;
    const retry = app.els["review-state"].querySelector("button");
    retry.click(); await app.flush();
    return { failedText, retried: app.calls.api.length, pages: app.screen.pages().length, stateHidden: app.els["review-state"].hidden };
  },
  async keys() {
    const app = boot({ phone: false, replies: [{ passages: [passage(1, { text: sentences(150, 3) }), passage(2)] }] });
    app.screen.show(); await app.flush();
    const p = page(app, 0);
    const press = (key) => { const e = { key, target: new El("div"), preventDefault() { this.prevented = true; } }; for (const fn of documentStub.listeners.keydown) fn(e); return e; };
    press("ArrowRight"); const afterRight = p.part;
    press("ArrowLeft"); const afterLeft = p.part;
    press("ArrowDown");
    return { afterRight, afterLeft, scrolled: page(app, 1).el.scrolledIntoView === true,
      labels: p.el.querySelectorAll(".review-bar").map((b) => b.getAttribute("aria-label")),
      arrows: p.el.querySelectorAll(".review-arrow").map((b) => b.getAttribute("aria-label")) };
  },
  async paragraphsFirst() {
    const { screen } = boot();
    const para = (n) => sentences(n, 4);
    const text = [para(25), para(25), para(2)].join("\n\n");
    return screen.paginate(text, 110).map((part) => part.split("\n\n").map(count));
  },
  async askHandoff() {
    // The two functions from app.js that carry a passage to the Ask page, run against stubs.
    const source = fs.readFileSync("app/web/app.js", "utf8");
    const grab = (name) => { const at = source.indexOf(`function ${name}(`); return source.slice(at, at + source.slice(at).search(/\r?\n}\r?\n/) + 3); };
    const log = [];
    const els = {};
    const $ = (id) => (els[id] ||= new El("div"));
    const context = { $, log, window: { readerBlocks: { inline: (tag, cls, text) => { const el = new El(tag); el.textContent = text; return el; } } },
      newQuestion: () => log.push("newQuestion"), setScope: (scope) => { log.push(["scope", scope && scope.id]); if (!scope) context.setPassage(null); } };
    vm.createContext(context);
    vm.runInContext(`${grab("setPassage")}\n${grab("askAboutPassage")}\nglobalThis.setPassage = setPassage; globalThis.askAboutPassage = askAboutPassage;`, context);
    context.askAboutPassage(passage(7, { text: "First.\n\nSecond.", title: "A Book", author: "Ada Stone" }));
    const shown = { hidden: els["passage-card"].hidden, text: els["passage-card-text"].children.map((c) => c.textContent), source: els["passage-card-source"].textContent };
    context.setPassage(null);
    return { log, shown, afterClear: els["passage-card"].hidden };
  },
  async heldStaysUnderTheServerCap() {
    const many = Array.from({ length: 40 }, (_, i) => passage(i + 1));
    const app = boot({ replies: [{ passages: many.slice(0, 3) }, { passages: many.slice(3, 6) }] });
    app.screen.show(); await app.flush();
    // 38 passages skimmed past without a second on screen sit behind the reader.
    app.screen.pages().unshift(...many.slice(6).map((p) => ({ passage: p, el: new El("div"), logged: false, parts: [""], part: 0 })));
    app.seen(app.screen.pages().length - 1); await app.flush();
    const gets = app.calls.api.filter(([m]) => m === "GET").map(([, path]) => path);
    const held = gets[gets.length - 1].split("held=")[1].split(",").filter(Boolean);
    return { gets: gets.length, held: held.length };
  },
  async failedShownIsRetried() {
    const fail = Object.assign(new Error("x"), { status: 500 });
    const app = boot({ replies: [{ passages: [passage(1), passage(2)] }, { passages: [] }, fail, {}, {}] });
    app.screen.show(); await app.flush();
    app.seen(0); app.fire(); await app.flush();
    const first = { posts: app.calls.api.filter(([m]) => m === "POST").length, logged: page(app, 0).logged };
    app.seen(0, 0); app.seen(0); app.fire(); await app.flush();
    return { first, posts: app.calls.api.filter(([m]) => m === "POST").length, logged: page(app, 0).logged };
  },
  async typesStyleTheScreen() {
    const kinds = ["highlights", "quote", "note", "post", "podcast"];
    const app = boot({ replies: [{ passages: kinds.slice(0, 3).map((t, i) => passage(i + 1, { type: t })) }, { passages: kinds.slice(3).map((t, i) => passage(i + 4, { type: t })) }] });
    app.screen.show(); await app.flush();
    app.seen(0); await app.flush(); app.seen(1); await app.flush(); app.seen(3); await app.flush();
    const pages = app.screen.pages();
    return { classes: pages.map((p) => p.el.className.split(" ").filter((c) => c.startsWith("is-") && c !== "is-short")),
      kickers: pages.map((p) => p.el.querySelector(".review-kicker").textContent),
      source: pages[0].el.querySelectorAll(".review-source-title").map((e) => e.textContent).concat(pages[0].el.querySelectorAll(".review-source-author").map((e) => e.textContent)),
      type: app.els["review"].dataset.type, tone: app.els["app"].dataset.reviewTone };
  },
  async wheelFlipsOnePassagePerGesture() {
    const app = boot({ replies: [{ passages: [passage(1), passage(2), passage(3)] }, { passages: [] }, { passages: [] }] });
    app.screen.show(); await app.flush();
    const pager = app.els["review-pager"];
    app.seen(0);
    const wheel = (deltaY) => pager.dispatch("wheel", { deltaY });
    const e1 = wheel(100); wheel(80); wheel(60); wheel(40);  // one flick and its trailing inertia
    const flipped = page(app, 1).el.scrolledIntoView === true;
    const extra = page(app, 2).el.scrolledIntoView === true;
    const quiet = app.calls.timers.filter((t) => t.live && t.ms === 160).pop(); quiet.fn();  // the wheel goes quiet
    app.seen(1);
    wheel(120);
    const second = page(app, 2).el.scrolledIntoView === true;
    wheel(2);  // too small to count
    return { prevented: e1.prevented === true, flipped, extra, second };
  },
  async marksFillTapAgainClearsAndSwitches() {
    const app = boot({ replies: [{ passages: [passage(1, { type: "quote" })] }] });
    app.screen.show(); await app.flush();
    const el = page(app, 0).el;
    const [done, again] = [el.querySelector(".review-mark-done"), el.querySelector(".review-mark-again")];
    const state = () => [done.getAttribute("aria-pressed"), again.getAttribute("aria-pressed")];
    const initial = state();
    const writes = () => app.calls.api.filter(([m]) => m !== "GET").map(([m, path, body]) => [m, path.split("/").pop(), body]);
    again.click(); await app.flush(); const afterAgain = state();
    again.click(); await app.flush(); const afterClear = state();
    again.click(); await app.flush(); done.click(); await app.flush(); const afterSwitch = state();
    return { initial, afterAgain, afterClear, afterSwitch, writes: writes(), labels: [done.getAttribute("aria-label"), again.getAttribute("aria-label")],
      keys: [done.getAttribute("aria-keyshortcuts"), again.getAttribute("aria-keyshortcuts")], titles: [done.title, again.title] };
  },
  async aMarkClearedBeforeTheBeatEndsDoesNotAdvance() {
    const app = boot({ replies: [{ passages: [passage(1), passage(2)] }, {}, {}, {}] });
    app.screen.show(); await app.flush();
    app.seen(0);
    const done = page(app, 0).el.querySelector(".review-mark-done");
    done.click(); done.click();  // set, then undo a slip inside the 250 ms beat
    for (const t of app.calls.timers.filter((x) => x.live && x.ms === 250)) { t.live = false; t.fn(); }
    return { moved: page(app, 1).el.scrolledIntoView === true, mark: page(app, 0).mark };
  },
  async aNewMarkMovesOnToTheNextPassageButClearingOrAListDoesNot() {
    const app = boot({ replies: [{ passages: [passage(1), passage(2), passage(3)] }, {}, {}, {}, {}] });
    app.screen.show(); await app.flush();
    app.seen(0);
    const advance = () => { const t = app.calls.timers.filter((x) => x.live && x.ms === 250)[0]; if (t) { t.live = false; t.fn(); } return !!t; };
    const first = page(app, 0).el.querySelector(".review-mark-done");
    first.click(); const set = advance();
    const moved = page(app, 1).el.scrolledIntoView === true;
    first.click(); const clearTimer = advance();  // clearing: no timer
    app.seen(1);
    page(app, 1).el.querySelector(".review-mark-again").click();
    app.seen(2);  // the reader moved on by hand before the beat ended: nothing more happens
    advance();
    const stale = page(app, 2).el.scrolledIntoView === true;
    app.screen.enterList("again", [passage(11, { mark: "again" }), passage(12, { mark: "again" })], 0);
    const inList = app.screen.pages()[0].el.querySelector(".review-mark-done");
    inList.click();
    const listTimer = advance();
    return { set, moved, clearTimer, stale, listTimer };
  },
  async aMarkSetOnOnePartShowsOnTheOthers() {
    const app = boot({ replies: [{ passages: [passage(1, { text: sentences(110, 3) })] }] });
    app.screen.show(); await app.flush();
    const p = page(app, 0);
    p.el.querySelector(".review-mark-again").click(); await app.flush();
    const pressed = [];
    for (let i = 0; i < p.parts.length; i++) { pressed.push(p.el.querySelector(".review-mark-again").getAttribute("aria-pressed")); p.el.querySelector(".review-next").click(); }
    return { parts: p.parts.length, pressed };
  },
  async keysXAndBMarkOnDesktopAndAreIgnoredWhileTyping() {
    const app = boot({ phone: false, replies: [{ passages: [passage(1, { text: sentences(150, 3) }), passage(2)] }] });
    app.screen.show(); await app.flush();
    const press = (key, target = new El("div")) => { const e = { key, target, preventDefault() { this.prevented = true; } }; for (const fn of documentStub.listeners.keydown) fn(e); return e; };
    const p = page(app, 0);
    press("x"); await app.flush();
    press("B"); await app.flush();
    const typing = new El("input"); typing.tagName = "input";
    press("x", typing); await app.flush();
    press("ArrowRight"); const part = p.part;
    return { writes: app.calls.api.filter(([m]) => m !== "GET").map(([m, , body]) => [m, body]), part,
      pressed: [p.el.querySelector(".review-mark-done").getAttribute("aria-pressed"), p.el.querySelector(".review-mark-again").getAttribute("aria-pressed")] };
  },
  async aFailedSaveGivesTheFillBack() {
    const fail = Object.assign(new Error("x"), { status: 500 });
    const app = boot({ replies: [{ passages: [passage(1)] }, fail] });
    app.screen.show(); await app.flush();
    const again = page(app, 0).el.querySelector(".review-mark-again");
    again.click();
    const during = again.getAttribute("aria-pressed");
    await app.flush();
    return { during, after: again.getAttribute("aria-pressed"), notices: app.calls.notices };
  },
  async circlesNeverShowThePillButThePassageDoesAndAnAgainServeStartsFilled() {
    const app = boot({ replies: [{ passages: [passage(1, { mark: "again", again: true }), passage(2)] }, {}, {}] });
    app.screen.show(); await app.flush();
    const el = page(app, 0).el; const pill = app.els["review-ask"];
    const filled = el.querySelector(".review-mark-again").getAttribute("aria-pressed");
    el.querySelector(".review-mark-done").click();
    const afterCircle = pill.hidden;
    tapAt(el, 200);
    const afterPassage = { hidden: pill.hidden, ask: app.els["review"].dataset.ask };
    tapAt(el, 200);
    app.seen(0); app.fire(); await app.flush();
    return { filled, afterCircle, afterPassage, afterHide: app.els["review"].dataset.ask || null,
      shown: app.calls.api.filter(([m, path]) => m === "POST" && path === "/api/review/shown").map(([, , body]) => body) };
  },
  async listModeSetsTheShuffleAsideAndRestoresIt() {
    const app = boot({ replies: [{ passages: [passage(1), passage(2), passage(3)] }, {}, {}, {}] });
    app.screen.show(); await app.flush();
    app.seen(1); await app.flush();
    const before = { pages: app.screen.pages().map((p) => p.passage.passage_key), current: 1, children: app.els["review-pager"].children.length };
    const apiBefore = app.calls.api.length;
    const listed = [passage(11, { mark: "again", type: "quote" }), passage(12, { mark: "again" }), passage(13, { mark: "again" })];
    app.screen.enterList("again", listed, 1);
    app.screen.show();
    const inList = { keys: app.screen.pages().map((p) => p.passage.passage_key), classes: app.els["review"].className, bar: app.els["review-listbar"].hidden,
      name: app.els["review-list-name"].textContent, count: app.els["review-list-count"].textContent,
      caption: app.els["review-list-caption"].textContent, appFlag: app.els.app.dataset.reviewList,
      filled: app.screen.pages().map((p) => p.el.querySelector(".review-mark-again").getAttribute("aria-pressed")) };
    app.seen(1); app.seen(2); await app.flush();
    const atEnd = { count: app.els["review-list-count"].textContent, caption: app.els["review-list-caption"].textContent };
    const timers = app.calls.timers.filter((t) => t.live && t.ms === 1000).length;
    app.screen.leave();
    const after = { pages: app.screen.pages().map((p) => p.passage.passage_key), children: app.els["review-pager"].children.length, classes: app.els["review"].className,
      bar: app.els["review-listbar"].hidden, appFlag: app.els.app.dataset.reviewList || null };
    return { before, inList, atEnd, timers, after, extraApi: app.calls.api.length - apiBefore };
  },
  async inAListAMarkClearedStaysOnScreenUnfilledAndBackGoesToTheList() {
    const app = boot({ replies: [{ passages: [passage(1)] }, {}, {}] });
    app.screen.show(); await app.flush();
    app.screen.enterList("done", [passage(11, { mark: "done" }), passage(12, { mark: "done" })], 0);
    app.screen.show();
    const p = app.screen.pages()[0];
    const done = p.el.querySelector(".review-mark-done");
    const start = done.getAttribute("aria-pressed");
    done.click(); await app.flush();
    const writes = app.calls.api.filter(([m]) => m !== "GET").map(([m, path]) => [m, path.split("/").pop()]);
    app.els["review-list-back"].click();
    return { start, afterClear: [done.getAttribute("aria-pressed"), p.el.querySelector(".review-mark-again").getAttribute("aria-pressed")], onScreen: app.screen.pages().length,
      writes, backed: app.calls.backed };
  },
  async anAnswerThatArrivesInAListIsDropped() {
    let release; const gate = new Promise((r) => { release = r; });
    const app = boot({ replies: [gate.then(() => ({ passages: [passage(1), passage(2)] }))] });
    app.screen.show();
    app.screen.enterList("again", [passage(11, { mark: "again" })], 0);
    release(); await app.flush();
    const inList = app.screen.pages().map((p) => p.passage.passage_key);
    app.screen.leaveList();
    return { inList, afterLeave: app.screen.pages().length };
  },
  async everyPassageDoneShowsItsOwnStateWithALinkToTheDoneList() {
    const app = boot({ replies: [{ passages: [] }, { every: 10, again: 0, done: 4 }, { passages: [] }, { every: 10, again: 0, done: 0 }] });
    app.screen.show(); await app.flush();
    const box = app.els["review-state"];
    const text = box.textContent; const link = box.querySelector("button");
    link.click();
    const second = boot({ replies: [{ passages: [] }, { every: 10, again: 0, done: 0 }] });
    second.screen.show(); await second.flush();
    return { text, shown: !box.hidden, opened: app.calls.opened, plain: second.els["review-state"].textContent };
  },
  async tailStaysPutWhileFetching() {
    const app = boot({ replies: [{ passages: [passage(1), passage(2)] }, { passages: [passage(3)] }] });
    app.screen.show(); await app.flush();
    const pager = app.els["review-pager"];
    const tail = pager.children[pager.children.length - 1];
    app.seen(1); await app.flush();
    const after = pager.children.map((c) => c.className.includes("review-tail") ? "tail" : "page");
    return { sameTail: pager.children[pager.children.length - 1] === tail, order: after };
  },
};

scenarios[process.argv[2]]().then((out) => console.log(JSON.stringify(out)), (error) => { console.error(error.stack); process.exit(1); });
