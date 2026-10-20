"use strict";

// Every piece of text from the server is inserted with textContent, never as HTML.
const $ = (id) => document.getElementById(id);
const DRAFT_KEY = "ka-draft";
const CONVERSATION_KEY = "ka-conversation";
const SIDEBAR_KEY = "ka-sidebar";
const POLL_MS = 2000;

const SVG_NS = "http://www.w3.org/2000/svg";
// Messages the agent writes when it could not finish checking: a run problem, not a gap in the notes.
const RUN_PROBLEMS = ["Hybrid retrieval failed.", "Document find failed.", "Document find provenance failed.",
  "Original source read failed.", "Investigation budget ended before a valid grounded answer."];

const state = { conversationId: null, polling: new Map(), running: false, capReached: false, view: "ask", loadSeq: 0 };

function store(key, value) {
  try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch (_) { /* private mode */ }
}
function load(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}

// What each answer on screen needs after it is drawn: its sources, gaps and saved feedback.
const turnData = new Map();

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

class ApiError extends Error {
  constructor(status, message, body) { super(message); this.status = status; this.body = body; }
}

async function api(method, path, body) {
  const options = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body || {});
  }
  const response = await fetch(path, options);
  let data = {};
  try { data = await response.json(); } catch (_) { /* empty body */ }
  if (response.status === 401 && !path.startsWith("/auth/verify")) {
    showSignIn("Please sign in again.");
  }
  if (!response.ok) throw new ApiError(response.status, data.error || "Something went wrong.", data);
  return data;
}

// Sign-in ---------------------------------------------------------------------------------------
function showSignIn(message) {
  const draft = $("question").value.trim();
  if (draft) store(DRAFT_KEY, draft);
  if (state.conversationId) store(CONVERSATION_KEY, state.conversationId);
  stopPolling();
  closeSheet();
  setNavOpen(false);
  setAccountOpen(false);
  window.captureScreen.reset();
  $("app").hidden = true;
  document.title = "Knowledge";
  $("signin").hidden = false;
  $("signin-message").textContent = message || "";
  $("email").focus();
}

async function sendCode() {
  const email = $("email").value.trim();
  if (!email) { $("signin-message").textContent = "Enter your email."; return; }
  try {
    await api("POST", "/auth/code", { email });
    $("email-form").hidden = true;
    $("code-form").hidden = false;
    $("signin-message").textContent = "";
    $("code").focus();
  } catch (error) {
    $("signin-message").textContent = error.status === 429 ? "Too many attempts. Wait a minute." : error.message;
  }
}

async function verifyCode() {
  const code = $("code").value.trim();
  try {
    await api("POST", "/auth/verify", { email: $("email").value.trim(), code });
    $("code").value = "";
    $("code-form").hidden = true;
    $("email-form").hidden = false;
    await startApp();
  } catch (error) {
    $("signin-message").textContent =
      error.status === 403 ? "This account is not authorised." :
      error.status === 429 ? "Too many attempts. Wait a minute." :
      error.status === 502 ? "Sign-in is unavailable right now. Try again shortly." :
      "That code expired or is wrong. Try again or send a new code.";
  }
}

// Conversations -------------------------------------------------------------------------------
async function startApp() {
  $("signin").hidden = true;
  $("app").hidden = false;
  setSidebarCollapsed(load(SIDEBAR_KEY) === "1");
  const draft = load(DRAFT_KEY);
  if (draft) { $("question").value = draft; autosize(); store(DRAFT_KEY, null); }
  await refreshConversations();
  const remembered = load(CONVERSATION_KEY);
  store(CONVERSATION_KEY, null);
  if (remembered) await openConversation(remembered); else newQuestion();
  await window.captureScreen.takeShared().catch(() => {});
}

