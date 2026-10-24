/*
 * Visitor progress, kept in this browser only and only once the visitor agrees. Until then it lives in memory for the
 * visit. Turning it on also asks the browser for persistent storage, so the data isn't evicted when the device runs
 * low on space.
 *
 * As in the app (ItemReview), each question and card counts its successes in a row, at most one per study day; a
 * mistake starts the count again. An item that reaches its target and isn't recovering from a mistake is mastered,
 * and a course is mastered when all its items are. Each answer also marks the study day, for the day streak.
 */
(function () {
  "use strict";
  var t = window.FC.t;
  var ON = "fcs:on";
  var PREFIX = "fcs:course:";
  var DAYS = "fcs:days";
  var CERT = "fcs:cert:";
  var DISMISSED = "fcs:banner-dismissed";
  var VERSION = 2;
  /** Same defaults as the app: 3 successes in a row, study days starting at 4 a.m. */
  var TARGET = 3, DAY_START = 4, MAX_DAYS = 800;
  var GAPS = [1, 3, 7, 16, 35];
  var mem = {};
  var days = null;
  var certs = {};
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

  // --- Study days ---------------------------------------------------------------------------------

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function format(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }

  /** The study day of a moment: answers before 4 a.m. count for the day before, so a late session stays on one day. */
  function studyDay(at) {
    var d = new Date(at == null ? Date.now() : at);
    d.setHours(d.getHours() - DAY_START);
    return format(d);
  }

  function addDays(day, n) {
    var p = day.split("-");
    return format(new Date(+p[0], +p[1] - 1, +p[2] + n));
  }

  // --- One item: [encounters, streak, lapses, last study day, last answer right (0/1), interval in days] ---------

  function state(s) {
    return Array.isArray(s) && s.length === 6 ? s : [0, 0, 0, null, 0, 0];
  }

  /** Retries the same study day change the latest result but earn no extra success in a row. */
  function next(before, right, day) {
    var spaced = before[3] == null || day > before[3];
    var streak = right ? before[1] + (spaced ? 1 : 0) : 0;
    var gap = streak <= 0 ? 1 : streak <= GAPS.length ? GAPS[streak - 1] : Math.min(90, Math.max(1, before[5] * 2));
    if (right && !spaced) gap = Math.max(1, before[5]);
    return [before[0] + (spaced ? 1 : 0), streak, before[2] + (right ? 0 : 1), spaced ? day : before[3], right ? 1 : 0, gap];
  }

  function recovering(s) { return s[0] > 0 && (!s[4] || (s[2] > 0 && s[1] < 2)); }

  function mastered(s, target) { return target > 0 && s[1] >= target && !recovering(s); }

  function due(s, today) { return s[3] == null || addDays(s[3], Math.max(1, s[5])) <= today; }

  /** Recovering first, then never seen, then due, then the rest: the app's order of review. */
  function priority(s, today) { return recovering(s) ? 0 : s[0] === 0 ? 1 : due(s, today) ? 2 : 3; }

  // --- Courses ------------------------------------------------------------------------------------

  function entries(raw, upgrade) {
    var out = {};
    if (!raw || typeof raw !== "object") return out;
    Object.keys(raw).forEach(function (k) {
      var s = raw[k];
      if (!Array.isArray(s)) return;
      s = upgrade ? upgrade(s) : s;
      if (s.length === 6) out[k] = s;
    });
    return out;
  }

  // Before successes in a row, questions kept [asked, missed, last wrong] and cards [seen, known], without days:
  // their counts stay, their streaks start again.
  function fromAnswers(s) { return [s[0] || 0, 0, s[1] || 0, null, s[2] ? 0 : 1, 0]; }
  function fromGrades(s) { return [s[0] || 0, 0, 0, null, s[1] ? 1 : 0, 0]; }

  function clean(d) {
    d = d && typeof d === "object" ? d : {};
    var old = d.v !== VERSION;
    return {
      v: VERSION,
      q: entries(d.q, old ? fromAnswers : null),
      c: entries(d.c, old ? fromGrades : null),
      quizzes: Array.isArray(d.quizzes) ? d.quizzes : [],
      sum: d.sum && typeof d.sum === "object" ? d.sum : null
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

  function loadDays() {
    if (days) return days;
    var saved = null;
    if (enabled()) {
      try { saved = JSON.parse(ls.getItem(DAYS)); } catch (e) { saved = null; }
    }
    days = Array.isArray(saved) ? saved.filter(function (x) { return typeof x === "string"; }) : [];
    return days;
  }

  function writeDays() {
    if (!enabled()) return;
    try { ls.setItem(DAYS, JSON.stringify(days)); } catch (e) { /* quota or blocked */ }
  }

  function studied(day) {
    var list = loadDays();
    if (list[list.length - 1] === day) return;
    if (list.indexOf(day) < 0) {
      list.push(day);
      list.sort();
      if (list.length > MAX_DAYS) list.splice(0, list.length - MAX_DAYS);
    }
    writeDays();
  }

  /** Consecutive study days ending today, or yesterday while today isn't done yet. */
  function streak() {
    var set = {};
    loadDays().forEach(function (x) { set[x] = true; });
    var day = studyDay();
    if (!set[day]) day = addDays(day, -1);
    var n = 0;
    while (set[day]) { n++; day = addDays(day, -1); }
    return n;
  }

  /** One answer or grade; returns the item before and after, so the page can tell a success that masters it. */
  function record(id, kind, itemId, right) {
    var d = load(id);
    var day = studyDay();
    var before = state(d[kind][itemId]);
    var after = next(before, right, day);
    d[kind][itemId] = after;
    studied(day);
    changed(id);
    return { before: before, after: after };
  }

  function answer(id, qid, right) { return record(id, "q", qid, right); }

  function card(id, cid, known) { return record(id, "c", cid, known); }

  function quizDone(id, score, total) {
    var d = load(id);
    d.quizzes.push({ at: Date.now(), score: score, total: total });
    if (d.quizzes.length > 30) d.quizzes = d.quizzes.slice(-30);
    changed(id);
  }

  /** Targets of a course: its author's app sends its certification's; otherwise the app's defaults. */
  function targets(raw) {
    raw = raw && typeof raw === "object" ? raw : {};
    var q = raw.questions, c = raw.cards;
    return {
      questions: typeof q === "number" && q >= 1 ? Math.floor(q) : TARGET,
      cards: typeof c === "number" && c >= 0 ? Math.floor(c) : TARGET
    };
  }

  /**
   * Where a course stands: items counted (cards only with a target), items mastered, successes in a row earned
   * against the sum of targets. Kept with the course, so the course lists show it without the questions at hand;
   * qn and cn tell them when the course has changed since.
   */
  function summary(id, questionIds, cardIds, goal) {
    var d = load(id);
    var out = { items: 0, mastered: 0, done: 0, target: 0, qn: questionIds.length, cn: cardIds.length };
    function add(kind, list, target) {
      if (target <= 0) return;
      list.forEach(function (itemId) {
        var s = state(d[kind][itemId]);
        out.items++;
        out.target += target;
        out.done += Math.min(target, s[1]);
        if (mastered(s, target)) out.mastered++;
      });
    }
    add("q", questionIds, goal.questions);
    add("c", cardIds, goal.cards);
    var started = Object.keys(d.q).length || Object.keys(d.c).length;
    if (started && JSON.stringify(out) !== JSON.stringify(d.sum)) {
      d.sum = out;
      write(id);
    }
    return out;
  }

  function courseMastered(sum) { return !!sum && sum.items > 0 && sum.mastered === sum.items; }

  /** How many courses a certification had when its "every course mastered" moment last showed; 0 before. */
  function certCelebrated(certId) {
    if (certs[certId] != null) return certs[certId];
    var n = 0;
    if (enabled()) {
      try { n = parseInt(ls.getItem(CERT + certId), 10) || 0; } catch (e) { n = 0; }
    }
    return (certs[certId] = n);
  }

  function setCertCelebrated(certId, courses) {
    certs[certId] = courses;
    if (!enabled()) return;
    try { ls.setItem(CERT + certId, String(courses)); } catch (e) { /* quota or blocked */ }
  }

  function savedKeys() {
    var out = [];
    if (!ls) return out;
    for (var i = 0; i < ls.length; i++) {
      var k = ls.key(i);
      if (k && (k.indexOf(PREFIX) === 0 || k.indexOf(CERT) === 0 || k === DAYS)) out.push(k);
    }
    return out;
  }

  function enable() {
    if (!ls) return Promise.resolve("blocked");
    try { ls.setItem(ON, "1"); } catch (e) { return Promise.resolve("blocked"); }
    Object.keys(mem).forEach(write);
    if (days) writeDays();
    Object.keys(certs).forEach(function (id) { if (certs[id]) setCertCelebrated(id, certs[id]); });
    hideBanner();
    listeners.forEach(function (fn) { fn(null); });
    if (!navigator.storage || !navigator.storage.persist) return Promise.resolve("unavailable");
    return navigator.storage.persist().then(function (ok) { return ok ? "persisted" : "best_effort"; },
      function () { return "best_effort"; });
  }

  function forget() {
    if (ls) {
      savedKeys().forEach(function (k) { ls.removeItem(k); });
      ls.removeItem(ON);
    }
    mem = {};
    days = null;
    certs = {};
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
    today: studyDay,
    item: function (id, kind, itemId) { return state(load(id)[kind][itemId]); },
    recovering: recovering,
    mastered: mastered,
    priority: priority,
    targets: targets,
    summary: summary,
    courseMastered: courseMastered,
    streak: streak,
    daysStudied: function () { return loadDays().length; },
    certCelebrated: certCelebrated,
    setCertCelebrated: setCertCelebrated,
    open: openDialog,
    onChange: function (fn) { listeners.push(fn); }
  };
})();
