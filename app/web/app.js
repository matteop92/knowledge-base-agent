"use strict";

// Every piece of text from the server is inserted with textContent, never as HTML.
const $ = (id) => document.getElementById(id);
const DRAFT_KEY = "ka-draft";
const CONVERSATION_KEY = "ka-conversation";
const SIDEBAR_KEY = "ka-sidebar";
const POLL_MS = 2000;

const state = { conversationId: null, polling: new Map(), running: false, capReached: false, view: "ask", title: "New question" };

function store(key, value) {
  try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch (_) { /* private mode */ }
}
function load(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
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
  window.captureScreen.reset();
  $("app").hidden = true;
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
  if (draft) { $("question").value = draft; store(DRAFT_KEY, null); }
  await refreshConversations();
  const remembered = load(CONVERSATION_KEY);
  store(CONVERSATION_KEY, null);
  if (remembered) await openConversation(remembered); else newQuestion();
  await window.captureScreen.takeShared().catch(() => {});
}

async function refreshConversations() {
  const { conversations } = await api("GET", "/api/conversations");
  for (const list of [$("conversation-list"), $("sheet-conversation-list")]) {
    list.replaceChildren();
    for (const item of conversations) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation";
      button.textContent = item.title;
      if (item.id === state.conversationId) button.setAttribute("aria-current", "true");
      button.addEventListener("click", () => closeSheet(() => openConversation(item.id)));
      li.append(button);
      list.append(li);
    }
  }
  for (const empty of document.querySelectorAll(".conversation-empty")) empty.hidden = conversations.length > 0;
}

function stopPolling() {
  for (const timer of state.polling.values()) clearTimeout(timer);
  state.polling.clear();
}

function setTitle(title) {
  state.title = title;
  if (state.view === "ask") $("view-title").textContent = title;
}

function newQuestion() {
  stopPolling();
  state.conversationId = null;
  $("turns").replaceChildren();
  setTitle("New question");
  $("empty-state").hidden = false;
  setRunning(false);
  setView("ask");
  $("question").focus();
}

async function openConversation(id) {
  setView("ask");
  stopPolling();
  try {
    const conversation = await api("GET", `/api/conversations/${encodeURIComponent(id)}`);
    state.conversationId = conversation.id;
    setTitle(conversation.title);
    $("empty-state").hidden = true;
    $("turns").replaceChildren();
    let running = false;
    for (const turn of conversation.turns) {
      renderTurn(turn);
      if (turn.status === "running") { running = true; poll(turn.id); }
    }
    setRunning(running);
    refreshConversations();
  } catch (error) {
    if (error.status !== 401) newQuestion();
  }
}

