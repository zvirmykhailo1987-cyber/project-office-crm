// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mykhailo Zvir

/* App shell behaviour (no inline scripts: the CSP forbids them).
   - CSRF: every same-origin unsafe fetch/htmx request carries X-CSRF-Token.
   - Mobile navigation rail toggle.
   - Demo mode: one-click fill of the sign-in form. */
(function () {
  "use strict";
  var D = document;
  var meta = D.querySelector('meta[name="csrf-token"]');
  var TOKEN = meta ? meta.content : "";
  var UNSAFE = /^(POST|PUT|PATCH|DELETE)$/i;


  var origFetch = window.fetch;
  window.fetch = function (input, init) {
    init = init || {};
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var sameOrigin = false;
    try { sameOrigin = new URL(url, location.href).origin === location.origin; } catch (e) {}
    if (sameOrigin && UNSAFE.test(init.method || "GET")) {
      var h = new Headers(init.headers || {});
      if (!h.has("X-CSRF-Token")) h.set("X-CSRF-Token", TOKEN);
      init.headers = h;
    }
    return origFetch.call(this, input, init);
  };

  D.addEventListener("htmx:configRequest", function (e) {
    if (UNSAFE.test(e.detail.verb || "")) e.detail.headers["X-CSRF-Token"] = TOKEN;
  });

  // Demo mode sign-in: a demo-account button fills the form (no inline handlers: CSP).
  Array.prototype.forEach.call(D.querySelectorAll(".demo-akaunt"), function (k) {
    k.addEventListener("click", function () {
      var l = D.getElementById("l-login"), p = D.getElementById("l-parol");
      if (!l || !p) return;
      l.value = k.getAttribute("data-login");
      p.value = k.getAttribute("data-parol");
      p.focus();
    });
  });

  var b = D.getElementById("rejka-perem");
  if (b) b.addEventListener("click", function () {
    var a = D.querySelector(".kk-app");
    var open = a.dataset.rejka !== "vidkryto";
    a.dataset.rejka = open ? "vidkryto" : "zakryto";
    b.setAttribute("aria-expanded", String(open));
  });
})();
