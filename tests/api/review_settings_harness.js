"use strict";
// Runs app/web/review-settings.js (and the tab switching in settings.js) against the small fake DOM and prints what a
// scenario observed as JSON. Usage: node tests/api/review_settings_harness.js <scenario>
const fs = require("fs");
const vm = require("vm");

const { El } = require("./fake_dom");

const documentStub = { hidden: false, listeners: {}, createElement: (tag) => new El(tag),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); } };
El.document = documentStub;

const KEY = (n) => String(n).padStart(32, "0");
const passage = (n, mark, extra = {}) => ({ passage_key: KEY(n), text: `Passage ${n}.`, title: `Title ${n}`, author: "Ada", mark, marked_at: `2026-11-0${n}T10:00:00+00:00`, ...extra });

function boot({ routes = {}, tab = "review" } = {}) {
  const els = {};
  const calls = { api: [], views: [], entered: [], tabs: [] };
  const context = {
    console, document: documentStub,
    $: (id) => (els[id] ||= Object.assign(new El(id === "settings" ? "section" : "div"), { id })),
    api: async (method, path, body) => {
      calls.api.push([method, path, body || null]);
      const handler = routes[`${method} ${path}`];
      const out = typeof handler === "function" ? handler(body) : handler;
      if (out instanceof Error) throw out;
      return out === undefined ? {} : out;
    },
    setView: async (view) => { calls.views.push(view); },
    window: { reviewScreen: { enterList: (...args) => calls.entered.push(args) } },
    setTimeout, clearTimeout,
  };
  context.globalThis = context;
  vm.createContext(context);
  for (const file of ["settings.js", "review-settings.js"]) vm.runInContext(fs.readFileSync(`app/web/${file}`, "utf8"), context, { filename: file });
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };
  return { els, calls, flush, win: context.window };
}

const SETTINGS = (every, again = 3, done = 1) => ({ every, again, done });
const dotsOn = (app) => app.els["every-dots"].children.map((d) => d.className.includes("is-on"));
const fail = (status = 500) => Object.assign(new Error("x"), { status });

