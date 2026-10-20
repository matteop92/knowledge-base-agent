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

  const DETECTED = {
    youtube: { icon: "video", title: "YouTube video", sub: "Saved to Transcripts" },
    markdown: { icon: "doc", title: "Markdown file", sub: "The app files it in the right folder" },
    textfile: { icon: "doc", title: "Text file", sub: "The app files it in the right folder" },
    link: { icon: "link", title: "Web link", sub: "The app files it in the right folder" },
    text: { icon: "doc", title: "Text", sub: "The app files it in the right folder" },
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
    const tags = $("capture-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean);
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
    let sent = false;
    try {
      const { capture_id: id } = await api("POST", "/api/captures", body);
      retries.set(id, body);
      if (fromForm) {
        pending = null;
        fileLabel = null;
        fileToken += 1;
        $("capture-preview").hidden = true;
        $("capture-kind").value = "text";
        $("capture-text").value = "";
        $("capture-title").value = "";
        $("capture-tags").value = "";
        $("capture-file").value = "";
        $("capture-more").open = false;
      }
      message("Saving… it appears under Recent below.", true);
      sent = true;
      renderCapture({ id, kind: body.kind, status: "running", title: body.title || "New capture" }, true);
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
          const sent = await send(retries.get(job.id), false);
          retrying.delete(job.id);
          // Resent: this failed row keeps no Retry, so the note cannot be added twice.
          if (sent) retries.delete(job.id); else retry.disabled = false;
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
    item.append(state, body);
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

  // Called by the shell when the Add tab is shown: load the setting and the recent saves.
  async function show() {
    try {
      const { captures, enabled, vault_name: vaultName } = await api("GET", "/api/captures");
      state.captureEnabled = enabled;
      state.vaultName = vaultName;
      $("capture-disabled").hidden = enabled;
      $("capture-list").replaceChildren();
      for (const job of captures) {
        renderCapture(job);
        if (job.status === "running") pollCapture(job.id);
      }
    } catch (error) {
      if (error.status !== 401) message("Could not load your recent saves. Check your connection and try again.");
    }
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
    for (const timer of pollers.values()) clearTimeout(timer);
    pollers.clear();
    $("capture-kind").value = "text";
    for (const id of ["capture-text", "capture-title", "capture-tags", "capture-file"]) $(id).value = "";
    $("capture-preview").hidden = true;
    $("capture-preview").removeAttribute("src");
    $("capture-more").open = false;
    $("capture-list").replaceChildren();
    message("");
    refresh();
  }

  // Open Add with some text already in the box (used when a gap asks for a note).
  async function openWith(text) {
    await setView("add");
    const box = $("capture-text");
    // A draft already in the box is kept: the new text goes after it.
    if (box.value.trim()) { box.value = `${box.value.trimEnd()}\n\n${text}`; refresh(); } else takeText(text);
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
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

  document.addEventListener("DOMContentLoaded", () => {
    $("capture-form").addEventListener("submit", (event) => { event.preventDefault(); save(); });
    $("capture-file").addEventListener("change", (event) => takeFile(event.target.files[0]));
    $("capture-attach").addEventListener("click", () => $("capture-file").click());
    $("capture-text").addEventListener("input", () => {
      // Clearing the box clears a loaded file's label, so a new paste is judged on its own.
      if (!$("capture-text").value.trim()) { fileLabel = null; $("capture-kind").value = "text"; }
      refresh();
    });
    const drop = $("capture");
    drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("dragging"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("dragging"));
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      drop.classList.remove("dragging");
      if (event.dataTransfer.files.length) takeFile(event.dataTransfer.files[0]);
      else takeText(event.dataTransfer.getData("text/plain"));
    });
    drop.addEventListener("paste", (event) => {
      const image = [...event.clipboardData.files].find((file) => file.type.startsWith("image/"));
      if (image) { event.preventDefault(); takeFile(image); }
    });
  });

  window.captureScreen = { takeShared, show, reset, openWith };
})();
