"use strict";

// Review: one saved passage at a time, in an endless shuffle. A vertical pager shows one passage per screen; a long passage
// splits into parts that fit one screen (story-style bars at the top; tap the edges on a phone, arrow buttons or keys on a
// computer). A passage counts as shown after about a second on screen. "Ask about this" opens Ask limited to the passage's
// source. Every piece of text from the server is drawn with textContent or the reader's inline drawer, never as HTML.
(() => {
  const SHORT_WORDS = 60;       // at most this long: large type, one page
  const NOTE_OWN_WORDS = 60;    // a note longer than this gets a last part of its own
  const PHONE_BUDGET = 110;     // words per part
  const WIDE_BUDGET = 140;
  const BATCH = 3;              // passages asked for at a time
  const AHEAD = 2;              // fetch more when fewer than this many passages are left after the current one
  const DWELL_MS = 1000;
  const VISIBLE = 0.6;          // share of a page that must be on screen
  const HINT_KEY = "ka-review-hint";
  const HINT_PASSAGES = 5;

  const view = { pages: [], current: 0, fetching: false, failed: false, stalled: false, loaded: false, hinted: 0,
    askOpen: false, observer: null, byElement: new Map() };

  const h = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const words = (text) => String(text || "").split(/\s+/).filter(Boolean).length;
  const budget = () => (phone.matches ? PHONE_BUDGET : WIDE_BUDGET);

  // Split a passage into parts that fit one screen: whole paragraphs first, then sentences, balanced so the last part is
  // not a stub. A note on the last part counts toward the split; a long note is a last part of its own (an empty body).
  function paginate(text, size, noteWords = 0) {
    const ownNote = noteWords > NOTE_OWN_WORDS;
    const total = words(text) + (ownNote ? 0 : noteWords);
    const count = Math.max(1, Math.ceil(total / size));
    const cap = Math.ceil((total / count) * 1.15);
    const units = [];
    String(text).split(/\n\n+/).forEach((paragraph) => {
      if (words(paragraph) <= cap) { units.push({ t: paragraph, w: words(paragraph), lead: "\n\n" }); return; }
      paragraph.split(/(?<=[.!?”"])\s+/).forEach((sentence, k) => units.push({ t: sentence, w: words(sentence), lead: k === 0 ? "\n\n" : " " }));
    });
    if (!ownNote && noteWords) units.push({ t: "", w: noteWords, lead: "", note: true });
    // Break where a part is nearest the even size, or before a part would pass the cap.
    const ideal = total / count;
    const parts = [];
    let current = [];
    let weight = 0;
    for (const unit of units) {
      const evener = Math.abs(weight + unit.w - ideal) > Math.abs(weight - ideal);
      if (current.length && ((parts.length < count - 1 && evener) || weight + unit.w > cap)) {
        parts.push(current);
        current = [];
        weight = 0;
      }
      current.push(unit);
      weight += unit.w;
    }
    if (current.length) parts.push(current);
    // A stub of a last part joins the one before it.
    if (parts.length > 1 && parts[parts.length - 1].reduce((sum, unit) => sum + unit.w, 0) < cap * 0.35) {
      const last = parts.pop();
      parts[parts.length - 1] = parts[parts.length - 1].concat(last);
    }
    const bodies = parts.map((part) => part.filter((unit) => !unit.note).map((unit, k) => (k ? unit.lead : "") + unit.t).join(""));
    return ownNote ? [...bodies, ""] : bodies;
  }

  function split(passage) {
    const noteWords = words(passage.note);
    const long = words(passage.text) > SHORT_WORDS;
    if (!long && noteWords <= NOTE_OWN_WORDS) return [passage.text];
    return paginate(passage.text, budget(), noteWords);
  }

  // Drawing ----------------------------------------------------------------------------------------
  const inlineText = (tag, cls, text) => window.readerBlocks.inline(tag, cls, text);

  function drawPart(page) {
    const { parts, part, passage } = page;
    const body = page.el.querySelector(".review-text");
    const nodes = parts[part].split(/\n\n+/).filter(Boolean).map((paragraph) => inlineText("p", "review-p", paragraph));
    body.replaceChildren(...nodes);
    const note = page.el.querySelector(".review-note");
    note.replaceChildren();
    note.hidden = !(passage.note && part === parts.length - 1);
    if (!note.hidden) note.append(inlineText("span", "", passage.note));
    const bars = page.el.querySelectorAll(".review-bar");
    bars.forEach((bar, index) => {
      bar.classList.toggle("is-done", index <= part);
      if (index === part) bar.setAttribute("aria-current", "step"); else bar.removeAttribute("aria-current");
    });
    page.el.querySelector(".review-prev").disabled = part === 0;
    page.el.querySelector(".review-next").disabled = part === parts.length - 1;
  }

  function buildPage(passage) {
    const el = h("div", "review-page");
    el.setAttribute("role", "group");
    el.setAttribute("aria-roledescription", "passage");
    el.setAttribute("aria-label", passage.title);
    el.dataset.key = passage.passage_key;
    const page = { passage, el, parts: [], part: 0, logged: false, timer: null };
    const bars = h("div", "review-bars");
    const body = h("div", "review-body");
    body.append(h("div", "review-text"), h("div", "review-note"));
    const source = h("div", "review-source");
    source.append(h("span", "review-source-title", passage.title));
    const byline = [passage.author, passage.locator].filter(Boolean).join(" · ");
    if (byline) source.append(h("span", "review-source-author", byline));
    const prev = h("button", "icon-button review-arrow review-prev");
    const next = h("button", "icon-button review-arrow review-next");
    for (const [button, label, name] of [[prev, "Previous part", "i-back"], [next, "Next part", "i-back"]]) {
      button.type = "button";
      button.setAttribute("aria-label", label);
      button.append(icon(name.slice(2)));
    }
    next.classList.add("is-forward");
    prev.addEventListener("click", (event) => { event.stopPropagation(); turn(page, -1); });
    next.addEventListener("click", (event) => { event.stopPropagation(); turn(page, 1); });
    el.append(bars, body, source, prev, next);
    el.addEventListener("click", (event) => tap(page, event));
    layout(page);
    return page;
  }

  // (Re)split the passage for the current screen width and redraw its bars and first part.
  function layout(page, keepPart = false) {
    const before = page.parts.length ? page.part / page.parts.length : 0;
    page.parts = split(page.passage);
    page.part = keepPart ? Math.min(page.parts.length - 1, Math.floor(before * page.parts.length)) : 0;
    const paged = page.parts.length > 1;
    page.el.classList.toggle("is-short", !paged && words(page.passage.text) <= SHORT_WORDS);
    const bars = page.el.querySelector(".review-bars");
    bars.hidden = !paged;
    bars.replaceChildren(...page.parts.map((_, index) => {
      const bar = h("span", "review-bar");
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", `Part ${index + 1} of ${page.parts.length}`);
      return bar;
    }));
    for (const arrow of page.el.querySelectorAll(".review-arrow")) arrow.hidden = !paged;
    drawPart(page);
  }

  function turn(page, step) {
    const next = Math.max(0, Math.min(page.parts.length - 1, page.part + step));
    if (next === page.part) return;
    page.part = next;
    hideAsk();
    drawPart(page);
    $("review-live").textContent = `Part ${next + 1} of ${page.parts.length}`;
  }

  // On a phone the outer quarters turn the part and the middle half shows the Ask pill; elsewhere any click shows it.
  function tap(page, event) {
    if (event.target && event.target.closest && event.target.closest("button")) return;
    if (typeof getSelection === "function" && String(getSelection()).length) return;
    if (phone.matches && page.parts.length > 1) {
      const rect = page.el.getBoundingClientRect();
      const share = (event.clientX - rect.left) / (rect.width || 1);
      if (share < 0.25) { turn(page, -1); return; }
      if (share > 0.75) { turn(page, 1); return; }
    }
    if (view.askOpen) hideAsk(); else showAsk();
  }

  function showAsk() {
    view.askOpen = true;
    $("review-ask").hidden = false;
  }

  function hideAsk() {
    view.askOpen = false;
    $("review-ask").hidden = true;
  }

  // States ------------------------------------------------------------------------------------------
  function setState(kind) {
    const box = $("review-state");
    box.hidden = !kind;
    box.replaceChildren();
    if (!kind) return;
    const copy = {
      loading: ["Loading passages…", ""],
      failed: ["Passages did not load.", "Check your connection and try again."],
      empty: ["Nothing to review yet.", "Highlights, quotes and short notes appear here once they are indexed."],
    }[kind];
    box.append(h("p", "review-state-title", copy[0]));
    if (copy[1]) box.append(h("p", "review-state-sub", copy[1]));
    if (kind === "failed") {
      const retry = h("button", "chip-button", "Retry");
      retry.type = "button";
      retry.addEventListener("click", () => fetchMore());
      box.append(retry);
    }
  }

  // The last page while more is on its way, or when loading more failed.
  function drawTail() {
    const pager = $("review-pager");
    const old = pager.querySelector(".review-tail");
    if (old) old.remove();
    if (!view.pages.length || (!view.fetching && !view.failed)) return;
    const tail = h("div", "review-page review-tail");
    tail.setAttribute("role", "status");
    if (view.failed) {
      tail.append(h("p", "review-state-title", "More passages did not load."));
      const retry = h("button", "chip-button", "Retry");
      retry.type = "button";
      retry.addEventListener("click", () => fetchMore());
      tail.append(retry);
    } else {
      tail.append(h("p", "review-state-title", "Finding the next passage…"));
    }
    pager.append(tail);
  }

  // Fetching ----------------------------------------------------------------------------------------
  const heldKeys = () => view.pages.filter((page) => !page.logged).map((page) => page.passage.passage_key);

  async function fetchMore() {
    if (view.fetching) return;
    view.fetching = true;
    view.failed = false;
    view.stalled = false;
    if (!view.pages.length) setState("loading");
    drawTail();
    try {
      const data = await api("GET", `/api/review/next?count=${BATCH}&held=${heldKeys().join(",")}`);
      const passages = data.passages || [];
      view.loaded = true;
      for (const passage of passages) addPage(passage);
      // Nothing new while passages are held: wait for one to be shown. Nothing and nothing held: the pool is empty.
      if (!passages.length) view.stalled = true;
      setState(!view.pages.length ? "empty" : null);
    } catch (error) {
      if (error.status === 401) return;
      view.failed = true;
      setState(view.pages.length ? null : "failed");
    } finally {
      view.fetching = false;
      drawTail();
    }
  }

  function addPage(passage) {
    const page = buildPage(passage);
    view.pages.push(page);
    $("review-pager").append(page.el);
    view.byElement.set(page.el, page);
    if (view.observer) view.observer.observe(page.el);
  }

  function maybeFetch() {
    if (view.fetching || view.stalled || view.failed) return;
    if (view.pages.length - 1 - view.current < AHEAD) fetchMore();
  }

  // Shown means on screen ----------------------------------------------------------------------------
  function startDwell(page) {
    if (page.logged || page.timer !== null) return;
    page.timer = setTimeout(() => { page.timer = null; markShown(page); }, DWELL_MS);
  }

  function stopDwell(page) {
    if (page.timer !== null) { clearTimeout(page.timer); page.timer = null; }
  }

  async function markShown(page) {
    if (page.logged) return;
    page.logged = true;
    countHint();
    try {
      await api("POST", "/api/review/shown", { passage_key: page.passage.passage_key, round: page.passage.round });
    } catch (error) {
      if (error.status === 401) return;
    }
    // A shown passage may free the server to start the next round or hand out what was held.
    view.stalled = false;
    maybeFetch();
  }

  function onVisible(entries) {
    for (const entry of entries) {
      const page = view.byElement.get(entry.target);
      if (!page) continue;
      if (entry.isIntersecting && entry.intersectionRatio >= VISIBLE) {
        const index = view.pages.indexOf(page);
        if (index !== view.current || !page.announced) {
          view.current = index;
          page.announced = true;
          hideAsk();
          $("review-live").textContent = `${page.passage.title}${page.passage.author ? `, ${page.passage.author}` : ""}`;
        }
        syncHint();
        startDwell(page);
        maybeFetch();
      } else {
        stopDwell(page);
      }
    }
  }

  // The hint under the passage: shown on the first few passages only.
  function readHint() {
    try { return Number(localStorage.getItem(HINT_KEY)) || 0; } catch (_) { return 0; }
  }

  function countHint() {
    view.hinted = Math.max(view.hinted, readHint()) + 1;
    try { localStorage.setItem(HINT_KEY, String(view.hinted)); } catch (_) { /* a private window keeps no count */ }
  }

  function syncHint() {
    view.hinted = Math.max(view.hinted, readHint());
    const hint = $("review-hint");
    hint.hidden = view.hinted >= HINT_PASSAGES;
    hint.textContent = phone.matches ? "Tap the edges for the next part · tap the middle to ask"
      : "← → turn the part · click the passage to ask";
  }

  // Moving ------------------------------------------------------------------------------------------
  function go(step) {
    const target = view.pages[Math.max(0, Math.min(view.pages.length - 1, view.current + step))];
    if (target && target.el.scrollIntoView) target.el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function onKey(event) {
    if (state.view !== "review" || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    const tag = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : "";
    if (["input", "textarea", "select"].includes(tag) || sheet.open) return;
    const page = view.pages[view.current];
    const moves = { ArrowDown: 1, PageDown: 1, ArrowUp: -1, PageUp: -1 };
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      if (page) turn(page, event.key === "ArrowRight" ? 1 : -1);
    } else if (event.key in moves) {
      go(moves[event.key]);
    } else {
      return;
    }
    event.preventDefault();
  }

  // Screen ------------------------------------------------------------------------------------------
  function show() {
    syncHint();
    if (!view.pages.length) {
      if (!view.fetching) fetchMore();
      return undefined;
    }
    const page = view.pages[view.current];
    if (page) startDwell(page);
    return undefined;
  }

  function leave() {
    hideAsk();
    for (const page of view.pages) stopDwell(page);
  }

  function init() {
    $("review-ask").addEventListener("click", () => {
      const page = view.pages[view.current];
      if (!page) return;
      hideAsk();
      askAboutPassage(page.passage);
    });
    $("review-pager").addEventListener("scroll", hideAsk, { passive: true });
    document.addEventListener("keydown", onKey);
    document.addEventListener("visibilitychange", () => { if (document.hidden) leave(); else if (state.view === "review") show(); });
    phone.addEventListener("change", () => {
      for (const page of view.pages) layout(page, true);
      syncHint();
    });
    if (typeof IntersectionObserver === "function") {
      view.observer = new IntersectionObserver(onVisible, { root: $("review-pager"), threshold: [0, VISIBLE, 1] });
    }
  }

  window.reviewScreen = { show, leave, paginate, onVisible, pages: () => view.pages };
  document.addEventListener("DOMContentLoaded", init);
})();