async function refreshConversations() {
  const { conversations } = await api("GET", "/api/conversations");
  const list = $("conversation-list");
  list.replaceChildren();
  for (const item of conversations) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "conversation";
    button.textContent = item.title;
    if (item.id === state.conversationId) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => { setNavOpen(false); openConversation(item.id); });
    li.append(button);
    list.append(li);
  }
  document.querySelector(".conversation-empty").hidden = conversations.length > 0;
}

// A new question has no answers yet: the heading and the question bar sit together in the middle of the page.
function setEmpty(empty) {
  $("empty-state").hidden = !empty;
  $("view-ask").classList.toggle("is-empty", empty);
}

function stopPolling() {
  for (const timer of state.polling.values()) clearTimeout(timer);
  state.polling.clear();
}

// The question itself is the Ask tab's heading; the window title follows the conversation.
function setTitle(title) {
  document.title = title === "New question" ? "Knowledge" : `${title} - Knowledge`;
}

function newQuestion() {
  state.loadSeq += 1;
  stopPolling();
  state.conversationId = null;
  turnData.clear();
  $("turns").replaceChildren();
  setTitle("New question");
  setEmpty(true);
  setRunning(false);
  setView("ask").then(() => $("question").focus());
}

async function openConversation(id) {
  const mine = ++state.loadSeq;  // a slower, older request must not replace what the user opened since
  setView("ask");
  stopPolling();
  try {
    const conversation = await api("GET", `/api/conversations/${encodeURIComponent(id)}`);
    if (mine !== state.loadSeq) return;
    state.conversationId = conversation.id;
    setTitle(conversation.title);
    setEmpty(false);
    turnData.clear();
    $("turns").replaceChildren();
    let running = false;
    for (const turn of conversation.turns) {
      renderTurn(turn);
      if (turn.status === "running") { running = true; poll(turn.id); }
    }
    setRunning(running);
    refreshConversations();
  } catch (error) {
    if (mine !== state.loadSeq) return;
    if (error.status !== 401) newQuestion();
  }
}

// Asking --------------------------------------------------------------------------------------
async function ask(question) {
  if (!question || state.running || state.capReached) return;
  setRunning(true);
  const mine = state.loadSeq;
  try {
    const path = state.conversationId
      ? `/api/conversations/${encodeURIComponent(state.conversationId)}/turns` : "/api/conversations";
    const started = await api("POST", path, { question });
    $("question").value = "";
    autosize();
    // The user moved to another conversation while this was being sent: it shows up in History, not here.
    if (mine !== state.loadSeq) { setRunning(false); refreshConversations(); return; }
    if (state.conversationId !== started.conversation_id) {
      state.conversationId = started.conversation_id;
      setTitle(question);
    }
    setEmpty(false);
    renderTurn({ id: started.turn_id, question, status: "running", created_at: new Date().toISOString() });
    poll(started.turn_id);
    refreshConversations();
  } catch (error) {
    setRunning(false);
    if (error.status === 429) showCap(error.body);
    else if (error.status === 409) notify("An answer is still running in this conversation.");
    else if (error.status !== 401) notify(error.message);
  }
}

function poll(turnId) {
  clearTimeout(state.polling.get(turnId));
  const tick = async () => {
    try {
      const turn = await api("GET", `/api/turns/${encodeURIComponent(turnId)}`);
      // The user may have switched conversations while this answer was running.
      if (turn.conversation_id !== state.conversationId) { state.polling.delete(turnId); return; }
      renderTurn(turn);
      if (turn.status === "running") { state.polling.set(turnId, setTimeout(tick, POLL_MS)); return; }
      state.polling.delete(turnId);
      setRunning(false);
      announce(turn.status === "failed" ? "The answer failed." : "The answer is ready.");
    } catch (error) {
      if (error.status !== 401) state.polling.set(turnId, setTimeout(tick, POLL_MS * 2));
    }
  };
  state.polling.set(turnId, setTimeout(tick, POLL_MS));
}

function setRunning(running) {
  state.running = running;
  $("ask").disabled = running || state.capReached;
  $("question").disabled = state.capReached;
}

