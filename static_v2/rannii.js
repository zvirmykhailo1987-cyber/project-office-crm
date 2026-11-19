/* Runs synchronously in <head>: theme, density and motion profile are applied
   before the first paint, so the page never flashes the wrong theme. */
(function () {
  var d = document.documentElement.dataset;
  function v(k, def) {
    try { return localStorage.getItem("ofis-" + k) || def; } catch (e) { return def; }
  }
  d.tema = v("tema", matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  d.shchilnist = v("shchilnist", "norm");
  d.ruh = v("ruh", "tryvymir");
})();

/* Client-side translation: T("українська") → current-language string from the
   JSON catalogue embedded by the server (#i18n-js). Read lazily on first use. */
(function () {
  var CAT = null;
  function cat() {
    if (CAT === null) {
      try { CAT = JSON.parse((document.getElementById("i18n-js") || {}).textContent || "{}"); }
      catch (e) { CAT = {}; }
    }
    return CAT;
  }
  window.T = function (s) {
    var c = cat();
    if (Object.prototype.hasOwnProperty.call(c, s)) return c[s];
    var k = String(s).trim();
    if (k !== s && Object.prototype.hasOwnProperty.call(c, k)) return String(s).replace(k, c[k]);
    return s;
  };
})();

/* Dynamic per-element styles (a bar width, a status colour) arrive as data-styl
   and are applied through CSSOM: the CSP forbids style="" in markup but not
   element.style. The observer is attached before <body> is parsed, so every
   element is styled before the first paint, and later HTMX/JS inserts too. */
(function () {
  function one(el) { el.style.cssText += ";" + el.getAttribute("data-styl"); }
  function apply(n) {
    if (n.nodeType !== 1) return;
    if (n.hasAttribute("data-styl")) one(n);
    if (n.querySelectorAll) n.querySelectorAll("[data-styl]").forEach(one);
  }
  new MutationObserver(function (ms) {
    ms.forEach(function (m) {
      if (m.type === "attributes") one(m.target);
      else m.addedNodes.forEach(apply);
    });
  }).observe(document.documentElement,
             { childList: true, subtree: true, attributes: true, attributeFilter: ["data-styl"] });
})();
