/* Palette preference is independent of saved study progress. */
(() => {
  "use strict";
  const palettes = ["flashy", "ink", "blue_orange", "navy_gold", "tangerine", "violet", "forest", "coral", "neon"];
  const key = "flashcert:palette";
  const themeKey = "flashcert:theme";
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
  let theme = "system";
  let palette = "flashy";
  try {
    const saved = localStorage.getItem(key);
    if (palettes.includes(saved)) palette = saved;
    const savedTheme = localStorage.getItem(themeKey);
    if (["system", "light", "dark"].includes(savedTheme)) theme = savedTheme;
  } catch (_) { /* Storage may be unavailable. */ }
  document.documentElement.dataset.palette = palette;
  document.documentElement.dataset.theme = theme;

  function updateBrowserColor() {
    const color = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta => { meta.content = color; });
  }
  document.addEventListener("DOMContentLoaded", () => {
    const select = document.getElementById("palette-select");
    select.value = palette;
    document.getElementById("palette-control").hidden = false;
    const themeSelect = document.getElementById("theme-select");
    themeSelect.value = theme;
    document.getElementById("theme-control").hidden = false;
    themeSelect.addEventListener("change", () => {
      theme = themeSelect.value;
      document.documentElement.dataset.theme = theme;
      try { localStorage.setItem(themeKey, theme); } catch (_) { /* Keep it for this page. */ }
      updateBrowserColor();
    });
    select.addEventListener("change", () => {
      palette = select.value;
      document.documentElement.dataset.palette = palette;
      try { localStorage.setItem(key, palette); } catch (_) { /* Keep it for this page. */ }
      updateBrowserColor();
    });
    updateBrowserColor();
    systemTheme.addEventListener("change", updateBrowserColor);
  });
})();
