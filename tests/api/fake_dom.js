"use strict";
// The small fake DOM the UI harnesses run the web app against. A harness sets El.document to its document stub.

class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.parent = null; this.attrs = {}; this.dataset = {}; this.listeners = {};
    this.className = ""; this._text = ""; this.hidden = false; this.value = ""; this.id = ""; this.disabled = false;
    this.scrollTop = 0; this.style = {}; this.rect = { left: 0, width: 400 };
    const self = this;
    this.classList = {
      add: (...names) => { for (const n of names) if (!self.classList.contains(n)) self.className = `${self.className} ${n}`.trim(); },
      remove: (name) => { self.className = self.className.split(/\s+/).filter((n) => n && n !== name).join(" "); },
      contains: (name) => self.className.split(/\s+/).includes(name),
      toggle: (name, on) => { if (on === undefined ? !self.classList.contains(name) : on) self.classList.add(name); else self.classList.remove(name); },
    };
  }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  append(...nodes) { for (const n of nodes) this._add(n); }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  _add(node) {
    if (node && node.fragment) { for (const c of [...node.children]) this._add(c); node.children = []; return; }
    if (typeof node === "string") { this._text += node; return; }
    node.parent = this; this.children.push(node);
  }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "id") this.id = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) { const e = { target: this, preventDefault() { e.prevented = true; }, stopPropagation() { e.stopped = true; }, composedPath: () => [this], ...event }; for (const fn of this.listeners[type] || []) fn(e); return e; }
  click(event = {}) { return this.dispatch("click", event); }
  focus() { if (El.document) El.document.activeElement = this; }
  getBoundingClientRect() { return this.rect; }
  scrollIntoView() { this.scrolledIntoView = true; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); }
  get parentElement() { return this.parent; }
  get nodeType() { return this.tagName === "#text" ? 3 : 1; }
  normalize() {
    const merged = [];
    for (const child of this.children) {
      const last = merged[merged.length - 1];
      if (last && last.tagName === "#text" && child.tagName === "#text") last._text += child._text; else merged.push(child);
    }
    this.children = merged;
  }
  replaceWith(...nodes) {
    const siblings = this.parent.children;
    for (const n of nodes) n.parent = this.parent;
    siblings.splice(siblings.indexOf(this), 1, ...nodes);
  }
  closest(selector) {
    const data = /^\[data-([\w-]+)\]$/.exec(selector);
    for (let node = this; node; node = node.parent) {
      if (data ? node.dataset[data[1]] !== undefined : node.tagName === selector) return node;
    }
    return null;
  }
  requestSubmit() { this.dispatch("submit"); }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  matches(compound) {
    const tag = /^[a-z0-9]+/i.exec(compound);
    if (tag && tag[0].toLowerCase() !== this.tagName) return false;
    for (const cls of compound.match(/\.[\w-]+/g) || []) if (!this.classList.contains(cls.slice(1))) return false;
    for (const [, k, v] of compound.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
      const key = k.startsWith("data-") ? k.slice(5).replace(/-(\w)/g, (_, c) => c.toUpperCase()) : k;
      const actual = k.startsWith("data-") ? this.dataset[key] : this.attrs[k];
      if (v === undefined ? actual === undefined : actual !== v) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/);
    let scope = [this];
    for (const part of parts) scope = scope.flatMap((node) => node.all().filter((el) => el.matches(part)));
    return [...new Set(scope)];
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  isConnected = true;
  getClientRects() { return [1]; }
}

module.exports = { El };
