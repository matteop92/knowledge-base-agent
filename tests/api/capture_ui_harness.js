"use strict";
// Runs app/web/capture-fields.js, capture-row.js and capture.js against the small fake DOM and prints what a scenario observed as
// JSON. The Add card's optional row is built here in the shape index.html gives it. Usage: node tests/api/capture_ui_harness.js <scenario>
const fs = require("fs");
const vm = require("vm");

const { El } = require("./fake_dom");

// The fake DOM does not move a node that is appended again; the row moves buttons and the list, so it must.
const add = El.prototype._add;
El.prototype._add = function (node) {
  if (node && typeof node === "object" && node.parent) node.parent.children = node.parent.children.filter((c) => c !== node);
  add.call(this, node);
};
const insertBefore = El.prototype.insertBefore;
El.prototype.insertBefore = function (node, ref) {
  if (node.parent) node.parent.children = node.parent.children.filter((c) => c !== node);
  insertBefore.call(this, node, ref);
};
El.prototype.prepend = function (node) { node.parent = this; this.children.unshift(node); };

const documentStub = { hidden: false, activeElement: null, listeners: {}, createElement: (tag) => new El(tag), querySelector: () => null, querySelectorAll: () => [],
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); } };
El.document = documentStub;

const SLOTS = { type: ["Type", "capture-type"], author: ["Author", "capture-author"], tags: ["Tags", "capture-tags"], title: ["Title", "capture-title"] };
const FIELDS = Object.keys(SLOTS);
const TAGS = [{ name: "marketing", count: 14 }, { name: "market-research", count: 3 }, { name: "ai", count: 9 }, { name: "farming", count: 0 }];
const PEOPLE = [{ name: "Naval Ravikant", count: 4 }, { name: "Paul Graham", count: 11 }];

function el(tag, className) {
  const node = new El(tag);
  node.className = className || "";
  return node;
}

