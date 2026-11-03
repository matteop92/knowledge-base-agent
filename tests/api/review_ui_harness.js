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
  for (const id of ["app", "review", "review-pager", "review-state", "review-hint", "review-ask", "review-live"]) {
    els[id] = new El("div"); els[id].id = id;
  }
  els["review-state"].hidden = true; els["review-hint"].hidden = true; els["review-ask"].hidden = true;
  const calls = { api: [], asked: [], timers: [] };
  const queue = replies.slice();
  const stored = {};
  class FakeObserver { constructor(cb) { this.cb = cb; this.watched = []; observers.push(this); } observe(el) { this.watched.push(el); } }
  const observers = [];
  const context = {
    console, document: documentStub, window: { readerBlocks: { inline: (tag, cls, text) => { const el = new El(tag); el.className = cls; el.textContent = text; return el; } } },
    $: (id) => { if (!els[id]) throw new Error(`no element ${id}`); return els[id]; },
    icon: (name) => { const el = new El("svg"); el.dataset.name = name; return el; },
    phone: { matches: phone, listeners: [], addEventListener(type, fn) { this.listeners.push(fn); } },
    state: { view: "review" }, sheet: { open: false },
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
  async hintShowsOnTheFirstPassagesOnly() {
    const app = boot({ replies: [{ passages: [passage(1)] }] });
    app.stored["ka-review-hint"] = "5";
    app.screen.show(); await app.flush();
    const late = app.els["review-hint"].hidden;
    const fresh = boot({ replies: [{ passages: [passage(1)] }] });
    fresh.screen.show(); await fresh.flush();
    return { lateHidden: late, freshHidden: fresh.els["review-hint"].hidden, text: fresh.els["review-hint"].textContent };
  },
};

scenarios[process.argv[2]]().then((out) => console.log(JSON.stringify(out)), (error) => { console.error(error.stack); process.exit(1); });
