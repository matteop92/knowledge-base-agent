"use strict";

// Capture screen: paste, drop or share text, a screenshot or a .md file; it becomes a new note.
// Every piece of text from the server or the shared item is inserted with textContent, never as HTML.
(() => {
  const MAX_SIDE = 2000;  // screenshots are downscaled here, so the server never receives huge images
  const SHARE_CACHE = "ka-share";
  const YOUTUBE = /^(https?:\/\/)?(www\.|m\.|music\.)?(youtube\.com\/(watch|shorts|live|embed)|youtu\.be\/)/i;
  let pending = null;  // {kind, content?, image?, label}
  let fileLabel = null;  // "Markdown file" or "Text file" while the box holds a loaded file
  const retries = new Map();  // capture id -> request body, for Retry
  const pollers = new Map();  // capture id -> timer, so one save is never polled twice

  const STATUS = {
    running: "Saving…",
    indexed: "Saved and searchable",
    queued: "Saved; searchable after the next index pass (within five minutes)",
  };

  // What the app will treat the box contents as, using the same rules requestBody applies.
  function detect() {
    if (pending) return { icon: "image", title: "Screenshot", sub: pending.label.replace(/^Screenshot: /, "") };
    const text = $("capture-text").value.trim();
    if (!text) return null;
    const filed = "The app files it in the right folder";
    if (fileLabel) return { icon: "doc", title: fileLabel, sub: filed };
    if (YOUTUBE.test(text) && !/\s/.test(text)) return { icon: "video", title: "YouTube video", sub: "Saved to Transcripts" };
    if (/^https?:\/\/\S+$/.test(text)) return { icon: "link", title: "Web link", sub: filed };
    return { icon: "doc", title: "Text", sub: filed };
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
    $("capture-save").disabled = !state.captureEnabled || !found;
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
    $("capture-preview").hidden = true;
    if (file.type.startsWith("image/")) {
      fileLabel = null;
      pending = { kind: "screenshot", image: await downscale(file), label: `Screenshot: ${file.name || "image"}` };
      // Show exactly what Save will send.
      $("capture-preview").src = pending.image;
      $("capture-preview").hidden = false;
    } else if (/\.(md|markdown|txt)$/i.test(file.name || "") || file.type.startsWith("text/")) {
      // The file's text goes into the visible box, so nothing is saved that cannot be seen.
      pending = null;
      $("capture-text").value = await file.text();
      const plain = /\.txt$/i.test(file.name || "");
      $("capture-kind").value = plain ? "text" : "markdown";
      fileLabel = plain ? "Text file" : "Markdown file";
    } else {
      pending = null;
      message("Only screenshots (PNG, JPEG, WebP), .md and .txt files can be added.");
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
    if (YOUTUBE.test(text) && !/\s/.test(text)) return { kind: "youtube", content: text, title, tags };
    if ($("capture-kind").value === "markdown") return { kind: "markdown", content: text, title, tags };
    const url = /^https?:\/\/\S+$/.test(text) ? text : null;
    return { kind: "text", content: text, url, title, tags };
  }

  async function save() {
    const body = requestBody();
    if (!body) { message("Paste text, drop a screenshot or choose a file first."); return; }
    await send(body);
  }

  async function send(body) {
    $("capture-save").disabled = true;
    try {
      const { capture_id: id } = await api("POST", "/api/captures", body);
      retries.set(id, body);
      pending = null;
      fileLabel = null;
      $("capture-preview").hidden = true;
      $("capture-kind").value = "text";
      $("capture-text").value = "";
      $("capture-title").value = "";
      $("capture-tags").value = "";
      $("capture-file").value = "";
      $("capture-more").open = false;
      message("Saving… it appears under Recent below.", true);
      renderCapture({ id, status: "running", title: body.title || "New capture" }, true);
      pollCapture(id, true);
    } catch (error) {
      message(error.status === 429 ? "The daily spending cap was reached." : error.message);
    } finally {
      refresh();
    }
  }

  function renderCapture(job, prepend) {
    let item = document.querySelector(`.capture-item[data-id="${CSS.escape(job.id)}"]`);
    if (!item) {
      item = document.createElement("li");
      item.className = "capture-item";
      item.dataset.id = job.id;
      prepend ? $("capture-list").prepend(item) : $("capture-list").append(item);
    }
    item.replaceChildren();
    const body = document.createElement("div");
    body.className = "capture-body";
    const title = document.createElement("p");
    title.className = "capture-title";
    title.textContent = job.title || "Capture";
    const line = document.createElement("p");
    line.className = "muted small";
    line.textContent = job.status === "running" ? STATUS.running :
      job.status === "failed" ? `Failed: ${job.error || "unknown reason"}` :
      job.indexed ? STATUS.indexed : STATUS.queued;
    item.dataset.status = job.status;
    body.append(title, line);
    item.append(icon("doc"), body);
    if (job.vault_path) {
      const path = document.createElement("p");
      path.className = "muted small path";
      path.textContent = job.vault_path;
      const open = document.createElement("a");
      open.href = obsidianLink(job.vault_path);
      open.textContent = "Open in Obsidian";
      body.append(path, open);
    }
    if (job.status === "failed" && retries.has(job.id)) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Retry";
      retry.addEventListener("click", () => send(retries.get(job.id)));
      body.append(retry);
    }
  }

  function pollCapture(id, announceResult) {
    if (pollers.has(id)) return;
    const later = (ms) => pollers.set(id, setTimeout(tick, ms));
    const tick = async () => {
      pollers.delete(id);
      try {
        const job = await api("GET", `/api/captures/${encodeURIComponent(id)}`);
        renderCapture(job);
        if (job.status === "running") { later(POLL_MS); return; }
        if (announceResult) {
          message(job.status === "failed" ? `Not saved: ${job.error || "unknown reason"}` : `Saved to ${job.vault_path}`,
                  job.status !== "failed");
        }
      } catch (error) {
        if (error.status !== 401) later(POLL_MS * 2);
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
    } catch (_) { /* signed out: the sign-in screen is showing */ }
    refresh();
    $("capture-text").focus();
  }

  // Sign-out and a 401 clear whatever was half-typed, so the next person sees an empty box.
  function reset() {
    pending = null;
    fileLabel = null;
    retries.clear();
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
    takeText(text);
    const box = $("capture-text");
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