// Asking --------------------------------------------------------------------------------------
async function ask(question) {
  if (!question || state.running || state.capReached) return;
  setRunning(true);
  try {
    const path = state.conversationId
      ? `/api/conversations/${encodeURIComponent(state.conversationId)}/turns` : "/api/conversations";
    const started = await api("POST", path, { question });
    $("question").value = "";
    if (state.conversationId !== started.conversation_id) {
      state.conversationId = started.conversation_id;
      setTitle(question);
    }
    $("empty-state").hidden = true;
    renderTurn({ id: started.turn_id, question, status: "running", created_at: new Date().toISOString() });
    poll(started.turn_id);
    refreshConversations();
  } catch (error) {
    setRunning(false);
    if (error.status === 429) showCap(error.body);
    else if (error.status === 409) announce("An answer is still running in this conversation.");
    else if (error.status !== 401) announce(error.message);
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
  announce(`Daily spending cap of ${cap} reached. It resets at ${body && body.resets ? body.resets : "midnight UTC"}.`, true);
}

function announce(message, visible) {
  $("status").textContent = message;
  if (visible) {
    let banner = document.querySelector(".banner");
    if (!banner) {
      banner = document.createElement("p");
      banner.className = "banner";
      banner.setAttribute("role", "alert");
      $("composer").before(banner);
    }
    banner.textContent = message;
  }
}

// Rendering -----------------------------------------------------------------------------------
function turnElement(turn) {
  let element = document.querySelector(`.turn[data-id="${CSS.escape(turn.id)}"]`);
  if (!element) {
    element = $("turn-template").content.firstElementChild.cloneNode(true);
    element.dataset.id = turn.id;
    element.querySelector(".question").textContent = turn.question;
    element.querySelector(".retry").addEventListener("click", () => ask(turn.question));
    wireFeedback(element, turn.id);
    $("turns").append(element);
  }
  return element;
}

function renderTurn(turn) {
  const element = turnElement(turn);
  const stateLine = element.querySelector(".turn-state");
  const answer = element.querySelector(".answer");
  const gaps = element.querySelector(".gaps");
  element.dataset.status = turn.status;
  element.querySelector(".retry").hidden = turn.status !== "failed";
  element.querySelector(".thumbs").hidden = !["done", "done_with_gaps"].includes(turn.status);
  if (turn.status === "running") {
    const seconds = Math.max(0, Math.round((Date.now() - Date.parse(turn.created_at)) / 1000));
    stateLine.textContent = `Answering… ${seconds}s`;
    return;
  }
  if (turn.status === "failed") {
    stateLine.textContent = turn.error || "The answer failed.";
    answer.replaceChildren();
    return;
  }
  stateLine.textContent = turn.status === "done_with_gaps" ? "Answered with gaps" : "";
  renderAnswer(element, turn.receipt || {});
  gaps.replaceChildren();
  for (const gap of (turn.receipt && turn.receipt.gaps) || []) {
    const li = document.createElement("li");
    li.textContent = gap;
    gaps.append(li);
  }
  const feedback = turn.feedback;
  const thumbs = element.querySelector(".thumbs");
  thumbs.setAttribute("aria-pressed", feedback ? "true" : "false");
  element.querySelector(".feedback-form").hidden = !feedback;
  if (feedback && feedback.note) element.querySelector(".feedback-form textarea").value = feedback.note;
}

function renderAnswer(element, receipt) {
  const container = element.querySelector(".answer");
  container.replaceChildren();
  const citations = receipt.citations || {};
  const numbers = new Map();
  const paragraphs = String(receipt.answer || "").split(/\n{2,}/);
  for (const text of paragraphs) {
    const p = document.createElement("p");
    for (const part of text.split(/(\[E\d+\.\d+(?:@\d+)?\])/)) {
      const match = /^\[(E\d+\.\d+(?:@\d+)?)\]$/.exec(part);
      if (match && citations[match[1]]) {
        const ref = match[1];
        // Passages from the same source file share one number.
        const source = citations[ref].relative_path || ref;
        if (!numbers.has(source)) numbers.set(source, numbers.size + 1);
        const number = numbers.get(source);
        const marker = document.createElement("button");
        marker.type = "button";
        marker.className = "marker";
        marker.textContent = String(number);
        marker.setAttribute("aria-expanded", "false");
        marker.setAttribute("aria-label", `Show source ${number}`);
        marker.addEventListener("click", () => toggleExcerpt(element, marker, citations[ref]));
        p.append(marker);
      } else {
        p.append(document.createTextNode(part));
      }
    }
    container.append(p);
  }
}

function toggleExcerpt(element, marker, citation) {
  const panel = element.querySelector(".excerpt");
  const wasOpen = marker.getAttribute("aria-expanded") === "true";
  for (const other of element.querySelectorAll(".marker")) other.setAttribute("aria-expanded", "false");
  if (wasOpen) { panel.hidden = true; return; }
  marker.setAttribute("aria-expanded", "true");
  panel.querySelector(".excerpt-title").textContent = citation.title || citation.relative_path;
  panel.querySelector(".excerpt-path").textContent = citation.relative_path;
  panel.querySelector(".excerpt-text").textContent = citation.text;
  const link = panel.querySelector(".open-obsidian");
  const url = String(citation.obsidian_url || "");
  if (url.startsWith("obsidian://")) { link.href = url; link.hidden = false; } else { link.removeAttribute("href"); link.hidden = true; }
  panel.hidden = false;
}

function wireFeedback(element, turnId) {
  const thumbs = element.querySelector(".thumbs");
  const form = element.querySelector(".feedback-form");
  thumbs.addEventListener("click", async () => {
    const on = thumbs.getAttribute("aria-pressed") === "true";
    try {
      if (on) {
        await api("DELETE", `/api/turns/${encodeURIComponent(turnId)}/feedback`);
        thumbs.setAttribute("aria-pressed", "false");
        form.hidden = true;
      } else {
        await api("POST", `/api/turns/${encodeURIComponent(turnId)}/feedback`, {});
        thumbs.setAttribute("aria-pressed", "true");
        form.hidden = false;
        form.querySelector("textarea").focus();
      }
    } catch (error) { if (error.status !== 401) announce(error.message, true); }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("POST", `/api/turns/${encodeURIComponent(turnId)}/feedback`, { note: form.querySelector("textarea").value });
      announce("Note saved.");
    } catch (error) { if (error.status !== 401) announce(error.message, true); }
  });
}

