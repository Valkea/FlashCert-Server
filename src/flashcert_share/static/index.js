/* Course list: search, and the visitor's own progress when they chose to save it on this device. */
(function () {
  "use strict";
  var t = FC.t;
  var input = document.getElementById("course-search");
  var items = Array.prototype.slice.call(document.querySelectorAll(".course-item"));
  var noMatch = document.getElementById("no-match");

  function fold(s) {
    return s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
  }

  if (input) {
    input.addEventListener("input", function () {
      var words = fold(input.value).split(/\s+/).filter(Boolean);
      var shown = 0;
      items.forEach(function (li) {
        var hay = fold(li.dataset.search);
        var ok = words.every(function (w) { return hay.indexOf(w) >= 0; });
        li.hidden = !ok;
        if (ok) shown++;
      });
      document.querySelectorAll(".cert-group").forEach(function (g) {
        g.hidden = !g.querySelector(".course-item:not([hidden])");
      });
      noMatch.hidden = shown > 0;
    });
  }

  function badges() {
    var on = FC.progress.enabled();
    items.forEach(function (li) {
      var a = li.querySelector("a[data-share-id]");
      var box = li.querySelector(".course-progress");
      box.textContent = "";
      box.hidden = true;
      if (!on) return;
      var d = FC.progress.load(a.dataset.shareId);
      var parts = [];
      if (d.quizzes.length) {
        var best = Math.max.apply(null, d.quizzes.map(function (z) { return Math.round(100 * z.score / Math.max(1, z.total)); }));
        parts.push(t("progress_quiz", best));
      }
      var known = Object.keys(d.c).filter(function (k) { return d.c[k][1]; }).length;
      if (known) parts.push(t("progress_cards", known, a.dataset.cards));
      if (!parts.length) return;
      box.textContent = parts.join(" · ");
      box.hidden = false;
    });
  }

  badges();
  FC.progress.onChange(badges);
})();