const scenarios = {
  async tabShowsLevelWordNumberAndDots() {
    const out = {};
    for (const every of [10, 5, 3, 2]) {
      const app = boot({ routes: { "GET /api/review/settings": SETTINGS(every) } });
      await app.win.reviewSettings.show(); await app.flush();
      out[every] = { word: app.els["every-word"].textContent, dots: dotsOn(app), n: app.els["every-n-full"].textContent, short: app.els["every-n-short"].textContent,
        less: app.els["every-less"].disabled, more: app.els["every-more"].disabled, label: app.els["every-dots"].getAttribute("aria-label"),
        counts: [app.els["again-count"].textContent, app.els["done-count"].textContent] };
    }
    return out;
  },
  async steppersSaveAndFailureReverts() {
    const app = boot({ routes: { "GET /api/review/settings": SETTINGS(10), "PUT /api/review/settings": (body) => SETTINGS(body.every) } });
    await app.win.reviewSettings.show(); await app.flush();
    app.els["every-more"].click(); app.els["every-more"].click();
    const optimistic = app.els["every-word"].textContent;
    await app.flush();
    const saved = app.calls.api.filter(([m]) => m === "PUT").map(([, , body]) => body);
    const failing = boot({ routes: { "GET /api/review/settings": () => SETTINGS(5), "PUT /api/review/settings": fail() } });
    await failing.win.reviewSettings.show(); await failing.flush();
    failing.els["every-less"].click();
    const during = failing.els["every-word"].textContent;
    await failing.flush();
    return { optimistic, saved, final: app.els["every-word"].textContent, during, afterFail: failing.els["every-word"].textContent,
      message: failing.els["review-settings-message"].textContent, caption: app.els["every-n-full"].textContent };
  },
  async listsOpenNewestFirstWithTheSubLineAndAnEmptyOne() {
    const app = boot({ routes: {
      "GET /api/review/settings": SETTINGS(5, 2, 0),
      "GET /api/review/marks/again": { passages: [passage(3, "again"), passage(2, "again")], count: 2 },
      "GET /api/review/marks/done": { passages: [], count: 0 } } });
    await app.win.reviewSettings.show(); await app.flush();
    app.els["open-again"].click(); await app.flush();
    const again = { title: app.els["list-title"].textContent, sub: app.els["list-sub"].textContent, rows: app.els["list-rows"].children.map((r) => r.textContent),
      mainHidden: app.els["settings-main"].hidden, listHidden: app.els["settings-list"].hidden, isList: app.els["settings"].classList.contains("is-list"),
      moves: app.els["list-rows"].children.map((r) => r.querySelectorAll("button")[2].textContent), empty: app.els["list-empty"].hidden };
    app.els["list-back"].click(); await app.flush();
    const afterBack = { main: app.els["settings-main"].hidden };
    app.els["open-done"].click(); await app.flush();
    const done = { title: app.els["list-title"].textContent, sub: app.els["list-sub"].textContent, rows: app.els["list-rows"].children.length, empty: app.els["list-empty"].hidden,
      emptyText: app.els["list-empty"].textContent };
    return { again, done, afterBack };
  },
  async moveToDoneMovesTheRowAndBothCounts() {
    const app = boot({ routes: {
      "GET /api/review/settings": SETTINGS(10, 2, 1),
      "GET /api/review/marks/again": { passages: [passage(3, "again"), passage(2, "again")], count: 2 },
      "PUT /api/review/marks/00000000000000000000000000000003": { passage_key: KEY(3), mark: "done" } } });
    await app.win.reviewSettings.show(); await app.flush();
    app.els["open-again"].click(); await app.flush();
    const move = app.els["list-rows"].children[0].querySelectorAll("button")[2];
    move.click(); await app.flush();
    const writes = app.calls.api.filter(([m]) => m === "PUT").map(([, path, body]) => [path.split("/").pop(), body]);
    const rows = app.els["list-rows"].children.length; const sub = app.els["list-sub"].textContent;
    app.els["list-back"].click(); await app.flush();
    return { writes, rows, sub, counts: [app.els["again-count"].textContent, app.els["done-count"].textContent] };
  },
  async readOpensTheListPassageInReviewAndBackComesBack() {
    const passages = [passage(3, "again"), passage(2, "again"), passage(1, "again")];
    const app = boot({ routes: { "GET /api/review/settings": SETTINGS(10, 3, 0), "GET /api/review/marks/again": { passages, count: 3 } } });
    await app.win.reviewSettings.show(); await app.flush();
    app.els["open-again"].click(); await app.flush();
    app.els["list-rows"].children[1].querySelectorAll("button")[0].click();  // the row itself opens the passage
    await app.flush();
    const opened = { entered: app.calls.entered.map(([name, list, index]) => [name, list.length, index]), views: app.calls.views.slice() };
    app.els["list-rows"].children[2].querySelectorAll("button")[1].click();  // the "Read" action
    await app.flush();
    app.win.settingsScreen.chooseTab("review");
    await app.win.reviewSettings.backToList("again"); await app.flush();
    await app.win.settingsScreen.show(); await app.flush();  // the shell shows Settings again
    return { opened, entered: app.calls.entered.map(([name, , index]) => [name, index]), views: app.calls.views,
      listAgain: app.els["settings-list"].hidden, title: app.els["list-title"].textContent,
      gets: app.calls.api.filter(([m, p]) => m === "GET" && p.includes("marks/again")).length };
  },
  async tabsSwitchPanelsAndTheReviewTabLoads() {
    const app = boot({ routes: { "GET /api/review/settings": SETTINGS(3) } });
    const state = () => ({ tags: [app.els["settings-panel-tags"].hidden, app.els["settings-tab-tags"].getAttribute("aria-current")],
      review: [app.els["settings-panel-review"].hidden, app.els["settings-tab-review"].getAttribute("aria-current")] });
    app.els["settings-tab-review"].click(); await app.flush();
    const onReview = state(); const word = app.els["every-word"].textContent;
    app.els["settings-tab-tags"].click(); await app.flush();
    return { onReview, word, onTags: state() };
  },
  async everyPassageDoneLinkOpensTheDoneListFromReview() {
    const app = boot({ routes: { "GET /api/review/settings": SETTINGS(10, 0, 2), "GET /api/review/marks/done": { passages: [passage(1, "done")], count: 1 } } });
    await app.win.reviewSettings.openFromReview("done"); await app.flush();
    const before = { views: app.calls.views.slice() };
    await app.win.settingsScreen.show(); await app.flush();  // the shell shows Settings
    return { before, title: app.els["list-title"].textContent, listHidden: app.els["settings-list"].hidden, reviewPanel: app.els["settings-panel-review"].hidden };
  },
};

scenarios[process.argv[2]]().then((out) => console.log(JSON.stringify(out)), (error) => { console.error(error.stack); process.exit(1); });
