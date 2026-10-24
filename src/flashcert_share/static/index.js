/* Course lists: search, the visitor's own progress when they chose to save it on this device, and on a certification's
 * page the moment every course on it is mastered. */
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

  /** The course's standing as its page last counted it, unless the course has changed since. */
  function standing(a, d) {
    var sum = d.sum;
    return sum && String(sum.qn) === a.dataset.questions && String(sum.cn) === a.dataset.cards ? sum : null;
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
      var sum = standing(a, d);
      var parts = [];
      if (FC.progress.courseMastered(sum)) parts.push(t("progress_course_mastered"));
      else if (sum && sum.mastered) parts.push(t("progress_mastered", sum.mastered, sum.items));
      if (d.quizzes.length) {
        var best = Math.max.apply(null, d.quizzes.map(function (z) { return Math.round(100 * z.score / Math.max(1, z.total)); }));
        parts.push(t("progress_quiz", best));
      }
      if (!parts.length) return;
      box.textContent = parts.join(" · ");
      box.hidden = false;
    });
  }

  /**
   * On a certification's page: once every course on it is mastered, the recap, as in the app. It shows once per
   * course count, so a course added and mastered later earns it again.
   */
  function certificationMoment() {
    var list = document.getElementById("cert-courses");
    if (!list || !items.length || !FC.celebrate.on()) return;
    var sums = items.map(function (li) {
      var a = li.querySelector("a[data-share-id]");
      return standing(a, FC.progress.load(a.dataset.shareId));
    });
    if (!sums.every(FC.progress.courseMastered) || FC.progress.certCelebrated(list.dataset.cert) >= items.length) return;
    FC.progress.setCertCelebrated(list.dataset.cert, items.length);
    FC.celebrate.certification({
      name: list.dataset.name,
      courses: sums.map(function () { return 1; }),
      items: sums.reduce(function (n, sum) { return n + sum.mastered; }, 0),
      days: FC.progress.daysStudied(),
      streak: FC.progress.streak()
    });
  }

  badges();
  certificationMoment();
  FC.progress.onChange(badges);
})();
