"use strict";

// A saved AI prompt split into runs of plain text and blanks to fill in before it is used: {name}, {{name}} and [NAME].
// Sources and Review draw the blanks in the accent colour. No DOM, so it runs in the browser and under Node.
(() => {
  const BLANK = /\{\{[^{}\n]{1,40}\}\}|\{[A-Za-z_][\w .-]{0,39}\}|\[[A-Z][A-Z0-9_ -]{1,39}\]/g;

  // [{ text, blank }] in order; joined back together they are the text unchanged.
  function runs(text) {
    const value = String(text == null ? "" : text);
    const result = [];
    let at = 0;
    for (const match of value.matchAll(BLANK)) {
      if (match.index > at) result.push({ text: value.slice(at, match.index), blank: false });
      result.push({ text: match[0], blank: true });
      at = match.index + match[0].length;
    }
    if (at < value.length) result.push({ text: value.slice(at), blank: false });
    return result;
  }

  const api = { runs };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.promptText = api;
})();