function showCap(body) {
  state.capReached = true;
  setRunning(false);
  const cap = body && body.cap_usd ? `$${body.cap_usd}` : "the daily limit";
  notify(`Daily spending cap of ${cap} reached. It resets at ${body && body.resets ? body.resets : "midnight UTC"}.`, true);
}

// A quiet message for screen readers only.
function announce(message) {
  $("status").textContent = message;
}

// A visible notice that shows on both tabs. The spending-cap one stays; the others fade after a while.
let noticeTimer = null;
function notify(message, sticky) {
  const banner = $("banner");
  // The spending-cap notice stays; later passing notices must not replace it.
  if (state.capReached && !sticky) return;
  banner.textContent = message;
  banner.hidden = false;
  clearTimeout(noticeTimer);
  if (!sticky) noticeTimer = setTimeout(() => { if (!state.capReached) banner.hidden = true; }, 8000);
}

// Rendering -----------------------------------------------------------------------------------
function turnElement(turn) {
  let element = document.querySelector(`.turn[data-id="${CSS.escape(turn.id)}"]`);
  if (!element) {
    element = $("turn-template").content.firstElementChild.cloneNode(true);
    element.dataset.id = turn.id;
    element.querySelector(".question").textContent = turn.question;
    element.querySelector(".retry").addEventListener("click", () => ask(turn.question));
    element.querySelector(".gap-chip").addEventListener("click", (event) => openGaps(turn.id, event.currentTarget));
    element.querySelector(".thumbs").addEventListener("click", (event) => openFeedback(element, turn.id, event.currentTarget));
    $("turns").append(element);
  }
  return element;
}

// Split the receipt's gap strings: real gaps lose their internal code prefix; run problems are set apart.
function splitGaps(raw) {
  const gaps = [];
  const problems = [];
  for (const entry of raw || []) {
    const text = String(entry).trim();
    if (RUN_PROBLEMS.includes(text) || text.startsWith("Model stopped:")) { problems.push(text); continue; }
    const sentence = text.replace(/^(?:Unresolved need\s+)?N\d+\s*:\s*/, "").trim();
    if (sentence) gaps.push(sentence);
  }
  return { gaps, problems };
}

function renderTurn(turn) {
  const element = turnElement(turn);
  const stateLine = element.querySelector(".turn-state");
  const answer = element.querySelector(".answer");
  const row = element.querySelector(".answer-row");
  element.dataset.status = turn.status;
  element.querySelector(".retry").hidden = turn.status !== "failed";
  stateLine.classList.remove("sr-only");
  if (turn.status === "running") {
    const seconds = Math.max(0, Math.round((Date.now() - Date.parse(turn.created_at)) / 1000));
    stateLine.textContent = `Answering… ${seconds}s`;
    row.hidden = true;
    return;
  }
  if (turn.status === "failed") {
    stateLine.textContent = turn.error || "The answer failed.";
    answer.replaceChildren();
    row.hidden = true;
    return;
  }
  // The gap icon says it visually; the words stay for screen readers.
  stateLine.textContent = turn.status === "done_with_gaps" ? "Answered with gaps" : "";
  stateLine.classList.toggle("sr-only", turn.status === "done_with_gaps");
  const receipt = turn.receipt || {};
  const sources = renderAnswer(element, receipt, turn.id);
  const { gaps, problems } = splitGaps(receipt.gaps);
  turnData.set(turn.id, { sources, gaps, feedback: turn.feedback || null });
  const note = element.querySelector(".run-note");
  note.textContent = problems.join(" ");
  note.hidden = problems.length === 0;
  renderRow(element, turn.id);
}

