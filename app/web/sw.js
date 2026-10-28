"use strict";
// Caches only the static app shell so it opens offline; API responses are never cached.
const CACHE = "ka-shell-v22";
const SHARE_CACHE = "ka-share";
const SHELL = ["/", "/static/app.js", "/static/capture.js", "/static/settings.js", "/static/source-filter.js", "/static/sources.js", "/static/styles.css", "/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png",
  "/static/fonts/Newsreader-Variable.woff2", "/static/fonts/Geist-Variable.woff2"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL.map((path) => new Request(path, { cache: "reload" })))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((key) => key !== CACHE && key !== SHARE_CACHE)
      .map((key) => caches.delete(key))))
    .then(() => self.clients.claim()));
});

// A share from the Android share sheet: keep the item locally until the app, after sign-in, picks it up.
async function keepShare(request) {
  const form = await request.formData();
  const cache = await caches.open(SHARE_CACHE);
  const meta = { title: form.get("title") || "", text: form.get("text") || "", url: form.get("url") || "" };
  await cache.put("/share/meta", new Response(JSON.stringify(meta), { headers: { "Content-Type": "application/json" } }));
  const file = form.getAll("files").find((item) => item instanceof File && item.size);
  if (file) {
    await cache.put("/share/file", new Response(file, { headers: { "Content-Type": file.type || "application/octet-stream",
                                                                    "x-name": encodeURIComponent(file.name || "shared") } }));
  } else {
    await cache.delete("/share/file");
  }
  return Response.redirect("/?share=1", 303);
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method === "POST" && url.origin === self.location.origin && url.pathname === "/share") {
    event.respondWith(keepShare(event.request).catch(() => Response.redirect("/?share=failed", 303)));
    return;
  }
  if (event.request.method !== "GET" || url.origin !== self.location.origin || !SHELL.includes(url.pathname)) return;
  // Network first so a new version shows at once; the cache is the offline fallback. "no-cache" makes the
  // browser check with the server every time, so a stale copy in its own HTTP cache is never served.
  event.respondWith(fetch(event.request, { cache: "no-cache" })
    .then((response) => {
      const copy = response.clone();
      if (response.ok) caches.open(CACHE).then((cache) => cache.put(event.request, copy));
      return response;
    })
    .catch(() => caches.match(event.request)));
});
