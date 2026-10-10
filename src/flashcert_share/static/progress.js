/*
 * Visitor progress (quiz answers, known flashcards), kept in this browser only and only once the visitor
 * agrees. Until then it lives in memory for the visit. Turning it on also asks the browser for persistent
 * storage, so the data isn't evicted when the device runs low on space.
 */
(function () {
  "use strict";
  var t = window.FC.t;
  var ON = "fcs:on";
  var PREFIX = "fcs:course:";
  var DISMISSED = "fcs:banner-dismissed";
  var mem = {};
  var listeners = [];

  var ls = (function () {
    try {
      var s = window.localStorage;
      s.setItem("fcs:probe", "1");
      s.removeItem("fcs:probe");
      return s;
    } catch (e) {
      return null;
    }
  })();

  function enabled() {
    try { return !!ls && ls.getItem(ON) === "1"; } catch (e) { return false; }
  }

  function clean(d) {
    d = d && typeof d === "object" ? d : {};
    return {
      q: d.q && typeof d.q === "object" ? d.q : {},
      c: d.c && typeof d.c === "object" ? d.c : {},
      quizzes: Array.isArray(d.quizzes) ? d.quizzes : []
    };
  }

  function load(id) {
    if (mem[id]) return mem[id];
    var d = null;
    if (enabled()) {
      try { d = JSON.parse(ls.getItem(PREFIX + id)); } catch (e) { d = null; }
    }
    return (mem[id] = clean(d));
  }

  function write(id) {
    if (!enabled()) return;
    try { ls.setItem(PREFIX + id, JSON.stringify(mem[id])); } catch (e) { /* quota or blocked */ }
  }

  function changed(id) {
    write(id);
    if (!enabled()) maybeBanner();
    listeners.forEach(function (fn) { fn(id); });
  }

  /** One answered question: [asked, missed, last answer wrong (0/1)]. */
  function answer(id, qid, right) {
    var d = load(id);
    var s = d.q[qid] || [0, 0, 0];
    d.q[qid] = [s[0] + 1, s[1] + (right ? 0 : 1), right ? 0 : 1];
    changed(id);
  }

  /** One flashcard seen: [seen, known (0/1)]. */
  function card(id, cid, known) {
    var d = load(id);
    var s = d.c[cid] || [0, 0];
    d.c[cid] = [s[0] + 1, known ? 1 : 0];
    changed(id);
  }

  function quizDone(id, score, total) {
    var d = load(id);
    d.quizzes.push({ at: Date.now(), score: score, total: total });
    if (d.quizzes.length > 30) d.quizzes = d.quizzes.slice(-30);
    changed(id);
  }

  function savedIds() {
    var out = [];
    if (!ls) return out;
    for (var i = 0; i < ls.length; i++) {
      var k = ls.key(i);
      if (k && k.indexOf(PREFIX) === 0) out.push(k.slice(PREFIX.length));
    }
    return out;
  }

  function enable() {
    if (!ls) return Promise.resolve("blocked");
    try { ls.setItem(ON, "1"); } catch (e) { return Promise.resolve("blocked"); }
    Object.keys(mem).forEach(write);
    hideBanner();
    listeners.forEach(function (fn) { fn(null); });
    if (!navigator.storage || !navigator.storage.persist) return Promise.resolve("unavailable");
    return navigator.storage.persist().then(function (ok) { return ok ? "persisted" : "best_effort"; },
      function () { return "best_effort"; });
  }

  function forget() {
    if (ls) {
      savedIds().forEach(function (id) { ls.removeItem(PREFIX + id); });
      ls.removeItem(ON);
    }
    mem = {};
    listeners.forEach(function (fn) { fn(null); });
  }

  // --- Banner: shown once something worth saving happened, until saved or dismissed for the visit ---

  var banner = null;

  function dismissed() {
    try { return sessionStorage.getItem(DISMISSED) === "1"; } catch (e) { return false; }
  }

  function maybeBanner() {
    if (banner || enabled() || dismissed() || !ls) return;
    banner = document.createElement("div");
    banner.className = "save-banner";
    banner.setAttribute("role", "status");
    var text = document.createElement("span");
    text.textContent = t("progress_banner");
    var more = document.createElement("button");
    more.type = "button";
    more.className = "btn small";
    more.textContent = t("progress_banner_more");
    more.addEventListener("click", openDialog);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "icon-button";
    close.setAttribute("aria-label", t("not_now"));
    close.innerHTML = '<svg class="icon"><use href="#i-x"/></svg>';
    close.addEventListener("click", function () {
      try { sessionStorage.setItem(DISMISSED, "1"); } catch (e) { /* ignore */ }
      hideBanner();
    });
    banner.append(text, more, close);
    document.body.appendChild(banner);
  }

  function hideBanner() {
    if (banner) banner.remove();
    banner = null;
  }

  // --- Dialog ---

  var dialog = document.getElementById("progress-dialog");
  var status = document.getElementById("progress-status");
  var enableBtn = document.getElementById("progress-enable");
  var forgetBtn = document.getElementById("progress-forget");
  var laterBtn = document.getElementById("progress-later");

  function humanBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }

  function refreshDialog(message) {
    var on = enabled();
    enableBtn.hidden = on || !ls;
    forgetBtn.hidden = !on;
    laterBtn.textContent = on ? t("close") : t("not_now");
    status.textContent = message || "";
    if (!ls) {
      status.textContent = t("progress_blocked");
    } else if (on && !message) {
      var parts = [];
      var p = navigator.storage && navigator.storage.persisted ? navigator.storage.persisted() : Promise.resolve(null);
      p.then(function (persisted) {
        parts.push(persisted === true ? t("progress_on_persisted")
          : persisted === false ? t("progress_on_best_effort") : t("progress_on_unavailable"));
        return navigator.storage && navigator.storage.estimate ? navigator.storage.estimate() : null;
      }).then(function (est) {
        if (est && est.usage != null) parts.push(t("progress_used", humanBytes(est.usage)));
        status.textContent = parts.join(" ");
      }).catch(function () { /* leave as is */ });
    }
  }

  function openDialog() {
    refreshDialog();
    if (!dialog.open) dialog.showModal();
  }

  enableBtn.addEventListener("click", function () {
    enable().then(function (result) {
      refreshDialog(result === "blocked" ? t("progress_blocked") : t("progress_on_" + result));
    });
  });
  forgetBtn.addEventListener("click", function () {
    forget();
    refreshDialog(t("progress_forgotten"));
  });
  laterBtn.addEventListener("click", function () {
    if (!enabled()) {
      try { sessionStorage.setItem(DISMISSED, "1"); } catch (e) { /* ignore */ }
      hideBanner();
    }
  });
  dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });

  var opener = document.getElementById("progress-open");
  opener.hidden = false;
  opener.addEventListener("click", openDialog);

  window.FC.progress = {
    enabled: enabled,
    load: load,
    answer: answer,
    card: card,
    quizDone: quizDone,
    open: openDialog,
    onChange: function (fn) { listeners.push(fn); }
  };
})();