function renderRow(element, turnId) {
  const data = turnData.get(turnId);
  const numbers = element.querySelector(".source-numbers");
  numbers.replaceChildren();
  for (const [index, source] of data.sources.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "source-number";
    button.textContent = String(source.number);
    button.setAttribute("aria-label", `Source ${source.number}`);
    button.setAttribute("aria-haspopup", "dialog");
    button.addEventListener("click", (event) => openSource(turnId, index, event.currentTarget));
    numbers.append(button);
  }
  element.querySelector(".row-label").hidden = data.sources.length === 0;
  const chip = element.querySelector(".gap-chip");
  chip.hidden = data.gaps.length === 0;
  element.querySelector(".gap-count").textContent = String(data.gaps.length);
  chip.setAttribute("aria-label", `${data.gaps.length} ${data.gaps.length === 1 ? "gap" : "gaps"} in this answer`);
  element.querySelector(".thumbs").setAttribute("aria-pressed", data.feedback ? "true" : "false");
  // The row always shows for a finished answer, so one with no sources still has its feedback button.
  element.querySelector(".answer-row").hidden = false;
}

// Draws the answer and returns its sources in numbering order: passages from one file share one number.
function renderAnswer(element, receipt, turnId) {
  const container = element.querySelector(".answer");
  container.replaceChildren();
  const citations = receipt.citations || {};
  const found = new Map();
  const paragraphs = String(receipt.answer || "").split(/\n{2,}/);
  for (const text of paragraphs) {
    const p = document.createElement("p");
    for (const part of text.split(/(\[E\d+\.\d+(?:@\d+)?\])/)) {
      const match = /^\[(E\d+\.\d+(?:@\d+)?)\]$/.exec(part);
      if (match && citations[match[1]]) {
        const citation = citations[match[1]];
        const key = citation.relative_path || match[1];
        let source = found.get(key);
        if (!source) {
          const path = String(citation.relative_path || "");
          source = {
            number: found.size + 1, title: citation.title || path || match[1],
            folder: path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "",
            url: String(citation.obsidian_url || ""), passages: [],
          };
          found.set(key, source);
        }
        if (citation.text && !source.passages.includes(citation.text)) source.passages.push(citation.text);
        const marker = document.createElement("button");
        marker.type = "button";
        marker.className = "marker";
        marker.textContent = String(source.number);
        marker.setAttribute("aria-label", `Source ${source.number}`);
        marker.setAttribute("aria-haspopup", "dialog");
        const index = source.number - 1;
        marker.addEventListener("click", (event) => openSource(turnId, index, event.currentTarget));
        p.append(marker);
      } else {
        p.append(document.createTextNode(part));
      }
    }
    container.append(p);
  }
  return [...found.values()];
}

// Sheet content --------------------------------------------------------------------------------
function textElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function fillSource(section, data, index) {
  const source = data.sources[index];
  const intro = document.createElement("div");
  intro.className = "source-titles";
  intro.append(textElement("span", "kicker", `Source ${source.number}`), textElement("h3", "sheet-heading", source.title));
  // The folder path, then a plain underlined link to the note in Obsidian beside it.
  const where = document.createElement("p");
  where.className = "sheet-sub";
  if (source.folder) where.append(textElement("span", "", source.folder));
  const url = source.url;
  if (url.startsWith("obsidian://")) {
    if (source.folder) where.append(textElement("span", "dot", "·"));
    const link = textElement("a", "open-obsidian", "See in Obsidian");
    link.href = url;
    where.append(link);
  }
  if (where.childNodes.length) intro.append(where);
  const parts = [intro];
  for (const passage of source.passages) parts.push(textElement("blockquote", "passage", passage));
  if (data.sources.length > 1) {
    const numbers = document.createElement("div");
    numbers.className = "sheet-numbers";
    for (const [other, item] of data.sources.entries()) {
      const button = textElement("button", "sheet-number", String(item.number));
      button.type = "button";
      button.setAttribute("aria-label", `Source ${item.number}`);
      if (other === index) button.setAttribute("aria-current", "true");
      button.addEventListener("click", () => openSheet("source", null, (target) => fillSource(target, data, other)));
      numbers.append(button);
    }
    parts.push(numbers);
  }
  section.replaceChildren(...parts);
}

