"use strict";

// Sources: browse every indexed source, narrow the list in the search bar, and read one on its own page.
// The list loads once and is filtered here (see source-filter.js). The reader is a state of this view, not a sheet.
// Every piece of text from the server is inserted with textContent, never as HTML.
(() => {
  const F = window.sourceFilter;
  const FIRST_PAGE = 60;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const KICKERS = { book: "Book", highlights: "Highlights", post: "Post", note: "Note", transcript: "Transcript",
    article: "Article", quote: "Quote" };
  const MARKS = { linkedin: "linkedin", x: "xlogo", youtube: "youtube", podcast: "mic", website: "globe" };
  const MENUS = [["types", "Type"], ["tags", "Tags"], ["people", "People"], ["dates", "Date added"]];
  const ASK_HINTS = { transcript: "Ask about this episode…", article: "Ask about this article…",
    highlights: "Ask about these highlights…", note: "Ask about this note…", post: "Ask about this post…",
    quote: "Ask about this quote…", book: "Ask about this book…" };
  const COVERS = 8;
  const TRANSITION_GRACE_MS = 250;

  const view = { sources: [], vaultName: "", loaded: false, loadSeq: 0, filter: F.empty(), showAll: false, groups: [],
    active: 0, menu: null, menuTrigger: null, expanded: { tags: false, people: false }, dateMode: null };
  const reader = { open: false, pushed: false, closing: false, gen: 0, source: null, opener: null, scroll: 0, seq: 0 };

  const h = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
  const now = () => new Date();

  function formatDate(iso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    return match ? `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}` : "";
  }

  // The cover colour comes from the title, so a book's cover is the same on every visit.
  function coverClass(source) {
    if (source.type === "book") return "cover-0";
    let hash = 0;
    for (const char of source.title) hash = (hash * 31 + char.charCodeAt(0)) % 9973;
    return `cover-${hash % COVERS}`;
  }

  const kicker = (source) => {
    if (source.type === "book") return source.book_year ? `Book · ${source.book_year}` : "Book";
    return KICKERS[source.type] || "Note";
  };

  const isPodcast = (source) => source.platform === "podcast";

  // AUTHOR × GUEST for a podcast; the author alone when there is no guest. Empty when nobody is named.
  function voices(source) {
    const authors = source.authors || [];
    const guests = source.guests || [];
    if (!authors.length) return guests.join(", ");
    return guests.length ? `${authors.join(", ")} × ${guests.join(", ")}` : authors.join(", ");
  }

  const byline = (source) => (source.authors || []).join(", ");

  // The top line of a post, note or transcript: the type, then (only if a source is known) a dot, its mark and name.
  function topLine(source, small) {
    const line = h("div", "s-topline");
    const left = h("span", "s-topleft");
    left.append(h("span", "s-type", kicker(source)));
    if (source.platform && source.source_name) {
      left.append(h("span", "dot"));
      const mark = icon(MARKS[source.platform] || "globe");
      mark.classList.add("s-mark");
      left.append(mark, h("span", "s-source", source.source_name));
    }
    line.append(left);
    if (!small && source.date) line.append(h("span", "s-date", formatDate(source.date)));
    return line;
  }

  function pills(source, onlyActive) {
    const active = view.filter.tags;
    const tags = onlyActive ? (source.tags || []).filter((tag) => active.includes(tag)) : source.tags || [];
    if (!tags.length) return null;
    const list = h("ul", "s-tags");
    for (const tag of tags) {
      const item = h("li", active.includes(tag) ? "s-pill is-filter" : "s-pill", tag);
      list.append(item);
    }
    return list;
  }

  // Desktop cards -------------------------------------------------------------------------------
  function coverCard(source) {
    const card = h("div", `s-card s-cover ${coverClass(source)}`);
    const row = h("div", "s-kicker-row");
    row.append(h("span", "s-kicker", kicker(source)));
    if (source.opens_in_obsidian) {
      const out = icon("upright");
      out.classList.add("s-out");
      row.append(out);
    }
    card.append(row, h("h3", "s-title s-title-cover", source.title));
    if (byline(source)) card.append(h("p", "s-byline", byline(source)));
    return card;
  }

  function quoteCard(source) {
    const card = h("div", "s-card s-quote");
    card.append(h("span", "s-kicker", "Quote"));
    const text = h("p", "s-quote-text");
    text.append(h("span", "s-mark-text", source.quote_text || source.title));
    card.append(text);
    if (byline(source)) card.append(h("p", "s-attribution", byline(source)));
    card.append(h("h3", "sr-only s-title", source.title));
    return card;
  }

  function articleCard(source) {
    const card = h("div", "s-card s-article");
    card.append(h("span", "s-rule"));
    card.append(h("span", "s-kicker", source.source_name ? `Article · ${source.source_name}` : "Article"));
    card.append(h("h3", "s-title", source.title));
    if (byline(source)) card.append(h("p", "s-by", `by ${byline(source)}`));
    const matched = pills(source, true);
    if (matched) card.append(matched);
    return card;
  }

  function textCard(source) {
    const card = h("div", `s-card s-${source.type}`);
    card.append(topLine(source), h("h3", "s-title", source.title));
    if (isPodcast(source) && voices(source)) card.append(h("p", "s-voices", voices(source)));
    else if (byline(source)) card.append(h("p", "s-author", byline(source)));
    const tags = pills(source, false);
    if (tags) card.append(tags);
    return card;
  }

  // Phone rows ----------------------------------------------------------------------------------
  function thumbnail(source) {
    const thumb = h("span", `s-thumb s-thumb-${source.type === "highlights" ? "book" : source.type}`);
    if (source.type === "book" || source.type === "highlights") thumb.classList.add(coverClass(source));
    const parts = { post: ["t-avatar", "t-line", "t-light", "t-light", "t-light"], quote: ["t-open", "t-bar", "t-bar", "t-bar"],
      transcript: ["t-talk", "t-talk", "t-talk"], note: ["t-rule", "t-rule", "t-rule", "t-rule"],
      article: ["t-head", "t-head", "t-green", "t-bar", "t-bar"] }[source.type] || [];
    for (const part of parts) thumb.append(h("span", part));
    thumb.setAttribute("aria-hidden", "true");
    return thumb;
  }

  function phoneRow(source) {
    const row = h("div", "s-row");
    const text = h("span", "s-row-text");
    const line = h("span", "s-topline s-topline-row");
    line.append(h("span", "s-type", kicker(source)));
    if (source.platform && source.source_name) {
      line.append(h("span", "dot"));
      const mark = icon(MARKS[source.platform] || "globe");
      mark.classList.add("s-mark");
      line.append(mark, h("span", "s-source", source.source_name));
    }
    text.append(line, h("span", "s-title", source.title));
    if (isPodcast(source) && voices(source)) text.append(h("span", "s-voices", voices(source)));
    else if (byline(source)) text.append(h("span", "s-sub", byline(source)));
    row.append(thumbnail(source), text);
    return row;
  }

  function buildCard(source) {
    const item = h("li", "s-item");
    const body = phone.matches ? phoneRow(source) : ({ book: coverCard, highlights: coverCard, quote: quoteCard,
      article: articleCard }[source.type] || textCard)(source);
    let control;
    if (source.opens_in_obsidian) {
      control = h("a", "s-link");
      control.href = String(source.obsidian_url || "").startsWith("obsidian://") ? source.obsidian_url : "#";
    } else {
      control = h("button", "s-link");
      control.type = "button";
      control.addEventListener("click", () => openReader(source, control));
    }
    control.dataset.id = source.id;
    control.setAttribute("aria-label", source.opens_in_obsidian ? `${source.title}, opens in Obsidian` : source.title);
    control.append(body);
    item.append(control);
    return item;
  }

  // The bar, tokens and menus ---------------------------------------------------------------------
  const hasFilter = () => F.tokens(view.filter).length > 0;
  const results = () => F.filtered(view.sources, view.filter, now());

  function setFilter(next) {
    view.filter = next;
    view.showAll = false;
    render();
  }

  function renderTokens() {
    const tokens = F.tokens(view.filter);
    const list = $("sources-tokens");
    const chips = $("sources-chips");
    list.replaceChildren();
    chips.replaceChildren();
    for (const token of tokens) {
      const label = `${token.key} ${token.text}`;
      for (const [target, cls] of [[list, "stoken"], [chips, "schip"]]) {
        const item = h("li", cls);
        const key = h("span", "stoken-key", token.key);
        item.append(key, h("span", "stoken-value", token.text));
        const remove = h("button", "stoken-remove");
        remove.type = "button";
        remove.setAttribute("aria-label", `Remove filter: ${label}`);
        remove.append(icon("close"));
        remove.addEventListener("click", () => { setFilter(F.removeToken(view.filter, token)); if (!phone.matches) $("sources-input").focus(); });
        item.append(remove);
        target.append(item);
      }
    }
    chips.hidden = !tokens.length;
    $("sources-clear").hidden = !tokens.length;
    $("sources-input").placeholder = tokens.length ? "Add another filter" : phone.matches ? "Title, person, tag or type"
      : "Search titles, people, tags or types";
    const badge = $("sources-badge");
    badge.hidden = !tokens.length;
    badge.textContent = String(tokens.length);
    $("sources-filters").setAttribute("aria-label", tokens.length ? `Filters, ${tokens.length} active` : "Filters");
  }

  function renderMenuTriggers() {
    const host = $("sources-menus");
    // Built once, so the trigger a person is on keeps keyboard focus (and no ring appears on a mouse click).
    if (host.children.length) {
      for (const trigger of host.children) trigger.setAttribute("aria-expanded", view.menu === trigger.dataset.menu ? "true" : "false");
      return;
    }
    for (const [key, label] of MENUS) {
      const trigger = h("button", "smenu-trigger");
      trigger.type = "button";
      trigger.dataset.menu = key;
      trigger.setAttribute("aria-haspopup", "true");
      trigger.setAttribute("aria-expanded", view.menu === key ? "true" : "false");
      trigger.append(h("span", "", label), icon("chevron"));
      trigger.addEventListener("click", () => toggleMenu(key, trigger));
      host.append(trigger);
    }
  }

  function toggleMenu(key, trigger) {
    closeSuggest();
    if (view.menu === key) { closeMenu(false); return; }
    view.menu = key;
    view.menuTrigger = trigger;
    renderMenuTriggers();
    renderMenuPanel();
  }

  function closeMenu(returnFocus) {
    if (!view.menu) return;
    const trigger = view.menuTrigger;
    view.menu = null;
    $("sources-menu-panel").hidden = true;
    renderMenuTriggers();
    if (returnFocus) document.querySelector(`.smenu-trigger[data-menu="${trigger && trigger.dataset.menu}"]`)?.focus();
  }

  const MENU_PICK = { types: "type", tags: "tag", people: "person" };
  const MENU_EMPTY = "Nothing to filter by";

  // The marker at the left of a row: an empty ring, filled green with a check once chosen (a dot for a single choice).
  function marker(radio) {
    const mark = h("span", radio ? "smenu-mark is-radio" : "smenu-mark");
    if (!radio) mark.append(icon("check"));
    return mark;
  }

  function menuRow(row, onPick, radio) {
    const button = h("button", "smenu-row");
    button.type = "button";
    button.setAttribute("aria-pressed", row.selected ? "true" : "false");
    if (row.selected) button.classList.add("is-selected");
    button.append(marker(radio), h("span", "smenu-name", row.label), h("span", "smenu-count", String(row.count)));
    button.addEventListener("click", onPick);
    return button;
  }

  function renderMenuPanel() {
    const panel = $("sources-menu-panel");
    const key = view.menu;
    if (!key) { panel.hidden = true; return; }
    const rows = F.counts(view.filter, view.sources, now());
    panel.replaceChildren();
    panel.dataset.menu = key;
    if (key === "dates") {
      const list = h("div", "smenu-list");
      for (const row of rows.dates) {
        const button = menuRow(row, () => {
          const position = [...list.children].indexOf(button);
          setFilter({ ...view.filter, date: row.key === "any" ? null : { preset: row.key } });
          const fresh = $("sources-menu-panel").querySelector(".smenu-list");
          if (fresh && fresh.children[position]) fresh.children[position].focus({ preventScroll: true });
        }, true);
        list.append(button);
      }
      panel.append(list);
      const range = h("div", "smenu-range");
      range.append(dateInput("From", "from"), dateInput("To", "to"));
      panel.append(range);
    } else {
      const list = h("div", "smenu-list");
      const items = rows[key];
      if (!items.length) list.append(h("p", "smenu-empty", MENU_EMPTY));
      for (const row of items) {
        const button = menuRow(row, () => {
          const token = { kind: MENU_PICK[key], value: row.value };
          const next = row.selected ? F.removeToken(view.filter, token) : F.addToken(view.filter, token);
          const scroll = list.scrollTop;
          const position = [...list.children].indexOf(button);
          setFilter(next);
          const fresh = $("sources-menu-panel").querySelector(".smenu-list");
          if (fresh) { fresh.scrollTop = scroll; if (fresh.children[position]) fresh.children[position].focus({ preventScroll: true }); }
        });
        list.append(button);
      }
      panel.append(list);
    }
    panel.hidden = false;
  }

  function dateInput(label, field) {
    const wrap = h("label", "smenu-date");
    wrap.append(h("span", "", label));
    const input = h("input");
    input.type = "date";
    input.value = (view.filter.date && view.filter.date[field]) || "";
    input.addEventListener("change", () => {
      const range = { from: field === "from" ? input.value : view.filter.date && view.filter.date.from,
        to: field === "to" ? input.value : view.filter.date && view.filter.date.to };
      const date = range.from || range.to ? { from: range.from || undefined, to: range.to || undefined } : null;
      view.filter = { ...view.filter, date };
      view.showAll = false;
      render();
    });
    wrap.append(input);
    return wrap;
  }

  // Suggestions -----------------------------------------------------------------------------------
  const flatItems = () => view.groups.flatMap((group) => group.items);

  function closeSuggest() {
    view.groups = [];
    view.active = 0;
    $("sources-suggest").hidden = true;
    $("sources-input").setAttribute("aria-expanded", "false");
    $("sources-input").removeAttribute("aria-activedescendant");
  }

  function renderSuggest() {
    const panel = $("sources-suggest");
    const input = $("sources-input");
    view.groups = F.suggest(input.value, view.filter, view.sources, now());
    panel.replaceChildren();
    if (!view.groups.length) { closeSuggest(); return; }
    let index = 0;
    for (const group of view.groups) {
      panel.append(h("div", "ssuggest-group", group.heading));
      for (const item of group.items) {
        const row = h("div", "ssuggest-row");
        row.id = `sg-${index}`;
        row.setAttribute("role", "option");
        const position = index;
        row.setAttribute("aria-selected", position === view.active ? "true" : "false");
        const symbol = icon({ type: "stack", person: "person", tag: "tag", date: "calendar", title: "search" }[group.kind]);
        symbol.classList.add("ssuggest-icon");
        row.append(symbol, h("span", "ssuggest-label", item.label), h("span", "ssuggest-count", String(item.count)));
        row.addEventListener("mousedown", (event) => event.preventDefault());
        row.addEventListener("click", () => chooseSuggestion(item));
        panel.append(row);
        index += 1;
      }
    }
    panel.append(h("div", "ssuggest-foot", "↑ ↓ to move · Enter to add · Backspace removes the last filter"));
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
    markActive();
  }

  function markActive() {
    const input = $("sources-input");
    const rows = $("sources-suggest").querySelectorAll(".ssuggest-row");
    rows.forEach((row, index) => {
      row.setAttribute("aria-selected", index === view.active ? "true" : "false");
      row.classList.toggle("is-active", index === view.active);
    });
    if (rows[view.active]) {
      input.setAttribute("aria-activedescendant", rows[view.active].id);
      rows[view.active].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function chooseSuggestion(item) {
    $("sources-input").value = "";
    closeSuggest();
    setFilter(F.addToken(view.filter, item));
    $("sources-input").focus();
  }

  function onKey(event) {
    const input = $("sources-input");
    if (event.isComposing || (event.repeat && event.key === "Backspace")) return;
    const items = flatItems();
    const action = F.keyAction(event.key, { empty: input.value === "", count: items.length, index: view.active });
    if (action.type === "none") return;
    if (action.type === "move") { event.preventDefault(); view.active = action.index; markActive(); }
    else if (action.type === "add") { event.preventDefault(); chooseSuggestion(items[action.index]); }
    else if (action.type === "remove-last") {
      event.preventDefault();
      setFilter(F.removeLast(view.filter));
    } else if (action.type === "close" && (view.groups.length || view.menu)) {
      event.preventDefault();
      closeSuggest();
    }
  }

  // The list ---------------------------------------------------------------------------------------
  function renderList() {
    const all = results();
    const filtering = hasFilter();
    const shown = filtering || view.showAll ? all : all.slice(0, FIRST_PAGE);
    $("sources-list").replaceChildren(...shown.map(buildCard));
    $("sources-list").classList.toggle("is-rows", phone.matches);
    const more = $("sources-more");
    more.hidden = shown.length >= all.length;
    more.textContent = `Show all ${all.length} sources`;
    const empty = $("sources-empty");
    empty.hidden = all.length > 0 || !view.loaded;
    if (!empty.hidden) {
      const none = view.sources.length === 0;
      empty.querySelector(".sources-empty-title").textContent = none ? "Nothing is indexed yet." : "No sources match these filters.";
      empty.querySelector(".sources-empty-sub").textContent = none ? "Add notes and they appear here."
        : "Remove a filter, or try a shorter search.";
    }
    $("sources-result").textContent = !view.loaded ? "" : filtering ? plural(all.length, "source", "sources") : "Newest first";
    return all.length;
  }

  function render(keepMenu) {
    $("sources-count").textContent = view.loaded ? `${view.sources.length} sources indexed` : "";
    renderTokens();
    renderMenuTriggers();
    if (view.menu && keepMenu !== true) renderMenuPanel();
    else if (view.menu) $("sources-menu-panel").hidden = false;
    renderList();
    if (sheet.open && sheet.name === "filters") fillFilters($("sheet").querySelector('[data-sheet="filters"]'));
  }

  function notice(text, retry) {
    const box = $("sources-notice");
    box.replaceChildren();
    box.hidden = !text;
    if (!text) return;
    box.append(h("span", "", text));
    if (retry) {
      const button = h("button", "chip-button", "Retry");
      button.type = "button";
      button.addEventListener("click", retry);
      box.append(button);
    }
  }

  async function load() {
    const mine = ++view.loadSeq;
    if (!view.loaded) notice("Loading your sources…");
    try {
      const data = await api("GET", "/api/sources");
      if (mine !== view.loadSeq) return;
      view.sources = data.sources;
      view.vaultName = data.vault_name;
      view.loaded = true;
      notice("");
      render();
    } catch (error) {
      if (mine !== view.loadSeq || error.status === 401) return;
      notice("Could not load your sources. Check your connection and try again.", load);
    }
  }

  // The phone filter sheet ------------------------------------------------------------------------
  const DATE_MODES = [["any", "Any"], ["week", "Week"], ["month", "30 days"], ["custom", "Custom"]];

  function dateMode() {
    if (view.dateMode === "custom") return "custom";
    const date = view.filter.date;
    return !date ? "any" : date.preset === "week" || date.preset === "month" ? date.preset : "custom";
  }

  function sheetPills(rows, kind, limit, expandKey) {
    const wrap = h("div", "f-pills");
    const shown = view.expanded[expandKey] ? rows : rows.slice(0, limit);
    for (const row of shown) {
      const pill = h("button", "f-pill");
      pill.type = "button";
      pill.dataset.focus = `${kind}:${row.value}`;
      pill.setAttribute("aria-pressed", row.selected ? "true" : "false");
      pill.classList.toggle("is-selected", row.selected);
      pill.append(row.label, h("span", "f-count", String(row.count)));
      pill.addEventListener("click", () => {
        const token = { kind, value: row.value };
        setFilter(row.selected ? F.removeToken(view.filter, token) : F.addToken(view.filter, token));
      });
      wrap.append(pill);
    }
    return wrap;
  }

  function expander(key, total, limit, noun) {
    if (total <= limit) return null;
    const link = h("button", "f-link", view.expanded[key] ? "Show fewer" : `All ${total} ${noun}`);
    link.type = "button";
    link.dataset.focus = `expand:${key}`;
    link.addEventListener("click", () => { view.expanded[key] = !view.expanded[key]; fillFilters($("sheet").querySelector('[data-sheet="filters"]')); });
    return link;
  }

  function fillFilters(section) {
    const previous = section.querySelector(".f-scroll");
    const scroll = previous ? previous.scrollTop : 0;
    const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.focus : null;
    const rows = F.counts(view.filter, view.sources, now());
    const total = results().length;
    const body = h("div", "f-scroll");
    body.append(h("h2", "f-title", "Filters"));

    const date = h("div", "f-section");
    date.append(h("span", "kicker", "Date added"));
    const segments = h("div", "f-segments");
    segments.setAttribute("role", "group");
    segments.setAttribute("aria-label", "Date added");
    const current = dateMode();
    for (const [mode, label] of DATE_MODES) {
      const button = h("button", "f-segment", label);
      button.type = "button";
      button.dataset.focus = `date:${mode}`;
      button.setAttribute("aria-pressed", mode === current ? "true" : "false");
      button.addEventListener("click", () => {
        view.dateMode = mode === "custom" ? "custom" : null;
        view.filter = { ...view.filter, date: mode === "any" ? null : mode === "custom" ? view.filter.date && !view.filter.date.preset ? view.filter.date : null : { preset: mode } };
        view.showAll = false;
        render();
      });
      segments.append(button);
    }
    date.append(segments);
    if (current === "custom") {
      const range = h("div", "f-range");
      for (const [label, field] of [["From", "from"], ["To", "to"]]) {
        const wrap = h("label", "f-date");
        wrap.append(h("span", "", label));
        const input = h("input");
        input.type = "date";
        input.value = (view.filter.date && view.filter.date[field]) || "";
        input.addEventListener("change", () => {
          const other = field === "from" ? "to" : "from";
          const next = { [field]: input.value || undefined, [other]: (view.filter.date && view.filter.date[other]) || undefined };
          view.filter = { ...view.filter, date: next.from || next.to ? next : null };
          view.dateMode = "custom";
          render();
        });
        wrap.append(input);
        range.append(wrap);
      }
      date.append(range);
    }
    body.append(date);

    const types = h("div", "f-section");
    types.append(h("span", "kicker", "Type"), sheetPills(rows.types, "type", 99, "types"));
    body.append(types);

    const tags = h("div", "f-section");
    tags.append(h("span", "kicker", "Tags"), sheetPills(rows.tags, "tag", 12, "tags"));
    const moreTags = expander("tags", rows.tags.length, 12, "tags");
    if (moreTags) tags.append(moreTags);
    body.append(tags);

    const people = h("div", "f-section");
    people.append(h("span", "kicker", "People"));
    const list = h("div", "f-people");
    const shown = view.expanded.people ? rows.people : rows.people.slice(0, 5);
    for (const row of shown) {
      const button = h("button", "f-person");
      button.type = "button";
      button.dataset.focus = `person:${row.value}`;
      button.setAttribute("aria-pressed", row.selected ? "true" : "false");
      button.classList.toggle("is-selected", row.selected);
      button.append(marker(false), h("span", "smenu-name", row.label), h("span", "f-count", String(row.count)));
      button.addEventListener("click", () => {
        const token = { kind: "person", value: row.value };
        setFilter(row.selected ? F.removeToken(view.filter, token) : F.addToken(view.filter, token));
      });
      list.append(button);
    }
    people.append(list);
    const morePeople = expander("people", rows.people.length, 5, "people");
    if (morePeople) people.append(morePeople);
    body.append(people);

    const foot = h("div", "f-foot");
    const clear = h("button", "f-clear", "Clear all");
    clear.type = "button";
    clear.addEventListener("click", () => { view.dateMode = null; setFilter(F.empty()); });
    const show = h("button", "f-show", `Show ${plural(total, "source", "sources")}`);
    show.type = "button";
    show.addEventListener("click", () => closeSheet());
    foot.append(clear, show);
    section.replaceChildren(body, foot);
    body.scrollTop = scroll;
    if (focused) {
      const again = [...section.querySelectorAll("[data-focus]")].find((node) => node.dataset.focus === focused);
      if (again) again.focus({ preventScroll: true });
    }
  }

  function openFilters() {
    openSheet("filters", $("sources-filters"), fillFilters);
  }

  // Reader ------------------------------------------------------------------------------------------
  function reducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // The card title grows into the page title where the browser can animate it; otherwise the swap is plain.
  function transition(update, from, to) {
    if (typeof document.startViewTransition !== "function" || reducedMotion() || !from || !to) { update(); return; }
    // The update must not depend on the transition: a page that is not being painted never starts it.
    let done = false;
    const once = () => { if (!done) { done = true; update(); } };
    from.style.viewTransitionName = "source-title";
    const run = document.startViewTransition(() => {
      from.style.viewTransitionName = "";
      once();
      to.style.viewTransitionName = "source-title";
    });
    run.finished.then(() => { to.style.viewTransitionName = ""; }, () => { to.style.viewTransitionName = ""; });
    setTimeout(once, TRANSITION_GRACE_MS);
  }

  function fillHeader(source) {
    const line = $("reader-typeline");
    line.replaceChildren();
    const left = h("span", "s-topleft");
    left.append(h("span", "s-type", kicker(source)));
    if (source.platform && source.source_name) {
      left.append(h("span", "dot"));
      const mark = icon(MARKS[source.platform] || "globe");
      mark.classList.add("s-mark");
      left.append(mark, h("span", "s-source", source.source_name));
    }
    line.append(left);
    if (source.date) line.append(h("span", "s-date", formatDate(source.date)));
    $("reader-title").textContent = source.title;
    const voice = isPodcast(source) ? voices(source) : byline(source);
    $("reader-voices").textContent = voice;
    $("reader-voices").hidden = !voice;
    $("reader-voices").classList.toggle("is-plain", !isPodcast(source));
    const tags = $("reader-tags");
    tags.replaceChildren(...(source.tags || []).map((tag) => h("li", "s-pill", tag)));
    tags.hidden = !(source.tags || []).length;
    $("reader-lead").textContent = source.description || "";
    $("reader-lead").hidden = !source.description;
    $("reader-inshort-text").textContent = source.description || "";
    $("reader-inshort").hidden = !source.description;
    $("reader-rule").hidden = !source.description;
    const link = $("reader-obsidian");
    const url = String(source.obsidian_url || "");
    link.hidden = !url.startsWith("obsidian://");
    if (!link.hidden) link.href = url;
    $("reader-ask-input").placeholder = ASK_HINTS[source.type] || "Ask about this…";
    $("reader-ask-input").value = "";
    $("reader-original").hidden = true;
    $("reader-body").replaceChildren();
  }

  function drawBlocks(blocks, source) {
    const guests = source.guests || [];
    const fragment = document.createDocumentFragment();
    let list = null;
    for (const block of blocks) {
      if (block.kind !== "list_item") list = null;
      if (block.kind === "speaker_turn") {
        const turn = h("div", "r-turn");
        turn.append(h("span", guests.includes(block.speaker) ? "r-speaker is-guest" : "r-speaker", block.speaker || ""),
          h("p", "r-text", block.text));
        fragment.append(turn);
      } else if (block.kind === "highlight") {
        const item = h("div", "r-highlight");
        item.append(h("p", "r-text", block.text));
        if (block.page_reference) item.append(h("span", "r-loc", block.page_reference));
        if (block.personal_note) item.append(h("p", "r-note", block.personal_note));
        fragment.append(item);
      } else if (block.kind === "heading") {
        const depth = (block.heading_path || []).length;
        fragment.append(h(depth > 2 ? "h3" : "h2", "r-heading", block.text));
      } else if (block.kind === "list_item") {
        if (!list) { list = h("ul", "r-list"); fragment.append(list); }
        list.append(h("li", "r-text", block.text));
      } else if (block.kind === "block_quote") {
        fragment.append(h("blockquote", "r-quote", block.text));
      } else if (block.kind !== "asset_reference") {
        fragment.append(h("p", source.type === "quote" ? "r-text r-quote-big" : "r-text", block.text));
      }
    }
    return fragment;
  }

  function readerNotice(text, actions) {
    const box = $("reader-notice");
    box.replaceChildren();
    box.hidden = !text;
    if (!text) return;
    box.append(h("span", "", text));
    for (const [label, run] of actions || []) {
      const button = h("button", "chip-button", label);
      button.type = "button";
      button.addEventListener("click", run);
      box.append(button);
    }
  }

  async function loadDetail(source) {
    const mine = ++reader.seq;
    readerNotice("Loading…");
    try {
      const detail = await api("GET", `/api/sources/${encodeURIComponent(source.id)}`);
      if (mine !== reader.seq || !reader.open) return;
      readerNotice("");
      $("reader-body").replaceChildren(drawBlocks(detail.body || [], source));
      const url = String(detail.source_url || "");
      if (/^https?:\/\//.test(url) && (source.type === "post" || source.type === "article")) {
        $("reader-original-link").href = url;
        $("reader-original").hidden = false;
      }
    } catch (error) {
      if (mine !== reader.seq || !reader.open || error.status === 401) return;
      const gone = error.status === 404;
      readerNotice(gone ? "This source is no longer in the index." : "Could not load this source.",
        [["Retry", () => loadDetail(source)], ["Back to Sources", () => closeReader()]]);
      if (gone) load();
    }
  }

  function showReader(source) {
    $("sources").hidden = true;
    $("source-reader").hidden = false;
    $("app").dataset.reader = "1";
    fillHeader(source);
    $("source-reader").querySelector(".reader-scroll").scrollTop = 0;
  }

  function showList() {
    $("source-reader").hidden = true;
    $("app").removeAttribute("data-reader");
    $("sources").hidden = false;
    $("sources").scrollTop = reader.scroll;
  }

  function openReader(source, opener) {
    if (reader.open || reader.closing) return;
    reader.open = true;
    const gen = ++reader.gen;
    reader.source = source;
    reader.opener = opener;
    reader.scroll = $("sources").scrollTop;
    history.pushState({ ka: "reader" }, "");
    reader.pushed = true;
    closeMenu(false);
    closeSuggest();
    const card = opener.querySelector(".s-title");
    // The body loads once the page is drawn: a view transition runs its update a frame later and would clear it.
    transition(() => {
      if (!reader.open || reader.gen !== gen) return;  // Back came before the transition ran
      showReader(source);
      $("reader-title").focus({ preventScroll: true });
      loadDetail(source);
    }, card, $("reader-title"));
  }

  // Every way out except the Back press itself comes through here and goes back once.
  function closeReader() {
    if (!reader.open) return;
    reader.open = false;
    reader.seq += 1;
    reader.gen += 1;
    const opener = reader.opener;
    transition(() => {
      if (reader.open) return;  // reopened before the transition ran
      showList();
      if (opener && opener.isConnected) opener.focus({ preventScroll: true });
    }, $("reader-title"), opener && opener.isConnected ? opener.querySelector(".s-title") : null);
    if (reader.pushed) { reader.closing = true; history.back(); }
  }

  // popstate: the browser already went back. Idempotent, so a repeat or a late event does nothing.
  function popped() {
    if (!reader.pushed) return;
    reader.pushed = false;
    reader.closing = false;
    if (reader.open) {
      reader.open = false;
      reader.seq += 1;
      showList();
      if (reader.opener && reader.opener.isConnected) reader.opener.focus({ preventScroll: true });
    }
  }

  function askAbout(source, question) {
    closeReader();
    newQuestion();
    setScope({ id: source.id, title: source.title });
    if (question) ask(question);
  }

  // Screen ------------------------------------------------------------------------------------------
  function show() {
    load();
    return undefined;
  }

  function leave() {
    if (reader.open) {
      reader.open = false;
      reader.seq += 1;
      $("source-reader").hidden = true;
      $("app").removeAttribute("data-reader");
      reader.gen += 1;
      if (reader.pushed) { reader.closing = true; history.back(); }
    }
    closeMenu(false);
    closeSuggest();
  }

  function init() {
    const input = $("sources-input");
    input.addEventListener("input", () => { closeMenu(false); view.active = 0; renderSuggest(); });
    input.addEventListener("keydown", onKey);
    input.addEventListener("focus", () => { if (input.value) renderSuggest(); });
    input.addEventListener("blur", closeSuggest);
    $("sources-clear").addEventListener("click", () => { input.value = ""; closeSuggest(); view.dateMode = null; setFilter(F.empty()); input.focus(); });
    $("sources-more").addEventListener("click", () => { view.showAll = true; renderList(); });
    $("sources-filters").addEventListener("click", openFilters);
    $("reader-back").addEventListener("click", () => closeReader());
    $("reader-ask").addEventListener("click", () => reader.source && askAbout(reader.source));
    $("reader-askbox").addEventListener("submit", (event) => {
      event.preventDefault();
      const text = $("reader-ask-input").value.trim();
      if (text && reader.source) askAbout(reader.source, text);
    });
    document.addEventListener("click", (event) => {
      const path = event.composedPath();
      if (!path.some((node) => node.id === "sources-bar")) { closeSuggest(); closeMenu(false); }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || sheet.open) return;
      if (view.menu) { event.preventDefault(); closeMenu(true); }
      else if (reader.open && state.view === "sources") closeReader();
    });
    phone.addEventListener("change", () => {
      if (sheet.open && sheet.name === "filters") closeSheet();
      render();
    });
    render();
  }

  window.sourcesScreen = { show, leave, popped };
  document.addEventListener("DOMContentLoaded", init);
})();