function boot({ phone = false, share = null } = {}) {
  const els = {};
  const timers = new Map();
  let timerId = 0;
  const posts = [];
  const storage = new Map();
  const state = { captureEnabled: true, vaultName: "v" };
  const phoneMedia = { matches: phone, listeners: [], addEventListener(type, fn) { this.listeners.push(fn); } };
  const buttons = {};
  const named = (id, tag = "div") => (els[id] ||= Object.assign(new El(tag), { id }));
  // The row, as index.html builds it: a slot per field holding its chips, its "+ Field" button and its input.
  for (const [field, [label, inputId]] of Object.entries(SLOTS)) {
    const slot = named(`capture-slot-${field}`, "span");
    const button = el("button", "cf-add");
    button.dataset.field = field;
    buttons[field] = button;
    button.textContent = label;
    const wrap = el("span", "cf-input");
    wrap.hidden = true;
    const input = named(inputId, "input");
    if (field !== "title") input.attrs.role = "combobox";
    wrap.append(input);
    slot.append(el("span", "cf-chips"), button, wrap);
  }
  named("capture-row").append(...FIELDS.map((field) => els[`capture-slot-${field}`]));
  named("capture-suggest", "ul").hidden = true;
  named("capture-list-slot");
  named("capture-rest", "span");
  named("capture-line");
  named("capture-tag-status", "p");
  named("capture");
  named("capture-save", "button").disabled = true;
  const toast = named("toast");
  toast.append(el("span", "toast-title"), el("span", "toast-sub"));
  named("capture-text", "textarea").value = "https://paulgraham.com/makersschedule.html";
  named("capture-kind", "input").value = "text";
  named("capture-detect").hidden = true;
  const context = {
    console, document: documentStub, state, phone: phoneMedia, POLL_MS: 2000, POLL_RETRIES: 3, CSS: { escape: (x) => x },
    $: (id) => named(id),
    icon: (name) => Object.assign(el("svg", "icon"), { iconName: name }),
    load: (key) => (storage.has(key) ? storage.get(key) : null),
    store: (key, value) => { if (value === null) storage.delete(key); else storage.set(key, value); },
    localStorage: { length: 0, key: () => null },
    api: async (method, path, body) => {
      if (method === "POST" && path === "/api/captures") { posts.push(JSON.parse(JSON.stringify(body))); return { capture_id: "c1" }; }
      if (path === "/api/tags") return { approved: TAGS.map((t) => t.name), in_use: TAGS };
      if (path === "/api/capture-people") return { people: PEOPLE };
      if (path === "/api/captures") return { captures: [], enabled: true, vault_name: "v" };
      return {};
    },
    setView: async () => {}, openConversation: async () => {},
    setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; }, clearTimeout: (id) => { timers.delete(id); },
    setInterval: () => 0, location: { search: share ? "?share=1" : "" }, history: { replaceState() {} }, URLSearchParams,
    window: { addEventListener() {} },
  };
  // The Android share sheet's kept item: the service worker's cache holds it until the app picks it up.
  if (share) {
    const kept = { "/share/meta": { json: async () => share } };
    context.caches = { open: async () => ({ match: async (path) => kept[path] || null, delete: async (path) => { delete kept[path]; } }) };
    context.window.caches = context.caches;
  }
  context.globalThis = context;
  vm.createContext(context);
  for (const file of ["capture-fields.js", "capture-row.js", "capture.js"]) vm.runInContext(fs.readFileSync(`app/web/${file}`, "utf8"), context, { filename: file });
  for (const fn of documentStub.listeners.DOMContentLoaded || []) fn();
  documentStub.listeners = {};
  // Runs the short timers (the wait after a blur, the duplicate flash); the 2 s poll of a save is left alone.
  const runTimers = () => { for (const [id, t] of [...timers]) if (t.ms <= 1000) { timers.delete(id); t.fn(); } };
  const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };
  const slot = (field) => els[`capture-slot-${field}`];
  const input = (field) => els[SLOTS[field][1]];
  const button = (field) => buttons[field];
  const chipEls = (field) => slot(field).querySelector(".cf-chips").children;
  const chips = (field) => chipEls(field).map((c) => c.textContent);
  const suggest = els["capture-suggest"];
  const list = () => ({ shown: !suggest.hidden, rows: suggest.children.map((c) => c.textContent),
    active: suggest.children.filter((c) => c.classList.contains("is-active")).map((c) => c.textContent),
    in: suggest.parent === els["capture-list-slot"] ? "list-slot" : suggest.parent && suggest.parent.className });
  const atRest = () => ({ chips: FIELDS.map((f) => chips(f).join("|")), buttons: FIELDS.map((f) => !button(f).hidden), inputHidden: FIELDS.map((f) => slot(f).querySelector(".cf-input").hidden),
    collapsed: els.capture.classList.contains("is-collapsed"), listShown: !suggest.hidden });
  return {
    els, context, posts, slot, input, button, chips, chipEls, list, atRest, runTimers, flush,
    type: (field, text) => { input(field).value = text; input(field).dispatch("input"); },
    key: (field, name) => input(field).dispatch("keydown", { key: name }),
    rest: () => els["capture-rest"].children.filter((b) => !b.hidden).map((b) => b.textContent),
    save: async () => { els["capture-form"].dispatch("submit"); await flush(); },
    show: async () => { await context.window.captureScreen.show(); await flush(); },
    collapsed: () => els.capture.classList.contains("is-collapsed"),
    home: (field) => button(field).parent === slot(field),
  };
}

