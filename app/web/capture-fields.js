"use strict";

// The Add card's optional fields: the ten note types, and matching for the tag and author type-ahead. No DOM, so it runs in
// the browser and under Node. A list row is { text, count, at, length }: `at` and `length` mark the typed letters inside `text`
// so the page can set them in weight 500.
(() => {
  // What the card shows, and the value the server stores for it.
  const TYPES = [
    { label: "Article", value: "article" },
    { label: "Book", value: "book" },
    { label: "Book highlights", value: "highlights" },
    { label: "Meeting", value: "meeting" },
    { label: "Note", value: "note" },
    { label: "Other", value: "other" },
    { label: "Post", value: "social_post" },
    { label: "Quote", value: "quote" },
    { label: "Reference", value: "reference" },
    { label: "Transcript", value: "transcript" },
  ];
  const MAX_LIST = 6;
  const MAX_NAME = 120;
  // The same rule the server's tag list uses (app/ingestion/properties.py).
  const TAG = /^[a-z0-9][a-z0-9 &/-]{0,39}$/;

  const typeLabel = (value) => (TYPES.find((type) => type.value === value) || { label: "" }).label;
  const typeValue = (label) => (TYPES.find((type) => type.label === label) || { value: "" }).value;
  const squash = (text) => String(text == null ? "" : text).split(/\s+/).filter(Boolean).join(" ");

  // "Mar " becomes "mar", "#Ai" becomes "ai"; `valid` says whether the result can be a tag.
  function normalizeTag(text) {
    const tag = squash(text).replace(/^#+/, "").trim().toLowerCase();
    return { tag, valid: TAG.test(tag) };
  }

  // Prefix matches first, then contains, each in the order given; at most MAX_LIST.
  function rank(query, items, nameOf) {
    const needle = query.toLowerCase();
    if (!needle) return [];
    const prefix = [];
    const inside = [];
    for (const item of items) {
      const at = nameOf(item).toLowerCase().indexOf(needle);
      if (at === 0) prefix.push({ item, at });
      else if (at > 0) inside.push({ item, at });
    }
    return [...prefix, ...inside].slice(0, MAX_LIST).map(({ item, at }) => ({ text: nameOf(item), count: item.count || 0, at, length: needle.length }));
  }

  // Tags matching what is typed. `tags` is [{ name, count }] as the server sends it, `taken` the tags already on the note.
  // `create` is the new tag the text would make, or null when it is invalid, already exists or is already on the note.
  function suggestTags(text, tags, taken) {
    const { tag, valid } = normalizeTag(text);
    const gone = new Set((taken || []).map((name) => name.toLowerCase()));
    const rows = rank(tag, (tags || []).filter((entry) => !gone.has(entry.name.toLowerCase())), (entry) => entry.name);
    const known = gone.has(tag) || (tags || []).some((entry) => entry.name.toLowerCase() === tag);
    return { rows, create: valid && !known ? tag : null };
  }

  // People already in the vault matching what is typed. `people` is [{ name, count }], `taken` the authors already set.
  // `create` is the typed name (as typed, spaces tidied) when nobody has exactly that name.
  function suggestPeople(text, people, taken) {
    const typed = squash(text);
    const gone = new Set((taken || []).map((name) => name.toLowerCase()));
    const rows = rank(typed, (people || []).filter((entry) => !gone.has(entry.name.toLowerCase())), (entry) => entry.name);
    const known = gone.has(typed.toLowerCase()) || (people || []).some((entry) => entry.name.toLowerCase() === typed.toLowerCase());
    return { rows, create: typed && typed.length <= MAX_NAME && !known ? typed : null };
  }

  // The ten types, in order, for the Type list: no typing, no create row, no counts.
  function typeRows() {
    return TYPES.map((type) => ({ text: type.label, count: 0, at: 0, length: 0, value: type.value }));
  }

  const api = { TYPES, MAX_LIST, MAX_NAME, typeLabel, typeValue, normalizeTag, suggestTags, suggestPeople, typeRows };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.captureFields = api;
})();