function openSource(turnId, index, opener) {
  const data = turnData.get(turnId);
  if (!data || !data.sources[index]) return;
  openSheet("source", opener, (section) => fillSource(section, data, index));
}

function fillGaps(section, data) {
  const count = data.gaps.length;
  const head = document.createElement("div");
  head.className = "gap-head";
  head.append(textElement("span", "kicker kicker-gap", `${count} ${count === 1 ? "gap" : "gaps"} in this answer`),
    textElement("h3", "sheet-heading", "What’s missing"));
  const list = document.createElement("div");
  list.className = "gap-list";
  for (const gap of data.gaps) {
    const item = document.createElement("div");
    item.className = "gap-item";
    const add = textElement("button", "add-gap", "+ Add a note to fill this gap");
    add.type = "button";
    add.addEventListener("click", () => closeSheet(() => window.captureScreen.openWith(gap)));
    item.append(textElement("p", "gap-text", gap), add);
    list.append(item);
  }
  section.replaceChildren(head, list);
}

function openGaps(turnId, opener) {
  const data = turnData.get(turnId);
  if (!data || !data.gaps.length) return;
  openSheet("gaps", opener, (section) => fillGaps(section, data));
}

// Thumbs-down: a note box and Send. A bottom sheet on phones; a panel under the answer on desktop.
function buildFeedback(turnId, inline, done) {
  const data = turnData.get(turnId);
  const saved = data.feedback;
  const form = document.createElement("form");
  form.className = inline ? "feedback-panel" : "feedback-sheet";
  const noteId = `feedback-note-${inline ? "inline-" : "sheet-"}${turnId}`;
  const label = document.createElement("label");
  label.htmlFor = noteId;
  if (inline) {
    label.className = "feedback-label";
    label.append("What was wrong? ", textElement("span", "muted", "(optional)"));
  } else {
    label.className = "feedback-note-label";
    label.textContent = "Note (optional)";
  }
  const textarea = document.createElement("textarea");
  textarea.id = noteId;
  textarea.rows = inline ? 2 : 4;
  textarea.maxLength = 2000;
  textarea.placeholder = "e.g. It should have used my notes on pricing";
  textarea.value = saved && saved.note ? saved.note : "";
  const message = textElement("p", "message small", "");
  message.setAttribute("role", "alert");
  const send = textElement("button", "primary send-feedback", "Send feedback");
  send.type = "submit";
  const actions = document.createElement("div");
  actions.className = "feedback-actions";
  if (inline) {
    const cancel = textElement("button", "plain", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => done());
    actions.append(cancel);
  }
  if (saved) {
    const remove = textElement("button", "link", "Remove feedback");
    remove.type = "button";
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      send.disabled = true;
      try {
        await api("DELETE", `/api/turns/${encodeURIComponent(turnId)}/feedback`);
        data.feedback = null;
        refreshThumbs(turnId);
        announce("Feedback removed.");
        done();
      } catch (error) {
        if (error.status !== 401) message.textContent = error.message;
        remove.disabled = false;
        send.disabled = false;
      }
    });
    actions.append(remove);
  }
  actions.append(send);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    send.disabled = true;
    message.textContent = "";
    try {
      await api("POST", `/api/turns/${encodeURIComponent(turnId)}/feedback`, { note: textarea.value });
      data.feedback = { note: textarea.value };
      refreshThumbs(turnId);
      announce("Feedback saved.");
      done();
    } catch (error) {
      if (error.status !== 401) message.textContent = error.message;
    } finally { send.disabled = false; }
  });
  if (inline) {
    form.append(label, textarea, message, actions);
  } else {
    const heading = document.createElement("div");
    heading.className = "feedback-heading";
    heading.append(textElement("h3", "sheet-heading", "What was wrong?"),
      textElement("p", "sheet-note", "This answer is saved as a test question, so future answers can be checked against it."));
    const field = document.createElement("div");
    field.className = "feedback-field";
    field.append(label, textarea);
    form.append(heading, field, message, actions);
  }
  return form;
}

