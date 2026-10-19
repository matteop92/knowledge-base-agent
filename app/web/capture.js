"use strict";

// Capture screen: paste, drop or share text, a screenshot or a .md file; it becomes a new note.
// Every piece of text from the server or the shared item is inserted with textContent, never as HTML.
(() => {
  const MAX_SIDE = 2000;  // screenshots are downscaled here, so the server never receives huge images
  const SHARE_CACHE = "ka-share";
  const YOUTUBE = /^(https?:\/\/)?(www\.|m\.)?(youtube\.com\/(watch|shorts|live)|youtu\.be\/)/i;
  let pending = null;  // {kind, content?, image?, label}
  const retries = new Map();  // capture id -> request body, for Retry

  const STATUS = {
    running: "Saving…",
    indexed: "Saved and searchable",
    queued: "Saved; searchable after the next index pass (within five minutes)",
  };

  function message(text) { $("capture-message").textContent = text || ""; }

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
    if (file.type.startsWith("image/")) {
      pending = { kind: "screenshot", image: await downscale(file), label: `Screenshot: ${file.name || "image"}` };
    } else if (/\.(md|markdown|txt)$/i.test(file.name || "") || file.type.startsWith("text/")) {
      pending = { kind: /\.txt$/i.test(file.name || "") ? "text" : "markdown", content: await file.text(),
                  label: `File: ${file.name || "note"}` };
    } else {
      pending = null;
      message("Only screenshots (PNG, JPEG, WebP) and .md files can be added.");
    }
    $("capture-pending").textContent = pending ? pending.label : "";
  }

  function takeText(text, title, url) {
    $("capture-text").value = [text, url && !(text || "").includes(url) ? url : ""].filter(Boolean).join("\n");
    if (title) $("capture-title").value = title;
  }

  function requestBody() {
    const title = $("capture-title").value.trim() || null;
    const tags = $("capture-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean);
    if (pending) return { ...pending, label: undefined, title, tags };
    const text = $("capture-text").value.trim();
    if (!text) return null;
    if (YOUTUBE.test(text) && !/\s/.test(text)) return { kind: "youtube", url: text, title, tags };
    const url = /^https?:\/\/\S+$/.test(text) ? text : null;
    return { kind: "text", content: text, url, title, tags };
  }

  async function save() {
    const body = requestBody();
    if (!body) { message("Paste text, drop a screenshot or choose a file first."); return; }
    if (body.kind === "youtube") { message("YouTube capture is coming soon."); return; }
    await send(body);
  }

  async function send(body) {
    $("capture-save").disabled = true;
    try {
      const { capture_id: id } = await api("POST", "/api/captures", body);
      retries.set(id, body);
      pending = null;
      $("capture-pending").textContent = "";
      $("capture-text").value = "";
      $("capture-title").value = "";
      message("");
      renderCapture({ id, status: "running", title: body.title || "New capture" }, true);
      pollCapture(id);
    } catch (error) {
      message(error.status === 429 ? "The daily spending cap was reached." : error.message);
    } finally {
      $("capture-save").disabled = !state.captureEnabled;
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
    const title = document.createElement("p");
    title.className = "capture-title";
    title.textContent = job.title || "Capture";
    const line = document.createElement("p");
    line.className = "muted small";
    line.textContent = job.status === "running" ? STATUS.running :
      job.status === "failed" ? `Failed: ${job.error || "unknown reason"}` :
      job.indexed ? STATUS.indexed : STATUS.queued;
    item.dataset.status = job.status;
    item.append(title, line);
    if (job.vault_path) {
      const path = document.createElement("p");
      path.className = "muted small path";
      path.textContent = job.vault_path;
      const open = document.createElement("a");
      open.href = obsidianLink(job.vault_path);
      open.textContent = "Open in Obsidian";
      item.append(path, open);
    }
    if (job.status === "failed" && retries.has(job.id)) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Retry";
      retry.addEventListener("click", () => send(retries.get(job.id)));
      item.append(retry);
    }
  }

  function pollCapture(id) {
    const tick = async () => {
      try {
        const job = await api("GET", `/api/captures/${encodeURIComponent(id)}`);
        renderCapture(job);
        if (job.status === "running") setTimeout(tick, POLL_MS);
      } catch (error) {
        if (error.status !== 401) setTimeout(tick, POLL_MS * 2);
      }
    };
    setTimeout(tick, POLL_MS);
  }

  async function openCapture() {
    closeSidebar();
    document.body.classList.add("capturing");
    $("capture").hidden = false;
    try {
      const { captures, enabled, vault_name: vaultName } = await api("GET", "/api/captures");
      state.captureEnabled = enabled;
      state.vaultName = vaultName;
      $("capture-disabled").hidden = enabled;
      $("capture-save").disabled = !enabled;
      $("capture-list").replaceChildren();
      for (const job of captures) {
        renderCapture(job);
        if (job.status === "running") pollCapture(job.id);
      }
    } catch (_) { /* signed out: the sign-in screen is showing */ }
    $("capture-text").focus();
  }

  function closeCapture() {
    document.body.classList.remove("capturing");
    $("capture").hidden = true;
  }

  // Android share sheet: the service worker keeps the shared item until the app (and sign-in) picks it up.
  async function takeShared() {
    if (!new URLSearchParams(location.search).has("share") || !("caches" in window)) return;
    history.replaceState(null, "", "/");
    const cache = await caches.open(SHARE_CACHE);
    const meta = await cache.match("/share/meta");
    if (!meta) return;
    const { title, text, url } = await meta.json();
    await openCapture();
    const file = await cache.match("/share/file");
    if (file) {
      const blob = await file.blob();
      const name = decodeURIComponent(file.headers.get("x-name") || "shared");
      await takeFile(new File([blob], name, { type: blob.type }));
    }
    takeText(text, title, url);
    await cache.delete("/share/meta");
    await cache.delete("/share/file");
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("add").addEventListener("click", openCapture);
    $("sidebar-add").addEventListener("click", openCapture);
    $("capture-close").addEventListener("click", closeCapture);
    $("capture-form").addEventListener("submit", (event) => { event.preventDefault(); save(); });
    $("capture-file").addEventListener("change", (event) => takeFile(event.target.files[0]));
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
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeCapture(); });
  });

  window.captureScreen = { takeShared, closeCapture };
})();
