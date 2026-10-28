"use strict";

// Matching, counts, suggestions and tokens for the Sources page. No DOM, so it runs in the browser and under Node.
// A source row is the one the server sends: { type, title, authors, guests, tags, added: "YYYY-MM-DD", ... }.
(() => {
  const TYPE_LABELS = { highlights: "Book highlights", post: "Social posts", note: "Notes", transcript: "Transcripts",
    article: "Articles", quote: "Quotes", book: "Books" };
  const PRESETS = [{ key: "week", label: "Past week", days: 7 }, { key: "month", label: "Past 30 days", days: 30 },
    { key: "year", label: "Past year", days: 365 }];
  const DAY = 86400000;
  const MAX_PER_GROUP = 3;

  const empty = () => ({ titles: [], types: [], tags: [], people: [], date: null });
  const norm = (text) => String(text).trim().toLowerCase();
  const peopleOf = (source) => [...new Set([...(source.authors || []), ...(source.guests || [])])];
  const tagsOf = (source) => source.tags || [];
  const pad = (number) => String(number).padStart(2, "0");

  function isoDay(ms) {
    const date = new Date(ms);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function dateMatches(added, date, now) {
    if (!date) return true;
    if (!added) return false;
    if (date.preset) return added >= isoDay(+now - PRESETS.find((item) => item.key === date.preset).days * DAY);
    if (date.year) return added.startsWith(date.year);
    return (!date.from || added >= date.from) && (!date.to || added <= date.to);
  }

  // `skip` names one filter to leave out, so a menu can count its own rows with every other filter applied.
  function matches(source, state, now, skip) {
    if (skip !== "types" && state.types.length && !state.types.includes(source.type)) return false;
    if (skip !== "tags" && !state.tags.every((tag) => tagsOf(source).includes(tag))) return false;
    if (skip !== "people" && state.people.length && !peopleOf(source).some((name) => state.people.includes(name))) return false;
    if (skip !== "date" && !dateMatches(source.added, state.date, now)) return false;
    if (skip !== "titles" && !state.titles.every((title) => norm(source.title).includes(norm(title)))) return false;
    return true;
  }

  const filtered = (sources, state, now, skip) => sources.filter((source) => matches(source, state, now, skip));

  function tally(pool, valuesOf, selected, label) {
    const found = new Map();
    for (const source of pool) for (const value of valuesOf(source)) found.set(value, (found.get(value) || 0) + 1);
    for (const value of selected) if (!found.has(value)) found.set(value, 0);
    return [...found].map(([value, count]) => ({ value, label: label(value), count, selected: selected.includes(value) }))
      .filter((row) => row.count > 0 || row.selected)
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  }

  // Rows for each browse menu, counted with every other active filter applied; zero rows only while selected.
  function counts(state, sources, now) {
    const dates = filtered(sources, state, now, "date");
    return {
      types: tally(filtered(sources, state, now, "types"), (s) => [s.type], state.types, (v) => TYPE_LABELS[v] || v),
      tags: tally(filtered(sources, state, now, "tags"), tagsOf, state.tags, (v) => v),
      people: tally(filtered(sources, state, now, "people"), peopleOf, state.people, (v) => v),
      dates: [{ key: "any", label: "Any time", count: dates.length, selected: !state.date },
        ...PRESETS.map((item) => ({ key: item.key, label: item.label,
          count: dates.filter((s) => dateMatches(s.added, { preset: item.key }, now)).length,
          selected: !!state.date && state.date.preset === item.key }))],
    };
  }

  function dateText(date) {
    if (!date) return "";
    if (date.preset) return PRESETS.find((item) => item.key === date.preset).label.toLowerCase();
    if (date.year) return `in ${date.year}`;
    if (date.from && date.to) return `${date.from} to ${date.to}`;
    return date.from ? `from ${date.from}` : `until ${date.to}`;
  }

  function addToken(state, item) {
    const next = { ...state, titles: [...state.titles], types: [...state.types], tags: [...state.tags], people: [...state.people] };
    const list = { type: next.types, tag: next.tags, person: next.people, title: next.titles }[item.kind];
    if (item.kind === "date") next.date = item.value;
    else if (!list.includes(item.value)) list.push(item.value);
    return next;
  }

  // The tokens in the order the bar draws them: Type, Tag, Person, Added, Title.
  function tokens(state) {
    return [
      ...state.types.map((value) => ({ kind: "type", key: "Type", value, text: (TYPE_LABELS[value] || value).toLowerCase() })),
      ...state.tags.map((value) => ({ kind: "tag", key: "Tag", value, text: value })),
      ...state.people.map((value) => ({ kind: "person", key: "Person", value, text: value })),
      ...(state.date ? [{ kind: "date", key: "Added", value: state.date, text: dateText(state.date) }] : []),
      ...state.titles.map((value) => ({ kind: "title", key: "Title", value, text: `“${value}”` })),
    ];
  }

  function removeToken(state, token) {
    const next = { ...state, titles: [...state.titles], types: [...state.types], tags: [...state.tags], people: [...state.people] };
    if (token.kind === "date") next.date = null;
    else {
      const list = { type: next.types, tag: next.tags, person: next.people, title: next.titles }[token.kind];
      list.splice(list.indexOf(token.value), 1);
    }
    return next;
  }

  function removeLast(state) {
    const all = tokens(state);
    return all.length ? removeToken(state, all[all.length - 1]) : state;
  }

  const topThree = (items) => items.filter((item) => item.count > 0)
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)).slice(0, MAX_PER_GROUP);

  // Groups for the text typed so far. Each item shows the result count it would give.
  function suggest(text, state, sources, now) {
    const query = norm(text);
    if (!query) return [];
    const countWith = (kind, value) => filtered(sources, addToken(state, { kind, value }), now).length;
    const item = (kind, value, label) => ({ kind, value, label, count: countWith(kind, value) });
    const pick = (names, selected, kind) => topThree([...new Set(names)]
      .filter((name) => !selected.includes(name) && norm(name).includes(query)).map((name) => item(kind, name, name)));
    const groups = [];
    const kinds = [...new Set(sources.map((source) => source.type))].filter((kind) => !state.types.includes(kind));
    const types = topThree(kinds.filter((kind) => (norm(TYPE_LABELS[kind] || kind).includes(query) || kind.includes(query)))
      .map((kind) => item("type", kind, TYPE_LABELS[kind] || kind)));
    if (types.length) groups.push({ kind: "type", heading: "Type", items: types });
    const people = pick(sources.flatMap(peopleOf), state.people, "person");
    if (people.length) groups.push({ kind: "person", heading: "People", items: people });
    const tags = pick(sources.flatMap(tagsOf), state.tags, "tag");
    if (tags.length) groups.push({ kind: "tag", heading: "Tags", items: tags });
    const dates = [];
    if (/^(19|20)\d\d$/.test(query)) dates.push(item("date", { year: query }, `Added in ${query}`));
    if (/^(past|recent|last)\b/.test(query)) {
      for (const preset of PRESETS) dates.push(item("date", { preset: preset.key }, `Added ${preset.label.toLowerCase()}`));
    }
    const shown = dates.filter((entry) => entry.count > 0);
    if (shown.length) groups.push({ kind: "date", heading: "Date added", items: shown });
    const title = text.trim();
    groups.push({ kind: "title", heading: "Titles", items: [item("title", title, `Titles containing “${title}”`)] });
    return groups;
  }

  // What a key does in the search box. ctx: { empty: input is empty, count: suggestion rows, index: active row }.
  function keyAction(key, ctx) {
    if (key === "ArrowDown" || key === "ArrowUp") {
      if (!ctx.count) return { type: "none" };
      const step = key === "ArrowDown" ? 1 : -1;
      return { type: "move", index: (ctx.index + step + ctx.count) % ctx.count };
    }
    if (key === "Enter") return ctx.count && ctx.index >= 0 ? { type: "add", index: ctx.index } : { type: "none" };
    if (key === "Backspace" && ctx.empty) return { type: "remove-last" };
    if (key === "Escape") return { type: "close" };
    return { type: "none" };
  }

  const api = { TYPE_LABELS, PRESETS, empty, matches, filtered, counts, suggest, addToken, removeToken, removeLast, tokens,
    keyAction, dateText, isoDay, peopleOf };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.sourceFilter = api;
})();
