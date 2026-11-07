"use strict";

// Capture screen: paste, drop or share text, a screenshot or a .md file; it becomes a new note.
// Every piece of text from the server or the shared item is inserted with textContent, never as HTML.
(() => {
  const MAX_SIDE = 2000;  // screenshots are downscaled here, so the server never receives huge images
  const SHARE_CACHE = "ka-share";
  const YOUTUBE = /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com\/(watch|shorts|live|embed)|youtu\.be\/)/i;
  let pending = null;  // {kind, content?, image?, label}
  let fileLabel = null;  // "Markdown file" or "Text file" while the box holds a loaded file
  let inFlight = 0;  // saves being sent; Save stays disabled until all are done
  let fileToken = 0;  // bumped by reset(), so a file still being read cannot fill a cleared box
  const retries = new Map();  // capture id -> request body, for Retry
  const pollers = new Map();  // capture id -> timer, so one save is never polled twice
  const retrying = new Set();  // failed saves being resent, so a re-drawn row cannot offer a second Retry

  // What was sent is kept in this browser for an hour so Retry still works after a reload. Only this device ever holds it:
  // one key per save, forgotten when the save is done, when it expires, and on sign-out or a 401.
  const RETRY_PREFIX = "ka-retry:";
  const RETRY_KEEP_MS = 60 * 60 * 1000;
  const RETRY_MAX_CHARS = 1500000;  // a bigger screenshot is not kept, so it cannot fill the store the draft also lives in
  const persisted = new Set();  // ids whose body is in storage, so a copy another tab already used is not sent twice
  let resets = 0;  // bumped by reset(), so a save that answers after sign-out cannot write anything back

  function readRetry(id) {
    let entry = null;
    try { entry = JSON.parse(load(RETRY_PREFIX + id)); } catch (_) { entry = null; }
    const fresh = entry && typeof entry.at === "number" && Date.now() - entry.at <= RETRY_KEEP_MS &&
      entry.body && typeof entry.body === "object";
    if (!fresh) { store(RETRY_PREFIX + id, null); return null; }
    return entry.body;
  }

  function keepRetry(id, body) {
    const json = body ? JSON.stringify({ body, at: Date.now() }) : null;
    store(RETRY_PREFIX + id, json && json.length <= RETRY_MAX_CHARS ? json : null);  // a full browser store just skips this
    return Boolean(json) && load(RETRY_PREFIX + id) === json;
  }

  function retryKeys() {
    const keys = [];
    try {
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (key && key.startsWith(RETRY_PREFIX)) keys.push(key);
      }
    } catch (_) { /* private mode */ }
    return keys;
  }

  // Drops everything that has expired, so nothing outlives its hour on disk; `all` clears the lot.
  function sweepRetries(all) {
    for (const key of retryKeys()) {
      if (all) store(key, null); else readRetry(key.slice(RETRY_PREFIX.length));
    }
  }

  const STATUS = {
    running: "Saving…",
    indexed: "Saved and searchable",
    queued: "Saved; searchable after the next index pass (within five minutes)",
  };

  // One classifier for what the box holds, used by both the detection row and the request body.
  function classify(text) {
    if (YOUTUBE.test(text) && !/\s/.test(text)) return "youtube";
    if (fileLabel) return $("capture-kind").value === "markdown" ? "markdown" : "textfile";
    if (/^https?:\/\/\S+$/.test(text)) return "link";
    return "text";
  }

  const FILED = "Filed in the right folder for you";
  const DETECTED = {
    youtube: { icon: "video", title: "YouTube video", sub: "Transcript saved to Transcripts" },
    markdown: { icon: "doc", title: "Markdown file", sub: FILED },
    textfile: { icon: "doc", title: "Text file", sub: FILED },
    link: { icon: "link", title: "Web link", sub: FILED },
    text: { icon: "doc", title: "Text", sub: FILED },
  };

  // What the app will treat the box contents as.
  function detect() {
    if (pending) return { icon: "image", title: "Screenshot", sub: pending.label.replace(/^Screenshot: /, "") };
    const text = $("capture-text").value.trim();
    return text ? DETECTED[classify(text)] : null;
  }

  // Keep the detection row and the Save button in step with what is in the box.
  function refresh() {
    const found = detect();
    $("capture-detect").hidden = !found;
    if (found) {
      $("capture-detect-icon").setAttribute("href", `#i-${found.icon}`);
      $("capture-detect-title").textContent = found.title;
      $("capture-detect-sub").textContent = found.sub;
    }
    $("capture-save").disabled = inFlight > 0 || !state.captureEnabled || !found;
    // "or drop a file anywhere" shows only while the box is empty.
    $("capture-drop-hint").hidden = Boolean(found);
  }

  // The optional row: Type, Author, Tags and Title. A set value is a chip, an unset field is a "+ Field" button, and the
  // field being typed in is an input with a type-ahead list. Matching and the type list live in capture-fields.js.
  // Splits on commas and new lines, trims, and drops repeats (any letter case); a repeat of a tag already there comes back in
  // `duplicates` so its chip can flash. The server accepts ten tags, so a note keeps the first ten and `full` says some were left out.
  const CF = window.captureFields;
  const MAX_TAGS = 10;
  const MAX_AUTHORS = 10;
  const FIELDS = ["type", "author", "tags", "title"];
  const INPUT_IDS = { type: "capture-type", author: "capture-author", tags: "capture-tags", title: "capture-title" };
  const LIST_LABELS = { type: "Types", author: "Matching people", tags: "Matching tags" };
  const BLUR_MS = 120;  // a tap on another control moves focus first; the card must not change shape under that tap
  function mergeTags(current, text) {
    const tags = [...current];
    const duplicates = [];
    let full = false;
    for (const part of String(text).split(/[,\n]/)) {
      const tag = part.trim();
      if (!tag) continue;
      const same = tags.find((entry) => entry.toLowerCase() === tag.toLowerCase());
      if (same) duplicates.push(same);
      else if (tags.length >= MAX_TAGS) full = true;
      else tags.push(tag);
    }
    return { tags, duplicates, full };
  }

  let typeValue = "";  // a stored type value ("social_post"), or ""
  let authorList = [];
  let tagList = [];
  let titleText = "";
  let tagChoices = [];  // [{name, count}] from the tag list
  let people = [];  // [{name, count}] from the vault
  let openField = null;  // the field being typed in, or null
  let focused = false;  // the open field has focus
  let listShown = false;
  let items = [];  // the list's rows: {kind: "option" | "create", row?, text?}
  let optionNodes = [];
  let active = -1;
  let flashTimer = null;
  let closeTimer = null;
  const addButtons = {};  // field -> its "+ Field" button, which moves under the list on a phone while a field has focus

  const slotOf = (field) => $(`capture-slot-${field}`);
  const inputOf = (field) => $(INPUT_IDS[field]);

  function tagStatus(text) {
    $("capture-tag-status").textContent = text;
  }

  function chip(label, text, removeLabel, onRemove, flash) {
    const item = document.createElement("span");
    item.className = flash ? "cf-chip is-duplicate" : "cf-chip";
    if (label) {
      const name = document.createElement("span");
      name.className = "cf-chip-label";
      name.textContent = label;
      item.append(name);
    }
    const value = document.createElement("span");
    value.className = "cf-chip-value";
    value.textContent = text;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "cf-remove";
    remove.setAttribute("aria-label", removeLabel);
    remove.append(icon("chip-cross"));
    remove.addEventListener("click", onRemove);
    item.append(value, remove);
    return item;
  }

  const canAdd = {
    type: () => !typeValue,
    author: () => authorList.length < MAX_AUTHORS,
    tags: () => tagList.length < MAX_TAGS,
    title: () => !titleText,
  };

  // On a phone the card collapses while a row field has focus: one line of the text, the list in flow under the row.
  const collapsed = () => phone.matches && openField !== null && focused;

  function removed(field, message) {
    tagStatus(message);
    renderRow();
    if (!openField && !addButtons[field].hidden) addButtons[field].focus();
  }

  // Draws the chips and buttons. The open field's input is never rebuilt, so typing and focus are not interrupted.
  function renderRow(flash) {
    const chips = {
      type: typeValue ? [chip("Type", CF.typeLabel(typeValue), "Remove type", () => { typeValue = ""; removed("type", "Removed type"); })] : [],
      author: authorList.map((name, index) => chip(index === 0 ? "Author" : "", name, `Remove author ${name}`, () => {
        authorList = authorList.filter((entry) => entry !== name);
        removed("author", `Removed author ${name}`);
      })),
      tags: tagList.map((tag) => chip("", tag, `Remove tag ${tag}`, () => {
        tagList = tagList.filter((entry) => entry !== tag);
        removed("tags", `Removed tag ${tag}`);
      }, Boolean(flash) && flash.includes(tag))),
      title: titleText ? [chip("Title", titleText, "Remove title", () => { titleText = ""; removed("title", "Removed title"); })] : [],
    };
    const away = collapsed();
    for (const field of FIELDS) {
      const slot = slotOf(field);
      slot.querySelector(".cf-chips").replaceChildren(...chips[field]);
      const button = addButtons[field];
      button.hidden = openField === field || !canAdd[field]();
      slot.querySelector(".cf-input").hidden = openField !== field;
      if (away) { if (button.parentElement !== $("capture-rest")) $("capture-rest").append(button); }
      else if (button.parentElement !== slot) slot.insertBefore(button, slot.querySelector(".cf-input"));
    }
    $("capture").classList.toggle("is-collapsed", away);
    $("capture-line").textContent = away ? (pending ? pending.label.replace(/^Screenshot: /, "") : $("capture-text").value.trim().replace(/\s+/g, " ")) : "";
    placeList();
    // A repeated tag flashes its chip for a moment; only the class changes back, so nothing is redrawn under the cursor.
    clearTimeout(flashTimer);
    if (flash && flash.length) {
      flashTimer = setTimeout(() => { for (const mark of $("capture-row").querySelectorAll(".is-duplicate")) mark.classList.remove("is-duplicate"); }, 900);
    }
  }

  // The list floats under the open field, or sits in flow under the row while the card is collapsed on a phone.
  function placeList() {
    const list = $("capture-suggest");
    const target = collapsed() || !openField ? $("capture-list-slot") : slotOf(openField).querySelector(".cf-input");
    if (list.parentElement !== target) target.append(list);
  }

  function countText(row) {
    return row.count > 0 ? `${row.count} ${row.count === 1 ? "note" : "notes"}` : "";
  }

  function optionNode(item, index) {
    const node = document.createElement("li");
    node.id = `capture-option-${index}`;
    node.setAttribute("role", "option");
    node.className = item.kind === "create" ? "cf-option cf-create" : "cf-option";
    const text = document.createElement("span");
    text.className = "cf-option-text";
    if (item.kind === "create") {
      text.append(icon("step-plus"), openField === "tags" ? `Create tag “${item.text}”` : `Add “${item.text}”`);
    } else {
      const row = item.row;
      if (row.length) {
        const hit = document.createElement("span");
        hit.className = "cf-hit";
        hit.textContent = row.text.slice(row.at, row.at + row.length);
        text.append(row.text.slice(0, row.at), hit, row.text.slice(row.at + row.length));
      } else {
        text.append(row.text);
      }
    }
    node.append(text);
    if (item.kind === "option" && countText(item.row)) {
      const count = document.createElement("span");
      count.className = "cf-option-count";
      count.textContent = countText(item.row);
      node.append(count);
    }
    // The input keeps focus while a row is pressed, so tapping a row never counts as tapping outside.
    node.addEventListener("mousedown", (event) => event.preventDefault());
    node.addEventListener("mousemove", () => { if (active !== index) { active = index; paintActive(); } });
    node.addEventListener("click", () => pick(item));
    return node;
  }

  function drawList() {
    const list = $("capture-suggest");
    const input = openField ? inputOf(openField) : null;
    list.hidden = !listShown;
    if (input && input.getAttribute("role") === "combobox") input.setAttribute("aria-expanded", listShown ? "true" : "false");
    if (!listShown) {
      list.replaceChildren();
      optionNodes = [];
      if (input) input.removeAttribute("aria-activedescendant");
      return;
    }
    list.setAttribute("aria-label", LIST_LABELS[openField]);
    optionNodes = items.map(optionNode);
    const nodes = [];
    items.forEach((item, index) => {
      if (item.kind === "create" && index > 0) {
        const rule = document.createElement("li");
        rule.className = "cf-divider";
        rule.setAttribute("role", "presentation");
        nodes.push(rule);
      }
      nodes.push(optionNodes[index]);
    });
    list.replaceChildren(...nodes);
    placeList();
    paintActive();
  }

  function paintActive() {
    optionNodes.forEach((node, index) => {
      node.classList.toggle("is-active", index === active);
      node.setAttribute("aria-selected", index === active ? "true" : "false");
    });
    const input = openField ? inputOf(openField) : null;
    if (!input) return;
    if (listShown && active >= 0) input.setAttribute("aria-activedescendant", `capture-option-${active}`);
    else input.removeAttribute("aria-activedescendant");
  }

  // What the open field offers for what is typed in it; the first row is highlighted.
  function refreshList() {
    items = [];
    if (openField === "type") items = CF.typeRows().map((row) => ({ kind: "option", row }));
    else if (openField === "tags") items = asItems(CF.suggestTags(inputOf("tags").value, tagChoices, tagList));
    else if (openField === "author") items = asItems(CF.suggestPeople(inputOf("author").value, people, authorList));
    active = items.length ? 0 : -1;
    listShown = items.length > 0;
    drawList();
  }

  function asItems(found) {
    return [...found.rows.map((row) => ({ kind: "option", row })), ...(found.create ? [{ kind: "create", text: found.create }] : [])];
  }

  function openInput(field) {
    clearTimeout(closeTimer);
    if (openField && openField !== field) closeInput();
    openField = field;
    renderRow();
    inputOf(field).focus();
    focused = true;
    refreshList();
    renderRow();
  }

  // Closes the open field; whatever is typed in it becomes a chip first.
  function closeInput() {
    if (openField) commitField(openField);
    openField = null;
    listShown = false;
    items = [];
    drawList();
    renderRow();
  }

  function afterBlur() {
    if (focused || !openField) return;
    listShown = false;
    if (openField === "title" || !inputOf(openField).value.trim()) { closeInput(); return; }
    drawList();  // typed text stays in its field; Save turns it into a chip
    renderRow();
  }

  function commitField(field) {
    if (field === "tags") commitTags();
    else if (field === "author") commitAuthor();
    else if (field === "title") commitTitle();
  }

  // Turns what is typed in the tag field into chips. Text that is not a valid tag stays in the field.
  function commitTags() {
    const field = inputOf("tags");
    if (!field.value.trim()) { field.value = ""; return; }
    const good = [];
    const bad = [];
    for (const part of field.value.split(/[,\n]/)) {
      if (!part.trim()) continue;
      const one = CF.normalizeTag(part);
      if (one.valid) good.push(one.tag); else bad.push(part.trim());
    }
    const before = tagList.length;
    const merged = mergeTags(tagList, good.join(","));
    tagList = merged.tags;
    field.value = bad.join(", ");
    renderRow(merged.duplicates);
    if (bad.length) tagStatus(`“${bad[0]}” is not a valid tag`);
    else if (merged.full) tagStatus(`A note keeps up to ${MAX_TAGS} tags`);
    else if (tagList.length > before) tagStatus(`Added ${tagList.slice(before).join(", ")}`);
  }

  function commitAuthor() {
    const field = inputOf("author");
    const name = field.value.split(/\s+/).filter(Boolean).join(" ");
    field.value = "";
    if (!name) return;
    if (authorList.some((entry) => entry.toLowerCase() === name.toLowerCase())) { tagStatus(`${name} is already added`); return; }
    if (authorList.length >= MAX_AUTHORS) { tagStatus(`A note keeps up to ${MAX_AUTHORS} authors`); return; }
    authorList = [...authorList, name];
    renderRow();
    tagStatus(`Added author ${name}`);
  }

  function commitTitle() {
    const field = inputOf("title");
    const text = field.value.trim();
    field.value = "";
    if (!text) return;
    titleText = text;
    renderRow();
  }

  function commitFields() {
    for (const field of ["tags", "author", "title"]) commitField(field);
  }

  // Picks a row: a type closes its field; tags and authors clear it and stay open for the next one.
  function pick(item) {
    if (openField === "type") {
      typeValue = item.row.value;
      tagStatus(`Type ${CF.typeLabel(typeValue)}`);
      closeInput();
      return;
    }
    const text = item.kind === "create" ? item.text : item.row.text;
    const field = inputOf(openField);
    if (openField === "tags") {
      const merged = mergeTags(tagList, text);
      tagList = merged.tags;
      field.value = "";
      renderRow(merged.duplicates);
      tagStatus(merged.full ? `A note keeps up to ${MAX_TAGS} tags` : tagList.length >= MAX_TAGS ? `Added ${text}. A note keeps up to ${MAX_TAGS} tags` : `Added ${text}`);
      if (tagList.length >= MAX_TAGS) { closeInput(); return; }
    } else {
      field.value = text;
      commitAuthor();
      if (authorList.length >= MAX_AUTHORS) { closeInput(); return; }
    }
    field.focus();
    refreshList();
  }

  function onKey(field, event) {
    if (event.isComposing) return;
    const input = inputOf(field);
    const key = event.key;
    if (key === "ArrowDown" || key === "ArrowUp") {
      if (!items.length) return;
      event.preventDefault();
      if (!listShown) { listShown = true; drawList(); return; }
      active = (active + (key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      paintActive();
    } else if (key === "Enter") {
      event.preventDefault();  // Enter never sends the form
      if (listShown && active >= 0) pick(items[active]);
      else if (field === "title") closeInput();
      else commitField(field);
    } else if (key === "Escape") {
      if (!listShown) return;
      event.preventDefault();
      event.stopPropagation();
      listShown = false;  // the text stays
      drawList();
    } else if (key === "," && field === "tags") {
      event.preventDefault();
      commitTags();
      refreshList();
    } else if (key === "Backspace" && !input.value && (field === "tags" || field === "author")) {
      const list = field === "tags" ? tagList : authorList;
      if (!list.length) return;
      const gone = list[list.length - 1];
      if (field === "tags") tagList = tagList.slice(0, -1); else authorList = authorList.slice(0, -1);
      tagStatus(`Removed ${field === "tags" ? "tag" : "author"} ${gone}`);
      renderRow();
      refreshList();
    }
  }

  function wireField(field) {
    const input = inputOf(field);
    input.addEventListener("keydown", (event) => onKey(field, event));
    input.addEventListener("input", () => {
      if (field !== "title" && field !== "type") input.size = Math.max(phone.matches ? 12 : 6, input.value.length + 1);
      if (field === "tags" && /[,\n]/.test(input.value)) commitTags();
      refreshList();
    });
    input.addEventListener("focus", () => {
      clearTimeout(closeTimer);
      focused = true;
      if (openField === field) { refreshList(); renderRow(); }
    });
    // Leaving the field (a tap outside) closes the list and keeps the text; the change waits a moment so a tap on
    // another control is not moved out from under the finger.
    input.addEventListener("blur", () => {
      focused = false;
      clearTimeout(closeTimer);
      closeTimer = setTimeout(afterBlur, BLUR_MS);
    });
    addButtons[field] = slotOf(field).querySelector(".cf-add");
    addButtons[field].addEventListener("click", () => openInput(field));
  }

  // Clears every field back to the row at rest.
  function clearFields() {
    typeValue = "";
    authorList = [];
    tagList = [];
    titleText = "";
    for (const field of FIELDS) inputOf(field).value = "";
    clearTimeout(closeTimer);
    openField = null;
    focused = false;
    listShown = false;
    items = [];
    drawList();
    renderRow();
  }

  async function loadApprovedTags() {
    try {
      const data = await api("GET", "/api/tags");
      tagChoices = data.in_use || (data.approved || []).map((name) => ({ name, count: 0 }));
    } catch (error) {
      tagChoices = [];
    }
  }

  async function loadPeople() {
    try {
      people = (await api("GET", "/api/capture-people")).people || [];
    } catch (error) {
      people = [];
    }
  }

  function message(text, ok) {
    $("capture-message").textContent = text || "";
    $("capture-message").classList.toggle("ok", Boolean(ok));
  }

  function obsidianLink(path) {
    return `obsidian://open?vault=${encodeURIComponent(state.vaultName || "")}&file=${encodeURIComponent(path)}`;
  }

  async function downscale(file) {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return canvas.toDataURL("image/jpeg", 0.85);
  }

  async function takeFile(file) {
    message("");
    if (!file) return;
    const mine = ++fileToken;
    $("capture-preview").hidden = true;
    try {
      if (file.type.startsWith("image/")) {
        const image = await downscale(file);
        if (mine !== fileToken) return;
        fileLabel = null;
        pending = { kind: "screenshot", image, label: `Screenshot: ${file.name || "image"}` };
        // Show exactly what Save will send.
        $("capture-preview").src = pending.image;
        $("capture-preview").hidden = false;
      } else if (/\.(md|markdown|txt)$/i.test(file.name || "") || file.type.startsWith("text/")) {
        // The file's text goes into the visible box, so nothing is saved that cannot be seen.
        const text = await file.text();
        if (mine !== fileToken) return;
        pending = null;
        $("capture-text").value = text;
        const plain = /\.txt$/i.test(file.name || "");
        $("capture-kind").value = plain ? "text" : "markdown";
        fileLabel = plain ? "Text file" : "Markdown file";
      } else {
        pending = null;
        message("Only screenshots (PNG, JPEG, WebP), .md and .txt files can be added.");
      }
    } catch (_) {
      pending = null;
      $("capture-file").value = "";
      message("That file could not be read.");
    }
    refresh();
  }

  function takeText(text, title, url) {
    $("capture-text").value = [text, url && !(text || "").includes(url) ? url : ""].filter(Boolean).join("\n");
    if (title) { titleText = String(title).trim().slice(0, 200); renderRow(); }
    refresh();
  }

  // The type and the authors are sent only when set, so an empty one leaves the note to the classifier or its own frontmatter.
  function requestBody() {
    const extras = { title: titleText || null, tags: [...tagList] };
    if (typeValue) extras.type = typeValue;
    if (authorList.length) extras.authors = [...authorList];
    if (pending) return { ...pending, label: undefined, ...extras };
    const text = $("capture-text").value.trim();
    if (!text) return null;
    const kind = classify(text);
    if (kind === "youtube" || kind === "markdown") return { kind, content: text, ...extras };
    return { kind: "text", content: text, url: /^https?:\/\/\S+$/.test(text) ? text : null, ...extras };
  }

  async function save() {
    commitFields();  // valid text still typed in a field becomes a chip before sending
    const body = requestBody();
    if (body) await send(body, true);
  }

  // `fromForm` is true for a normal Save (the box is cleared after it); Retry resends the stored body and leaves the box alone.
  async function send(body, fromForm) {
    inFlight += 1;
    refresh();
    const session = resets;
    let sent = false;
    try {
      const { capture_id: id } = await api("POST", "/api/captures", body);
      if (session !== resets) return false;  // signed out meanwhile: nothing from this save may reappear
      retries.set(id, body);
      if (keepRetry(id, body)) persisted.add(id);
      if (fromForm) {
        pending = null;
        fileLabel = null;
        fileToken += 1;
        $("capture-preview").hidden = true;
        $("capture-kind").value = "text";
        $("capture-text").value = "";
        clearFields();
        $("capture-file").value = "";
        leaveGap();
      }
      message("Saving… it appears under Recent below.", true);
      sent = true;
      renderCapture({ id, kind: body.kind, status: "running", title: body.title || "New capture" }, true);
      refreshTimes();
      showToast(body.title || "New capture");
      pollCapture(id, true);
    } catch (error) {
      message(error.status === 429 ? "The daily spending cap was reached." : error.message);
    } finally {
      inFlight = Math.max(0, inFlight - 1);
      refresh();
    }
    return sent;
  }

  const KINDS = { text: "Text", markdown: "Markdown", screenshot: "Screenshot", youtube: "YouTube" };

  // The time column: "now", "12 min", "1 h", "3 d", then a short date from a week on. A clock set behind the server's shows "now".
  function formatAge(iso, now) {
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return "";
    const minutes = Math.floor((now.getTime() - then.getTime()) / 60000);
    if (minutes < 1) return "now";
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} h`;
    const days = Math.floor(hours / 24);
    return days < 7 ? `${days} d` : then.toLocaleDateString([], { day: "numeric", month: "short" });
  }

  // One row per save: the icon says the state (its tooltip has the detail), then the title, then the link, the type and Retry.
  function renderCapture(job, prepend) {
    let item = document.querySelector(`.capture-item[data-id="${CSS.escape(job.id)}"]`);
    if (!item) {
      item = document.createElement("li");
      item.className = "capture-item";
      item.dataset.id = job.id;
      prepend ? $("capture-list").prepend(item) : $("capture-list").append(item);
    }
    item.replaceChildren();
    if (job.status === "done") store(RETRY_PREFIX + job.id, null);
    // A save still running at reload that then fails gets its Retry back from the kept copy.
    if (job.status === "failed" && !retries.has(job.id)) {
      const kept = readRetry(job.id);
      if (kept) { retries.set(job.id, kept); persisted.add(job.id); }
    }
    item.dataset.status = job.status === "failed" ? "failed" : job.status === "running" ? "running" : "done";
    const detail = job.status === "running" ? STATUS.running :
      job.status === "failed" ? `Failed: ${job.error || "unknown reason"}` :
      job.indexed ? STATUS.indexed : STATUS.queued;
    const state = document.createElement("span");
    state.className = "capture-state";
    state.title = detail;
    state.setAttribute("role", "img");
    state.setAttribute("aria-label", detail);
    state.append(icon(job.status === "running" ? "saving" : job.status === "failed" ? "failed" : "saved"));
    const body = document.createElement("div");
    body.className = "capture-body";
    const title = document.createElement("p");
    title.className = "capture-title";
    const label = document.createElement("span");
    label.textContent = job.title || "Capture";
    title.append(label);
    const meta = document.createElement("div");
    meta.className = "capture-meta";
    if (job.status === "running") {
      const saving = document.createElement("span");
      saving.className = "muted";
      saving.textContent = STATUS.running;
      meta.append(saving);
    } else if (job.vault_path) {
      const open = document.createElement("a");
      open.href = obsidianLink(job.vault_path);
      open.className = "open-obsidian";
      open.textContent = "Open in Obsidian";
      open.prepend(icon("obsidian"));
      meta.append(open);
    }
    if (KINDS[job.kind]) {
      const pill = document.createElement("span");
      pill.className = "type-pill";
      pill.textContent = KINDS[job.kind];
      meta.append(pill);
    }
    if (job.status === "failed") {
      if (retries.has(job.id)) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "retry-capture";
        retry.append(icon("retry"), "Retry");
        retry.disabled = retrying.has(job.id);
        retry.addEventListener("click", async () => {
          if (retrying.has(job.id)) return;
          retrying.add(job.id);
          retry.disabled = true;
          // Another tab may have resent this one already: its kept copy is gone, so this tab must not send it again.
          if (persisted.has(job.id) && !readRetry(job.id)) {
            retrying.delete(job.id);
            retries.delete(job.id);
            persisted.delete(job.id);
            renderCapture(job);
            return;
          }
          const body = retries.get(job.id);
          const session = resets;
          keepRetry(job.id, null);
          persisted.delete(job.id);  // gone from storage before the resend, so a reload mid-way cannot offer it twice
          const sent = await send(body, false);
          retrying.delete(job.id);
          // Resent: this failed row keeps no Retry, so the note cannot be added twice.
          if (sent) retries.delete(job.id); else { if (session === resets && keepRetry(job.id, body)) persisted.add(job.id); retry.disabled = false; }
        });
        meta.append(retry);
      } else {
        const again = document.createElement("span");
        again.className = "muted";
        again.textContent = "Add it again";
        meta.append(again);
      }
    }
    body.append(title, meta);
    // The time sits in its own column, outside the meta line. A save made just now has no server time yet.
    const when = job.created_at || item.dataset.created || new Date().toISOString();
    item.dataset.created = when;
    const time = document.createElement("span");
    time.className = "capture-time";
    time.textContent = formatAge(when, new Date());
    item.append(state, body, time);
    // A saved and indexed note opens on its own Sources page from its title; the Obsidian link beside it still opens Obsidian.
    const openable = job.status === "done" && job.source_id;
    title.classList.toggle("is-openable", Boolean(openable));
    if (openable) title.append(icon("chevron"));
    if (openable) { title.tabIndex = 0; title.setAttribute("role", "link"); } else { title.removeAttribute("tabindex"); title.removeAttribute("role"); }
    const open = () => window.sourcesScreen.openById(job.source_id);
    title.onclick = openable ? open : null;
    title.onkeydown = openable ? (event) => { if (event.key === "Enter") open(); } : null;
  }

  function pollCapture(id, announceResult) {
    if (pollers.has(id)) return;
    const later = (ms) => pollers.set(id, setTimeout(tick, ms));
    let failures = 0;
    const tick = async () => {
      pollers.delete(id);
      try {
        const job = await api("GET", `/api/captures/${encodeURIComponent(id)}`);
        failures = 0;
        renderCapture(job);
        refreshTimes();
        if (job.status === "running") { later(POLL_MS); return; }
        if (announceResult) {
          message(job.status === "failed" ? `Not saved: ${job.error || "unknown reason"}` : `Saved to ${job.vault_path}`,
                  job.status !== "failed");
        }
      } catch (error) {
        if (error.status === 401) return;
        failures += 1;
        if (failures < POLL_RETRIES) later(POLL_MS * 2);
        else message("Lost touch while checking this save. Open Add again to see where it got to.");
      }
    };
    later(POLL_MS);
  }

  // Times and the weekly count are worked out when the list is drawn and then once a minute while Add is open.
  const RECENT_LIMIT = 20;  // /api/captures returns at most this many; beyond it a weekly count would be incomplete
  function refreshTimes() {
    const now = new Date();
    let week = 0;
    const items = document.querySelectorAll(".capture-item");
    for (const item of items) {
      const created = new Date(item.dataset.created);
      item.querySelector(".capture-time").textContent = formatAge(item.dataset.created, now);
      if (!Number.isNaN(created.getTime()) && now.getTime() - created.getTime() < 7 * 86400000) week += 1;
    }
    const count = $("recent-count");
    count.hidden = week === 0 || items.length >= RECENT_LIMIT;
    count.textContent = `${week} this week`;
  }

  // The phone's "Saving…" message: it stays a few seconds and never takes focus.
  let toastTimer = null;
  function showToast(title) {
    const toast = $("toast");
    toast.querySelector(".toast-title").textContent = `Saving “${title}”`;
    toast.querySelector(".toast-sub").textContent = "It appears under Recent when it’s ready";
    toast.classList.add("is-shown");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("is-shown"), 4000);
    // Recent is where the new row appears: on a phone, with the keyboard up, bring it into view.
    if (phone.matches) $("capture-list").firstElementChild?.scrollIntoView?.({ block: "nearest" });
  }

  // Fill a gap: Add opens with the gap above the box and "Save note" in place of Save, until the note is saved or the user goes back.
  let gapMode = null;  // {gap, question, conversationId}

  function renderMode() {
    const filling = Boolean(gapMode);
    $("add-title").textContent = filling ? "Fill a gap" : "Add to your notes";
    $("add-sub").hidden = filling;
    $("gap-back").hidden = !filling;
    $("gap-panel").hidden = !filling;
    $("gap-after").hidden = !filling;
    $("capture-save").textContent = filling ? "Save note" : "Save";
    // Nothing of a gap is left in the page once the mode ends (sign-out included).
    $("gap-panel-text").textContent = filling ? gapMode.gap : "";
    $("gap-panel-from").textContent = filling && gapMode.question ? `From “${gapMode.question}”` : "";
    $("gap-panel-from").hidden = !filling || !gapMode.question;
  }

  function leaveGap() {
    gapMode = null;
    renderMode();
  }

  // Called by the shell when the Add tab is shown: load the setting and the recent saves.
  // Opening Add for a gap sets the mode first, so the tab's own load does not clear it.
  let keepGap = false;
  async function show() {
    if (keepGap) keepGap = false; else leaveGap();
    loadApprovedTags();
    loadPeople();
    try {
      const { captures, enabled, vault_name: vaultName } = await api("GET", "/api/captures");
      state.captureEnabled = enabled;
      state.vaultName = vaultName;
      $("capture-disabled").hidden = enabled;
      $("capture-list").replaceChildren();
      sweepRetries(false);
      for (const job of captures) {
        renderCapture(job);
        if (job.status === "running") pollCapture(job.id);
      }
    } catch (error) {
      if (error.status !== 401) message("Could not load your recent saves. Check your connection and try again.");
    }
    refreshTimes();
    refresh();
    $("capture-text").focus();
  }

  // Sign-out and a 401 clear whatever was half-typed, so the next person sees an empty box.
  function reset() {
    pending = null;
    fileLabel = null;
    fileToken += 1;
    inFlight = 0;
    retries.clear();
    retrying.clear();
    persisted.clear();
    resets += 1;
    sweepRetries(true);
    for (const timer of pollers.values()) clearTimeout(timer);
    pollers.clear();
    $("capture-kind").value = "text";
    for (const id of ["capture-text", "capture-title", "capture-tags", "capture-file"]) $(id).value = "";
    clearFields();
    $("capture-preview").hidden = true;
    $("capture-preview").removeAttribute("src");
    $("capture-list").replaceChildren();
    $("recent-count").hidden = true;
    $("toast").classList.remove("is-shown");
    $("toast").querySelector(".toast-title").textContent = "";
    clearTimeout(flashTimer);
    endDrag();
    leaveGap();
    message("");
    refresh();
  }

  // Open Add for one gap from "What's missing". A draft already in the box is kept.
  async function openGap(next) {
    gapMode = next;
    renderMode();
    keepGap = true;
    await setView("add");
    keepGap = false;
    $("capture-text").focus();
  }

  // Back to the answer the gap came from.
  async function backToAnswer() {
    const conversationId = gapMode && gapMode.conversationId;
    leaveGap();
    // The answer is usually still on screen: go back to it as it is, so a failed fetch cannot wipe it.
    const onScreen = conversationId && state.conversationId === conversationId && $("turns").children.length > 0;
    if (conversationId && !onScreen) await openConversation(conversationId); else await setView("ask");
    $("question").focus();
  }

  // Android share sheet: the service worker keeps the shared item until the app (and sign-in) picks it up.
  async function takeShared() {
    if (!new URLSearchParams(location.search).has("share") || !("caches" in window)) return;
    history.replaceState(null, "", "/");
    const cache = await caches.open(SHARE_CACHE);
    try {
      const meta = await cache.match("/share/meta");
      if (!meta) return;
      const { title, text, url } = await meta.json();
      await setView("add");
      const file = await cache.match("/share/file");
      if (file) {
        const blob = await file.blob();
        const name = decodeURIComponent(file.headers.get("x-name") || "shared");
        await takeFile(new File([blob], name, { type: blob.type }));
      }
      takeText(text, title, url);
    } finally {
      // The shared item lives only in the form now; never leave it in the browser's cache.
      await cache.delete("/share/meta");
      await cache.delete("/share/file");
    }
  }

  // The browser sends dragover every few hundred milliseconds while a drag is over the page; if they stop (the drag left the
  // window, or a node under it was replaced) the target clears itself.
  const DRAG_IDLE_MS = 700;
  let dragTimer = null;
  function startDrag() {
    $("capture").classList.add("dragging");
    $("drop-target").hidden = false;
    clearTimeout(dragTimer);
    dragTimer = setTimeout(endDrag, DRAG_IDLE_MS);
  }
  function endDrag() {
    clearTimeout(dragTimer);
    $("capture").classList.remove("dragging");
    $("drop-target").hidden = true;
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("capture-form").addEventListener("submit", (event) => { event.preventDefault(); save(); });
    $("gap-back").addEventListener("click", backToAnswer);
    // The optional row: each field's input, its "+ Field" button and the type-ahead list.
    for (const field of FIELDS) wireField(field);
    phone.addEventListener("change", () => renderRow());
    setInterval(() => { if (!$("capture").hidden) refreshTimes(); }, 60000);
    $("capture-file").addEventListener("change", (event) => takeFile(event.target.files[0]));
    $("capture-attach").addEventListener("click", () => $("capture-file").click());
    $("capture-text").addEventListener("input", () => {
      // Clearing the box clears a loaded file's label, so a new paste is judged on its own.
      if (!$("capture-text").value.trim()) { fileLabel = null; $("capture-kind").value = "text"; }
      refresh();
    });
    const drop = $("capture");
    // The target shows while a drag is over the page and clears on drop, dragend, Escape, leaving the window, or silence.
    drop.addEventListener("dragenter", (event) => { event.preventDefault(); startDrag(); });
    drop.addEventListener("dragover", (event) => { event.preventDefault(); startDrag(); });
    drop.addEventListener("dragleave", (event) => { if (!event.relatedTarget) endDrag(); });
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      endDrag();
      if (event.dataTransfer.files.length) takeFile(event.dataTransfer.files[0]);
      else takeText(event.dataTransfer.getData("text/plain"));
    });
    window.addEventListener("dragend", endDrag);
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") endDrag(); });
    drop.addEventListener("paste", (event) => {
      const image = [...event.clipboardData.files].find((file) => file.type.startsWith("image/"));
      if (image) { event.preventDefault(); takeFile(image); }
    });
  });

  window.captureScreen = { takeShared, show, reset, openGap };
})();
