"use strict";
// Runs app/web/sources.js, reader-blocks.js and source-filter.js against a small fake DOM and prints what a scenario observed as JSON.
// Usage: node tests/api/sources_ui_harness.js <scenario>
const fs = require("fs");
const vm = require("vm");

class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.parent = null; this.attrs = {}; this.dataset = {}; this.listeners = {};
    this.className = ""; this._text = ""; this.hidden = false; this.value = ""; this.id = ""; this.disabled = false;
    this.scrollTop = 0; this.style = {};
    const self = this;
    this.classList = {
      add: (...names) => { for (const n of names) if (!self.classList.contains(n)) self.className = `${self.className} ${n}`.trim(); },
      remove: (name) => { self.className = self.className.split(/\s+/).filter((n) => n && n !== name).join(" "); },
      contains: (name) => self.className.split(/\s+/).includes(name),
      toggle: (name, on) => { if (on === undefined ? !self.classList.contains(name) : on) self.classList.add(name); else self.classList.remove(name); },
    };
  }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  append(...nodes) { for (const n of nodes) this._add(n); }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  _add(node) {
    if (node && node.fragment) { for (const c of [...node.children]) this._add(c); node.children = []; return; }
    if (typeof node === "string") { this._text += node; return; }
    node.parent = this; this.children.push(node);
  }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) { const e = { preventDefault() { e.prevented = true; }, composedPath: () => [this], ...event }; for (const fn of this.listeners[type] || []) fn(e); return e; }
  click() { return this.dispatch("click"); }
  focus() { documentStub.activeElement = this; }
  scrollIntoView() {}
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  requestSubmit() { this.dispatch("submit"); }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  matches(compound) {
    const tag = /^[a-z0-9]+/i.exec(compound);
    if (tag && tag[0].toLowerCase() !== this.tagName) return false;
    for (const cls of compound.match(/\.[\w-]+/g) || []) if (!this.classList.contains(cls.slice(1))) return false;
    for (const [, k, v] of compound.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
      const key = k.startsWith("data-") ? k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase()) : k;
      const actual = k.startsWith("data-") ? this.dataset[key] : this.attrs[k];
      if (v === undefined ? actual === undefined : actual !== v) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/);
    let scope = [this];
    for (const part of parts) scope = scope.flatMap((node) => node.all().filter((el) => el.matches(part)));
    return [...new Set(scope)];
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  isConnected = true;
  getClientRects() { return [1]; }
}

const documentStub = { activeElement: null, listeners: {}, createElement: (tag) => new El(tag),
  createTextNode: (text) => Object.assign(new El("#text"), { _text: String(text) }),
  createDocumentFragment: () => Object.assign(new El("#fragment"), { fragment: true }),
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }, querySelector: () => null, querySelectorAll: () => [], getElementById: () => null, head: null };

function boot({ phone = false, sources = [], detail = null, detailStatus = 200 } = {}) {
  const page = fs.readFileSync("app/web/index.html", "utf8");
  const els = {};
  for (const match of page.matchAll(/<(\w+)([^>]*?)\sid="([\w-]+)"([^>]*)>/g)) {
    const el = new El(match[1]); el.id = match[3];
    el.hidden = /\shidden(\s|$|=)/.test(match[2] + " " + match[4]);
    els[match[3]] = el;
  }
  const child = (parent, tag, cls) => { const el = new El(tag); el.className = cls; parent.append(el); return el; };
  child(els["sources-empty"], "p", "sources-empty-title"); child(els["sources-empty"], "p", "sources-empty-sub");
  child(els["source-reader"], "div", "reader-scroll");
  const filters = child(els["sheet"], "section", ""); filters.dataset.sheet = "filters";
  els["sources-input"].value = "";
  const calls = { pushes: [], backs: 0, apis: [], asked: [], scopes: [], sheets: [] };
  const sheetState = { open: false, name: null };
  const window_ = { matchMedia: () => ({ matches: false, addEventListener() {} }) };
  const phoneQuery = { matches: phone, addEventListener() {} };
  const context = {
    console, document: documentStub, window: window_, setTimeout, clearTimeout, history: { pushState: (s) => calls.pushes.push(s), back: () => { calls.backs += 1; } },
    $: (id) => { if (!els[id]) throw new Error(`no element ${id}`); return els[id]; },
    icon: (name) => { const el = new El("svg"); el.className = "icon"; el.dataset.name = name; return el; },
    phone: phoneQuery, sheet: sheetState, state: { view: "sources" },
    api: async (method, path) => {
      calls.apis.push(`${method} ${path}`);
      if (path === "/api/sources") return { sources, vault_name: "Vault" };
      if (detailStatus !== 200) { const e = new Error("x"); e.status = detailStatus; throw e; }
      return detail || { body: [], source_url: null };
    },
    openSheet: (name, opener, fill) => { fill(filters); sheetState.open = true; sheetState.name = name; calls.sheets.push(name); },
    closeSheet: () => { sheetState.open = false; }, newQuestion: () => {}, setScope: (scope) => calls.scopes.push(scope),
    ask: (q) => calls.asked.push(q), announce() {},
  };
  context.globalThis = context;
  context.window.sourceFilter = undefined;
  vm.createContext(context);
  for (const file of ["app/web/source-filter.js", "app/web/reader-blocks.js", "app/web/sources.js"]) vm.runInContext(fs.readFileSync(file, "utf8"), context, { filename: file });
  for (const fn of documentStub.listeners.DOMContentLoaded || []) fn();
  const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
  return { els, calls, context, flush, screen: context.window.sourcesScreen, sheetState };
}