function refreshThumbs(turnId) {
  const element = document.querySelector(`.turn[data-id="${CSS.escape(turnId)}"]`);
  if (element) element.querySelector(".thumbs").setAttribute("aria-pressed", turnData.get(turnId).feedback ? "true" : "false");
}

function openFeedback(element, turnId, opener) {
  if (!turnData.get(turnId)) return;
  if (!phone.matches) {
    const panel = element.querySelector(".feedback-inline");
    if (!panel.hidden) { panel.hidden = true; panel.replaceChildren(); return; }
    const close = () => { panel.hidden = true; panel.replaceChildren(); opener.focus(); };
    panel.replaceChildren(buildFeedback(turnId, true, close));
    panel.hidden = false;
    panel.querySelector("textarea").focus();
    return;
  }
  // The note may be saved after the sheet was dismissed; only close the sheet this form lives in.
  const serial = sheet.serial + 1;
  openSheet("feedback", opener, (section) => {
    section.replaceChildren(buildFeedback(turnId, false, () => {
      if (sheet.open && sheet.name === "feedback" && sheet.serial === serial) closeSheet();
    }));
  });
}

// Views and sheets ----------------------------------------------------------------------------
// Two tabs share one page: each owns a container shown with `hidden`, so switching keeps what was typed.
function applyView(view) {
  const changed = state.view !== view;
  state.view = view;
  $("app").dataset.view = view;
  $("view-ask").hidden = view !== "ask";
  $("capture").hidden = view !== "add";
  for (const tab of document.querySelectorAll("[data-tab]")) {
    if (tab.dataset.tab === view) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
  }
  return view === "add" && changed ? window.captureScreen.show() : undefined;
}

// A tab switch first lets any open sheet finish closing, so no stale history entry is left behind.
function setView(view) {
  return new Promise((resolve) => closeSheet(() => resolve(applyView(view))));
}

const SHEET_TITLES = { source: "Source", gaps: "What’s missing", feedback: "What was wrong?" };
const sheet = { open: false, closing: false, opener: null, name: null, serial: 0, afterClose: [] };
// Source and Gaps swap in place while the desktop panel stays open; the other sheets never do.
const SWAPPABLE = new Set(["source", "gaps"]);

// `fill` builds the sheet's content (Source, Gaps, Feedback).
// Source and Gaps can swap in place while open (the desktop panel stays beside the page): same history entry.
function openSheet(name, opener, fill) {
  const swapping = sheet.open && !sheet.closing && SWAPPABLE.has(name) && SWAPPABLE.has(sheet.name);
  if ((sheet.open && !swapping) || sheet.closing) return;
  const section = $("sheet").querySelector(`[data-sheet="${name}"]`);
  if (fill) fill(section);
  const root = $("sheet");
  root.setAttribute("aria-label", SHEET_TITLES[name]);
  root.dataset.kind = name;
  $("app").dataset.sheet = name;
  for (const other of root.querySelectorAll("[data-sheet]")) other.hidden = other.dataset.sheet !== name;
  sheet.name = name;
  syncModal();
  if (swapping) {
    // Keep keyboard focus on the number just chosen, or on the sheet itself.
    (root.querySelector('.sheet-number[aria-current="true"]') || root).focus();
    return;
  }
  sheet.open = true;
  sheet.serial += 1;
  sheet.opener = opener || document.activeElement;
  history.pushState({ ka: "sheet", name }, "");
  root.hidden = false;
  $("sheet-scrim").hidden = false;
  root.focus();
}

// On desktop the Source and Gaps panel sits beside the page: keyboard users can leave it with Tab.
function syncModal() {
  const beside = SWAPPABLE.has(sheet.name) && !phone.matches;
  $("sheet").setAttribute("aria-modal", beside ? "false" : "true");
}

