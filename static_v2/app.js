/* App shell behaviour (no inline scripts: the CSP forbids them).
   - CSRF: every same-origin unsafe fetch/htmx request carries X-CSRF-Token.
   - Mobile navigation rail toggle. */
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
    var sameOrigin = !/^[a-z]+:\/\//i.test(url) || url.indexOf(location.origin) === 0;
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

  var b = D.getElementById("rejka-perem");
  if (b) b.addEventListener("click", function () {
    var a = D.querySelector(".kk-app");
    var open = a.dataset.rejka !== "vidkryto";
    a.dataset.rejka = open ? "vidkryto" : "zakryto";
    b.setAttribute("aria-expanded", String(open));
  });
})();
