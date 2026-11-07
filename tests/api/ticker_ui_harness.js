"use strict";
// Runs app/web/ticker.js against a small fake DOM and prints what a scenario observed as JSON.
// Usage: node tests/api/ticker_ui_harness.js <scenario>
const fs = require("fs");
const vm = require("vm");

const { El } = require("./fake_dom");

const documentStub = { createElement: (tag) => new El(tag) };
El.document = documentStub;

const SOURCES = [
  { id: "s1", title: "A post on X", type: "social_post", platform: "x", author: "Ada Stone", added_at: "2026-11-06T10:00:00Z" },
  { id: "s2", title: "A LinkedIn post", type: "social_post", platform: "linkedin", author: "Bo Lane" },
  { id: "s3", title: "A Reddit thread", type: "social_post", platform: "reddit" },
  { id: "s4", title: "A post on the web", type: "social_post", platform: "website", author: "Cy Moss" },
  { id: "s5", title: "An article", type: "article", author: "Di Park" },
  { id: "s6", title: "A book", type: "book", author: "Ed Ross" },
  { id: "s7", title: "Some highlights", type: "highlights", author: "Flo Reed" },
  { id: "s8", title: "A note", type: "note" },
  { id: "s9", title: "A quote", type: "quote", author: "Gus Hale" },
  { id: "s10", title: "A talk", type: "transcript", author: "Hal Wynn" },
  { id: "s11", title: "A new kind", type: "flashcard" },
];

function boot({ bandWidth = 400, trackWidth = 1200, reduced = false, slow = false } = {}) {
  const els = {};
  for (const id of ["ticker", "ticker-track"]) { els[id] = new El("div"); els[id].id = id; }
  els.ticker.hidden = true;
  els.ticker.rect = { width: bandWidth };
  els["ticker-track"].rect = { width: trackWidth };
  els["ticker-track"].style = { setProperty(name, value) { this[name] = value; } };
  const calls = { api: [], opened: [] };
  const state = { reply: { sources: [] } };
  const release = [];
  const context = {
    console, document: documentStub,
    window: { matchMedia: (query) => ({ matches: reduced && query.includes("reduce") }), sourcesScreen: { openById: async (id) => { calls.opened.push(id); return true; } } },
    $: (id) => { if (!els[id]) throw new Error(`no element ${id}`); return els[id]; },
    icon: (name) => { const el = new El("svg"); el.dataset.name = name; el.className = "icon"; return el; },
    api: async (method, path) => {
      calls.api.push([method, path]);
      if (slow) return new Promise((resolve) => { release.push(resolve); });
      if (state.reply instanceof Error) throw state.reply;
      return state.reply;
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync("app/web/ticker.js", "utf8"), context, { filename: "ticker.js" });
  return { els, calls, state, release, ticker: context.window.ticker };
}

const buttons = (el) => el.querySelectorAll("button");
const copies = (app) => app.els["ticker-track"].children;
const summary = (app) => ({
  hidden: app.els.ticker.hidden,
  scrolling: app.els["ticker-track"].classList.contains("is-scrolling"),
  duration: app.els["ticker-track"].style["--ticker-duration"] || null,
  copies: copies(app).map((copy) => ({ hidden: copy.getAttribute("aria-hidden"), buttons: buttons(copy).length })),
});

const scenarios = {
  async mixed() {
    const app = boot();
    app.state.reply = { sources: SOURCES };
    await app.ticker.load();
    const real = buttons(copies(app)[0]);
    return {
      api: app.calls.api,
      ...summary(app),
      items: real.map((button) => ({
        label: button.getAttribute("aria-label"),
        type: button.type,
        icon: button.querySelector("svg").dataset.name,
        parts: button.children.map((c) => `${c.tagName}.${c.className.replace(/^icon /, "")}`),
        title: button.querySelector("span.ticker-title").textContent,
        author: (button.querySelector("span.ticker-author") || { textContent: null }).textContent,
        tabindex: button.getAttribute("tabindex"),
      })),
      copyTabindexes: [...new Set(buttons(copies(app)[1]).map((b) => b.getAttribute("tabindex")))],
      titlesInDom: SOURCES.map((s) => app.els["ticker-track"].querySelectorAll("span.ticker-title").filter((t) => t.textContent === s.title).length),
    };
  },
  async fits() {
    const app = boot({ bandWidth: 1000, trackWidth: 800 });
    app.state.reply = { sources: SOURCES.slice(0, 3) };
    await app.ticker.load();
    return summary(app);
  },
  async reducedMotion() {
    const app = boot({ reduced: true });
    app.state.reply = { sources: SOURCES };
    await app.ticker.load();
    return summary(app);
  },
  async notOnScreen() {
    const app = boot({ bandWidth: 0, trackWidth: 0 });
    app.state.reply = { sources: SOURCES };
    await app.ticker.load();
    return summary(app);
  },
  async speed() {
    const app = boot({ bandWidth: 400, trackWidth: 2000 });
    app.state.reply = { sources: SOURCES };
    await app.ticker.load();
    return summary(app);
  },
  async emptyAndFailure() {
    const app = boot();
    const out = {};
    app.state.reply = { sources: SOURCES };
    await app.ticker.load();
    out.shown = summary(app).hidden;
    app.state.reply = { sources: [] };
    await app.ticker.load();
    out.empty = summary(app).hidden;
    app.state.reply = { sources: SOURCES };
    await app.ticker.load();
    app.state.reply = Object.assign(new Error("down"), { status: 503 });
    await app.ticker.load();
    out.failed = summary(app).hidden;
    app.state.reply = {};
    await app.ticker.load();
    out.malformed = summary(app).hidden;
    return out;
  },
  async click() {
    const app = boot();
    app.state.reply = { sources: SOURCES };
    await app.ticker.load();
    buttons(copies(app)[0])[4].click();
    buttons(copies(app)[1])[7].click();  // the repeated copy opens the same source
    return app.calls.opened;
  },
  async newestLoadWins() {
    // Two loads in flight: the first answers last and must not draw over the second.
    const app = boot({ slow: true });
    const first = app.ticker.load();
    const second = app.ticker.load();
    app.release[1]({ sources: SOURCES.slice(0, 2) });
    await second;
    app.release[0]({ sources: SOURCES.slice(0, 1) });
    await first;
    return buttons(copies(app)[0]).length;
  },
};

scenarios[process.argv[2]]().then((out) => console.log(JSON.stringify(out)), (error) => { console.error(error.stack); process.exit(1); });
