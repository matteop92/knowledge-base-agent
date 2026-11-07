"use strict";

// The Add card's optional row: Type, Author, Tags and Title. A set value is a chip, an unset field is a "+ Field" button, and
// the field being typed in is an input with a type-ahead list. capture.js starts it with init() and reads the row back through
// extras(); matching and the type list live in capture-fields.js. Every piece of text is inserted with textContent, never as HTML.
(() => {
  const CF = window.captureFields;
  const MAX_TAGS = 10;
  const MAX_AUTHORS = 10;
  const FIELDS = ["type", "author", "tags", "title"];
  const INPUT_IDS = { type: "capture-type", author: "capture-author", tags: "capture-tags", title: "capture-title" };
  const LIST_LABELS = { type: "Types", author: "Matching people", tags: "Matching tags" };
  const BLUR_MS = 120;  // a tap on another control moves focus first; the card must not change shape under that tap
  // Splits on commas and new lines, trims, and drops repeats (any letter case); a repeat of a tag already there comes back in
  // `duplicates` so its chip can flash. The server accepts ten tags, so a note keeps the first ten and `full` says some were left out.
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
  let lineText = () => "";  // what the collapsed card shows as its one line (given by capture.js)
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
    $("capture-line").textContent = away ? lineText() : "";
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

  async function loadTags() {
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

  function loadChoices() {
    loadTags();
    loadPeople();
  }

  // Wires the four fields and the phone's collapse. `options.lineText` gives the one line the collapsed card shows.
  function init(options) {
    lineText = options.lineText;
    for (const field of FIELDS) wireField(field);
    phone.addEventListener("change", () => renderRow());
  }

  // What the request carries from the row; the type and the authors only when set, so an empty one leaves the note to the
  // classifier or its own frontmatter.
  function extras() {
    const out = { title: titleText || null, tags: [...tagList] };
    if (typeValue) out.type = typeValue;
    if (authorList.length) out.authors = [...authorList];
    return out;
  }

  // The shared title becomes a Title chip.
  function setTitle(text) {
    titleText = String(text).trim().slice(0, 200);
    renderRow();
  }

  // Sign-out also stops a pending duplicate flash.
  function reset() {
    clearFields();
    clearTimeout(flashTimer);
  }

  window.captureRow = { init, loadChoices, commitFields, extras, setTitle, clear: clearFields, reset };
})();
