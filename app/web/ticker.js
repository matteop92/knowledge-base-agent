"use strict";
// The band at the top of an empty Ask page: notes added in the last 7 days, scrolling slowly to the left.
// A click opens that note's own Sources page. A failure or an empty list leaves the band hidden.
(() => {
  const SPEED = 40;  // pixels per second
  const BY_TYPE = { article: "browser", highlights: "book-open", book: "books", note: "notepad", quote: "ph-quotes", transcript: "mic" };
  const BY_PLATFORM = { x: "xlogo", linkedin: "linkedin", reddit: "reddit" };

  // Pure helpers (no DOM).
  function iconName(source) {
    if (source.type === "social_post") return BY_PLATFORM[source.platform] || "globe";
    return BY_TYPE[source.type] || "notepad";
  }
  const needsScroll = (trackWidth, bandWidth) => trackWidth > bandWidth;
  const durationFor = (trackWidth) => Math.max(1, trackWidth / SPEED);

  const part = (tag, className, text) => {
    const el = document.createElement(tag);
    el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  };

  function item(source, copy) {
    const button = part("button", "ticker-item");
    button.type = "button";
    button.setAttribute("aria-label", `Open ${source.title}`);
    if (copy) button.setAttribute("tabindex", "-1");
    const mark = icon(iconName(source));
    mark.classList.add("ticker-icon");
    button.append(mark, part("span", "ticker-title", source.title));
    if (source.author) button.append(part("span", "ticker-author", source.author));
    button.addEventListener("click", () => { window.sourcesScreen.openById(source.id); });
    return button;
  }

  function copyOf(sources, hidden) {
    const wrap = part("div", "ticker-copy");
    if (hidden) wrap.setAttribute("aria-hidden", "true");
    wrap.append(...sources.map((source) => item(source, hidden)));
    return wrap;
  }

  let sources = [];
  let loads = 0;
  let lastWidth = 0;
  let watching = false;

  const reducedMotion = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Draws the items once, measures them, and only repeats them and starts the scroll when they do not fit.
  function render() {
    const band = $("ticker"), track = $("ticker-track");
    track.classList.remove("is-scrolling");
    track.replaceChildren(copyOf(sources, false));
    band.hidden = sources.length === 0;
    if (band.hidden) return;
    const bandWidth = band.getBoundingClientRect().width;
    const trackWidth = track.getBoundingClientRect().width;
    lastWidth = bandWidth;
    if (!bandWidth || reducedMotion() || !needsScroll(trackWidth, bandWidth)) return;  // off screen: measured again when it shows
    track.append(copyOf(sources, true));
    track.style.setProperty("--ticker-duration", `${durationFor(trackWidth)}s`);
    track.classList.add("is-scrolling");
  }

  function watch() {
    if (watching || typeof ResizeObserver !== "function") return;
    watching = true;
    new ResizeObserver(() => {
      if (sources.length && $("ticker").getBoundingClientRect().width !== lastWidth) render();
    }).observe($("ticker"));
  }

  async function load() {
    const mine = ++loads;
    try {
      const reply = await api("GET", "/api/sources/recent");
      if (mine !== loads) return;
      sources = Array.isArray(reply && reply.sources) ? reply.sources : [];
    } catch (error) {
      if (mine !== loads) return;
      sources = [];
    }
    render();
    watch();
  }

  window.ticker = { load };
})();
