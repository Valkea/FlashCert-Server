/*
 * Markdown for course notes: the subset the notes prompt allows (## / ### headings, lists, tables, one
 * blockquote per section, fenced code, inline code, bold, italic, links, $…$ and $$…$$ LaTeX).
 * Every piece of text is escaped before markup is added, so the output is safe to insert as HTML.
 *
 * Also ports the app's NotesIndex: sections numbered "2" / "2.1", and quote lookup that ignores case,
 * accents, punctuation and Markdown markers, so a cited passage can be opened and highlighted.
 */
(function () {
  "use strict";

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Same rule as the app: no space just inside the dollars, and "$5 and $6" stays prices.
  var INLINE_MATH = /(?<![$\\])\$(?![$\s])([^$\n]+?)(?<!\s)\$(?![$\d])/g;
  var STATIC = ((document.currentScript && document.currentScript.src) || "").replace(/md\.js(\?.*)?$/, "");
  var SECTION_REF = /\[?§\s?(\d+(?:\.\d+)?)\]?/g;

  /** Inline Markdown → HTML. */
  function inline(text) {
    var stash = [];
    function keep(html) {
      stash.push(html);
      return "\u0000" + (stash.length - 1) + "\u0000";
    }
    var s = String(text == null ? "" : text);
    s = s.replace(/`([^`\n]+)`/g, function (_, code) { return keep("<code>" + esc(code) + "</code>"); });
    s = s.replace(INLINE_MATH, function (_, tex) {
      return keep('<span class="math" data-tex="' + esc(tex) + '">' + esc(tex) + "</span>");
    });
    s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, function (_, label, url) {
      return keep('<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer nofollow">') +
        label + keep("</a>");
    });
    s = esc(s);
    s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__(?=\S)([\s\S]*?\S)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^\w])_(?=\S)([^_]*?\S)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
    s = s.replace(SECTION_REF, function (_, id) {
      return '<a href="#notes" class="secref" data-sec="' + id + '">§' + id + "</a>";
    });
    return s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return stash[+i]; });
  }

  var HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
  var LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  var TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  function cells(line) {
    var s = line.trim();
    if (s.charAt(0) === "|") s = s.slice(1);
    if (s.charAt(s.length - 1) === "|" && s.charAt(s.length - 2) !== "\\") s = s.slice(0, -1);
    return s.split(/(?<!\\)\|/).map(function (c) { return c.trim().replace(/\\\|/g, "|"); });
  }

  function renderList(lines) {
    // Build a tree from indentation, then print it.
    var root = { indent: -1, children: [] };
    var stack = [root];
    lines.forEach(function (line) {
      var m = LIST_ITEM.exec(line);
      if (!m) {
        var last = stack[stack.length - 1];
        if (last !== root) last.text += " " + line.trim();
        return;
      }
      var indent = m[1].replace(/\t/g, "    ").length;
      while (stack.length > 1 && stack[stack.length - 1].indent >= indent) stack.pop();
      var item = { indent: indent, ordered: /\d/.test(m[2]), start: parseInt(m[2], 10), text: m[3], children: [] };
      stack[stack.length - 1].children.push(item);
      stack.push(item);
    });
    function print(items) {
      var html = "";
      var i = 0;
      while (i < items.length) {
        var ordered = items[i].ordered;
        var tag = ordered ? "ol" : "ul";
        var start = ordered && items[i].start > 1 ? ' start="' + items[i].start + '"' : "";
        html += "<" + tag + start + ">";
        while (i < items.length && items[i].ordered === ordered) {
          html += "<li>" + inline(items[i].text) + (items[i].children.length ? print(items[i].children) : "") + "</li>";
          i++;
        }
        html += "</" + tag + ">";
      }
      return html;
    }
    return print(root.children);
  }

  /**
   * Block Markdown → HTML.
   * opts.headingIds: give ##/### headings ids and data-sec numbers (the notes view)
   * opts.shift: added to heading levels (## inside the page becomes <h2> with shift 0)
   */
  function render(markdown, opts) {
    opts = opts || {};
    var lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    var out = [];
    var i = 0;
    var h2 = 0, h3 = 0;
    while (i < lines.length) {
      var line = lines[i];
      var trimmed = line.trim();
      if (!trimmed) { i++; continue; }

      if (trimmed.indexOf("```") === 0) {
        var lang = trimmed.slice(3).trim();
        var code = [];
        i++;
        while (i < lines.length && lines[i].trim().indexOf("```") !== 0) code.push(lines[i++]);
        i++;
        out.push('<pre><code' + (lang ? ' data-lang="' + esc(lang) + '"' : "") + ">" + esc(code.join("\n")) + "</code></pre>");
        continue;
      }

      if (trimmed.indexOf("$$") === 0) {
        var tex = trimmed.slice(2);
        if (tex.length >= 2 && tex.slice(-2) === "$$") {
          tex = tex.slice(0, -2);
          i++;
        } else {
          var parts = [tex];
          i++;
          while (i < lines.length && lines[i].trim().slice(-2) !== "$$") parts.push(lines[i++]);
          if (i < lines.length) parts.push(lines[i++].trim().slice(0, -2));
          tex = parts.join("\n");
        }
        out.push('<div class="math display" data-tex="' + esc(tex.trim()) + '">' + esc(tex.trim()) + "</div>");
        continue;
      }

      var h = HEADING.exec(line);
      if (h) {
        var level = h[1].length;
        var attrs = "";
        if (opts.headingIds && (level === 2 || level === 3)) {
          var id;
          if (level === 2) { h2++; h3 = 0; id = String(h2); } else { if (!h2) h2 = 1; h3++; id = h2 + "." + h3; }
          attrs = ' id="s-' + id + '" data-sec="' + id + '"';
        }
        var tag = "h" + Math.min(6, Math.max(2, level + (opts.shift || 0)));
        out.push("<" + tag + attrs + ">" + inline(h[2]) + "</" + tag + ">");
        i++;
        continue;
      }

      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { out.push("<hr>"); i++; continue; }

      if (trimmed.charAt(0) === "|" && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
        var head = cells(line);
        i += 2;
        var rows = [];
        while (i < lines.length && lines[i].trim().charAt(0) === "|") rows.push(cells(lines[i++]));
        var t = '<div class="table-wrap"><table><thead><tr>' +
          head.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr></thead><tbody>";
        rows.forEach(function (r) {
          t += "<tr>" + r.map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>";
        });
        out.push(t + "</tbody></table></div>");
        continue;
      }

      if (trimmed.charAt(0) === ">") {
        var quote = [];
        while (i < lines.length && lines[i].trim().charAt(0) === ">") quote.push(lines[i++].trim().replace(/^>\s?/, ""));
        out.push('<blockquote class="keypoint">' + render(quote.join("\n"), { shift: opts.shift }) + "</blockquote>");
        continue;
      }

      if (LIST_ITEM.test(line)) {
        var items = [];
        while (i < lines.length) {
          var l = lines[i];
          if (LIST_ITEM.test(l)) { items.push(l); i++; continue; }
          // An indented line continues the previous item; a blank line followed by an item keeps the list.
          if (l.trim() && /^\s{2,}/.test(l)) { items.push(l); i++; continue; }
          if (!l.trim() && i + 1 < lines.length && LIST_ITEM.test(lines[i + 1])) { i++; continue; }
          break;
        }
        out.push(renderList(items));
        continue;
      }

      var para = [];
      while (i < lines.length && lines[i].trim() && !HEADING.test(lines[i]) && !LIST_ITEM.test(lines[i]) &&
        lines[i].trim().charAt(0) !== ">" && lines[i].trim().indexOf("```") !== 0 && lines[i].trim().indexOf("$$") !== 0 &&
        !(lines[i].trim().charAt(0) === "|" && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]))) {
        para.push(lines[i++].trim());
      }
      out.push("<p>" + inline(para.join(" ")) + "</p>");
    }
    return out.join("\n");
  }

  // --- Notes index (port of the app's NotesIndex) -------------------------------------------------

  var SECTION_HEADING = /^(#{2,3})\s+(.+?)\s*#*\s*$/;

  function sections(markdown) {
    var lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    var heads = [];
    var fence = false;
    lines.forEach(function (l, i) {
      if (l.trim().indexOf("```") === 0) fence = !fence;
      if (fence) return;
      var m = SECTION_HEADING.exec(l);
      if (m) heads.push({ line: i, level: m[1].length, heading: m[2].trim() });
    });
    var out = [];
    var h2 = 0, h3 = 0, parent = "";
    heads.forEach(function (hd, k) {
      var id, path;
      if (hd.level === 2) {
        h2++; h3 = 0; id = String(h2); parent = hd.heading; path = hd.heading;
      } else {
        if (!h2) h2 = 1;
        h3++; id = h2 + "." + h3; path = parent ? parent + " › " + hd.heading : hd.heading;
      }
      var bodyEnd = k + 1 < heads.length ? heads[k + 1].line : lines.length;
      var fullEnd = bodyEnd;
      if (hd.level === 2) {
        fullEnd = lines.length;
        for (var j = k + 1; j < heads.length; j++) if (heads[j].level === 2) { fullEnd = heads[j].line; break; }
      }
      out.push({
        id: id, level: hd.level, heading: hd.heading, path: path,
        body: lines.slice(hd.line + 1, bodyEnd).join("\n").trim(),
        fullBody: lines.slice(hd.line + 1, fullEnd).join("\n").trim()
      });
    });
    return out;
  }

  /** Same folding as the app: no accents, no punctuation or Markdown markers, lower case. */
  function fold(s) {
    return String(s).normalize("NFKD").replace(/\p{M}/gu, "")
      .replace(/[’‘]/g, "'").replace(/[“”]/g, '"').replace(/[–—]/g, "-")
      .replace(/[\p{P}*`>|#]/gu, "").toLowerCase();
  }

  /** Folded text plus, for each folded character, the index of its source character. */
  function folded(text) {
    var norm = "";
    var map = [];
    var space = true;
    for (var i = 0; i < text.length; i++) {
      var f = fold(text.charAt(i));
      for (var k = 0; k < f.length; k++) {
        var ch = f.charAt(k);
        if (/\s/.test(ch)) {
          if (space) continue;
          space = true;
          norm += " ";
        } else {
          space = false;
          norm += ch;
        }
        map.push(i);
      }
    }
    return { norm: norm, map: map };
  }

  function find(text, quote) {
    if (!quote || !String(quote).trim() || text == null) return null;
    var f = folded(text);
    var q = fold(quote).replace(/\s+/g, " ").trim();
    if (!q) return null;
    var idx = f.norm.indexOf(q);
    if (idx < 0) return null;
    return [f.map[idx], f.map[Math.min(idx + q.length - 1, f.map.length - 1)] + 1];
  }

  /** The section a citation points to: the cited one when it holds the quote, else the one that does. */
  function locate(all, sectionId, quote) {
    var clean = String(sectionId || "").replace("§", "").trim();
    var cited = null;
    all.forEach(function (s) { if (s.id === clean) cited = s; });
    if (cited && find(cited.fullBody, quote)) {
      if (cited.level === 2) {
        for (var i = 0; i < all.length; i++) {
          var sub = all[i];
          if (sub.level === 3 && sub.id.indexOf(cited.id + ".") === 0 && find(sub.fullBody, quote)) return { section: sub, found: true };
        }
      }
      return { section: cited, found: true };
    }
    var hit = all.filter(function (s) { return s.level === 3 && find(s.fullBody, quote); })[0] ||
      all.filter(function (s) { return find(s.fullBody, quote); })[0];
    if (hit) return { section: hit, found: true };
    return cited ? { section: cited, found: false } : null;
  }

  /** Wraps the quote in <mark> inside an already rendered element. Returns the first mark, or null. */
  function highlight(root, quote) {
    var q = fold(quote || "").replace(/\s+/g, " ").trim();
    if (!q) return null;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        return n.parentElement && n.parentElement.closest(".katex") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [];
    var text = "";
    var n;
    while ((n = walker.nextNode())) {
      nodes.push({ node: n, start: text.length });
      // A space between nodes, so words from separate cells or items don't merge.
      text += n.nodeValue + " ";
    }
    var f = folded(text);
    var idx = f.norm.indexOf(q);
    if (idx < 0) return null;
    var from = f.map[idx];
    var to = f.map[Math.min(idx + q.length - 1, f.map.length - 1)] + 1;
    var first = null;
    nodes.forEach(function (e) {
      var len = e.node.nodeValue.length;
      var a = Math.max(from, e.start), b = Math.min(to, e.start + len);
      if (a >= b) return;
      var range = document.createRange();
      range.setStart(e.node, a - e.start);
      range.setEnd(e.node, b - e.start);
      var mark = document.createElement("mark");
      range.surroundContents(mark);
      if (!first) first = mark;
    });
    return first;
  }

  // --- LaTeX: KaTeX is loaded only on pages that have formulas ------------------------------------

  var katexReady = null;

  function loadKatex() {
    if (katexReady) return katexReady;
    var base = STATIC + "vendor/katex/";
    katexReady = new Promise(function (resolve, reject) {
      var css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = base + "katex.min.css";
      document.head.appendChild(css);
      var js = document.createElement("script");
      js.src = base + "katex.min.js";
      js.onload = function () { resolve(window.katex); };
      js.onerror = reject;
      document.head.appendChild(js);
    });
    return katexReady;
  }

  function typeset(root) {
    var nodes = root.querySelectorAll(".math:not([data-done])");
    if (!nodes.length) return;
    loadKatex().then(function (katex) {
      nodes.forEach(function (el) {
        el.setAttribute("data-done", "");
        try {
          katex.render(el.getAttribute("data-tex"), el, { displayMode: el.classList.contains("display"), throwOnError: false });
        } catch (e) { /* the TeX source stays visible */ }
      });
    }, function () { /* offline: TeX source stays visible */ });
  }

  window.FC = window.FC || {};
  window.FC.md = {
    esc: esc, inline: inline, render: render, sections: sections, find: find, locate: locate,
    highlight: highlight, typeset: typeset
  };
})();
