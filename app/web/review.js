"use strict";

// Review: one saved passage at a time, in an endless shuffle. A vertical pager shows one passage per screen; a long passage
// splits into parts that fit one screen (story-style bars at the top; tap the edges on a phone, arrow buttons or keys on a
// computer). A passage counts as shown after about a second on screen. "Ask about this" opens Ask limited to the passage's
// source. Every piece of text from the server is drawn with textContent or the reader's inline drawer, never as HTML.
(() => {
  const NOTE_OWN_WORDS = 60;    // a note longer than this gets a last part of its own
  // How many words a part may hold comes from the passage box (see the layout note in styles.css): the page's height less
  // the space above the box (100 px, 112 with bars) and below it (the source line and the circles' band: 256 px, 260 on a
  // computer). Every passage is 24 px type at 1.45, so a line is 34.8 px tall.
  const LINE_PX = 24 * 1.45;
  const WORD_PX = 58;           // a word and its space in 24 px type, on average
  const FILL = 0.9;             // paragraph gaps, the quote mark and uneven line ends take the rest
  const FALLBACK = { phone: [390, 844], wide: [1280, 800] };  // the boards' sizes, for a page that cannot be measured yet
  const BATCH = 3;             // passages asked for at a time
  const AHEAD = 2;              // fetch more when fewer than this many passages are left after the current one
  const DWELL_MS = 1000;
  const ADVANCE_MS = 250;       // how long a new mark shows filled before the next passage comes up
  const VISIBLE = 0.6;          // share of a page that must be on screen
  const WHEEL_QUIET_MS = 160;   // no wheel events for this long ends a gesture
  const MAX_HELD = 30;          // the most keys the server accepts in one request

  // What kind of source a passage comes from sets the colour of the whole screen and its label (Sources uses the same looks).
  const TYPES = { highlights: ["Highlights", "dark"], quote: ["Quote", "light"], note: ["Note", "light"], post: ["Post", "light"] };
  const typeOf = (passage) => (Object.hasOwn(TYPES, passage.type) ? passage.type : "note");

  const view = { pages: [], current: 0, fetching: false, failed: false, stalled: false, loaded: false,
    askOpen: false, observer: null, byElement: new Map(), list: null, saved: null };
  const LIST_NAMES = { again: "Again", done: "Done" };
  const LIST_BAR_PX = 36;       // list mode's top bar pushes the bars, kicker and passage box down by this much

  const h = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const words = (text) => String(text || "").split(/\s+/).filter(Boolean).length;
  function budget() {
    const pager = $("review-pager");
    const [fallbackWidth, fallbackHeight] = phone.matches ? FALLBACK.phone : FALLBACK.wide;
    const width = pager.clientWidth || fallbackWidth;
    const height = pager.clientHeight || fallbackHeight;
    const boxHeight = height - (phone.matches ? 112 + 256 : 122 + 260) - (view.list ? LIST_BAR_PX : 0);  // the box with bars, the smaller one
    const boxWidth = Math.min(640, width - (phone.matches ? 48 : 144));
    const lines = Math.max(1, Math.floor(boxHeight / LINE_PX));
    return Math.max(10, Math.floor(lines * (boxWidth / WORD_PX) * FILL));
  }

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
      paragraph.split(/(?<=[.!?”"])\s+/).forEach((sentence, k) => {
        const lead = k === 0 ? "\n\n" : " ";
        const count = words(sentence);
        if (count <= cap) { units.push({ t: sentence, w: count, lead }); return; }
        // One sentence longer than a part: break it at word boundaries into even pieces.
        const tokens = sentence.split(/\s+/).filter(Boolean);
        const pieces = Math.ceil(count / cap);
        const each = Math.ceil(count / pieces);
        for (let i = 0; i < pieces; i++) {
          const piece = tokens.slice(i * each, (i + 1) * each);
          units.push({ t: piece.join(" "), w: piece.length, lead: i === 0 ? lead : " " });
        }
      });
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

  // The passage and its note always go through the budget together; one that fits comes back as a single part.
  const split = (passage, scale = 1) => paginate(passage.text, Math.max(10, Math.floor(budget() * scale)), words(passage.note));

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
    const type = typeOf(passage);
    el.classList.add(`is-${type}`);
    const page = { passage, type, el, parts: [], part: 0, logged: false, timer: null, mark: passage.mark || null, saving: Promise.resolve(), scale: 1 };
    const bars = h("div", "review-bars");
    const kicker = h("div", "review-kicker", TYPES[type][0]);
    const body = h("div", "review-body");
    body.append(h("div", "review-text"), h("div", "review-note"));
    const source = h("div", "review-source");
    source.append(h("div", "review-source-title", passage.title));
    const byline = [passage.author, passage.locator].filter(Boolean).join(" · ");
    if (byline) source.append(h("div", "review-source-author", byline));
    const band = h("div", "review-band");
    for (const kind of ["done", "again"]) band.append(markButton(page, kind));
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
    el.append(bars, kicker, body, source, prev, next, band);
    paintMarks(page);
    el.addEventListener("click", (event) => tap(page, event));
    layout(page);
    return page;
  }

  // Marks -------------------------------------------------------------------------------------------
  // Done (cross) and Again (bookmark) cover the whole passage and can be set from any part. The chosen circle is filled;
  // tapping it clears the mark and tapping the other switches. The fill changes at once and goes back if the save fails.
  const MARKS = { done: ["Done", "X", "i-mark-cross"], again: ["Show again", "B", "i-mark-bookmark"] };

  function markButton(page, kind) {
    const [label, key, symbol] = MARKS[kind];
    const button = h("button", `review-mark review-mark-${kind}`);
    button.type = "button";
    button.dataset.mark = kind;
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-keyshortcuts", key);
    button.title = `${label} (${key})`;
    button.append(icon(symbol.slice(2)));
    button.addEventListener("click", (event) => { event.stopPropagation(); toggleMark(page, kind); });
    return button;
  }

  function paintMarks(page) {
    for (const button of page.el.querySelectorAll(".review-mark")) {
      button.setAttribute("aria-pressed", String(page.mark === button.dataset.mark));
    }
  }

  function toggleMark(page, kind) {
    const before = page.mark;
    const next = before === kind ? null : kind;
    page.mark = next;
    paintMarks(page);
    // Setting a mark moves on to the next passage after a beat, so the fill is seen. Clearing a mark stays put, and so does
    // a list (there you are reading one passage after another by choice).
    if (next && !view.list) {
      setTimeout(() => { if (view.pages[view.current] === page) go(1); }, ADVANCE_MS);
    }
    const url = `/api/review/marks/${encodeURIComponent(page.passage.passage_key)}`;
    // Saves for one passage go out in order, so quick taps cannot land the wrong way round.
    page.saving = page.saving.then(async () => {
      try {
        await (next ? api("PUT", url, { mark: next }) : api("DELETE", url));
        // A shuffle passage held behind a list shows the same mark when the shuffle comes back.
        for (const held of view.saved ? view.saved.pages : []) {
          if (held.passage.passage_key === page.passage.passage_key) { held.mark = next; paintMarks(held); }
        }
      } catch (error) {
        page.mark = before;
        paintMarks(page);
        if (error.status !== 401 && typeof notify === "function") notify("The mark was not saved. Try again.");
      }
    });
    return page.saving;
  }

  // (Re)split the passage for the current screen width and redraw its bars and first part.
  function layout(page, keepPart = false) {
    resplit(page, keepPart);
    drawPart(page);
    fit(page);
  }

  // The word budget is a first guess: long words and a quote mark take more room than the average. Once the page is on
  // screen, each part is measured against the passage box and the passage is split finer until none overflows.
  function fit(page) {
    const body = page.el.querySelector(".review-body");
    if (!body.clientHeight) return;  // not on screen yet
    const overflows = () => page.parts.some((text, index) => {
      if (!text && page.passage.note) return false;  // a part holding only the note: splitting the text cannot shorten it
      page.part = index;
      drawPart(page);
      return body.scrollHeight > body.clientHeight + 1;
    });
    const at = page.part / page.parts.length;
    for (let tries = 0; tries < 16 && page.scale > 0.3 && overflows(); tries++) {
      page.scale *= 0.93;
      resplit(page, false);
    }
    page.part = Math.min(page.parts.length - 1, Math.floor(at * page.parts.length));
    drawPart(page);
  }

  function resplit(page, keepPart) {
    const before = page.parts.length ? page.part / page.parts.length : 0;
    page.parts = split(page.passage, page.scale);
    page.part = keepPart ? Math.min(page.parts.length - 1, Math.floor(before * page.parts.length)) : 0;
    const paged = page.parts.length > 1;
    page.el.classList.toggle("has-bars", paged);
    const bars = page.el.querySelector(".review-bars");
    bars.hidden = !paged;
    bars.replaceChildren(...page.parts.map((_, index) => {
      const bar = h("span", "review-bar");
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", `Part ${index + 1} of ${page.parts.length}`);
      return bar;
    }));
    for (const arrow of page.el.querySelectorAll(".review-arrow")) arrow.hidden = !paged;
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

  // The pill sits just above the circles, over the source line, which fades while it shows.
  function showAsk() {
    view.askOpen = true;
    $("review-ask").hidden = false;
    $("review").dataset.ask = "open";
  }

  function hideAsk() {
    view.askOpen = false;
    $("review-ask").hidden = true;
    delete $("review").dataset.ask;
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
      allDone: ["You have marked every passage Done.", ""],
    }[kind];
    box.append(h("p", "review-state-title", copy[0]));
    if (copy[1]) box.append(h("p", "review-state-sub", copy[1]));
    if (kind === "failed") {
      const retry = h("button", "chip-button", "Retry");
      retry.type = "button";
      retry.addEventListener("click", () => fetchMore());
      box.append(retry);
    }
    if (kind === "allDone") {
      const open = h("button", "link", "Open the Done list");
      open.type = "button";
      open.addEventListener("click", () => window.reviewSettings.openFromReview("done"));
      box.append(open);
    }
  }

  // Nothing to show: either an empty vault, or every passage marked Done (the Done list is one tap away).
  async function emptyKind() {
    try {
      const counts = await api("GET", "/api/review/settings");
      return counts.done > 0 ? "allDone" : "empty";
    } catch (error) {
      return "empty";
    }
  }

  // The last page while more is on its way, or when loading more failed. It is one element that stays in the pager
  // (hidden when idle) and is updated in place, so a fetch that starts while the reader scrolls never moves the pages.
  function drawTail() {
    let tail = view.tail;
    if (!tail) {
      tail = view.tail = h("div", "review-page review-tail");
      tail.setAttribute("role", "status");
      tail.hidden = true;
      $("review-pager").append(tail);
    }
    const show = view.pages.length > 0 && (view.fetching || view.failed);
    const shown = show ? (view.failed ? "failed" : "waiting") : "";
    if (tail.dataset.shown === shown) return;
    tail.dataset.shown = shown;
    tail.hidden = !show;
    tail.replaceChildren();
    if (!show) return;
    if (view.failed) {
      tail.append(h("p", "review-state-title", "More passages did not load."));
      const retry = h("button", "chip-button", "Retry");
      retry.type = "button";
      retry.addEventListener("click", () => fetchMore());
      tail.append(retry);
    } else {
      tail.append(h("p", "review-state-title", "Finding the next passage…"));
    }
  }

  // List mode ----------------------------------------------------------------------------------------
  // A passage opened from Settings > Review > Again or Done shows here, like Review, with the whole list in the pager.
  // The shuffle's own state is set aside until the list is left; list mode never fetches, holds keys or logs shown.
  function enterList(name, passages, index = 0) {
    if (view.list) leaveList();
    const pager = $("review-pager");
    for (const page of view.pages) stopDwell(page);
    view.saved = { pages: view.pages, current: view.current, byElement: view.byElement, tail: view.tail,
      nodes: [...pager.children], scrollTop: pager.scrollTop };
    if (view.observer) view.observer.disconnect();
    pager.replaceChildren();
    Object.assign(view, { pages: [], current: index, byElement: new Map(), tail: null, list: { name, total: passages.length, start: index } });
    $("review").classList.add("is-list");
    $("app").dataset.reviewList = "true";
    setState(null);
    for (const passage of passages) addPage(passage);
    updateListBar();
  }

  function leaveList() {
    if (!view.list) return;
    const pager = $("review-pager");
    const saved = view.saved;
    if (view.observer) view.observer.disconnect();
    pager.replaceChildren(...saved.nodes);
    Object.assign(view, { pages: saved.pages, current: saved.current, byElement: saved.byElement, tail: saved.tail, list: null, saved: null });
    if (view.observer) for (const page of view.pages) view.observer.observe(page.el);
    pager.scrollTop = saved.scrollTop;
    $("review").classList.remove("is-list");
    delete $("app").dataset.reviewList;
    $("review-listbar").hidden = true;
    $("review-list-caption").hidden = true;
    hideAsk();
  }

  function updateListBar() {
    const { name, total } = view.list;
    const last = view.current >= total - 1;
    $("review-listbar").hidden = false;
    $("review-list-name").textContent = LIST_NAMES[name];
    $("review-list-count").textContent = `${view.current + 1} of ${total}`;
    const caption = $("review-list-caption");
    caption.hidden = false;
    caption.textContent = last ? "End of list" : phone.matches ? "Swipe up for the next passage in this list" : "Down arrow for the next passage in this list";
  }

  // Fetching ----------------------------------------------------------------------------------------
  // What the server must not hand out again: passages on screen or ahead that are not yet logged. Passages scrolled
  // past without a second on screen are let go (the list is capped at what the server accepts).
  const heldKeys = () => view.pages.filter((page, index) => !page.logged && index >= view.current)
    .map((page) => page.passage.passage_key).slice(-MAX_HELD);

  async function fetchMore() {
    if (view.fetching) return;
    view.fetching = true;
    view.failed = false;
    view.stalled = false;
    if (!view.pages.length) setState("loading");
    drawTail();
    try {
      const data = await api("GET", `/api/review/next?count=${BATCH}&held=${heldKeys().join(",")}`);
      if (view.list) return;  // a list was opened meanwhile: these belong to the shuffle, which asks again when it returns
      const passages = data.passages || [];
      view.loaded = true;
      for (const passage of passages) if (!view.pages.some((p) => !p.logged && p.passage.passage_key === passage.passage_key)) addPage(passage);
      // Nothing new while passages are held: wait for one to be shown. Nothing and nothing held: the pool is empty.
      if (!passages.length) view.stalled = true;
      if (!view.pages.length) setState(await emptyKind()); else setState(null);
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
    $("review-pager").insertBefore(page.el, view.tail || null);
    fit(page);
    view.byElement.set(page.el, page);
    if (view.observer) view.observer.observe(page.el);
  }

  function maybeFetch() {
    if (view.list || view.fetching || view.stalled || view.failed) return;
    if (view.pages.length - 1 - view.current < AHEAD) fetchMore();
  }

  // Shown means on screen ----------------------------------------------------------------------------
  function startDwell(page) {
    if (view.list || page.logged || page.timer !== null) return;  // list mode logs nothing as shown
    page.timer = setTimeout(() => { page.timer = null; markShown(page); }, DWELL_MS);
  }

  function stopDwell(page) {
    if (page.timer !== null) { clearTimeout(page.timer); page.timer = null; }
  }

  async function markShown(page) {
    if (page.logged || page.posting) return;
    page.posting = true;  // stays held until the server has it
    try {
      const body = { passage_key: page.passage.passage_key, round: page.passage.round };
      if (page.passage.again) body.again = true;  // an Again serve is recorded on its mark, not in the shown log
      await api("POST", "/api/review/shown", body);
      page.logged = true;
    } catch (error) {
      page.posting = false;  // the next second on screen tries again
      if (error.status === 401) return;
      if (error.status === 400 || error.status === 404) page.logged = true;  // the server will never take it
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
          if (view.list) updateListBar();
          hideAsk();
          syncTone(page);
          $("review-live").textContent = `${page.passage.title}${page.passage.author ? `, ${page.passage.author}` : ""}`;
        }
        startDwell(page);
        maybeFetch();
      } else {
        stopDwell(page);
      }
    }
  }

  // Moving ------------------------------------------------------------------------------------------
  function go(step) {
    const target = view.pages[Math.max(0, Math.min(view.pages.length - 1, view.current + step))];
    if (target && target.el.scrollIntoView) target.el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // A mouse wheel or trackpad flips one passage per gesture, with a smooth glide: the browser's own snapping stutters
  // on a full-screen page (and a trackpad's trailing inertia would flip several). The lock lifts when the wheel goes quiet.
  const wheel = { locked: false, idle: null };
  function onWheel(event) {
    event.preventDefault();
    clearTimeout(wheel.idle);
    wheel.idle = setTimeout(() => { wheel.locked = false; }, WHEEL_QUIET_MS);
    if (wheel.locked || Math.abs(event.deltaY) < 8) return;
    wheel.locked = true;
    go(event.deltaY > 0 ? 1 : -1);
  }

  function onKey(event) {
    if (state.view !== "review" || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    const tag = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : "";
    if (["input", "textarea", "select"].includes(tag) || sheet.open) return;
    const page = view.pages[view.current];
    const moves = { ArrowDown: 1, PageDown: 1, ArrowUp: -1, PageUp: -1 };
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      if (page) turn(page, event.key === "ArrowRight" ? 1 : -1);
    } else if (event.key.toLowerCase() === "x" || event.key.toLowerCase() === "b") {
      if (page) toggleMark(page, event.key.toLowerCase() === "x" ? "done" : "again");
    } else if (event.key in moves) {
      go(moves[event.key]);
    } else {
      return;
    }
    event.preventDefault();
  }

  // The page behind the controls over it (menu button, Ask button) is dark for highlights, light otherwise.
  function syncTone(page) {
    $("review").dataset.type = page.type;
    $("app").dataset.reviewTone = TYPES[page.type][1];
    $("app").dataset.reviewBars = String(page.parts.length > 1);  // the menu button drops under the part bars
  }

  // Screen ------------------------------------------------------------------------------------------
  function show() {
    for (const page of view.pages) fit(page);
    if (view.list) {
      // Opened from a list: land on the passage that was tapped, with no glide.
      const start = view.pages[view.list.start];
      if (start && start.el.scrollIntoView) start.el.scrollIntoView({ behavior: "instant", block: "start" });
      view.current = view.list.start;
      if (start) syncTone(start);
      updateListBar();
      return undefined;
    }
    if (view.pages[view.current]) syncTone(view.pages[view.current]);
    if (!view.pages.length) {
      if (!view.fetching) fetchMore();
      return undefined;
    }
    const page = view.pages[view.current];
    if (page) startDwell(page);
    return undefined;
  }

  function pause() {
    hideAsk();
    for (const page of view.pages) stopDwell(page);
  }

  function leave() {
    leaveList();
    hideAsk();
    delete $("app").dataset.reviewTone;
    delete $("app").dataset.reviewBars;
    for (const page of view.pages) stopDwell(page);
  }

  function init() {
    $("review-list-back").addEventListener("click", () => {
      if (view.list) window.reviewSettings.backToList(view.list.name);
    });
    $("review-ask").addEventListener("click", () => {
      const page = view.pages[view.current];
      if (!page) return;
      hideAsk();
      askAboutPassage(page.passage);
    });
    $("review-pager").addEventListener("scroll", hideAsk, { passive: true });
    $("review-pager").addEventListener("wheel", onWheel, { passive: false });
    document.addEventListener("keydown", onKey);
    // Switching apps pauses the screen; only leaving the view (leave) ends list mode.
    document.addEventListener("visibilitychange", () => { if (document.hidden) pause(); else if (state.view === "review") show(); });
    const relayout = () => { for (const page of view.pages) { page.scale = 1; layout(page, true); } };
    phone.addEventListener("change", relayout);
    // A taller or shorter screen (a rotation, a resized window) changes how much a part holds.
    let lastBudget = budget();
    if (typeof window.addEventListener === "function") {
      window.addEventListener("resize", () => {
        const now = budget();
        if (now !== lastBudget) relayout();
        lastBudget = now;
      });
    }
    if (typeof IntersectionObserver === "function") {
      view.observer = new IntersectionObserver(onVisible, { root: $("review-pager"), threshold: [0, VISIBLE, 1] });
    }
  }

  window.reviewScreen = { show, leave, paginate, budget, onVisible, toggleMark, enterList, leaveList, pages: () => view.pages };
  document.addEventListener("DOMContentLoaded", init);
})();