const cards = (app) => app.els["sources-list"].children.map((item) => item.children[0].children[0]);
const links = (app) => app.els["sources-list"].children.map((item) => item.children[0]);
const kinds = ["book", "highlights", "post", "note", "transcript", "article", "quote"];
const row = (i, type, title, extra = {}) => ({ id: `id-${i}`, type, title, authors: [], guests: [], tags: [], added: "2026-10-20", date: "2026-10-20",
  description: null, obsidian_url: "obsidian://open?vault=V&file=a.md", opens_in_obsidian: type === "book", ...extra });
const mixed = () => [
  row(1, "book", "Book title", { authors: ["Ada Stone"], book_year: "2015" }),
  row(2, "highlights", "Highlights title", { authors: ["Ada Stone"] }),
  row(3, "post", "Post title", { platform: "linkedin", source_name: "LinkedIn", authors: ["Cy Lane"], tags: ["x", "z"] }),
  row(4, "note", "Note title", { authors: ["Ben Hart"], tags: ["y"] }),
  row(5, "transcript", "Transcript title", { platform: "podcast", source_name: "Show Alpha", authors: ["Ben Hart"], guests: ["Ada Stone"] }),
  row(6, "article", "Article title", { platform: "website", source_name: "example.org", authors: ["Cy Lane"], tags: ["x"] }),
  row(7, "quote", "Quote title", { authors: ["Ada Stone"], quote_text: "Be the change." }),
];
const text = (el) => el.textContent;
const has = (el, cls) => el.all().some((c) => c.classList.contains(cls));

