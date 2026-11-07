"use strict";

// Settings > Review: how often Again passages come back, and the Again and Done lists. A row opens its list as a page in
// place of the tab; a passage opened from a list shows in Review's list mode (review.js). Every piece of text from the
// server is drawn with textContent, never as HTML.
(() => {
  // The four levels: the word shown large and "1 in N".
  const LEVELS = [{ every: 10, word: "Normal" }, { every: 5, word: "Sometimes" }, { every: 3, word: "Often" }, { every: 2, word: "Frequently" }];
  const LISTS = { again: "Again", done: "Done" };
  const OTHER = { again: "done", done: "again" };

  const state = { data: null, list: null, resume: null, saving: Promise.resolve() };

  const h = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const level = () => Math.max(0, LEVELS.findIndex((step) => step.every === state.data.every));
  const total = (name) => (state.data ? state.data[name] : 0);
  const message = (text) => { $("review-settings-message").textContent = text || ""; };
  const listMessage = (text) => { $("list-message").textContent = text || ""; };
  const SAVE_FAILED = "Could not save that. Check your connection and try again.";

  // The how-often control ---------------------------------------------------------------------------
  function paintHow() {
    if (!state.data) return;
    const at = level();
    $("every-word").textContent = LEVELS[at].word;
    $("every-n-full").textContent = String(LEVELS[at].every);
    $("every-n-short").textContent = String(LEVELS[at].every);
    $("every-less").disabled = at === 0;
    $("every-more").disabled = at === LEVELS.length - 1;
    const dots = $("every-dots");
    dots.setAttribute("aria-label", `Level ${at + 1} of ${LEVELS.length}`);
    dots.replaceChildren(...LEVELS.map((_, index) => h("span", index <= at ? "how-dot is-on" : "how-dot")));
    $("again-count").textContent = String(total("again"));
    $("done-count").textContent = String(total("done"));
  }

  function step(delta) {
    if (!state.data) return Promise.resolve();
    const next = level() + delta;
    if (next < 0 || next >= LEVELS.length) return Promise.resolve();
    const before = state.data.every;
    state.data.every = LEVELS[next].every;
    paintHow();
    message("");
    // Saves go out in order, so quick taps land the way they were made.
    state.saving = state.saving.then(async () => {
      try {
        await api("PUT", "/api/review/settings", { every: LEVELS[next].every });
      } catch (error) {
        state.data.every = before;
        paintHow();
        if (error.status !== 401) message(SAVE_FAILED);
      }
    });
    return state.saving;
  }

  async function loadSettings() {
    try {
      state.data = await api("GET", "/api/review/settings");
      paintHow();
      message("");
    } catch (error) {
      if (error.status !== 401) message("Could not load your Review settings. Check your connection and try again.");
    }
  }

  // The list page -----------------------------------------------------------------------------------
  const plural = (count) => (count === 1 ? "1 passage" : `${count} passages`);

  function renderList() {
    const { name, passages } = state.list;
    $("list-title").textContent = LISTS[name];
    const every = state.data ? state.data.every : 10;
    $("list-sub").textContent = name === "again"
      ? `${plural(passages.length)}, newest first. They come back about 1 in ${every}.`
      : `${plural(passages.length)}, newest first.`;
    $("list-empty").hidden = passages.length > 0;
    $("list-rows").replaceChildren(...passages.map((passage, index) => row(passage, index)));
  }

  function row(passage, index) {
    const item = h("li", "list-row");
    const open = h("button", "list-open");
    open.type = "button";
    open.append(h("span", "list-text", passage.text));
    const meta = [passage.title, passage.author].filter(Boolean).join(" · ");
    if (meta) open.append(h("span", "list-meta", meta));
    open.addEventListener("click", () => read(index));
    const acts = h("span", "list-acts");
    const readButton = h("button", "list-act", "Read");
    readButton.type = "button";
    readButton.addEventListener("click", () => read(index));
    const other = OTHER[state.list.name];
    const move = h("button", "list-act", `Move to ${LISTS[other]}`);
    move.type = "button";
    move.addEventListener("click", () => moveTo(passage, other));
    acts.append(readButton, move);
    item.append(open, acts);
    return item;
  }

  function showPage(list) {
    $("settings-main").hidden = list;
    $("settings-list").hidden = !list;
    $("settings").classList.toggle("is-list", list);
    $("settings").scrollTop = 0;
  }

  async function openList(name) {
    listMessage("");
    try {
      const [data, settings] = await Promise.all([api("GET", `/api/review/marks/${name}`), api("GET", "/api/review/settings")]);
      state.data = settings;
      state.list = { name, passages: data.passages || [] };
      paintHow();
      renderList();
      showPage(true);
      $("list-back").focus();
    } catch (error) {
      if (error.status !== 401) message("Could not load that list. Check your connection and try again.");
    }
  }

  async function closeList() {
    state.list = null;
    showPage(false);
    await loadSettings();
  }

  async function moveTo(passage, other) {
    listMessage("");
    try {
      await api("PUT", `/api/review/marks/${encodeURIComponent(passage.passage_key)}`, { mark: other });
    } catch (error) {
      if (error.status !== 401) listMessage(SAVE_FAILED);
      return;
    }
    const name = state.list.name;
    state.list.passages = state.list.passages.filter((one) => one.passage_key !== passage.passage_key);
    if (state.data) { state.data[name] -= 1; state.data[other] += 1; }
    renderList();
    paintHow();
  }

  // Opens the passage in Review's list mode, with the whole list loaded.
  async function read(index) {
    const { name, passages } = state.list;
    window.reviewScreen.enterList(name, passages, index);
    await setView("review");
  }

  // Review's back link: back to the list the passage was opened from, with its marks as they are now.
  function backToList(name) {
    state.resume = name;
    return setView("settings");
  }

  // From Review's "every passage is Done" state: Settings > Review > Done.
  function openFromReview(name) {
    state.resume = name;
    window.settingsScreen.chooseTab("review");
    return setView("settings");
  }

  // Called when the Review tab shows (Settings shown, or the tab chosen).
  async function show() {
    const resume = state.resume;
    state.resume = null;
    if (resume) return openList(resume);
    showPage(false);
    return loadSettings();
  }

  $("every-less").addEventListener("click", () => step(-1));
  $("every-more").addEventListener("click", () => step(1));
  $("open-again").addEventListener("click", () => openList("again"));
  $("open-done").addEventListener("click", () => openList("done"));
  $("list-back").addEventListener("click", closeList);
  window.reviewSettings = { show, openList, closeList, backToList, openFromReview, step };
})();