function teardownSheet() {
  sheet.open = false;
  sheet.closing = false;
  $("sheet").hidden = true;
  $("sheet-scrim").hidden = true;
  delete $("app").dataset.sheet;
  for (const section of $("sheet").querySelectorAll("[data-sheet]")) section.replaceChildren();
  sheet.name = null;
  const opener = sheet.opener;
  sheet.opener = null;
  const after = sheet.afterClose.splice(0);
  for (const run of after) {
    try { run(); } catch (_) { /* one failing follow-up must not skip the others or the focus return */ }
  }
  // Keep keyboard focus somewhere real: back on the opener unless a follow-up action already moved it.
  if (!document.activeElement || document.activeElement === document.body) {
    // The opener may have been hidden meanwhile (the window crossed the phone/desktop width).
    const visible = [opener, $("sidebar-history")].find((el) => el && el.isConnected && el.getClientRects().length);
    if (visible) visible.focus();
  }
}

// popstate is the only closer: a programmatic close just goes back, and the handler tears down.
function closeSheet(after) {
  if (!sheet.open) { if (after) after(); return; }
  if (after) sheet.afterClose.push(after);
  // One Back per sheet: a repeat trigger before popstate arrives must not pop a second history entry.
  if (sheet.closing) return;
  sheet.closing = true;
  history.back();
}

function trapFocus(event) {
  if (!sheet.open || event.key !== "Tab" || $("sheet").getAttribute("aria-modal") === "false") return;
  const items = [...$("sheet").querySelectorAll("button, a[href], input, textarea")].filter((el) => !el.closest("[hidden]"));
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  else if (!$("sheet").contains(document.activeElement)) { event.preventDefault(); first.focus(); }
}

// Navigation -----------------------------------------------------------------------------------
// Desktop: a sidebar beside the page. Phone: the same sidebar is a drawer opened by the icon at the top left.
// History opens in place under its item; the account circle at the foot opens the sign-out options.
const phone = window.matchMedia("(max-width: 899px)");
const navState = { open: false, historyOpen: true };

function syncHistory() {
  const collapsed = !phone.matches && document.body.classList.contains("sidebar-collapsed");
  document.body.classList.toggle("history-closed", !navState.historyOpen);
  $("sidebar-history").setAttribute("aria-expanded", !collapsed && navState.historyOpen ? "true" : "false");
}

function setNavOpen(open) {
  // Closing the drawer while focus is inside it: put focus back on the menu button.
  const hadFocus = !open && phone.matches && $("sidebar").contains(document.activeElement);
  navState.open = open;
  // While the drawer is open on a phone, the page behind it cannot be reached or tabbed to.
  document.querySelector(".main").inert = open && phone.matches;
  document.body.classList.toggle("nav-open", open);
  $("nav-scrim").hidden = !open;
  $("nav-button").setAttribute("aria-expanded", open ? "true" : "false");
  if (!open) setAccountOpen(false);
  syncHistory();
  if (hadFocus) $("nav-button").focus();
}

function toggleHistory() {
  if (!phone.matches && document.body.classList.contains("sidebar-collapsed")) {
    setSidebarCollapsed(false);
    store(SIDEBAR_KEY, null);
    navState.historyOpen = true;
  } else {
    navState.historyOpen = !navState.historyOpen;
  }
  syncHistory();
}

function setAccountOpen(open) {
  $("account-menu").hidden = !open;
  $("account-button").setAttribute("aria-expanded", open ? "true" : "false");
}

function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  $("collapse").setAttribute("aria-expanded", collapsed ? "false" : "true");
  syncHistory();
}

// Only say "signed out" once the server has said so; a failed request must not look like success.
async function signOut(everywhere) {
  const notices = document.querySelectorAll(".sign-out-message");
  for (const notice of notices) notice.textContent = "";
  try {
    await api("POST", everywhere ? "/auth/sign-out-everywhere" : "/auth/sign-out");
  } catch (error) {
    if (error.status === 401) return;  // the session had already ended; api() showed the sign-in screen
    for (const notice of notices) notice.textContent = "Could not sign out. Check your connection and try again.";
    return;
  }
  // A deliberate sign-out leaves no unsent draft behind for the next person on this browser.
  $("question").value = "";
  store(DRAFT_KEY, null);
  showSignIn(everywhere ? "Signed out on every device." : "");
}

