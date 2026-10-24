/*
 * The visitor's progress across courses, as the app's progress screen shows it: Now (figures), Past (answers per day,
 * courses by certification) and Future (reviews to come). Everything comes from this browser: a course shows up once
 * it has been answered on its page, under the title it had then.
 */
(function () {
  "use strict";
  var t = FC.t, progress = FC.progress, S = FC.stats, h = S.h;
  var out = document.getElementById("stats-page");

  function render() {
    out.textContent = "";
    var all = progress.courses();
    var ids = Object.keys(all);
    if (!ids.length) {
      out.append(h("p", "empty", t("stats_empty")));
      if (!progress.enabled()) out.append(notSaved());
      return;
    }
    var today = progress.today();
    var logs = ids.map(function (id) { return all[id].log; });
    var courses = ids.map(function (id) {
      var d = all[id], meta = d.meta || {}, targets = progress.targets(meta.targets);
      var states = [], due = 0, last = null;
      function add(kind, target) {
        Object.keys(d[kind]).forEach(function (k) {
          var s = d[kind][k];
          if (s[3] && (!last || s[3] > last)) last = s[3];
          if (target <= 0) return;
          states.push(s);
          if (s[0] > 0 && (progress.recovering(s) || progress.due(s, today))) due++;
        });
      }
      add("q", targets.questions);
      add("c", targets.cards);
      // Items never answered have no state here: as many as the course counted come today, as on its page.
      var unseen = d.sum ? Math.max(0, d.sum.items - states.length) : 0;
      for (var i = 0; i < unseen; i++) states.push(progress.unseen());
      return { id: id, title: meta.title || id, cert: meta.cert || null, path: meta.path || "/c/" + id, sum: d.sum,
        states: states, due: due, last: last };
    });

    // Now
    var counted = courses.filter(function (c) { return c.sum && c.sum.items; });
    var mastered = counted.filter(function (c) { return progress.courseMastered(c.sum); }).length;
    var items = counted.reduce(function (a, c) { return a + c.sum.mastered; }, 0);
    out.append(S.group("progress_now"));
    out.append(S.tiles(S.answerTiles(logs).concat([
      { value: counted.length ? mastered + "/" + counted.length : null, label: t("stat_courses") },
      { value: items, label: t("stat_mastered") },
      { value: progress.streak(), label: t("stat_streak") },
      { value: progress.daysStudied(), label: t("stat_days") }])));
    if (!progress.enabled()) out.append(notSaved());

    // Past
    out.append(S.group("progress_past"));
    var days = S.series(logs, S.WINDOW);
    var answered = days.reduce(function (a, x) { return a + x.n; }, 0);
    var right = days.reduce(function (a, x) { return a + x.right; }, 0);
    var daily = S.section(t("daily_title"), answered ? t("daily_total", answered, S.pct(right, answered)) : t("daily_total_none"));
    daily.append(S.bars(days));
    out.append(daily);

    var groups = {};
    courses.forEach(function (c) { (groups[c.cert || ""] = groups[c.cert || ""] || []).push(c); });
    Object.keys(groups).sort(function (a, b) { return !a - !b || a.localeCompare(b); }).forEach(function (cert) {
      var box = S.section(cert || (Object.keys(groups).length > 1 ? t("stats_other") : t("stats_courses")));
      box.append(S.rows(groups[cert].sort(function (a, b) { return a.title.localeCompare(b.title); }).map(function (c) {
        var sum = c.sum || { items: 0, mastered: 0, done: 0, target: 0 };
        var done = progress.courseMastered(sum);
        return {
          label: c.title, href: c.path,
          detail: done ? t("progress_course_mastered") : t("stats_course_row", sum.mastered, sum.items, c.due),
          fraction: sum.target ? sum.done / sum.target : 0, cls: done ? "done" : "",
          note: c.last ? t("stats_last", lastDay(c.last)) : null
        };
      })));
      out.append(box);
    });

    // Future
    out.append(S.group("progress_future"));
    var states = [];
    courses.forEach(function (c) { states = states.concat(c.states); });
    var ahead = S.upcomingDays(states, S.AHEAD);
    var coming = S.section(t("upcoming_title"), t("upcoming_total", ahead.reduce(function (a, x) { return a + x.n; }, 0)));
    coming.append(S.bars(ahead, true), h("p", "hint", t("upcoming_note")));
    out.append(coming);
  }

  function lastDay(day) {
    var p = day.split("-");
    return new Date(+p[0], +p[1] - 1, +p[2]).toLocaleDateString(FC.lang, { day: "numeric", month: "long" });
  }

  function notSaved() {
    var b = h("button", "btn small ghost", t("progress_banner_more"));
    b.type = "button";
    b.addEventListener("click", progress.open);
    return h("div", "card stats-card", null, h("p", "hint", t("stats_not_saved")), b);
  }

  render();
  progress.onChange(render);
})();
