"use strict";

// Settings screen. The Tags tab lists the tags the classifier suggested; one tap approves or blocks one.
// Tapping a tag shows the notes behind it in the shared sheet (a bottom sheet on phones, a right panel on desktop).
// Every piece of text from the server is inserted with textContent, never as HTML.
(() => {
  let suggested = [];  // [{name, count, notes}]
  const busy = new Set();  // tags with a decision being sent

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

  // Sends one decision. Returns true when the server saved it; the row then leaves the list.
  async function decide(name, verdict) {
    if (busy.has(name)) return false;
    busy.add(name);
    message("");
    try {
      await api("POST", `/api/tags/${encodeURIComponent(name)}/${verdict}`);
    } catch (error) {
      busy.delete(name);
      if (error.status !== 401) message("Could not save that. Check your connection and try again.");
      return false;
    }
    busy.delete(name);
    if (showingTag(name)) closeSheet();
    suggested = suggested.filter((tag) => tag.name !== name);
    render();
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
    row.className = "tag-row";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "tag-open";
    open.setAttribute("aria-haspopup", "dialog");
    open.setAttribute("aria-expanded", showingTag(tag.name) ? "true" : "false");
    open.append(textElement("span", "tag-name", tag.name), textElement("span", "tag-count", plural(tag.count)));
    open.addEventListener("click", () => openTag(tag, open));
    row.append(open, iconButton("tag-block", "Block", "close", tag, "block"),
      iconButton("tag-approve", "Approve", "check", tag, "approve"));
    return row;
  }

  function render() {
    $("tags-title").textContent = suggested.length ? `Suggested · ${suggested.length}` : "Suggested";
    $("tag-list").replaceChildren(...suggested.map(renderRow));
    $("tags-empty").hidden = suggested.length > 0;
    $("tags-approve-all").hidden = suggested.length < 2;
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
    const intro = document.createElement("div");
    intro.className = "source-titles";
    intro.append(textElement("h3", "sheet-heading", tag.name),
      textElement("p", "sheet-sub", `${plural(tag.count)} proposed it`));
    const actions = document.createElement("div");
    actions.className = "sheet-actions";
    const approve = textElement("button", "primary", "Approve");
    approve.type = "button";
    approve.addEventListener("click", () => decide(tag.name, "approve"));
    const block = textElement("button", "", "Block");
    block.type = "button";
    block.addEventListener("click", () => decide(tag.name, "block"));
    actions.append(approve, block);
    section.replaceChildren(intro, ...tag.notes.map(noteCard), actions);
  }

  function openTag(tag, opener) {
    openSheet("tags", opener, (section) => fillTag(section, tag));
    for (const row of document.querySelectorAll(".tag-open")) {
      row.setAttribute("aria-expanded", row === opener ? "true" : "false");
    }
  }

  async function approveAll() {
    for (const tag of [...suggested]) {
      if (!(await decide(tag.name, "approve"))) return;
    }
  }

  // Called by the shell when Settings is shown: load the suggestions.
  async function show() {
    message("");
    try {
      const data = await api("GET", "/api/tags");
      suggested = data.suggested || [];
      render();
    } catch (error) {
      if (error.status !== 401) message("Could not load the suggested tags. Check your connection and try again.");
    }
  }

  $("tags-approve-all").addEventListener("click", approveAll);
  window.settingsScreen = { show };
})();
