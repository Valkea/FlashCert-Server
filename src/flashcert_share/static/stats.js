/*
 * Progress figures and charts, as in the app's progress screen: Now (figures with their change), Past (the daily
 * answers, rows of bars) and Future (reviews to come). Shared by a course's Progress tab and the progress page; both
 * read only what this browser keeps (static/progress.js).
 */
(function () {
  "use strict";
  var t = window.FC.t, progress = window.FC.progress;
  var WINDOW = 30, AHEAD = 14;

  function h(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    for (var i = 3; i < arguments.length; i++) if (arguments[i]) el.append(arguments[i]);
    return el;
  }

  function pct(right, answers) { return answers ? Math.round(100 * right / answers) : null; }

  /** Answers and right answers per study day, from the daily logs of one or several courses, oldest first. */
  function series(logs, days, from) {
    var out = [];
    for (var i = days - 1; i >= 0; i--) {
      var day = progress.addDays(from || progress.today(), -i);
      var n = 0, right = 0;
      logs.forEach(function (log) {
        var e = log[day];
        if (e) { n += e[0]; right += e[1]; }
      });
      out.push({ day: day, n: n, right: right });
    }
    return out;
  }

  function total(list) {
    return list.reduce(function (a, d) { return { n: a.n + d.n, right: a.right + d.right }; }, { n: 0, right: 0 });
  }

  /** The last 30 study days and the 30 before, for the figures and their change. */
  function windows(logs) {
    var now = total(series(logs, WINDOW));
    var before = total(series(logs, WINDOW, progress.addDays(progress.today(), -WINDOW)));
    return { now: now, before: before };
  }

  function dayLabel(day) {
    var p = day.split("-");
    return new Date(+p[0], +p[1] - 1, +p[2]).toLocaleDateString(window.FC.lang, { day: "numeric", month: "short" });
  }

  // --- Building blocks ------------------------------------------------------------------------------

  /** A heading of a group of cards: now, the past or the future. */
  function group(key) {
    return h("h2", "stats-group", t(key));
  }

  function section(title, hint) {
    var box = h("section", "card stats-card", null, h("h3", "stats-title", title));
    if (hint) box.append(h("p", "hint", hint));
    return box;
  }

  /** Figures in tiles: {value, label, delta (points, optional), good (true when up is good)}. */
  function tiles(list) {
    var box = h("div", "stat-tiles");
    list.forEach(function (x) {
      var tile = h("div", "stat-tile", null, h("strong", "stat-value", x.value == null ? t("stat_none") : String(x.value)),
        h("span", "stat-label", x.label));
      if (x.delta) tile.append(h("span", "stat-delta " + (x.delta > 0 ? "up" : "down"),
        t(x.delta > 0 ? "stat_delta_up" : "stat_delta_down", Math.abs(x.delta))));
      box.append(tile);
    });
    return box;
  }

  /** The figures of the last 30 days: share right with its change from the 28 days before, and answers. */
  function answerTiles(logs) {
    var w = windows(logs);
    var now = pct(w.now.right, w.now.n), before = pct(w.before.right, w.before.n);
    return [
      { value: now == null ? null : t("stat_pct", now), label: t("stat_accuracy"), delta: now != null && before != null ? now - before : 0 },
      { value: w.now.n, label: t("stat_answered"), delta: w.before.n ? w.now.n - w.before.n : 0 }
    ];
  }

  /**
   * Bars, one per day: answers split into right and wrong, or a single count ({@code single}). The first and last
   * days are written under them; each bar names its day and count for pointers and screen readers.
   */
  function bars(days, single) {
    var max = Math.max(1, Math.max.apply(null, days.map(function (d) { return d.n; })));
    var chart = h("div", "bars" + (single ? " single" : ""));
    var sum = 0;
    days.forEach(function (d) {
      sum += d.n;
      var label = dayLabel(d.day) + " · " + (single ? t("stats_reviews_n", d.n) : t("stats_day_answers", d.n, d.right));
      var col = h("div", "bar");
      col.title = label;
      if (d.n) {
        if (single) {
          var all = h("span", "seg all");
          all.style.height = (100 * d.n / max) + "%";
          col.append(all);
        } else {
          var right = h("span", "seg right"), wrong = h("span", "seg wrong");
          right.style.height = (100 * d.right / max) + "%";
          wrong.style.height = (100 * (d.n - d.right) / max) + "%";
          col.append(right, wrong);
        }
      }
      chart.append(col);
    });
    chart.setAttribute("role", "img");
    var right = days.reduce(function (a, d) { return a + d.right; }, 0);
    chart.setAttribute("aria-label", single ? t("upcoming_total", sum)
      : sum ? t("daily_total", sum, pct(right, sum)) : t("daily_total_none"));
    var axis = h("div", "bars-axis", null, h("span", null, days.length ? dayLabel(days[0].day) : ""),
      h("span", null, days.length ? dayLabel(days[days.length - 1].day) : ""));
    return h("div", "chart", null, chart, axis);
  }

  /** Rows with a bar: {label, detail, fraction, cls, action (a button, optional)}. */
  function rows(list) {
    var box = h("ul", "stat-rows");
    list.forEach(function (r) {
      var fill = h("span");
      fill.style.width = Math.round(100 * Math.max(0, Math.min(1, r.fraction))) + "%";
      var head = h("div", "stat-row-head", null, r.href ? null : h("span", "stat-row-label", r.label),
        h("span", "stat-row-detail", r.detail));
      if (r.href) {
        var link = h("a", "stat-row-label", r.label);
        link.href = r.href;
        head.prepend(link);
      }
      var li = h("li", "stat-row" + (r.cls ? " " + r.cls : ""), null, head, h("span", "meter-bar", null, fill));
      if (r.note) li.append(h("p", "hint", r.note));
      if (r.action) li.append(r.action);
      box.append(li);
    });
    return box;
  }

  /** A bar split in parts: [{n, cls, label}], with its legend. */
  function split(parts) {
    var sum = parts.reduce(function (a, p) { return a + p.n; }, 0) || 1;
    var bar = h("div", "split-bar");
    var legend = h("ul", "legend");
    parts.forEach(function (p) {
      var seg = h("span", "seg " + p.cls);
      seg.style.width = (100 * p.n / sum) + "%";
      bar.append(seg);
      legend.append(h("li", p.cls, p.label + " · " + p.n));
    });
    return h("div", "split", null, bar, legend);
  }

  /** Reviews to come per study day, from today: {items: [[state, ...]]} already flattened to states. */
  function upcomingDays(states, days) {
    var today = progress.today(), end = progress.addDays(today, days - 1);
    var counts = {};
    states.forEach(function (s) {
      progress.upcoming(s, today, end).forEach(function (day) { counts[day] = (counts[day] || 0) + 1; });
    });
    var out = [];
    for (var i = 0; i < days; i++) {
      var day = progress.addDays(today, i);
      out.push({ day: day, n: counts[day] || 0, right: 0 });
    }
    return out;
  }

  window.FC.stats = {
    WINDOW: WINDOW,
    AHEAD: AHEAD,
    h: h,
    pct: pct,
    series: series,
    group: group,
    section: section,
    tiles: tiles,
    answerTiles: answerTiles,
    bars: bars,
    rows: rows,
    split: split,
    upcomingDays: upcomingDays
  };
})();
