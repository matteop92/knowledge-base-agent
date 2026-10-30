"use strict";

// Tables, code and diagrams on the Sources reading page, drawn as elements like the rest of the page. A mermaid diagram
// is drawn by the vendored library (vendor/mermaid.min.js), loaded the first time a source has one; its SVG is read as
// XML and adopted, never set as HTML. If the library or a diagram fails, the diagram's source is shown as code.
// sources.js passes in its own element builder `h` and inline-text drawer `inline`.
window.readerBlocks = (h, inline) => {
  let mermaidReady = null;
  let diagramSeq = 0;

  function loadMermaid() {
    if (!mermaidReady) {
      mermaidReady = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/vendor/mermaid.min.js";
        script.onload = () => {
          try {
            window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", htmlLabels: false, theme: "base",
              flowchart: { htmlLabels: false, useMaxWidth: true }, fontFamily: "Geist, system-ui, sans-serif",
              themeVariables: { fontSize: "14px", primaryColor: "#ece8df", primaryTextColor: "#1d1c1a",
                primaryBorderColor: "#a8a398", lineColor: "#625e57", secondaryColor: "#dfeae5", tertiaryColor: "#fffdf8",
                background: "#fffdf8", clusterBkg: "#f1eee6", clusterBorder: "#d9d4c9", edgeLabelBackground: "#fffdf8" } });
            resolve(window.mermaid);
          } catch (error) {
            mermaidReady = null;
            reject(error);
          }
        };
        script.onerror = () => { mermaidReady = null; script.remove(); reject(new Error("mermaid did not load")); };
        document.head.append(script);
      });
    }
    return mermaidReady;
  }

  function codeBlock(text) {
    const pre = h("pre", "r-code");
    pre.append(h("code", "", text));
    return pre;
  }

  // A diagram can set its own config (a `%%{init: ...}%%` directive or a leading `---` frontmatter block), and its
  // `themeCSS` would restyle the whole page under our inline-style policy, so both are taken out before drawing.
  const withoutConfig = (text) => text.replace(/%%\{[\s\S]*?\}%%/g, "").replace(/^\s*---[\s\S]*?\n---[ \t]*(?:\n|$)/, "");

  function diagram(text) {
    const figure = h("figure", "r-diagram");
    figure.hidden = true;
    const id = `diagram-${++diagramSeq}`;
    loadMermaid().then((mermaid) => mermaid.render(id, withoutConfig(text))).then(({ svg }) => {
      const parsed = new DOMParser().parseFromString(svg, "image/svg+xml");
      if (parsed.querySelector("parsererror")) throw new Error("diagram is not valid SVG");
      figure.append(document.importNode(parsed.documentElement, true));
      figure.hidden = false;
    }).catch(() => {
      document.getElementById(`d${id}`)?.remove();  // mermaid leaves its error drawing in the page
      figure.replaceChildren(codeBlock(text));
      figure.classList.add("is-source");
      figure.hidden = false;
    });
    return figure;
  }

  function table(rows) {
    const wrap = h("div", "r-table-wrap");
    const grid = h("table", "r-table");
    const head = h("thead");
    const headRow = h("tr");
    for (const cell of rows[0] || []) headRow.append(inline("th", "", cell));
    head.append(headRow);
    const body = h("tbody");
    for (const cells of rows.slice(1)) {
      const row = h("tr");
      for (const cell of cells) row.append(inline("td", "", cell));
      body.append(row);
    }
    grid.append(head, body);
    wrap.append(grid);
    return wrap;
  }

  return { codeBlock, diagram, table };
};