function autosize() {
  const box = $("question");
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, 200)}px`;
}

document.addEventListener("DOMContentLoaded", async () => {
  if (history.state && history.state.ka === "sheet") history.replaceState(null, "", location.href);
  $("email-form").addEventListener("submit", (event) => { event.preventDefault(); sendCode(); });
  $("code-form").addEventListener("submit", (event) => { event.preventDefault(); verifyCode(); });
  $("resend").addEventListener("click", sendCode);
  $("composer").addEventListener("submit", (event) => { event.preventDefault(); ask($("question").value.trim()); });
  $("question").addEventListener("input", autosize);
  $("question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && window.matchMedia("(pointer: fine)").matches) {
      event.preventDefault();
      ask($("question").value.trim());
    }
  });
  $("new-question").addEventListener("click", () => { setNavOpen(false); newQuestion(); });
  for (const tab of document.querySelectorAll("[data-tab]")) {
    tab.addEventListener("click", () => { setNavOpen(false); setView(tab.dataset.tab); });
  }
  $("sidebar-history").addEventListener("click", toggleHistory);
  $("nav-button").addEventListener("click", () => { setNavOpen(!navState.open); if (navState.open) $("sidebar-history").focus(); });
  $("nav-close").addEventListener("click", () => setNavOpen(false));
  $("account-button").addEventListener("click", () => setAccountOpen($("account-menu").hidden));
  // A click anywhere else folds the account menu away. The desktop panel has no dimmed backdrop: a click on the
  // page behind it closes it, but not a click on the panel or on the controls that open or swap it. Where the click
  // happened is read from the event path, because a control may already have been replaced by the time this runs.
  const SHEET_CONTROLS = ["marker", "source-number", "gap-chip", "thumbs"];
  let pressedInSheet = false;
  document.addEventListener("pointerdown", (event) => {
    pressedInSheet = event.composedPath().some((node) => node.id === "sheet");
  }, true);
  document.addEventListener("click", (event) => {
    const path = event.composedPath();
    if (!$("account-menu").hidden && !path.some((node) => node.classList && node.classList.contains("sidebar-foot"))) setAccountOpen(false);
    const onSheet = path.some((node) => node.id === "sheet" || (node.classList && SHEET_CONTROLS.some((name) => node.classList.contains(name))));
    // A text selection that starts inside the panel and ends outside it is not a click outside.
    if (sheet.open && !sheet.closing && !onSheet && !pressedInSheet) closeSheet();
    pressedInSheet = false;
  });
  $("nav-scrim").addEventListener("click", () => setNavOpen(false));
  phone.addEventListener("change", () => { setNavOpen(false); syncModal(); });
  $("collapse").addEventListener("click", () => {
    const collapsed = !document.body.classList.contains("sidebar-collapsed");
    setSidebarCollapsed(collapsed);
    store(SIDEBAR_KEY, collapsed ? "1" : null);
  });
  $("sheet-close").addEventListener("click", () => closeSheet());
  $("sheet-scrim").addEventListener("click", () => closeSheet());
  window.addEventListener("popstate", () => { if (sheet.open) teardownSheet(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeSheet();
      if (!$("account-menu").hidden) { setAccountOpen(false); $("account-button").focus(); }
      else if (navState.open) setNavOpen(false);
    }
    trapFocus(event);
  });
  for (const button of document.querySelectorAll(".sign-out")) button.addEventListener("click", () => signOut(false));
  for (const button of document.querySelectorAll(".sign-out-everywhere")) button.addEventListener("click", () => signOut(true));
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try {
    await api("GET", "/auth/me");
    await startApp();
  } catch (_) {
    showSignIn("");
  }
});
