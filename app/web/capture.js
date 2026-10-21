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

  // Tags are typed into one field and become removable chips. Splits on commas and new lines, trims, and drops
  // repeats (any letter case); a repeat of a tag already there comes back in `duplicates` so its chip can flash.
  function mergeTags(current, text) {
    const tags = [...current];
    const duplicates = [];
    for (const part of String(text).split(/[,\n]/)) {
      const tag = part.trim();
      if (!tag) continue;
      const same = tags.find((entry) => entry.toLowerCase() === tag.toLowerCase());
      if (same) duplicates.push(same); else tags.push(tag);
    }
    return { tags, duplicates };
  }

  let tagList = [];

  function renderTags(flash) {
    const chips = $("capture-tag-chips");
    chips.replaceChildren();
    for (const tag of tagList) {
      const chip = document.createElement("li");
      chip.className = flash && flash.includes(tag) ? "tag-chip is-duplicate" : "tag-chip";
      const name = document.createElement("span");
      name.className = "tag-chip-name";
      name.textContent = tag;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chip-remove";
      remove.setAttribute("aria-label", `Remove tag ${tag}`);
      remove.append(icon("close"));
      remove.addEventListener("click", () => {
        tagList = tagList.filter((entry) => entry !== tag);
        renderTags();
        tagStatus(`Removed tag ${tag}`);
        $("capture-tags").focus();
      });
      chip.append(name, remove);
      chips.append(chip);
    }
    if (flash && flash.length) setTimeout(() => renderTags(), 900);
  }

  function tagStatus(text) {
    $("capture-tag-status").textContent = text;
  }

  // Turns what is typed in the tag field into chips.
  function commitTags() {
    const field = $("capture-tags");
    if (!field.value.trim()) { field.value = ""; return; }
    const before = tagList.length;
    const merged = mergeTags(tagList, field.value);
    tagList = merged.tags;
    field.value = "";
    renderTags(merged.duplicates);
    if (tagList.length > before) tagStatus(`Added ${tagList.slice(before).join(", ")}`);
  }

  function setTags(tags) {
    tagList = tags;
    $("capture-tags").value = "";
    renderTags();
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
    if (title) { $("capture-title").value = title; $("capture-more").open = true; }
    refresh();
  }

  function requestBody() {
    const title = $("capture-title").value.trim() || null;
    // The chips plus anything still being typed in the tag field.
    const tags = mergeTags(tagList, $("capture-tags").value).tags;
    if (pending) return { ...pending, label: undefined, title, tags };
    const text = $("capture-text").value.trim();
    if (!text) return null;
    const kind = classify(text);
    if (kind === "youtube" || kind === "markdown") return { kind, content: text, title, tags };
    return { kind: "text", content: text, url: /^https?:\/\/\S+$/.test(text) ? text : null, title, tags };
  }

  async function save() {
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
        $("capture-title").value = "";
        setTags([]);
        $("capture-file").value = "";
        $("capture-more").open = false;
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
    title.textContent = job.title || "Capture";
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
      open.textContent = "See in Obsidian";
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
    if (filling) {
      $("gap-panel-text").textContent = gapMode.gap;
      $("gap-panel-from").textContent = gapMode.question ? `From “${gapMode.question}”` : "";
      $("gap-panel-from").hidden = !gapMode.question;
    }
  }

  function leaveGap() {
    gapMode = null;
    renderMode();
  }

  // Called by the shell when the Add tab is shown: load the setting and the recent saves.
  async function show() {
    leaveGap();
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
    setTags([]);
    $("capture-preview").hidden = true;
    $("capture-preview").removeAttribute("src");
    $("capture-more").open = false;
    $("capture-list").replaceChildren();
    $("recent-count").hidden = true;
    $("toast").classList.remove("is-shown");
    endDrag();
    leaveGap();
    message("");
    refresh();
  }

  // Open Add for one gap from "What's missing". A draft already in the box is kept.
  async function openGap(next) {
    await setView("add");
    gapMode = next;
    renderMode();
    $("capture-text").focus();
  }

  // Back to the answer the gap came from.
  async function backToAnswer() {
    const conversationId = gapMode && gapMode.conversationId;
    leaveGap();
    if (conversationId) await openConversation(conversationId); else await setView("ask");
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

  let dragDepth = 0;
  function startDrag() {
    $("capture").classList.add("dragging");
    $("drop-target").hidden = false;
  }
  function endDrag() {
    dragDepth = 0;
    $("capture").classList.remove("dragging");
    $("drop-target").hidden = true;
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("capture-form").addEventListener("submit", (event) => { event.preventDefault(); save(); });
    $("gap-back").addEventListener("click", backToAnswer);
    // The tag field: Enter and comma make a chip (Enter never sends the form); Backspace in an empty field removes the last.
    const tagField = $("capture-tags");
    tagField.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === ",") { event.preventDefault(); commitTags(); }
      else if (event.key === "Backspace" && !tagField.value && tagList.length) {
        const removed = tagList[tagList.length - 1];
        setTags(tagList.slice(0, -1));
        tagStatus(`Removed tag ${removed}`);
      }
    });
    tagField.addEventListener("input", () => { if (/[,\n]/.test(tagField.value)) commitTags(); });
    tagField.addEventListener("blur", commitTags);
    setInterval(() => { if (!$("capture").hidden) refreshTimes(); }, 60000);
    $("capture-file").addEventListener("change", (event) => takeFile(event.target.files[0]));
    $("capture-attach").addEventListener("click", () => $("capture-file").click());
    $("capture-text").addEventListener("input", () => {
      // Clearing the box clears a loaded file's label, so a new paste is judged on its own.
      if (!$("capture-text").value.trim()) { fileLabel = null; $("capture-kind").value = "text"; }
      refresh();
    });
    const drop = $("capture");
    // The target shows while a drag is over the page. Moving between elements fires dragleave then dragenter, so the
    // depth is counted; a cancelled drag (Escape, leaving the window) clears it too.
    drop.addEventListener("dragenter", (event) => { event.preventDefault(); dragDepth += 1; startDrag(); });
    drop.addEventListener("dragover", (event) => { event.preventDefault(); startDrag(); });
    drop.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) endDrag(); });
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
