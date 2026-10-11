/* Course page: notes, revision sheet, quiz and flashcards, read-only, with passages and error reports. */
(function () {
  "use strict";
  var t = FC.t, md = FC.md, progress = FC.progress;
  var root = document.getElementById("course");
  var data = JSON.parse(document.getElementById("course-data").textContent);
  var shareId = data.share_id;
  var sections = md.sections(data.notes);
  var questions = data.questions || [];
  var cards = data.cards || [];
  var sheet = data.sheet || null;
  var BLANK = /＿+|_{2,}/;

  // --- DOM helpers --------------------------------------------------------------------------------

  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v == null || v === false) return;
        if (k === "class") el.className = v;
        else if (k === "text") el.textContent = v;
        else if (k === "style") el.style.cssText = v; // CSSOM: allowed by the CSP, unlike a style attribute
        else if (k === "html") el.innerHTML = v; // only for output of FC.md, which escapes everything
        else if (k.indexOf("on") === 0) el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? "" : v);
      });
    }
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      (Array.isArray(c) ? c : [c]).forEach(function (x) {
        if (x != null && x !== false) el.append(x.nodeType ? x : document.createTextNode(String(x)));
      });
    }
    return el;
  }

  function icon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  function mdBlock(markdown, cls) {
    var el = h("div", { class: "md" + (cls ? " " + cls : ""), html: md.render(markdown) });
    md.typeset(el);
    return el;
  }

  function mdInline(text, tag) {
    var el = h(tag || "span", { html: md.inline(text) });
    md.typeset(el);
    return el;
  }

  function button(label, cls, onclick, ico) {
    return h("button", { type: "button", class: "btn" + (cls ? " " + cls : ""), onclick: onclick }, ico ? icon(ico) : null, label);
  }

  function flagButton(kind, id) {
    return h("button", {
      type: "button", class: "flag", "data-kind": kind, "data-id": id,
      "aria-label": t("report_this"), title: t("report_this")
    }, icon("flag"));
  }

  function shuffle(a) {
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var x = a[i]; a[i] = a[j]; a[j] = x;
    }
    return a;
  }

  function range(n) {
    var out = [];
    for (var i = 0; i < n; i++) out.push(i);
    return out;
  }

  // --- Header -------------------------------------------------------------------------------------

  var meta = document.getElementById("course-meta");
  var updated = data.updated_at ? new Date(data.updated_at).toLocaleDateString(FC.lang) : "";
  if (questions.length) meta.append(h("span", { text: t("questions_n", questions.length) }));
  if (cards.length) meta.append(h("span", { text: t("cards_n", cards.length) }));
  if (updated) meta.append(h("span", { text: t("updated", updated) }));

  var copyBtn = document.getElementById("copy-link");
  copyBtn.addEventListener("click", function () {
    var url = root.dataset.url;
    var label = copyBtn.querySelector("span");
    var done = function () {
      label.textContent = t("copied");
      setTimeout(function () { label.textContent = t("share"); }, 2000);
    };
    if (navigator.share && matchMedia("(pointer: coarse)").matches) {
      navigator.share({ title: data.title, url: url }).catch(function () { /* cancelled */ });
    } else if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(done, function () { prompt("", url); });
    } else {
      prompt("", url);
    }
  });

  // --- Tabs ---------------------------------------------------------------------------------------

  var panels = {
    notes: document.getElementById("panel-notes"),
    sheet: document.getElementById("panel-sheet"),
    quiz: document.getElementById("panel-quiz"),
    cards: document.getElementById("panel-cards")
  };
  var available = {
    notes: true,
    sheet: !!(sheet && Object.keys(sheet).length),
    quiz: questions.length > 0,
    cards: cards.length > 0
  };
  var tabs = root.querySelectorAll("[role=tab]");
  var current = null;

  function select(name, push) {
    if (!available[name]) name = "notes";
    current = name;
    tabs.forEach(function (b) {
      var on = b.dataset.tab === name;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
    Object.keys(panels).forEach(function (k) { panels[k].hidden = k !== name; });
    if (push !== false) history.replaceState(null, "", "#" + name);
  }

  tabs.forEach(function (b, i) {
    b.hidden = !available[b.dataset.tab];
    b.addEventListener("click", function () { select(b.dataset.tab); });
    b.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      var visible = Array.prototype.filter.call(tabs, function (x) { return !x.hidden; });
      var at = visible.indexOf(b) + (e.key === "ArrowRight" ? 1 : -1);
      var next = visible[(at + visible.length) % visible.length];
      next.focus();
      select(next.dataset.tab);
    });
  });

  // --- Notes --------------------------------------------------------------------------------------

  var notesBody = h("div", { class: "md notes-body", html: md.render(data.notes, { headingIds: true }) });
  notesBody.querySelectorAll("h2[data-sec], h3[data-sec]").forEach(function (hd) {
    hd.append(flagButton("section", hd.dataset.sec));
  });
  var tops = sections.filter(function (s) { return s.level === 2; });
  if (tops.length > 2) {
    var toc = h("details", { class: "toc" }, h("summary", { text: t("contents") }));
    var list = h("ol");
    tops.forEach(function (s) {
      list.append(h("li", null, h("a", { href: "#s-" + s.id, html: md.inline(s.heading) })));
    });
    toc.append(list);
    panels.notes.append(toc);
  }
  panels.notes.append(notesBody);
  md.typeset(notesBody);

  function clearMarks(el) {
    el.querySelectorAll("mark").forEach(function (m) { m.replaceWith.apply(m, Array.from(m.childNodes)); });
    el.normalize();
  }

  /** Opens the notes on a section, with the quote highlighted when given. */
  function showInNotes(sectionId, quote) {
    select("notes");
    clearMarks(notesBody);
    var target = document.getElementById("s-" + sectionId);
    var mark = null;
    if (quote) {
      var scope = target ? sectionScope(target) : notesBody;
      mark = md.highlight(scope, quote);
      if (scope !== notesBody) scope.replaceWith.apply(scope, Array.from(scope.childNodes));
    }
    var el = mark || target;
    if (el) el.scrollIntoView({ behavior: "smooth", block: mark ? "center" : "start" });
  }

  /** Wraps a heading's section (up to the next heading of the same or a higher level) to search it alone. */
  function sectionScope(heading) {
    var level = +heading.tagName.charAt(1);
    var wrap = document.createElement("div");
    heading.parentNode.insertBefore(wrap, heading);
    var n = heading;
    do {
      var next = n.nextSibling;
      wrap.appendChild(n);
      n = next;
    } while (n && !(n.nodeType === 1 && /^H[2-6]$/.test(n.tagName) && +n.tagName.charAt(1) <= level));
    return wrap;
  }

  // --- Revision sheet -----------------------------------------------------------------------------

  function renderSheet() {
    var s = sheet || {};
    var out = panels.sheet;
    function heading(key) {
      out.append(h("h2", { class: "sheet-title" }, h("span", { class: "marker-title", text: t(key) })));
    }
    function field(col, label, value) {
      if (value == null || !String(value).trim()) return;
      col.append(mdInline("*" + t(label) + "* : " + value, "p"));
    }
    var ess = (s.essentiel || []).filter(function (e) { return typeof e === "string"; });
    if (ess.length) {
      heading("sheet_essentials");
      var ul = h("ul", { class: "essentials" });
      ess.forEach(function (e, i) { ul.append(h("li", null, mdInline(e), flagButton("sheet", "essentiel." + i))); });
      out.append(ul);
    }
    var defs = s.definitions || [];
    if (defs.length) {
      heading("sheet_definitions");
      var dl = h("dl", { class: "card definitions" });
      defs.forEach(function (d, i) {
        dl.append(h("dt", null, mdInline(d.terme || ""), flagButton("sheet", "definitions." + i)),
          h("dd", null, mdInline(d.definition || "")));
      });
      out.append(dl);
    }
    var blocks = s.structure || [];
    if (blocks.length) {
      heading("sheet_structure");
      blocks.forEach(function (b, i) {
        var col = h("div", { class: "card block block-" + (b.type || "other") },
          h("h3", null, mdInline(b.titre || ""), flagButton("sheet", "structure." + i)));
        var items = b.items || [];
        switch (b.type) {
          case "tableau":
            var cols = b.colonnes || [];
            var tbl = h("table");
            tbl.append(h("thead", null, h("tr", null, cols.map(function (c) { return h("th", null, mdInline(c)); }))));
            var tb = h("tbody");
            items.forEach(function (r) {
              tb.append(h("tr", null, (r.cellules || []).map(function (c) { return h("td", null, mdInline(c)); })));
            });
            tbl.append(tb);
            col.append(h("div", { class: "table-wrap" }, tbl));
            break;
          case "procedures":
            var ol = h("ol", { class: "steps" });
            items.forEach(function (it) {
              var li = h("li", null, mdInline(it.action || "", "p"));
              field(li, "field_trap", it.piege);
              ol.append(li);
            });
            col.append(ol);
            break;
          case "chronologie":
            items.forEach(function (it) {
              col.append(h("p", { class: "item-title", text: it.date || "" }), mdInline(it.evenement || "", "p"));
              field(col, "field_phase", it.phase);
            });
            break;
          case "formules":
            items.forEach(function (it) {
              col.append(h("p", { class: "item-title", text: it.nom || "" }));
              if (it.formule && String(it.formule).trim()) {
                col.append(mdBlock("$$" + String(it.formule).trim() + "$$", "formula"));
              }
              field(col, "field_context", it.contexte);
            });
            break;
          case "schemas":
            items.forEach(function (it) {
              col.append(h("p", { class: "item-title", text: it.nom || "" }));
              field(col, "field_function", it.fonction);
              var keys = (it.elements_cles || []).map(function (e) { return "- " + e; }).join("\n");
              if (keys) col.append(mdBlock(keys));
            });
            break;
          case "regles":
            items.forEach(function (it) {
              col.append(mdInline("**" + (it.regle || "") + "**", "p"));
              field(col, "field_example", it.exemple);
              field(col, "field_exception", it.exception);
            });
            break;
          case "concepts":
            items.forEach(function (it) {
              col.append(h("p", { class: "item-title", text: it.concept || "" }), mdInline(it.definition || "", "p"));
              field(col, "field_link", it.lien);
            });
            break;
          default:
            col.append(h("pre", { text: JSON.stringify(items, null, 2) }));
        }
        out.append(col);
      });
    }
    var traps = s.pieges || [];
    if (traps.length) {
      heading("sheet_pitfalls");
      traps.forEach(function (p, i) {
        out.append(h("div", { class: "card pitfall" },
          h("p", { class: "pitfall-confusion" }, mdInline(p.confusion || ""), flagButton("sheet", "pieges." + i)),
          mdInline(p.precision || "", "p")));
      });
    }
    if (!out.children.length) out.append(h("p", { class: "empty", text: t("no_sheet") }));
  }
  if (available.sheet) renderSheet();

  // --- Sources ------------------------------------------------------------------------------------

  function sourcesOf(item) {
    return (item && item.sources || []).filter(function (s) { return s && (s.section_id || s.quote); });
  }

  function sourcesRow(item) {
    var src = sourcesOf(item);
    if (!src.length) return null;
    var row = h("div", { class: "sources" });
    row.append(button(t("read_passage"), "ghost small", function () { openPassage(src, 0); }, "passage"));
    src.forEach(function (s, i) {
      if (s.section_id) row.append(h("button", { type: "button", class: "chip link", onclick: function () { openPassage(src, i); } }, "§" + s.section_id));
    });
    return row;
  }

  var passageDialog = document.getElementById("passage-dialog");
  var passageTitle = document.getElementById("passage-title");
  var passageBody = document.getElementById("passage-body");
  var passageChips = document.getElementById("passage-chips");
  var passageMissing = document.getElementById("passage-missing");
  var passageInNotes = document.getElementById("passage-in-notes");
  var passageTarget = null;

  function openPassage(src, index) {
    var s = src[index];
    var where = md.locate(sections, s.section_id, s.quote);
    passageChips.textContent = "";
    if (src.length > 1) {
      src.forEach(function (x, i) {
        passageChips.append(h("button", {
          type: "button", class: "chip link" + (i === index ? " active" : ""), "aria-pressed": i === index ? "true" : "false",
          onclick: function () { openPassage(src, i); }
        }, "§" + (x.section_id || "?")));
      });
    }
    passageBody.innerHTML = "";
    var mark = null;
    if (where) {
      passageTitle.textContent = "§" + where.section.id + " · " + where.section.path;
      passageBody.innerHTML = md.render(where.section.fullBody);
      md.typeset(passageBody);
      mark = where.found ? md.highlight(passageBody, s.quote) : null;
      passageTarget = { id: where.section.id, quote: s.quote };
    } else {
      passageTitle.textContent = s.section_id ? "§" + s.section_id : t("passage_eyebrow");
      passageBody.append(h("blockquote", { class: "keypoint" }, mdInline(s.quote || "")));
      passageTarget = null;
    }
    passageMissing.hidden = !!mark;
    passageInNotes.hidden = !passageTarget;
    if (!passageDialog.open) passageDialog.showModal();
    requestAnimationFrame(function () {
      if (mark) mark.scrollIntoView({ block: "center" });
      else passageBody.scrollTop = 0;
    });
  }

  passageInNotes.addEventListener("click", function () {
    passageDialog.close();
    if (passageTarget) showInNotes(passageTarget.id, passageTarget.quote);
  });

  // "[§2.1]" references inside notes, explanations and cards.
  document.addEventListener("click", function (e) {
    var ref = e.target.closest && e.target.closest("a.secref");
    if (!ref) return;
    e.preventDefault();
    if (passageDialog.open) passageDialog.close();
    showInNotes(ref.dataset.sec, null);
  });

  // --- Reports ------------------------------------------------------------------------------------

  var reportDialog = document.getElementById("report-dialog");
  var reportForm = document.getElementById("report-form");
  var reportExcerpt = document.getElementById("report-excerpt");
  var reportStatus = document.getElementById("report-status");
  var reportSend = document.getElementById("report-send");
  var reportTarget = null;

  function excerptFor(kind, id) {
    if (kind === "course") return data.title;
    if (kind === "question") return (questions.filter(function (q) { return q.id === id; })[0] || {}).q;
    if (kind === "card") return (cards.filter(function (c) { return c.id === id; })[0] || {}).front;
    if (kind === "section") {
      var s = sections.filter(function (x) { return x.id === id; })[0];
      return s ? "§" + s.id + " " + s.heading : "";
    }
    if (kind === "sheet") {
      var parts = id.split(".");
      var item = ((sheet || {})[parts[0]] || [])[+parts[1]];
      if (typeof item === "string") return item;
      return item ? item.titre || item.terme || item.confusion || "" : "";
    }
    return "";
  }

  function openReport(kind, id) {
    reportTarget = { kind: kind, id: id };
    reportForm.reset();
    reportExcerpt.textContent = "";
    var excerpt = excerptFor(kind, id);
    if (excerpt) reportExcerpt.append(mdInline(String(excerpt).replace(BLANK, "＿＿＿")));
    reportExcerpt.hidden = !excerpt;
    reportStatus.textContent = "";
    reportStatus.className = "status";
    reportSend.disabled = false;
    reportForm.hidden = false;
    reportDialog.showModal();
    reportForm.elements.message.focus();
  }

  document.addEventListener("click", function (e) {
    var flag = e.target.closest && e.target.closest(".flag, .flag-course");
    if (!flag) return;
    e.preventDefault();
    e.stopPropagation();
    openReport(flag.dataset.kind, flag.dataset.id || "");
  });

  reportForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var f = reportForm.elements;
    reportSend.disabled = true;
    fetch(root.dataset.reportUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        element: reportTarget, message: f.message.value, name: f.name.value, website: f.website.value
      })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error(body.error || r.status);
      });
    }).then(function () {
      reportStatus.textContent = t("report_sent");
      reportStatus.className = "status good";
      setTimeout(function () { if (reportDialog.open) reportDialog.close(); }, 1600);
    }, function (err) {
      reportStatus.textContent = t("report_failed", err.message);
      reportStatus.className = "status bad";
      reportSend.disabled = false;
    });
  });

  // --- Dialog plumbing ----------------------------------------------------------------------------

  document.querySelectorAll("dialog").forEach(function (d) {
    d.addEventListener("click", function (e) {
      if (e.target === d || (e.target.closest && e.target.closest("[data-close]"))) d.close();
    });
  });

  // --- Quiz ---------------------------------------------------------------------------------------

  /** Same normalisation as the app's cloze grading: case, accents, punctuation and spaces ignored. */
  function norm(s) {
    return String(s || "").normalize("NFKC").toLowerCase().normalize("NFD").replace(/\p{M}/gu, "")
      .replace(/[\p{P}\p{S}«»“”’‘\s]+/gu, " ").trim();
  }

  function clozeMatches(q, given) {
    var g = norm(given);
    if (!g) return false;
    return g === norm(q.answer) || (q.accept || []).some(function (a) { return g === norm(a); });
  }

  var quiz = null;

  function quizSetup() {
    var out = panels.quiz;
    out.textContent = "";
    var d = progress.load(shareId);
    var missed = questions.filter(function (q) { return d.q[q.id] && d.q[q.id][2]; });
    var sizes = [5, 10, 20].filter(function (n) { return n < questions.length; });
    var chosen = sizes.indexOf(10) >= 0 ? 10 : (sizes[sizes.length - 1] || questions.length);
    var choice = h("div", { class: "segmented", role: "radiogroup", "aria-label": t("quiz_length") });
    sizes.concat([questions.length]).forEach(function (n) {
      var label = n === questions.length ? t("quiz_all") + " (" + n + ")" : String(n);
      var b = h("button", {
        type: "button", role: "radio", "aria-checked": n === chosen ? "true" : "false",
        onclick: function () {
          chosen = n;
          choice.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-checked", x === b ? "true" : "false"); });
        }
      }, label);
      choice.append(b);
    });
    var intro = h("div", { class: "card intro" },
      h("p", { text: t("quiz_intro", questions.length) }),
      h("p", { class: "label", text: t("quiz_length") }), choice,
      h("div", { class: "actions" },
        button(t("quiz_start"), "", function () { startQuiz(pick(chosen, false)); }),
        missed.length ? button(t("quiz_mistakes", missed.length), "ghost", function () { startQuiz(pick(missed.length, true)); }) : null));
    out.append(intro);
    if (d.quizzes.length) {
      var hist = h("ol", { class: "history" });
      d.quizzes.slice(-8).reverse().forEach(function (z) {
        var pct = Math.round(100 * z.score / Math.max(1, z.total));
        hist.append(h("li", { class: pct >= 70 ? "good" : "bad" },
          h("span", { text: new Date(z.at).toLocaleDateString(FC.lang) }),
          h("strong", { text: t("quiz_score", z.score, z.total) + " · " + pct + " %" })));
      });
      out.append(h("h3", { class: "label", text: t("quiz_history") }), hist);
    }
  }

  /** Missed last time first, then never asked, then the rest; random within each group. */
  function pick(n, onlyMissed) {
    var d = progress.load(shareId);
    function rank(q) {
      var s = d.q[q.id];
      return s && s[2] ? 0 : !s ? 1 : 2;
    }
    var pool = shuffle(questions.filter(function (q) { return !onlyMissed || rank(q) === 0; }));
    pool.sort(function (a, b) { return rank(a) - rank(b); });
    return shuffle(pool.slice(0, n));
  }

  function startQuiz(list) {
    quiz = { list: list, i: 0, results: [] };
    renderQuestion();
    panels.quiz.scrollIntoView({ block: "start" });
  }

  function renderQuestion() {
    var out = panels.quiz;
    out.textContent = "";
    var q = quiz.list[quiz.i];
    var n = quiz.list.length;
    var bar = h("div", { class: "progressbar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(n), "aria-valuenow": String(quiz.i) },
      h("span", { style: "width:" + (100 * quiz.i / n) + "%" }));
    out.append(h("div", { class: "quiz-head" },
      h("span", { class: "label", text: t("quiz_progress", quiz.i + 1, n) }),
      button(t("quiz_quit"), "ghost small", function () { quiz = null; quizSetup(); })), bar);

    var card = h("div", { class: "card question q-" + q.type });
    var stemHtml = md.inline(q.q).replace(/＿+|_{2,}/, '<span class="blank" data-blank>   </span>');
    var stem = h("div", { class: "stem", html: stemHtml });
    md.typeset(stem);
    card.append(h("div", { class: "question-top" }, stem, flagButton("question", q.id)));
    var body = h("div", { class: "answer-area" });
    var feedback = h("div", { class: "feedback", hidden: true, "aria-live": "polite" });
    card.append(body, feedback);
    out.append(card);

    function finish(right, detail) {
      quiz.results.push({ q: q, right: right });
      progress.answer(shareId, q.id, right);
      feedback.hidden = false;
      feedback.className = "feedback " + (right ? "right" : "wrong");
      feedback.append(h("p", { class: "verdict" }, icon(right ? "check" : "x"), right ? t("quiz_right") : t("quiz_wrong")));
      if (detail) feedback.append(detail);
      if (q.explanation) feedback.append(mdBlock(q.explanation, "explanation"));
      var src = sourcesRow(q);
      if (src) feedback.append(src);
      var last = quiz.i === n - 1;
      var next = button(last ? t("quiz_finish") : t("quiz_next"), "next", function () {
        quiz.i++;
        if (quiz.i < n) renderQuestion(); else quizEnd();
      });
      feedback.append(h("div", { class: "actions" }, next));
      next.focus({ preventScroll: true });
      feedback.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    var renderers = {
      mcq: function () {
        var order = shuffle(range(q.answers.length));
        var list = h("div", { class: "options" });
        order.forEach(function (idx, pos) {
          var opt = h("button", { type: "button", class: "option", onclick: function () {
            list.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
            opt.classList.add("chosen");
            list.children[order.indexOf(q.correct)].classList.add("right");
            if (idx !== q.correct) opt.classList.add("wrong");
            finish(idx === q.correct);
          } }, h("span", { class: "letter", text: String.fromCharCode(65 + pos) }), mdInline(q.answers[idx]));
          list.append(opt);
        });
        body.append(list);
      },
      multi: function () {
        var order = shuffle(range(q.options.length));
        var picked = {};
        body.append(h("p", { class: "hint", text: t("quiz_select_all") }));
        var list = h("div", { class: "options" });
        var check = button(t("quiz_check"), "", function () {
          check.disabled = true;
          var right = true;
          order.forEach(function (idx, pos) {
            var b = list.children[pos];
            b.disabled = true;
            var should = q.correct.indexOf(idx) >= 0;
            if (should) b.classList.add(picked[idx] ? "right" : "missed");
            else if (picked[idx]) b.classList.add("wrong");
            if (should !== !!picked[idx]) right = false;
          });
          finish(right);
        });
        check.disabled = true;
        order.forEach(function (idx) {
          var opt = h("button", { type: "button", class: "option check", "aria-pressed": "false", onclick: function () {
            picked[idx] = !picked[idx];
            opt.setAttribute("aria-pressed", picked[idx] ? "true" : "false");
            check.disabled = !Object.keys(picked).some(function (k) { return picked[k]; });
          } }, h("span", { class: "box" }, icon("check")), mdInline(q.options[idx]));
          list.append(opt);
        });
        body.append(list, h("div", { class: "actions" }, check));
      },
      true_false: function () {
        var list = h("div", { class: "options two" });
        [true, false].forEach(function (v) {
          var opt = h("button", { type: "button", class: "option", onclick: function () {
            list.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
            opt.classList.add("chosen");
            list.children[q.isTrue ? 0 : 1].classList.add("right");
            if (v !== q.isTrue) opt.classList.add("wrong");
            finish(v === q.isTrue);
          } }, v ? t("quiz_true") : t("quiz_false"));
          list.append(opt);
        });
        body.append(list);
      },
      cloze: function () {
        var blank = stem.querySelector("[data-blank]");
        var choices = shuffle([q.answer].concat(q.bank || []).filter(function (x, i, a) { return a.indexOf(x) === i; }));
        body.append(h("p", { class: "hint", text: t("quiz_cloze_hint") }));
        var chips = h("div", { class: "word-bank" });
        var input = h("input", { type: "text", class: "cloze-input", placeholder: t("quiz_cloze_type"), "aria-label": t("quiz_cloze_type"), autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
        var check = button(t("quiz_check"), "", function () { grade(input.value, null); });
        input.addEventListener("keydown", function (e) { if (e.key === "Enter" && input.value.trim()) grade(input.value, null); });
        function grade(given, chip) {
          var right = clozeMatches(q, given);
          chips.querySelectorAll("button").forEach(function (b) {
            b.disabled = true;
            if (clozeMatches(q, b.textContent)) b.classList.add("right");
          });
          if (chip && !right) chip.classList.add("wrong");
          input.disabled = true;
          check.disabled = true;
          if (blank) {
            blank.textContent = q.answer;
            blank.classList.add(right ? "right" : "wrong");
          }
          finish(right, right ? null : h("p", { class: "correct-answer", text: t("quiz_correct_answer", q.answer) }));
        }
        choices.forEach(function (c) {
          var chip = h("button", { type: "button", class: "chip word", onclick: function () { grade(c, chip); } }, c);
          chips.append(chip);
        });
        body.append(chips, h("div", { class: "cloze-type" }, input, check));
      },
      order: function () {
        var cur = shuffle(range(q.items.length));
        if (cur.every(function (v, i) { return v === i; })) cur.reverse();
        body.append(h("p", { class: "hint", text: t("quiz_order_hint") }));
        var list = h("ol", { class: "order-list" });
        function draw() {
          list.textContent = "";
          cur.forEach(function (idx, pos) {
            list.append(h("li", { class: "order-item" },
              mdInline(q.items[idx], "span"),
              h("span", { class: "order-moves" },
                h("button", { type: "button", class: "icon-button", "aria-label": t("move_up"), disabled: pos === 0, onclick: function () { move(pos, -1); } }, icon("up")),
                h("button", { type: "button", class: "icon-button", "aria-label": t("move_down"), disabled: pos === cur.length - 1, onclick: function () { move(pos, 1); } }, icon("down")))));
          });
        }
        function move(pos, delta) {
          var x = cur[pos]; cur[pos] = cur[pos + delta]; cur[pos + delta] = x;
          draw();
          var b = list.children[pos + delta].querySelectorAll("button")[delta < 0 ? 0 : 1];
          if (b && !b.disabled) b.focus(); else list.children[pos + delta].querySelector("button:not([disabled])").focus();
        }
        draw();
        var check = button(t("quiz_check"), "", function () {
          check.disabled = true;
          var right = cur.every(function (v, i) { return v === i; });
          list.querySelectorAll(".order-item").forEach(function (li, pos) {
            li.classList.add(cur[pos] === pos ? "right" : "wrong");
            li.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
          });
          var detail = null;
          if (!right) {
            detail = h("div", { class: "correct-answer" }, h("p", { text: t("quiz_correct_order") }),
              h("ol", null, q.items.map(function (it) { return h("li", null, mdInline(it)); })));
          }
          finish(right, detail);
        });
        body.append(list, h("div", { class: "actions" }, check));
      }
    };
    (renderers[q.type] || renderers.mcq)();
  }

  function quizEnd() {
    var out = panels.quiz;
    out.textContent = "";
    var score = quiz.results.filter(function (r) { return r.right; }).length;
    var total = quiz.results.length;
    var pct = Math.round(100 * score / Math.max(1, total));
    progress.quizDone(shareId, score, total);
    var missed = quiz.results.filter(function (r) { return !r.right; }).map(function (r) { return r.q; });
    out.append(h("div", { class: "card score " + (pct >= 70 ? "good" : "bad") },
      h("p", { class: "big", text: t("quiz_score", score, total) }),
      h("p", { class: "pct", text: pct + " %" }),
      h("p", { text: pct >= 70 ? t("quiz_passed") : t("quiz_failed") }),
      h("div", { class: "actions" },
        missed.length ? button(t("quiz_retry_missed", missed.length), "", function () { startQuiz(shuffle(missed.slice())); }) : null,
        button(t("quiz_new"), missed.length ? "ghost" : "", function () { quiz = null; quizSetup(); }))));
    var list = h("ol", { class: "review-list" });
    quiz.results.forEach(function (r) {
      list.append(h("li", { class: r.right ? "right" : "wrong" },
        icon(r.right ? "check" : "x"),
        h("div", null, mdInline(r.q.q.replace(BLANK, "＿＿＿"), "p"), sourcesRow(r.q))));
    });
    out.append(h("h3", { class: "label", text: t("quiz_review") }), list);
  }

  if (available.quiz) quizSetup();

  // --- Flashcards ---------------------------------------------------------------------------------

  var deck = null;

  function cardsSetup() {
    var out = panels.cards;
    out.textContent = "";
    var d = progress.load(shareId);
    var known = cards.filter(function (c) { return d.c[c.id] && d.c[c.id][1]; }).length;
    var learning = cards.filter(function (c) { return !(d.c[c.id] && d.c[c.id][1]); });
    var seenAny = cards.some(function (c) { return d.c[c.id]; });
    out.append(h("div", { class: "card intro" },
      h("p", { text: t("cards_intro", cards.length) }),
      seenAny ? h("div", { class: "meter" },
        h("span", { class: "meter-bar" }, h("span", { style: "width:" + (100 * known / cards.length) + "%" })),
        h("span", { class: "label", text: t("cards_stats", known, cards.length) })) : null,
      h("div", { class: "actions" },
        button(t("cards_start"), "", function () { startDeck(cards.slice()); }),
        seenAny && learning.length && learning.length < cards.length
          ? button(t("cards_review", learning.length), "ghost", function () { startDeck(learning); }) : null)));
  }

  function startDeck(list) {
    deck = { list: list, i: 0, known: 0, unknown: [] };
    renderCard();
  }

  function renderCard() {
    var out = panels.cards;
    out.textContent = "";
    var c = deck.list[deck.i];
    var n = deck.list.length;
    out.append(h("div", { class: "quiz-head" },
      h("span", { class: "label", text: t("cards_progress", deck.i + 1, n) }),
      button(t("quiz_quit"), "ghost small", function () { deck = null; cardsSetup(); })),
      h("div", { class: "progressbar" }, h("span", { style: "width:" + (100 * deck.i / n) + "%" })));

    var front = h("div", { class: "face front" }, mdBlock(c.front));
    var back = h("div", { class: "face back", hidden: true }, mdBlock(c.back));
    var flip = h("button", { type: "button", class: "flashcard", "aria-expanded": "false" }, front, back,
      h("span", { class: "flip-hint" }, icon("flip"), t("cards_flip")));
    var after = h("div", { class: "card-after", hidden: true });
    out.append(h("div", { class: "flash-wrap" }, flip, flagButton("card", c.id)), after);

    function reveal() {
      if (!back.hidden) return;
      back.hidden = false;
      flip.classList.add("flipped");
      flip.setAttribute("aria-expanded", "true");
      after.hidden = false;
      var src = sourcesRow(c);
      after.append(src || "", h("div", { class: "actions grade" },
        button(t("cards_unknown"), "ghost bad-text", function () { grade(false); }, "x"),
        button(t("cards_known"), "good", function () { grade(true); }, "check")));
      after.querySelector(".grade .good").focus({ preventScroll: true });
    }
    function grade(knew) {
      progress.card(shareId, c.id, knew);
      if (knew) deck.known++; else deck.unknown.push(c);
      deck.i++;
      if (deck.i < n) renderCard(); else deckEnd();
    }
    flip.addEventListener("click", reveal);
    flip.focus({ preventScroll: true });
  }

  function deckEnd() {
    var out = panels.cards;
    out.textContent = "";
    var again = deck.unknown.slice();
    out.append(h("div", { class: "card score" },
      h("p", { class: "big", text: t("cards_done") }),
      h("p", { text: t("cards_done_detail", deck.known, again.length) }),
      h("div", { class: "actions" },
        again.length ? button(t("cards_again", again.length), "", function () { startDeck(again); }) : null,
        button(t("cards_restart"), again.length ? "ghost" : "", function () { deck = null; cardsSetup(); }))));
  }

  if (available.cards) cardsSetup();

  // Refresh the start screens when progress is turned on or erased.
  progress.onChange(function (id) {
    if (id !== null) return;
    if (available.quiz && !quiz) quizSetup();
    if (available.cards && !deck) cardsSetup();
  });

  // --- Start --------------------------------------------------------------------------------------

  function fromHash() {
    var hash = location.hash.slice(1);
    if (hash.indexOf("s-") === 0) {
      select("notes", false);
      var el = document.getElementById(hash);
      if (el) el.scrollIntoView();
      return;
    }
    select(panels[hash] ? hash : (current || "notes"), false);
  }
  window.addEventListener("hashchange", fromHash);
  fromHash();
})();