// Views and sheets ----------------------------------------------------------------------------
// Two tabs share one page: each owns a container shown with `hidden`, so switching keeps what was typed.
function applyView(view) {
  const changed = state.view !== view;
  state.view = view;
  $("view-ask").hidden = view !== "ask";
  $("capture").hidden = view !== "add";
  $("view-title").textContent = view === "add" ? "Add to notes" : state.title;
  for (const tab of document.querySelectorAll("[data-tab]")) {
    if (tab.dataset.tab === view) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
  }
  return view === "add" && changed ? window.captureScreen.show() : undefined;
}

// A tab switch first lets any open sheet finish closing, so no stale history entry is left behind.
function setView(view) {
  return new Promise((resolve) => closeSheet(() => resolve(applyView(view))));
}

const SHEET_TITLES = { history: "History" };
const sheet = { open: false, closing: false, opener: null, afterClose: [] };

function openSheet(name, opener) {
  if (sheet.open) return;
  sheet.open = true;
  sheet.opener = opener || document.activeElement;
  history.pushState({ ka: "sheet", name }, "");
  $("sheet-title").textContent = SHEET_TITLES[name];
  for (const section of $("sheet").querySelectorAll("[data-sheet]")) section.hidden = section.dataset.sheet !== name;
  $("sheet").hidden = false;
  $("sheet-scrim").hidden = false;
  $("sheet-close").focus();
}

function teardownSheet() {
  sheet.open = false;
  sheet.closing = false;
  $("sheet").hidden = true;
  $("sheet-scrim").hidden = true;
  const opener = sheet.opener;
  sheet.opener = null;
  const after = sheet.afterClose.splice(0);
  for (const run of after) run();
  // Keep keyboard focus somewhere real: back on the opener unless a follow-up action already moved it.
  if ((!document.activeElement || document.activeElement === document.body) && opener && opener.isConnected) opener.focus();
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
  if (!sheet.open || event.key !== "Tab") return;
  const items = [...$("sheet").querySelectorAll("button, a[href], input, textarea")].filter((el) => !el.closest("[hidden]"));
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  else if (!$("sheet").contains(document.activeElement)) { event.preventDefault(); first.focus(); }
}

function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  $("collapse").setAttribute("aria-expanded", collapsed ? "false" : "true");
  $("collapse").setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
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
  $("sidebar-new").addEventListener("click", newQuestion);
  $("sheet-new").addEventListener("click", () => closeSheet(newQuestion));
  for (const tab of document.querySelectorAll("[data-tab]")) tab.addEventListener("click", () => setView(tab.dataset.tab));
  for (const id of ["history-button", "sidebar-history", "sidebar-account"]) {
    $(id).addEventListener("click", (event) => openSheet("history", event.currentTarget));
  }
  $("collapse").addEventListener("click", () => {
    const collapsed = !document.body.classList.contains("sidebar-collapsed");
    setSidebarCollapsed(collapsed);
    store(SIDEBAR_KEY, collapsed ? "1" : null);
  });
  $("sheet-close").addEventListener("click", () => closeSheet());
  $("sheet-scrim").addEventListener("click", () => closeSheet());
  window.addEventListener("popstate", () => { if (sheet.open) teardownSheet(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSheet();
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