const scenarios = {
  async sevenCards() {
    const app = boot({ sources: mixed() }); app.screen.show(); await app.flush();
    return cards(app).map((c) => ({ cls: c.className, kicker: (c.querySelector(".s-kicker") || c.querySelector(".s-type")).textContent,
      label: links(app)[cards(app).indexOf(c)].getAttribute("aria-label") }));
  },
  async postWithNoPlatform() {
    const app = boot({ sources: [row(1, "post", "Plain post")] }); app.screen.show(); await app.flush();
    const card = cards(app)[0];
    return { top: text(card.querySelector(".s-topline")), dot: has(card, "dot"), mark: has(card, "s-mark"), author: has(card, "s-author"), voices: has(card, "s-voices") };
  },
  async voicesAndPreviews() {
    const app = boot({ sources: mixed() }); app.screen.show(); await app.flush();
    const [, , , note, transcript] = cards(app);
    const podcastNote = row(9, "note", "Podcast note", { platform: "podcast", source_name: "Podcast", authors: ["Ben Hart"] });
    const only = boot({ sources: [podcastNote] }); only.screen.show(); await only.flush();
    return { transcript: text(transcript.querySelector(".s-voices")), note: text(note.querySelector(".s-author")), noteVoices: has(note, "s-voices"),
      authorOnly: text(cards(only)[0].querySelector(".s-voices")), previews: cards(app).some((c) => has(c, "s-preview")) };
  },
  async activeTagPill() {
    const app = boot({ sources: mixed() }); app.screen.show(); await app.flush();
    const input = app.els["sources-input"]; input.value = "x"; input.dispatch("input");
    const tag = app.els["sources-suggest"].children.find((c) => c.classList.contains("ssuggest-row") && text(c).startsWith("x"));
    tag.click(); await app.flush();
    const pills = (c) => c.querySelectorAll(".s-pill").map((p) => [p.textContent, p.classList.contains("is-filter")]);
    return { post: pills(cards(app)[0]), article: pills(cards(app)[1]), results: text(app.els["sources-result"]) };
  },
  async cardTagCap() {
    const app = boot({ sources: [row(1, "note", "Many tags", { tags: ["a", "b", "c", "d", "e"] })] }); app.screen.show(); await app.flush();
    const input = app.els["sources-input"]; input.value = "e"; input.dispatch("input");
    const before = cards(app)[0].querySelectorAll(".s-pill").map((p) => p.textContent);
    const tag = app.els["sources-suggest"].children.find((c) => c.classList.contains("ssuggest-row") && text(c).startsWith("e"));
    tag.click(); await app.flush();
    return { before, filtered: cards(app)[0].querySelectorAll(".s-pill").map((p) => p.textContent) };
  },
  async cap() {
    const many = Array.from({ length: 80 }, (_, i) => row(i, "note", `Note ${i}`, { tags: ["t"] }));
    const app = boot({ sources: many }); app.screen.show(); await app.flush();
    const before = { cards: app.els["sources-list"].children.length, more: app.els["sources-more"].hidden, label: text(app.els["sources-more"]) };
    app.els["sources-more"].click();
    const after = { cards: app.els["sources-list"].children.length, more: app.els["sources-more"].hidden };
    const filtered = boot({ sources: many }); filtered.screen.show(); await filtered.flush();
    filtered.els["sources-input"].value = "Note"; filtered.els["sources-input"].dispatch("input");
    const title = filtered.els["sources-suggest"].children.find((c) => text(c).startsWith("Titles containing"));
    title.click(); await filtered.flush();
    return { before, after, filtered: { cards: filtered.els["sources-list"].children.length, more: filtered.els["sources-more"].hidden } };
  },
  async zeroMatches() {
    const app = boot({ sources: mixed() }); app.screen.show(); await app.flush();
    app.els["sources-input"].value = "zzz"; app.els["sources-input"].dispatch("input");
    app.els["sources-suggest"].children.find((c) => text(c).startsWith("Titles containing")).click(); await app.flush();
    return { cards: app.els["sources-list"].children.length, empty: app.els["sources-empty"].hidden, title: text(app.els["sources-empty"].querySelector(".sources-empty-title")),
      sub: text(app.els["sources-empty"].querySelector(".sources-empty-sub")) };
  },
  async keyboard() {
    const app = boot({ sources: mixed() }); app.screen.show(); await app.flush();
    const input = app.els["sources-input"]; input.value = "ada"; input.dispatch("input");
    const active = input.getAttribute("aria-activedescendant");
    const expanded = input.getAttribute("aria-expanded");
    input.dispatch("keydown", { key: "Enter" });
    const afterEnter = { tokens: app.els["sources-tokens"].children.length, value: input.value, closed: app.els["sources-suggest"].hidden };
    input.dispatch("keydown", { key: "Backspace" });
    return { active, expanded, afterEnter, afterBackspace: app.els["sources-tokens"].children.length, live: app.els["sources-result"].attrs["aria-live"] };
  },
  async noAuthor() {
    const app = boot({ sources: [row(1, "post", "P"), row(2, "book", "B"), row(3, "article", "A")] }); app.screen.show(); await app.flush();
    return cards(app).map((c) => ({ byline: has(c, "s-byline") || has(c, "s-author") || has(c, "s-by"), text: text(c) }));
  },
  async quoteCard() {
    const app = boot({ sources: [row(1, "quote", "Q", { quote_text: "Be the change.", authors: ["Ada Stone"] }), row(2, "post", "P", { quote_text: "ignored" })] });
    app.screen.show(); await app.flush();
    const [quote, post] = cards(app);
    return { mark: text(quote.querySelector(".s-mark-text")), attribution: text(quote.querySelector(".s-attribution")), postText: text(post) };
  },
  async podcastMarks() {
    const app = boot({ sources: [row(1, "transcript", "T1", { platform: "podcast", source_name: "Show Alpha" }),
      row(2, "transcript", "T2", { platform: "podcast", source_name: "Podcast" })] });
    app.screen.show(); await app.flush();
    return cards(app).map((c) => ({ mark: c.querySelector(".s-mark").dataset.name, name: text(c.querySelector(".s-source")) }));
  },
  async phoneRows() {
    const app = boot({ phone: true, sources: mixed() }); app.screen.show(); await app.flush();
    app.els["sources-input"].value = "ada";
    const rows = cards(app);
    const empty = text(app.els["sources-filters"]);
    const aria0 = app.els["sources-filters"].attrs["aria-label"];
    const badge0 = app.els["sources-badge"].hidden;
    app.els["sources-input"].value = "x"; app.els["sources-input"].dispatch("input");
    app.els["sources-suggest"].children.find((c) => c.classList.contains("ssuggest-row") && text(c).startsWith("x")).click(); await app.flush();
    return { rows: rows.map((r) => [r.className, r.querySelector(".s-thumb").className, has(r, "s-preview"), has(r, "s-pill")]),
      badge0, aria0, aria1: app.els["sources-filters"].attrs["aria-label"], badge: text(app.els["sources-badge"]) };
  },
  async filterSheet() {
    const app = boot({ phone: true, sources: mixed().map((s, i) => ({ ...s, tags: ["alpha", "beta"].slice(0, (i % 2) + 1) })) });
    app.screen.show(); await app.flush();
    app.els["sources-filters"].click();
    const section = app.els["sheet"].querySelector('[data-sheet="filters"]');
    const open = { sheets: app.calls.sheets, show: text(section.querySelector(".f-show")) };
    const kickers = section.querySelectorAll(".f-section").map((x) => text(x.querySelector(".kicker")));
    section.querySelectorAll(".f-pill").filter((p) => p.dataset.focus === "type:note")[0].click();
    const afterType = text(section.querySelector(".f-show"));
    const summaries = section.querySelectorAll(".f-nav").map((n) => [text(n.querySelector(".f-nav-name")), text(n.querySelector(".f-nav-value"))]);
    section.querySelectorAll(".f-nav").filter((n) => n.dataset.focus === "facet:tags")[0].click();
    const page = section.querySelector(".f-facet");
    const facet = { facet: page.dataset.facet, title: text(page.querySelector(".f-title")), rows: page.querySelectorAll(".f-row").length };
    page.querySelectorAll(".f-row")[0].click();
    const picked = text(section.querySelector(".f-picked"));
    section.querySelector(".f-back").click();
    const afterBack = section.querySelectorAll(".f-nav").map((n) => text(n.querySelector(".f-nav-value")));
    section.querySelector(".f-clear").click();
    return { open, kickers, afterType, summaries, facet, picked, afterBack, afterClear: text(section.querySelector(".f-show")), badge: app.els["sources-badge"].hidden };
  },
  async facetPeople() {
    const people = Array.from({ length: 40 }, (_, i) => `${String.fromCharCode(65 + (i % 26))}name Person${i}`);
    const many = people.map((name, i) => row(i, "note", `Note ${i}`, { authors: [name] }));
    const app = boot({ phone: true, sources: many }); app.screen.show(); await app.flush();
    app.els["sources-filters"].click();
    const section = app.els["sheet"].querySelector('[data-sheet="filters"]');
    section.querySelectorAll(".f-nav").filter((n) => n.dataset.focus === "facet:people")[0].click();
    const page = section.querySelector(".f-facet");
    const headings = page.querySelectorAll(".f-group").map((g) => text(g));
    const rail = page.querySelectorAll(".f-rail-letter").length;
    const placeholder = page.querySelector("input").placeholder;
    const input = page.querySelector("input");
    input.value = "name person3"; input.dispatch("input");
    const searched = { groups: page.querySelectorAll(".f-group").map((g) => text(g)), rows: page.querySelectorAll(".f-row").length, bold: page.querySelectorAll(".f-match").length, rail: page.querySelectorAll(".f-rail-letter").length };
    page.querySelectorAll(".f-row")[0].click();
    const after = { sameInput: section.querySelector(".f-facet").querySelector("input") === input, picked: text(section.querySelector(".f-picked")), groups: section.querySelectorAll(".f-group").map((g) => text(g)).slice(0, 1) };
    input.value = "zzz"; input.dispatch("input");
    return { headings: headings.slice(0, 4), count: headings.length, rail, placeholder, searched, after, none: text(page.querySelector(".f-none")) };
  },
  async readerTranscript() {
    const detail = { source_url: null, body: [{ kind: "speaker_turn", text: "Hello.", speaker: "Ben Hart" }, { kind: "speaker_turn", text: "Hi.", speaker: "Ada Stone" }] };
    const app = boot({ sources: mixed(), detail }); app.screen.show(); await app.flush();
    const link = links(app)[4]; link.click(); await app.flush();
    const body = app.els["reader-body"];
    const labels = body.querySelectorAll(".r-speaker").map((l) => [l.textContent, l.classList.contains("is-guest")]);
    const markup = app.els["source-reader"].all().map((e) => e.className + " " + e.id + " " + e.textContent).join(" ");
    return { labels, pushes: app.calls.pushes.length, reader: app.els["source-reader"].hidden, list: app.els["sources"].hidden,
      title: text(app.els["reader-title"]), focus: documentStub.activeElement === app.els["reader-title"], counters: /\bof \d+|next|previous/i.test(markup),
      apis: app.calls.apis };
  },
  async readerMarkdown() {
    const detail = { source_url: null, body: [
      { kind: "paragraph", text: "Read [Opus 5.5](https://e.org/o) and [this](javascript:alert(1)) by **Ada** in *[[Note|a note]]*, 2 * 3 * 4." },
      { kind: "heading", text: "A [linked](https://e.org/h) heading", heading_path: ["A"] },
      { kind: "list_item", text: "1. First" }, { kind: "list_item", text: "Plain" }] };
    const app = boot({ sources: mixed(), detail }); app.screen.show(); await app.flush();
    links(app)[5].click(); await app.flush();
    const body = app.els["reader-body"];
    const anchors = body.querySelectorAll("a").map((a) => [a.textContent, a.href, a.target, a.rel]);
    return { anchors, strong: text(body.querySelector("strong")), em: text(body.querySelector("em")),
      paragraph: text(body.querySelector("p")), heading: text(body.querySelector("h2")),
      items: body.querySelectorAll("li").map((li) => [text(li), li.classList.contains("is-numbered")]) };
  },
  async readerCodeAndTables() {
    documentStub.head = new El("head");
    const detail = { source_url: null, body: [
      { kind: "table", text: "", rows: [["Name", "Route"], ["`plain`", "**a** | b"], ["short"]] },
      { kind: "code_block", language: "python", text: "# not a heading\nprint(1)" },
      { kind: "code_block", language: "mermaid", text: "flowchart TB\n  A --> B" }] };
    const app = boot({ sources: mixed(), detail }); app.screen.show(); await app.flush();
    links(app)[5].click(); await app.flush();
    const body = app.els["reader-body"];
    const before = body.querySelector("figure");
    const shownBefore = !before.hidden;
    const script = documentStub.head.children[0];
    script.onerror(); await app.flush();  // the library does not load: the diagram falls back to its source
    const figure = body.querySelector("figure");
    return { header: body.querySelectorAll("th").map(text), cells: body.querySelectorAll("td").map(text),
      inlineCode: body.querySelectorAll("code").filter((c) => c.classList.contains("r-inline-code")).map(text),
      strong: text(body.querySelector("strong")), rows: body.querySelectorAll("tr").length,
      pre: body.querySelectorAll("pre").map(text), headings: body.querySelectorAll("h2").length,
      script: script.src, shownBefore, fallback: { source: figure.classList.contains("is-source"), hidden: figure.hidden,
        text: text(figure) }, scriptsLeft: documentStub.head.children.length };
  },
  async readerImages() {
    const detail = { source_url: null, run_id: "run-9", body: [
      { kind: "image", index: 1, alt: "A chart" }, { kind: "image", index: 2, alt: "" }, { kind: "image", index: 3, alt: "Third" }] };
    const app = boot({ sources: mixed(), detail }); app.screen.show(); await app.flush();
    links(app)[5].click(); await app.flush();
    const body = app.els["reader-body"];
    const imgs = body.querySelectorAll("img");
    const before = { srcs: imgs.map((img) => img.src), alts: imgs.map((img) => img.alt),
      lazy: imgs.map((img) => img.loading), loading: body.querySelectorAll("figure.r-figure.is-loading").length };
    imgs[0].dispatch("load"); imgs[1].dispatch("error"); imgs[2].dispatch("error");
    return { ...before, figures: body.querySelectorAll("figure.r-figure").length, stillLoading: body.querySelectorAll("figure.is-loading").length,
      imgsLeft: body.querySelectorAll("img").length, captions: body.querySelectorAll("figcaption.r-caption").map(text) };
  },
  async readerDiagramConfig() {
    const open = async (text) => {
      documentStub.head = new El("head");
      const app = boot({ sources: mixed(), detail: { source_url: null, body: [{ kind: "code_block", language: "mermaid", text }] } });
      app.screen.show(); await app.flush();
      links(app)[5].click(); await app.flush();
      return app;
    };
    // Too long: shown as source at once and the library is never loaded.
    const long = await open("flowchart TB\n" + "  A --> B\n".repeat(6000));
    const oversize = { source: long.els["reader-body"].querySelector("figure").classList.contains("is-source"),
      scripts: documentStub.head.children.length };
    // Removing one directive joins the text around it into another; the library must get neither.
    const joined = await open('%%%%{x}%%{init: {"themeCSS": "svg{position:fixed}"}}%%\nflowchart TB\n  A --> B');
    const sent = [];
    joined.context.window.mermaid = { initialize() {}, render: async (id, text) => { sent.push(text); throw new Error("stop"); } };
    documentStub.head.children[0].onload(); await joined.flush();
    return { oversize, sent };
  },
  async readerLoadingAndFailure() {
    const app = boot({ sources: mixed(), detailStatus: 404 }); app.screen.show(); await app.flush();
    links(app)[3].click(); await app.flush();
    const buttons = app.els["reader-notice"].all().filter((e) => e.tagName === "button").map((b) => b.textContent);
    return { header: text(app.els["reader-title"]), notice: text(app.els["reader-notice"]), buttons, reloaded: app.calls.apis.filter((a) => a === "GET /api/sources").length };
  },
  async readerHighlights() {
    const detail = { source_url: null, body: [{ kind: "highlight", text: "Keep it small.", page_reference: "Page 12", personal_note: "Try it." }] };
    const app = boot({ sources: mixed(), detail }); app.screen.show(); await app.flush();
    links(app)[1].click(); await app.flush();
    const body = app.els["reader-body"];
    return { loc: text(body.querySelector(".r-loc")), note: text(body.querySelector(".r-note")) };
  },
  async noDescription() {
    const app = boot({ sources: [row(1, "note", "N"), row(2, "note", "D", { description: "In short." })] }); app.screen.show(); await app.flush();
    links(app)[0].click(); await app.flush();
    const bare = { lead: app.els["reader-lead"].hidden, inshort: app.els["reader-inshort"].hidden, rule: app.els["reader-rule"].hidden };
    app.els["reader-back"].click(); app.screen.popped();
    links(app)[1].click(); await app.flush();
    return { bare, full: { lead: app.els["reader-lead"].hidden, inshort: app.els["reader-inshort"].hidden, text: text(app.els["reader-lead"]) } };
  },
  async backContract() {
    const app = boot({ sources: mixed() }); app.screen.show(); await app.flush();
    app.els["sources"].scrollTop = 420;
    const link = links(app)[2]; link.click(); await app.flush();
    app.els["reader-back"].click();
    const afterButton = { backs: app.calls.backs, list: app.els["sources"].hidden, scroll: app.els["sources"].scrollTop, focus: documentStub.activeElement === link };
    app.screen.popped(); app.screen.popped();
    // a tab switch while the reader is open goes back once and leaves no entry behind
    links(app)[3].click(); await app.flush();
    app.screen.leave(); app.screen.popped();
    // a phone Back press closes the reader without calling back itself
    links(app)[3].click(); await app.flush();
    const before = app.calls.backs;
    app.screen.popped();
    return { afterButton, pushes: app.calls.pushes.length, backsAfterLeave: before, backsAfterPhoneBack: app.calls.backs, readerHidden: app.els["source-reader"].hidden };
  },
  async askAbout() {
    const app = boot({ sources: mixed(), detail: { body: [], source_url: null } }); app.screen.show(); await app.flush();
    links(app)[3].click(); await app.flush();
    app.els["reader-ask"].click();
    const first = { scopes: app.calls.scopes.slice(), asked: app.calls.asked.slice(), reader: app.els["source-reader"].hidden, backs: app.calls.backs };
    links(app)[3].click(); await app.flush();
    app.els["reader-ask-input"].value = "what is it?"; app.els["reader-askbox"].dispatch("submit");
    return { first, second: { scopes: app.calls.scopes.slice(), asked: app.calls.asked.slice() }, hint: app.els["reader-ask-input"].placeholder };
  },
  async booksLeaveTheApp() {
    const app = boot({ sources: mixed() }); app.screen.show(); await app.flush();
    const book = links(app)[0];
    return { tag: book.tagName, href: book.href, out: has(cards(app)[0], "s-out"), pushes: app.calls.pushes.length };
  },
};

scenarios[process.argv[2]]().then((out) => console.log(JSON.stringify(out)), (error) => { console.error(error.stack); process.exit(1); });
