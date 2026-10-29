"use strict";

// Settings screen. The Tags tab lists the tags the classifier suggested; one tap approves or blocks one.
// Tapping a tag shows the notes behind it in the shared sheet (a bottom sheet on phones, a right panel on desktop).
// Every piece of text from the server is inserted with textContent, never as HTML.
(() => {
  let suggested = [];  // [{name, count, notes}]
  let inUse = [];  // [{name, count}] the one tag list: every tag the agent may use, and how many notes carry it
  const busy = new Set();  // tags with a decision being sent
  let loads = 0;  // bumped by every load and every saved decision, so a late list cannot bring back a decided tag

  function message(text) {
    $("tags-message").textContent = text || "";
  }

  function plural(count) {
    return count === 1 ? "1 note" : `${count} notes`;
  }

  function showingTag(name) {
    const section = $("sheet").querySelector('[data-sheet="tags"]');
    return sheet.open && sheet.name === "tags" && section.dataset.tag === name;
  }

  // Keyboard focus stays in the list after a row leaves it: on the row now in the same place, or the heading.
  function keepFocus(index) {
    const rows = document.querySelectorAll(".tag-open");
    (rows[Math.min(index, rows.length - 1)] || $("tags-empty")).focus();
  }

  // Sends one decision. Returns true when the row has left the list (saved, or no longer suggested).
  async function decide(name, verdict) {
    if (busy.has(name)) return false;
    busy.add(name);
    message("");
    let gone = false;
    try {
      await api("POST", `/api/tags/${encodeURIComponent(name)}/${verdict}`);
      gone = true;
    } catch (error) {
      if (error.status === 404) {
        // Another device already decided it, or its notes changed: it is not suggested any more.
        gone = true;
        message("That tag is no longer suggested.");
      } else if (error.status !== 401) {
        message("Could not save that. Check your connection and try again.");
      }
    }
    busy.delete(name);
    if (!gone) return false;
    loads += 1;
    if (showingTag(name)) closeSheet();
    const index = suggested.findIndex((tag) => tag.name === name);
    const hadFocus = $("settings").contains(document.activeElement) || $("sheet").contains(document.activeElement);
    suggested = suggested.filter((tag) => tag.name !== name);
    if (verdict === "approve" && !inUse.some((tag) => tag.name === name)) inUse = [...inUse, { name, count: 0 }];
    render();
    if (hadFocus) keepFocus(index);
    announce(`${name} ${verdict === "approve" ? "approved" : "blocked"}`);
    return true;
  }

  function iconButton(className, label, name, tag, verdict) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `icon-button ${className}`;
    button.setAttribute("aria-label", `${label} ${tag.name}`);
    button.append(icon(name));
    button.addEventListener("click", () => decide(tag.name, verdict));
    return button;
  }

  function renderRow(tag) {
    const row = document.createElement("li");
    // A list drawn again while this tag's notes are open keeps the row lit.
    row.className = showingTag(tag.name) ? "tag-row is-active" : "tag-row";
    row.dataset.tag = tag.name;
    const open = document.createElement("button");
    open.type = "button";
    open.className = "tag-open";
    open.setAttribute("aria-haspopup", "dialog");
    const line = document.createElement("span");
    line.className = "tag-line";
    line.append(textElement("span", "tag-name", tag.name), textElement("span", "tag-count", plural(tag.count)));
    open.append(line);
    // A second line names a few of the notes behind the tag (on desktop; the phone row keeps one line).
    const titles = (tag.notes || []).map((note) => note.title).filter(Boolean).join(", ");
    if (titles) open.append(textElement("span", "tag-preview", titles));
    open.addEventListener("click", () => openTag(tag, open));
    // The open row swaps its two buttons for a chevron.
    const chevron = icon("chevron");
    chevron.setAttribute("class", "icon tag-open-chevron");
    row.append(open, iconButton("tag-block", "Block", "close", tag, "block"),
      iconButton("tag-approve", "Approve", "check", tag, "approve"), chevron);
    return row;
  }

  function render() {
    $("tags-title").textContent = suggested.length ? `Suggested · ${suggested.length}` : "Suggested";
    $("tag-list").replaceChildren(...suggested.map(renderRow));
    $("tags-head").hidden = suggested.length === 0;
    $("tags-empty").hidden = suggested.length > 0;
    $("tags-approve-all").hidden = suggested.length < 2;
    // The tags in use, read-only.
    const box = $("tags-inuse");
    box.hidden = inUse.length === 0;
    box.querySelector("ul").replaceChildren(...inUse.map((tag) => {
      const item = document.createElement("li");
      item.append(textElement("span", "pick-tag", tag.count ? `${tag.name} · ${tag.count}` : tag.name));
      return item;
    }));
  }

  function noteCard(note) {
    const url = String(note.obsidian_url || "");
    const card = document.createElement(url.startsWith("obsidian://") ? "a" : "div");
    card.className = "note-card";
    if (card.tagName === "A") card.href = url;
    const kind = [note.source_type, ...(note.people || [])].filter(Boolean).join(" · ");
    if (kind) card.append(textElement("span", "kicker", kind));
    card.append(textElement("span", "note-title", note.title));
    if (note.description) card.append(textElement("span", "note-desc", note.description));
    return card;
  }

  function fillTag(section, tag) {
    section.dataset.tag = tag.name;
    sheet.mark = { kind: "tag", name: tag.name };
    const scroll = document.createElement("div");
    scroll.className = "sheet-scroll";
    const notes = document.createElement("div");
    notes.className = "tags-list-gap";
    notes.append(...tag.notes.map(noteCard));
    scroll.append(textElement("span", "kicker", "Suggested tag"), textElement("h3", "sheet-heading tag-heading", tag.name),
      textElement("p", "sheet-note", `${plural(tag.count)} proposed it`), notes);
    // Block on the left, Approve on the right, as on the rows.
    const actions = document.createElement("div");
    actions.className = "sheet-actions";
    const block = textElement("button", "", "Block");
    block.type = "button";
    block.addEventListener("click", () => decide(tag.name, "block"));
    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "primary";
    approve.append(icon("check"), "Approve");
    approve.addEventListener("click", () => decide(tag.name, "approve"));
    actions.append(block, approve);
    section.replaceChildren(scroll, actions);
  }

  function openTag(tag, opener) {
    openSheet("tags", opener, (section) => fillTag(section, tag));
  }

  async function approveAll() {
    for (const tag of [...suggested]) {
      if ($("settings").hidden || !(await decide(tag.name, "approve"))) return;
    }
  }

  // Called by the shell when Settings is shown: load the suggestions.
  async function show() {
    message("");
    const mine = ++loads;
    try {
      const data = await api("GET", "/api/tags");
      if (mine !== loads) return;  // a decision was saved meanwhile; this list may still hold that tag
      suggested = data.suggested || [];
      inUse = data.in_use || [];
      render();
    } catch (error) {
      if (error.status !== 401) message("Could not load the suggested tags. Check your connection and try again.");
    }
  }

  $("tags-approve-all").addEventListener("click", approveAll);
  window.settingsScreen = { show };
})();