const scenarios = {
  async typePickShowsTheChipAndRemovingItClearsTheRequest() {
    const app = boot();
    await app.show();
    app.button("type").click();
    const opened = { list: app.list(), expanded: app.input("type").getAttribute("aria-expanded"), wrapShown: !app.slot("type").querySelector(".cf-input").hidden,
      buttonHidden: app.button("type").hidden, readonly: app.input("type").attrs.role };
    app.els["capture-suggest"].children[app.list().rows.indexOf("Post")].click();
    const picked = { chips: app.chips("type"), buttonHidden: app.button("type").hidden, listShown: app.list().shown };
    await app.save();
    app.els["capture-text"].value = "second note";
    app.els["capture-text"].dispatch("input");
    app.button("type").click();
    app.els["capture-suggest"].children[0].click();
    const removeButton = app.chipEls("type")[0].children.find((c) => c.className === "cf-remove");
    const removeLabel = removeButton.getAttribute("aria-label");
    removeButton.click();
    const afterRemove = { chips: app.chips("type"), button: !app.button("type").hidden };
    await app.save();
    return { opened, picked, posts: app.posts, removeLabel, afterRemove };
  },
  async authorsSuggestPeopleAndSeveralBecomeChips() {
    const app = boot();
    await app.show();
    app.button("author").click();
    const empty = app.list().shown;
    app.type("author", "nav");
    const typedNav = app.list();
    app.key("author", "ArrowUp");  // wraps round to the Add row
    const wrapped = app.list().active;
    app.key("author", "ArrowDown");
    app.key("author", "Enter");
    const first = { chips: app.chips("author"), value: app.input("author").value, stillOpen: !app.slot("author").querySelector(".cf-input").hidden };
    app.type("author", "Brand New");
    const second = app.list().rows;
    app.key("author", "Enter");  // the highlighted row is "Add", the only one
    const chipsNow = app.chips("author");
    await app.save();
    return { empty, typedNav, wrapped, first, second, chipsNow, posts: app.posts };
  },
  async tagsAreEmptyBeforeTypingThenPickCreateAndFlash() {
    const app = boot();
    await app.show();
    app.button("tags").click();
    const before = app.list().shown;
    app.type("tags", "mar");
    const rows = app.list().rows;
    app.key("tags", "Enter");  // the highlighted first row
    const picked = app.chips("tags");
    app.type("tags", "brand-new");
    const create = app.list().rows;
    app.key("tags", "Enter");
    const created = app.chips("tags");
    app.type("tags", "#!");
    const invalid = app.list().rows;
    app.input("tags").value = "";
    app.type("tags", "ai");
    app.key("tags", "Enter");
    app.type("tags", "ai");
    const noCreate = app.list().rows;
    app.key("tags", "Enter");  // the tag is already on the note: its chip flashes and nothing is added twice
    const flash = app.chipEls("tags").filter((c) => c.classList.contains("is-duplicate")).length;
    app.runTimers();
    const flashAfter = app.chipEls("tags").filter((c) => c.classList.contains("is-duplicate")).length;
    app.input("tags").value = "x1, x2,";
    app.input("tags").dispatch("input");
    return { before, rows, picked, create, created, invalid, noCreate, chips: app.chips("tags"), flash, flashAfter };
  },
  async tenTagsIsTheLimit() {
    const app = boot();
    await app.show();
    app.button("tags").click();
    for (let i = 0; i < 12 && !app.slot("tags").querySelector(".cf-input").hidden; i++) { app.type("tags", `t${i}`); app.key("tags", "Enter"); }
    return { chips: app.chips("tags").length, status: app.els["capture-tag-status"].textContent, buttonHidden: app.button("tags").hidden,
      open: !app.slot("tags").querySelector(".cf-input").hidden };
  },
  async keyboardMovesTheHighlightAndEscapeClosesTheListKeepingTheText() {
    const app = boot();
    await app.show();
    app.button("tags").click();
    app.type("tags", "mar");
    const open = { expanded: app.input("tags").getAttribute("aria-expanded"), active: app.input("tags").getAttribute("aria-activedescendant"), rows: app.list().rows.length };
    app.key("tags", "ArrowDown");
    const moved = { active: app.input("tags").getAttribute("aria-activedescendant"), highlighted: app.list().active };
    const esc = app.input("tags").dispatch("keydown", { key: "Escape" });
    const closed = { shown: app.list().shown, expanded: app.input("tags").getAttribute("aria-expanded"), text: app.input("tags").value,
      descendant: app.input("tags").getAttribute("aria-activedescendant"), stopped: esc.stopped === true, wrapShown: !app.slot("tags").querySelector(".cf-input").hidden };
    app.key("tags", "ArrowDown");
    const reopened = app.list().shown;
    const enter = app.input("tags").dispatch("keydown", { key: "Enter" });
    return { open, moved, closed, reopened, enterPrevented: enter.prevented === true,
      roles: [app.els["capture-suggest"].children[0] ? app.els["capture-suggest"].children[0].getAttribute("role") : null] };
  },
  async tappingOutsideClosesTheListAndKeepsTheText() {
    const app = boot();
    await app.show();
    app.button("tags").click();
    app.type("tags", "mar");
    app.input("tags").dispatch("blur");
    app.runTimers();
    const kept = { shown: app.list().shown, text: app.input("tags").value, wrapShown: !app.slot("tags").querySelector(".cf-input").hidden };
    app.input("tags").dispatch("focus");
    const refocused = app.list().shown;
    app.input("tags").value = "";
    app.input("tags").dispatch("blur");
    app.runTimers();
    return { kept, refocused, emptyClosed: app.atRest() };
  },
  async saveCommitsTypedTextFirstAndTheRowReturnsToRest() {
    const app = boot();
    await app.show();
    app.button("tags").click();
    app.input("tags").value = "Mar , #!";
    app.button("author").click();  // opening another field keeps what was typed in the first
    app.input("author").value = "  Ada   Lovelace ";
    app.button("title").click();
    app.input("title").value = "My title";
    app.button("type").click();
    app.els["capture-suggest"].children[0].click();  // Article
    const before = app.atRest();
    await app.save();
    return { before, posts: app.posts, after: app.atRest(), title: app.input("title").value };
  },
  async nothingSetSendsNeitherTypeNorAuthors() {
    const app = boot();
    await app.show();
    await app.save();
    return { posts: app.posts, keys: Object.keys(app.posts[0]).sort() };
  },
  async backspaceOnAnEmptyFieldRemovesTheLastChip() {
    const app = boot();
    await app.show();
    app.button("tags").click();
    for (const tag of ["one", "two", "three"]) { app.type("tags", tag); app.key("tags", "Enter"); app.key("tags", "Enter"); }
    const tagsBefore = app.chips("tags");
    app.type("tags", "x");  // text in the field: Backspace edits the text, it never takes a chip
    app.key("tags", "Backspace");
    const withText = app.chips("tags");
    app.input("tags").value = "";
    app.key("tags", "Backspace");
    const tagsAfterOne = { chips: app.chips("tags"), status: app.els["capture-tag-status"].textContent, open: !app.slot("tags").querySelector(".cf-input").hidden };
    app.key("tags", "Backspace");
    app.key("tags", "Backspace");
    const tagsAfterAll = app.chips("tags");
    app.key("tags", "Backspace");  // nothing left: nothing happens
    const tagsEmpty = { chips: app.chips("tags"), status: app.els["capture-tag-status"].textContent };
    app.button("author").click();
    for (const name of ["Ada Lovelace", "Alan Turing"]) { app.type("author", name); app.key("author", "Enter"); }
    const authorsBefore = app.chips("author");
    app.key("author", "Backspace");
    const authorsAfterOne = { chips: app.chips("author"), status: app.els["capture-tag-status"].textContent };
    app.key("author", "Backspace");
    const authorsAfterAll = app.chips("author");
    await app.save();
    return { tagsBefore, withText, tagsAfterOne, tagsAfterAll, tagsEmpty, authorsBefore, authorsAfterOne, authorsAfterAll, posts: app.posts };
  },
  async anAuthorAlreadyAddedIsNotAddedTwice() {
    const app = boot();
    await app.show();
    app.button("author").click();
    app.type("author", "paul");
    app.key("author", "Enter");  // Paul Graham, from the vault
    const first = app.chips("author");
    app.type("author", "PAUL   graham");  // same name, other letter case and spacing
    const rows = app.list().rows;
    app.key("author", "Enter");
    const status = app.els["capture-tag-status"].textContent;
    const after = { chips: app.chips("author"), value: app.input("author").value };
    await app.save();
    return { first, rows, status, after, posts: app.posts };
  },
  async tenAuthorsIsTheLimit() {
    const app = boot();
    await app.show();
    app.button("author").click();
    for (let i = 0; i < 12 && !app.slot("author").querySelector(".cf-input").hidden; i++) { app.type("author", `Person ${i}`); app.key("author", "Enter"); }
    const out = { chips: app.chips("author").length, buttonHidden: app.button("author").hidden, open: !app.slot("author").querySelector(".cf-input").hidden,
      status: app.els["capture-tag-status"].textContent };
    app.chipEls("author")[0].children.find((c) => c.className === "cf-remove").click();
    out.afterRemove = { chips: app.chips("author").length, buttonHidden: app.button("author").hidden };
    await app.save();
    out.posts = app.posts;
    return out;
  },
  async anInvalidTagStaysInTheFieldAndIsNotSent() {
    const app = boot();
    await app.show();
    app.button("tags").click();
    app.type("tags", "#!");
    const rows = app.list().rows;
    app.key("tags", "Enter");
    const typed = { chips: app.chips("tags"), value: app.input("tags").value, status: app.els["capture-tag-status"].textContent,
      open: !app.slot("tags").querySelector(".cf-input").hidden };
    app.input("tags").value = "good, #!";  // one valid, one not: the valid one becomes a chip, the other stays
    app.key("tags", "Enter");
    const mixed = { chips: app.chips("tags"), value: app.input("tags").value, status: app.els["capture-tag-status"].textContent };
    await app.save();
    return { rows, typed, mixed, posts: app.posts };
  },
  async aSharedTitleBecomesATitleChipAndIsSent() {
    const app = boot({ share: { title: "  A shared page  ", text: "Worth a read", url: "https://example.com/a" } });
    await app.show();
    app.els["capture-text"].value = "";
    await app.context.window.captureScreen.takeShared();
    await app.flush();
    const shown = { text: app.els["capture-text"].value, chips: app.chips("title"), titleButtonHidden: app.button("title").hidden };
    await app.save();
    return { shown, posts: app.posts };
  },
  async aSharedTitleIsCutToTwoHundredCharacters() {
    const app = boot({ share: { title: "t".repeat(250), text: "x", url: "" } });
    await app.show();
    await app.context.window.captureScreen.takeShared();
    await app.flush();
    await app.save();
    return { length: app.posts[0].title.length };
  },
  async onAPhoneAFocusedFieldCollapsesTheCardAndClosingRestoresIt() {
    const app = boot({ phone: true });
    await app.show();
    app.button("type").click();
    app.els["capture-suggest"].children[0].click();
    const rest0 = app.atRest();
    app.button("tags").click();
    app.type("tags", "mar");
    const open = { collapsed: app.collapsed(), line: app.els["capture-line"].textContent, rest: app.rest(), list: app.list(), typeChip: app.chips("type"),
      buttonsHome: ["author", "title"].map((f) => app.home(f)), tagsButtonHidden: app.button("tags").hidden };
    app.input("tags").dispatch("blur");
    const waiting = app.collapsed();  // not before the wait: a tap on Save or on "Title" must land where it was aimed
    app.runTimers();
    const closed = { collapsed: app.collapsed(), line: app.els["capture-line"].textContent, rest: app.rest(), text: app.input("tags").value,
      buttonsHome: ["author", "title"].map((f) => app.home(f)), order: app.els["capture-row"].children.map((c) => c.id), list: app.list() };
    const desk = boot();
    await desk.show();
    desk.button("tags").click();
    desk.type("tags", "mar");
    return { rest0, open, waiting, closed, desktop: { collapsed: desk.collapsed(), list: desk.list(), rest: desk.rest() } };
  },
};

scenarios[process.argv[2]]().then((out) => console.log(JSON.stringify(out)), (error) => { console.error(error.stack); process.exit(1); });
