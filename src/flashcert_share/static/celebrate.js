/*
 * The success moments, as in the app, the rarer the bigger: a question or card mastered (a tag, a pop, six sparks), a
 * course mastered (its bar fills and turns good, confetti) and every course of a certification mastered (a recap over
 * the page, tap to dismiss). With "reduce motion" each shows its end state at once; with celebrations switched off in
 * "Your progress", none plays. Drawn in the palette's roles, never the wrong-answer colours.
 */
(function () {
  "use strict";
  var t = window.FC.t;
  var KEY = "flashcert:celebrations";
  var COLORS = ["--accent", "--secondary", "--spark", "--good", "--marker"];
  var MAX_RINGS = 12;

  /** Whether the celebrations play at all; a preference like the palette, kept without asking. */
  function on() {
    try { return localStorage.getItem(KEY) !== "off"; } catch (e) { return true; }
  }

  function animates() {
    return !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function buzz(pattern) {
    try { if (navigator.vibrate) navigator.vibrate(pattern); } catch (e) { /* not offered */ }
  }

  function palette() {
    var style = getComputedStyle(document.documentElement);
    return COLORS.map(function (v) { return style.getPropertyValue(v).trim(); }).filter(Boolean);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  /** Six sparks flying out of {@code host}, which must be positioned. */
  function sparks(host) {
    if (!animates()) return;
    var colors = palette();
    for (var i = 0; i < 6; i++) {
      var angle = (i / 6) * 2 * Math.PI + Math.random() * 0.5;
      var s = el("span", "spark");
      s.setAttribute("aria-hidden", "true");
      s.style.setProperty("--dx", Math.round(Math.cos(angle) * (26 + Math.random() * 14)) + "px");
      s.style.setProperty("--dy", Math.round(Math.sin(angle) * (22 + Math.random() * 12)) + "px");
      s.style.background = colors[i % colors.length];
      host.appendChild(s);
    }
    setTimeout(function () { host.querySelectorAll(".spark").forEach(function (x) { x.remove(); }); }, 900);
  }

  /** A question or card reaching its target: its "Mastered" tag pops. */
  function item(tag) {
    buzz(15);
    if (!animates()) return;
    tag.classList.add("pop");
    sparks(tag);
  }

  /** A flashcard mastered: a stamp goes on the card before it leaves. */
  function stamp(cardEl) {
    var mark = el("span", "stamp", t("item_mastered"));
    mark.setAttribute("aria-hidden", "true");
    cardEl.appendChild(mark);
    buzz(15);
    if (animates()) {
      mark.classList.add("pop");
      sparks(mark);
    }
  }

  /** Confetti springing from {@code origin}, over the page; nothing to read or tap, gone in two seconds. */
  function confetti(origin, count) {
    if (!animates()) return;
    var canvas = el("canvas", "confetti");
    canvas.setAttribute("aria-hidden", "true");
    document.body.appendChild(canvas);
    var ratio = window.devicePixelRatio || 1;
    var w = window.innerWidth, h = window.innerHeight;
    canvas.width = w * ratio;
    canvas.height = h * ratio;
    var ctx = canvas.getContext("2d");
    if (!ctx) { canvas.remove(); return; }
    ctx.scale(ratio, ratio);
    var box = origin ? origin.getBoundingClientRect() : { left: 0, width: w, top: h / 2 };
    var colors = palette();
    var parts = [];
    for (var i = 0; i < (count || 90); i++) {
      var angle = -Math.PI / 2 + (Math.random() - 0.5) * 1.9;
      var speed = 7 + Math.random() * 8;
      parts.push({
        x: box.left + Math.random() * box.width, y: box.top,
        vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
        r: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.4,
        w: 5 + Math.random() * 5, h: 8 + Math.random() * 6, color: colors[i % colors.length]
      });
    }
    var start = null;
    function frame(now) {
      if (start == null) start = now;
      var age = now - start;
      ctx.clearRect(0, 0, w, h);
      ctx.globalAlpha = Math.max(0, Math.min(1, (2200 - age) / 500));
      parts.forEach(function (p) {
        p.vy += 0.32; p.vx *= 0.985; p.vy *= 0.985;
        p.x += p.vx; p.y += p.vy; p.r += p.vr;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.r);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.r * 1.7)));
        ctx.restore();
      });
      if (age < 2200) requestAnimationFrame(frame); else canvas.remove();
    }
    requestAnimationFrame(frame);
  }

  /** A course mastered: its bar fills from where it was and turns good, then confetti springs from it. */
  function course(meter, from) {
    var bar = meter.querySelector(".meter-bar span");
    buzz([20, 60, 30]);
    if (!animates() || !bar) {
      meter.classList.add("mastered");
      return;
    }
    bar.style.width = Math.round(100 * from) + "%";
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        meter.classList.add("filling");
        bar.style.width = "100%";
      });
    });
    setTimeout(function () {
      meter.classList.add("mastered", "pop");
      confetti(meter.querySelector(".meter-bar"), 110);
    }, 750);
  }

  function ring(fraction) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 36 36");
    svg.setAttribute("class", "ring" + (fraction >= 1 ? " full" : ""));
    svg.setAttribute("aria-hidden", "true");
    var track = document.createElementNS(ns, "circle");
    track.setAttribute("cx", "18"); track.setAttribute("cy", "18"); track.setAttribute("r", "15");
    track.setAttribute("class", "ring-track");
    var arc = track.cloneNode();
    arc.setAttribute("class", "ring-arc");
    arc.setAttribute("pathLength", "100");
    arc.style.setProperty("--fill", String(Math.round(100 * Math.max(0, Math.min(1, fraction)))));
    svg.append(track, arc);
    return svg;
  }

  /**
   * Every course of a certification mastered: a recap over the page (one ring per course, courses and items
   * mastered, days studied, day streak). A tap, Escape or the button dismisses it.
   */
  function certification(r) {
    var dialog = el("dialog", "recap-dialog");
    dialog.setAttribute("aria-labelledby", "recap-title");
    var body = el("div", "recap-body");
    body.append(el("p", "eyebrow", r.name));
    var title = el("h2", null, t("cert_complete"));
    title.id = "recap-title";
    body.append(title);
    var rings = el("div", "rings");
    r.courses.slice(0, MAX_RINGS).forEach(function (fraction) { rings.append(ring(fraction)); });
    body.append(rings);
    var stats = el("dl", "recap-stats");
    [["cert_stat_courses", r.courses.length], ["cert_stat_items", r.items],
      ["cert_stat_days", r.days], ["cert_stat_streak", r.streak]].forEach(function (s) {
      var cell = el("div");
      cell.append(el("dt", null, t(s[0])), el("dd", null, String(s[1])));
      stats.append(cell);
    });
    body.append(stats);
    var close = el("button", "btn", t("close"));
    close.type = "button";
    body.append(close);
    dialog.append(body);
    document.body.appendChild(dialog);
    function done() { if (dialog.open) dialog.close(); }
    close.addEventListener("click", done);
    dialog.addEventListener("click", done);
    dialog.addEventListener("close", function () { dialog.remove(); });
    dialog.showModal();
    close.focus();
    buzz([30, 80, 30, 80, 60]);
    if (animates()) {
      dialog.classList.add("enter");
      setTimeout(function () { confetti(rings, 160); }, 600);
    }
  }

  // The switch, in "Your progress".
  var toggle = document.getElementById("celebrations-toggle");
  if (toggle) {
    toggle.checked = on();
    toggle.addEventListener("change", function () {
      try { localStorage.setItem(KEY, toggle.checked ? "on" : "off"); } catch (e) { /* Keep it for this page. */ }
    });
  }

  window.FC.celebrate = {
    on: on,
    animates: animates,
    item: item,
    stamp: stamp,
    confetti: confetti,
    course: course,
    certification: certification
  };
})();
