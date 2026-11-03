"use strict";

// Add to Review in the Sources reader. Selecting words inside one paragraph or highlight offers "Add to Review": the
// server anchors the words in the file, and the page draws what it holds with the highlighter fill, from the display
// positions the server returns. sources.js passes in its element builder `h`, its `plural` helper and its `reader` state
// (open, source, picks and the selection and toast bookkeeping).
window.readerPicks = ({ h, plural, reader }) => {
  const SETTLE_MS = 300;
  const TOAST_MS = 6000;

  const isText = (node) => node.nodeType === 3 || node.tagName === "#text";
  const kids = (node) => Array.from(node.childNodes || node.children || []);
  const blockElement = (index) => $("reader-body").querySelector(`[data-block="${index}"]`);

  function wrapRange(root, start, end, id) {
    let at = 0;
    const walk = (node) => {
      for (const child of kids(node)) {
        if (!isText(child)) { walk(child); continue; }
        const text = child.textContent;
        const from = Math.max(start, at);
        const to = Math.min(end, at + text.length);
        if (from < to) {
          const mark = h("mark", "r-pick", text.slice(from - at, to - at));
          mark.dataset.pick = id;
          const pieces = [mark];
          if (from > at) pieces.unshift(document.createTextNode(text.slice(0, from - at)));
          if (to < at + text.length) pieces.push(document.createTextNode(text.slice(to - at)));
          child.replaceWith(...pieces);
        }
        at += text.length;
      }
    };
    walk(root);
  }

  function markBlock(index) {
    const el = blockElement(index);
    if (!el) return;
    for (const mark of el.querySelectorAll("mark.r-pick")) mark.replaceWith(document.createTextNode(mark.textContent));
    if (el.normalize) el.normalize();
    for (const pick of reader.picks) if (pick.block === index) wrapRange(el, pick.start, pick.end, pick.id);
  }

  function markAll() {
    for (const index of new Set(reader.picks.map((pick) => pick.block))) markBlock(index);
  }

  function syncPicksControl() {
    const count = reader.picks.length;
    $("reader-picks").hidden = count === 0;
    $("reader-picks-label").textContent = `In Review · ${count}`;
    if (sheet.open && sheet.name === "picks") fillPicks($("sheet").querySelector('[data-sheet="picks"]'));
  }

  // The selection as plain facts: its text item, the words, where they start in the item's shown text and which
  // occurrence of those words that is. Null unless it sits inside one text item.
  function readSelection() {
    const selection = typeof getSelection === "function" ? getSelection() : null;
    if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
    const range = selection.getRangeAt(0);
    const owner = (node) => {
      const el = node && node.nodeType === 3 ? node.parentElement : node;
      return el && el.closest ? el.closest("[data-block]") : null;
    };
    const el = owner(range.startContainer);
    if (!el || el !== owner(range.endContainer)) return null;
    const raw = String(range);
    const words = raw.trim();
    if (!words) return null;
    const before = document.createRange();
    before.selectNodeContents(el);
    before.setEnd(range.startContainer, range.startOffset);
    const text = el.textContent;
    let start = String(before).length + (raw.length - raw.trimStart().length);
    if (text.slice(start, start + words.length) !== words) start = text.indexOf(words);
    let occurrence = 0;
    for (let from = text.indexOf(words); from >= 0 && from <= start; from = text.indexOf(words, from + 1)) occurrence += 1;
    const pick = reader.picks.find((p) => p.block === Number(el.dataset.block) && p.start <= start && start + words.length <= p.end);
    return { block: Number(el.dataset.block), words, start, occurrence: Math.max(1, occurrence), inside: pick || null,
      rect: range.getBoundingClientRect() };
  }

  function showAction(info) {
    const box = $("pick-action");
    const bar = window.matchMedia("(pointer: coarse)").matches;
    $("pick-action-button").textContent = info.inside ? "Remove from Review" : "Add to Review";
    box.classList.toggle("is-bar", bar);
    box.classList.toggle("is-pill", !bar);
    box.style.left = bar ? "" : `${Math.max(90, Math.min(window.innerWidth - 90, info.rect.left + info.rect.width / 2))}px`;
    box.style.top = bar ? "" : `${Math.max(8, info.rect.top - 48)}px`;
    box.hidden = false;
    reader.pending = info;
  }

  function hideAction() {
    $("pick-action").hidden = true;
    reader.pending = null;
  }

  // A mouse sees the pill at once. On a touch screen nothing shows while the handles are dragged: the bar slides up once
  // the selection has held still for a moment.
  function onSelectionChange() {
    if (!reader.open) return;
    clearTimeout(reader.settle);
    const info = readSelection();
    if (!info) { hideAction(); return; }
    if (!window.matchMedia("(pointer: coarse)").matches) { showAction(info); return; }
    hideAction();
    reader.settle = setTimeout(() => {
      const again = readSelection();
      if (again && again.block === info.block && again.start === info.start && again.words === info.words) showAction(again);
    }, SETTLE_MS);
  }

  function hideToast() {
    clearTimeout(reader.toast);
    $("pick-toast").hidden = true;
    reader.undo = null;
  }

  // Say what happened; with a pick id, offer Undo.
  function showToast(text, undoId) {
    $("pick-toast-text").textContent = text;
    $("pick-toast-undo").hidden = !undoId;
    reader.undo = undoId || null;
    $("pick-toast").hidden = false;
    clearTimeout(reader.toast);
    reader.toast = setTimeout(hideToast, TOAST_MS);
  }

  async function addPick() {
    const info = reader.armed || reader.pending;
    reader.armed = null;
    if (!info || !reader.source) return;
    hideAction();
    if (typeof getSelection === "function" && getSelection()) getSelection().removeAllRanges();
    if (info.inside) { await removePick(info.inside.id); return; }
    const source = reader.source;
    try {
      const pick = await api("POST", "/api/review/picks", { source_id: source.id, block: info.block, text: info.words, occurrence: info.occurrence });
      if (!reader.open || reader.source !== source) return;
      // A pick that overlapped others replaces them; the server already merged them into this one.
      const before = reader.picks.length;
      reader.picks = reader.picks.filter((p) => p.id !== pick.id && !(p.block === pick.block && p.start < pick.end && p.end > pick.start));
      const merged = reader.picks.length < before;
      reader.picks.push(pick);
      markBlock(pick.block);
      syncPicksControl();
      // Undo removes the one pick; after a merge that would drop the older ones too, so none is offered.
      showToast("Added to Review", merged ? null : pick.id);
    } catch (error) {
      if (error.status === 401) return;
      showToast(error.message || "Could not add this passage.", null);
    }
  }

  async function removePick(id) {
    const pick = reader.picks.find((p) => p.id === id);
    const source = reader.source;
    try {
      await api("DELETE", `/api/review/picks/${encodeURIComponent(id)}`);
    } catch (error) {
      if (error.status === 401) return;
      if (error.status !== 404) { showToast(error.message || "Could not remove this passage.", null); return; }
    }
    if (!reader.open || reader.source !== source) return;
    reader.picks = reader.picks.filter((p) => p.id !== id);
    if (pick) markBlock(pick.block);
    syncPicksControl();
    showToast("Removed from Review", null);
  }

  // The panel of this source's picks, in the existing sheet.
  function fillPicks(section) {
    const scroll = h("div", "sheet-scroll");
    scroll.append(h("h2", "sheet-heading", "In Review"));
    scroll.append(h("p", "sheet-sub", reader.picks.length
      ? `${plural(reader.picks.length, "passage", "passages")} from this source` : "Nothing from this source is in Review."));
    const list = h("ul", "pick-list");
    for (const pick of reader.picks) {
      const row = h("li", "pick-row");
      row.append(h("p", "pick-row-text", pick.text));
      const remove = h("button", "chip-button", "Remove");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove from Review: ${pick.text.slice(0, 60)}`);
      remove.addEventListener("click", () => removePick(pick.id));
      row.append(remove);
      list.append(row);
    }
    scroll.append(list);
    section.replaceChildren(scroll);
  }

  function resetPicks() {
    reader.picks = [];
    clearTimeout(reader.settle);
    hideAction();
    hideToast();
    syncPicksControl();
  }

  function init() {
    $("reader-picks").addEventListener("click", () => openSheet("picks", $("reader-picks"), fillPicks));
    $("pick-action-button").addEventListener("click", addPick);
    // Pressing the button must not clear the selection it acts on.
    for (const type of ["mousedown", "pointerdown"]) {
      $("pick-action").addEventListener(type, (event) => { event.preventDefault(); reader.armed = reader.pending; });
    }
    // A press that slides off the button is not a click: forget what it armed.
    for (const type of ["mouseleave", "pointerleave", "pointercancel"]) {
      $("pick-action").addEventListener(type, () => { reader.armed = null; });
    }
    $("pick-toast-undo").addEventListener("click", () => { const id = reader.undo; if (id) removePick(id); });
    document.addEventListener("selectionchange", onSelectionChange);
    $("source-reader").querySelector(".reader-scroll").addEventListener("scroll", () => hideAction(), { passive: true });
  }

  return { markAll, syncPicksControl, resetPicks, init };
};
