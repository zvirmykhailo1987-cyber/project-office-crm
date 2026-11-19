/* interactions.js — self-contained патерни інтерактивності (скіл `interaktyvnist`).
 * Призначення: вбудовувати inline у HTML-артефакти під СТРОГИЙ CSP (без CDN/eval/fetch).
 * Усе — vanilla, без залежностей. Кожен анімований патерн поважає prefers-reduced-motion.
 * Використання: <script>…вставити тіло…</script>  → window.IX.viewBoxPanZoom(...), тощо.
 * Джерела прийомів — references/top-tehniky.md (з maturity + self_contained).
 */
(function (root) {
  'use strict';
  var IX = {};

  // ── 0. Наскрізне: reduced-motion гейт (тверде правило) ─────────────────────
  var RM = root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)');
  IX.reducedMotion = function () { return !!(RM && RM.matches); };
  // обгортка: якщо reduced-motion — миттєвий стрибок замість анімації
  IX.motion = function (animatedFn, instantFn) {
    return IX.reducedMotion() ? (instantFn && instantFn()) : animatedFn();
  };
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };
  var clamp = function (v, a, b) { return Math.max(a, Math.min(b, v)); };
  IX.$ = $; IX.$$ = $$; IX.clamp = clamp;

  // ── 1. SVG pan/zoom через viewBox (self=yes) ───────────────────────────────
  // svg: <svg viewBox="x y w h">. UI-контроли тримати ОКРЕМИМ HTML-шаром (viewBox масштабує все всередині).
  IX.viewBoxPanZoom = function (svg, opts) {
    opts = opts || {};
    var min = opts.minScale || 0.2, max = opts.maxScale || 12;
    var vb = (svg.getAttribute('viewBox') || '0 0 100 100').split(/\s+/).map(Number);
    var base = vb.slice(); var scale = 1, drag = null;
    function apply() { svg.setAttribute('viewBox', vb.join(' ')); }
    function toUser(cx, cy) { // clientXY → user-space
      var pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy;
      var m = svg.getScreenCTM(); return m ? pt.matrixTransform(m.inverse()) : { x: cx, y: cy };
    }
    if (opts.wheelZoom !== false) svg.addEventListener('wheel', function (e) {
      e.preventDefault();
      var f = e.deltaY < 0 ? 0.9 : 1.1; var ns = clamp(scale * (1 / f), min, max);
      if (ns === scale) return; var k = ns / scale === 0 ? 1 : (base[2] * (1 / ns)) / vb[2];
      var p = toUser(e.clientX, e.clientY);
      vb[0] = p.x - (p.x - vb[0]) * f; vb[1] = p.y - (p.y - vb[1]) * f;
      vb[2] *= f; vb[3] *= f; scale = base[2] / vb[2]; apply();
    }, { passive: false });
    if (opts.dragPan !== false) {
      svg.style.cursor = 'grab';
      svg.addEventListener('pointerdown', function (e) { drag = { x: e.clientX, y: e.clientY, vb: vb.slice() }; svg.setPointerCapture(e.pointerId); svg.style.cursor = 'grabbing'; });
      svg.addEventListener('pointermove', function (e) {
        if (!drag) return; var r = svg.getBoundingClientRect();
        vb[0] = drag.vb[0] - (e.clientX - drag.x) * vb[2] / r.width;
        vb[1] = drag.vb[1] - (e.clientY - drag.y) * vb[3] / r.height; apply();
      });
      var up = function () { drag = null; svg.style.cursor = 'grab'; };
      svg.addEventListener('pointerup', up); svg.addEventListener('pointerleave', up);
    }
    return { reset: function () { vb = base.slice(); scale = 1; apply(); }, get: function () { return vb.slice(); } };
  };

  // Альтернатива: pan/zoom матрицею на <g> — UI поза групою НЕ масштабується.
  IX.matrixPanZoom = function (svg, gEl, opts) {
    opts = opts || {}; var min = opts.minScale || 0.2, max = opts.maxScale || 12;
    var m = { a: 1, d: 1, e: 0, f: 0 }, drag = null;
    function apply() { gEl.setAttribute('transform', 'matrix(' + m.a + ' 0 0 ' + m.d + ' ' + m.e + ' ' + m.f + ')'); }
    svg.addEventListener('wheel', function (e) {
      e.preventDefault(); var f = e.deltaY < 0 ? 1.1 : 0.9; var ns = clamp(m.a * f, min, max); var k = ns / m.a;
      var r = svg.getBoundingClientRect(); var px = e.clientX - r.left, py = e.clientY - r.top;
      m.e = px - (px - m.e) * k; m.f = py - (py - m.f) * k; m.a *= k; m.d *= k; apply();
    }, { passive: false });
    svg.addEventListener('pointerdown', function (e) { drag = { x: e.clientX, y: e.clientY, e: m.e, f: m.f }; svg.setPointerCapture(e.pointerId); });
    svg.addEventListener('pointermove', function (e) { if (!drag) return; m.e = drag.e + (e.clientX - drag.x); m.f = drag.f + (e.clientY - drag.y); apply(); });
    svg.addEventListener('pointerup', function () { drag = null; });
    apply(); return { reset: function () { m = { a: 1, d: 1, e: 0, f: 0 }; apply(); } };
  };

  // ── 2. zoom-to-bbox (плавний перехід viewBox на getBBox цілі) ──────────────
  IX.zoomToBBox = function (svg, targetEl, opts) {
    opts = opts || {}; var pad = opts.padding == null ? 0.15 : opts.padding, dur = opts.duration || 500;
    var b = targetEl.getBBox(); var px = b.width * pad, py = b.height * pad;
    var to = [b.x - px, b.y - py, b.width + px * 2, b.height + py * 2];
    var from = (svg.getAttribute('viewBox') || (to.join(' '))).split(/\s+/).map(Number);
    if (IX.reducedMotion()) { svg.setAttribute('viewBox', to.join(' ')); return; }
    var t0 = null;
    function step(ts) {
      if (t0 == null) t0 = ts; var k = clamp((ts - t0) / dur, 0, 1);
      var e = 1 - Math.pow(1 - k, 3); // ease-out
      var v = to.map(function (n, i) { return from[i] + (n - from[i]) * e; });
      svg.setAttribute('viewBox', v.join(' ')); if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  };

  // ── 3. hotspot-шар на SVG-плані ([data-hotspot], :hover CSS, клік→деталі) ──
  IX.hotspotLayer = function (svgOrRoot, opts) {
    opts = opts || {};
    svgOrRoot.addEventListener('pointerover', function (e) {
      var h = e.target.closest && e.target.closest('[data-hotspot]'); if (h && opts.onEnter) opts.onEnter(h, e);
    });
    svgOrRoot.addEventListener('click', function (e) {
      var h = e.target.closest && e.target.closest('[data-hotspot]'); if (h && opts.onClick) opts.onClick(h, e);
    });
    // клавіатура: hotspot із tabindex=0 реагує на Enter/Space
    svgOrRoot.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var h = e.target.closest && e.target.closest('[data-hotspot]'); if (h && opts.onClick) { e.preventDefault(); opts.onClick(h, e); }
    });
    return {
      toggleLayer: function (name, on) { $$('[data-layer="' + name + '"]', svgOrRoot).forEach(function (el) { el.style.display = on ? '' : 'none'; }); }
    };
  };

  // ── 4. nearest-point hover (Voronoi без ліби: лінійний find; для великих — вендорити d3-delaunay) ──
  IX.nearestPointHover = function (surface, points, opts) {
    opts = opts || {}; var maxR = opts.maxRadius || Infinity, last = null;
    surface.addEventListener('pointermove', function (e) {
      var r = surface.getBoundingClientRect(); var mx = e.clientX - r.left, my = e.clientY - r.top;
      var best = -1, bd = maxR * maxR;
      for (var i = 0; i < points.length; i++) { var dx = points[i].x - mx, dy = points[i].y - my, d = dx * dx + dy * dy; if (d < bd) { bd = d; best = i; } }
      if (best !== last) { last = best; opts.onChange && opts.onChange(best < 0 ? null : points[best], best, { x: mx, y: my }); }
    });
    surface.addEventListener('pointerleave', function () { last = null; opts.onChange && opts.onChange(null, -1); });
    if (opts.clickToStick) surface.addEventListener('click', function () { if (last >= 0) opts.onStick && opts.onStick(points[last], last); });
  };

  // ── 5. crosshair на осях (rule+text, без оклюзії марок) ────────────────────
  IX.crosshair = function (svg, opts) {
    opts = opts || {}; var ns = 'http://www.w3.org/2000/svg';
    var g = document.createElementNS(ns, 'g'); g.setAttribute('pointer-events', 'none'); g.style.opacity = 0;
    var vx = document.createElementNS(ns, 'line'), hy = document.createElementNS(ns, 'line');
    [vx, hy].forEach(function (l) { l.setAttribute('stroke', opts.color || 'currentColor'); l.setAttribute('stroke-dasharray', '3 3'); l.setAttribute('stroke-width', .8); g.appendChild(l); });
    svg.appendChild(g);
    svg.addEventListener('pointermove', function (e) {
      var r = svg.getBoundingClientRect(); var vb = (svg.getAttribute('viewBox') || ('0 0 ' + r.width + ' ' + r.height)).split(/\s+/).map(Number);
      var x = vb[0] + (e.clientX - r.left) / r.width * vb[2], y = vb[1] + (e.clientY - r.top) / r.height * vb[3];
      vx.setAttribute('x1', x); vx.setAttribute('x2', x); vx.setAttribute('y1', vb[1]); vx.setAttribute('y2', vb[1] + vb[3]);
      hy.setAttribute('y1', y); hy.setAttribute('y2', y); hy.setAttribute('x1', vb[0]); hy.setAttribute('x2', vb[0] + vb[2]);
      g.style.opacity = 1; opts.onMove && opts.onMove(x, y);
    });
    svg.addEventListener('pointerleave', function () { g.style.opacity = 0; });
  };

  // ── 6. filter-chips / dynamic queries (стан читається з DOM) ────────────────
  IX.filterChips = function (rootEl, data, render, opts) {
    opts = opts || {}; var attr = opts.attr || 'data-filter';
    function activeFilters() {
      var f = {}; $$('[' + attr + '].is-on, [' + attr + ']:checked', rootEl).forEach(function (el) {
        var k = el.getAttribute(attr), v = el.getAttribute('data-value'); (f[k] = f[k] || []).push(v == null ? true : v);
      }); return f;
    }
    function run() {
      var f = activeFilters();
      var out = data.filter(function (row) {
        return Object.keys(f).every(function (k) { return f[k].length === 0 || f[k].indexOf(String(row[k])) >= 0 || f[k].indexOf(true) >= 0; });
      });
      render(out, f);
    }
    rootEl.addEventListener('click', function (e) { var c = e.target.closest('[' + attr + ']'); if (c && c.tagName !== 'INPUT') { c.classList.toggle('is-on'); c.setAttribute('aria-pressed', c.classList.contains('is-on')); run(); } });
    rootEl.addEventListener('change', function (e) { if (e.target.matches('[' + attr + ']')) run(); });
    run(); return { refresh: run };
  };

  // ── 7. what-if: один стан → багато рендерів ([data-out], [data-bind]) ──────
  IX.whatIf = function (rootEl, model, opts) {
    opts = opts || {}; var state = {};
    function readInputs() { $$('[data-bind]', rootEl).forEach(function (el) { var k = el.getAttribute('data-bind'); state[k] = el.type === 'checkbox' ? el.checked : (el.type === 'range' || el.type === 'number' ? +el.value : el.value); }); }
    function paint() {
      var out = model(state) || {};
      $$('[data-out]', rootEl).forEach(function (el) {
        var k = el.getAttribute('data-out'); if (out[k] == null) return;
        var fmt = el.getAttribute('data-format'); el.textContent = fmt && out['_' + k + '_fmt'] ? out['_' + k + '_fmt'] : out[k];
      });
      opts.onUpdate && opts.onUpdate(state, out);
    }
    rootEl.addEventListener('input', function (e) { if (e.target.matches('[data-bind]')) { readInputs(); paint(); } });
    readInputs(); paint(); return { state: function () { return state; }, repaint: paint };
  };

  // ── 8. tangle: drag-adjustable число в прозі ───────────────────────────────
  IX.tangleNumber = function (el, opts) {
    opts = opts || {}; var min = opts.min == null ? -Infinity : opts.min, max = opts.max == null ? Infinity : opts.max, step = opts.step || 1;
    var val = opts.value == null ? +el.textContent || 0 : opts.value;
    var fmt = opts.format || function (v) { return v; };
    el.setAttribute('role', 'slider'); el.setAttribute('tabindex', '0'); el.style.cursor = 'ew-resize'; el.style.userSelect = 'none';
    el.setAttribute('aria-valuemin', min); el.setAttribute('aria-valuemax', max);
    function set(v) { val = clamp(Math.round(v / step) * step, min, max); el.textContent = fmt(val); el.setAttribute('aria-valuenow', val); opts.onChange && opts.onChange(val); }
    var drag = null;
    el.addEventListener('pointerdown', function (e) { drag = { x: e.clientX, v: val }; el.setPointerCapture(e.pointerId); e.preventDefault(); });
    el.addEventListener('pointermove', function (e) { if (drag) set(drag.v + (e.clientX - drag.x) * step * (opts.sensitivity || 1)); });
    el.addEventListener('pointerup', function () { drag = null; });
    el.addEventListener('keydown', function (e) { if (e.key === 'ArrowUp' || e.key === 'ArrowRight') { set(val + step); e.preventDefault(); } else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') { set(val - step); e.preventDefault(); } });
    set(val); return { get: function () { return val; }, set: set };
  };

  // ── 9. scroll-steps: sticky-графіка + IntersectionObserver кроки ───────────
  IX.scrollSteps = function (stepSelectorOrEls, opts) {
    opts = opts || {}; var els = typeof stepSelectorOrEls === 'string' ? $$(stepSelectorOrEls) : stepSelectorOrEls;
    var offset = opts.offset == null ? 0.5 : opts.offset;
    if (!('IntersectionObserver' in root)) { // фолбек rAF+throttle
      var tick = false;
      root.addEventListener('scroll', function () { if (tick) return; tick = true; requestAnimationFrame(function () { tick = false; var vh = innerHeight; els.forEach(function (el, i) { var r = el.getBoundingClientRect(); if (r.top < vh * (1 - offset) && r.bottom > vh * offset) opts.onEnter && opts.onEnter(el, i); }); }); });
      return;
    }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) { if (en.isIntersecting) { var i = els.indexOf(en.target); opts.onEnter && opts.onEnter(en.target, i, en.intersectionRatio); } else opts.onExit && opts.onExit(en.target, els.indexOf(en.target)); });
    }, { rootMargin: '-' + (offset * 100) + '% 0px -' + ((1 - offset) * 100) + '% 0px', threshold: opts.threshold || 0 });
    els.forEach(function (el) { io.observe(el); }); return io;
  };

  // ── 10. state ↔ URL (permalink сценарію) ───────────────────────────────────
  IX.stateToURL = function (state, opts) {
    opts = opts || {}; var p = new URLSearchParams();
    Object.keys(state).forEach(function (k) { if (state[k] != null && state[k] !== '') p.set(k, state[k]); });
    var url = location.pathname + (opts.hash ? '#' : '?') + p.toString();
    if (opts.replace) history.replaceState(state, '', url); else history.pushState(state, '', url);
  };
  IX.urlToState = function (opts) {
    opts = opts || {}; var raw = opts.hash ? location.hash.slice(1) : location.search.slice(1);
    var p = new URLSearchParams(raw), s = {}; p.forEach(function (v, k) { s[k] = v; }); return s;
  };
  IX.onURLChange = function (cb) { // Navigation API з фолбеком на popstate
    if (root.navigation) root.navigation.addEventListener('navigate', function (e) { if (e.canIntercept && !e.hashChange) return; cb(IX.urlToState()); });
    else root.addEventListener('popstate', function () { cb(IX.urlToState()); });
  };

  // ── 11. View Transition обгортка (no-op фолбек) ────────────────────────────
  IX.viewTransition = function (mutate) {
    if (IX.reducedMotion() || !document.startViewTransition) { mutate(); return { finished: Promise.resolve() }; }
    return document.startViewTransition(mutate);
  };

  // ── 12. FLIP-реордер (layout-анімація через WAAPI) ─────────────────────────
  IX.flipReorder = function (containerEl, mutate, opts) {
    opts = opts || {}; var dur = opts.duration || 300; var kids = $$(opts.itemSelector || ':scope > *', containerEl);
    var first = new Map(); kids.forEach(function (el) { first.set(el, el.getBoundingClientRect()); });
    mutate();
    if (IX.reducedMotion()) return;
    $$(opts.itemSelector || ':scope > *', containerEl).forEach(function (el) {
      var f = first.get(el); if (!f) return; var l = el.getBoundingClientRect();
      var dx = f.left - l.left, dy = f.top - l.top; if (!dx && !dy) return;
      el.animate([{ transform: 'translate(' + dx + 'px,' + dy + 'px)' }, { transform: 'none' }], { duration: dur, easing: 'cubic-bezier(.2,.8,.2,1)' });
    });
  };

  // ── 13. spring через CSS linear() (compositor-only) ────────────────────────
  // Генерує рядок linear(...) з фізики пружини — підставляти в transition-timing-function / animation.
  IX.linearEasing = function (opts) {
    opts = opts || {}; var stiffness = opts.stiffness || 170, damping = opts.damping || 26, mass = opts.mass || 1, n = opts.samples || 40;
    var w0 = Math.sqrt(stiffness / mass), zeta = damping / (2 * Math.sqrt(stiffness * mass));
    var pts = [], dur = opts.duration || 1;
    for (var i = 0; i <= n; i++) {
      var t = i / n, x;
      if (zeta < 1) { var wd = w0 * Math.sqrt(1 - zeta * zeta); x = 1 - Math.exp(-zeta * w0 * t * 6) * (Math.cos(wd * t * 6) + (zeta * w0 / wd) * Math.sin(wd * t * 6)); }
      else { x = 1 - Math.exp(-w0 * t * 6) * (1 + w0 * t * 6); }
      pts.push(+x.toFixed(4));
    }
    return 'linear(' + pts.join(', ') + ')';
  };

  // ── 14. boop: тимчасовий hover-стан з авто-скиданням ───────────────────────
  IX.boop = function (el, opts) {
    opts = opts || {}; var t = opts.timing || 200, tf = opts.transform || 'scale(1.1)';
    if (IX.reducedMotion()) return;
    var timer; el.addEventListener('pointerenter', function () { el.style.transition = 'transform ' + t + 'ms cubic-bezier(.2,.8,.2,1)'; el.style.transform = tf; clearTimeout(timer); timer = setTimeout(function () { el.style.transform = 'none'; }, t); });
  };

  // ── 15. dialogController (нативний <dialog> + повернення фокуса) ───────────
  IX.dialogController = function (dlgOrId) {
    var dlg = typeof dlgOrId === 'string' ? document.getElementById(dlgOrId) : dlgOrId; var opener = null;
    return {
      open: function (trigger) { opener = trigger || document.activeElement; dlg.showModal ? dlg.showModal() : (dlg.open = true); },
      close: function (v) { dlg.close ? dlg.close(v) : (dlg.open = false); if (opener && opener.focus) opener.focus(); }
    };
  };

  // ── 16. a11y-примітиви: roving tabindex, focus trap, live announce ─────────
  IX.rovingTabindex = function (containerEl, opts) {
    opts = opts || {}; var sel = opts.itemSelector || '[role="tab"],[role="menuitem"],[role="option"],button';
    var horiz = opts.orientation !== 'vertical';
    function items() { return $$(sel, containerEl); }
    function focusAt(i) { var it = items(); i = (i + it.length) % it.length; it.forEach(function (el, j) { el.tabIndex = j === i ? 0 : -1; }); it[i].focus(); }
    var it0 = items(); it0.forEach(function (el, j) { el.tabIndex = j === 0 ? 0 : -1; });
    containerEl.addEventListener('keydown', function (e) {
      var it = items(), cur = it.indexOf(document.activeElement); if (cur < 0) return;
      var nextK = horiz ? 'ArrowRight' : 'ArrowDown', prevK = horiz ? 'ArrowLeft' : 'ArrowUp';
      if (e.key === nextK) { focusAt(cur + 1); e.preventDefault(); }
      else if (e.key === prevK) { focusAt(cur - 1); e.preventDefault(); }
      else if (e.key === 'Home') { focusAt(0); e.preventDefault(); }
      else if (e.key === 'End') { focusAt(it.length - 1); e.preventDefault(); }
    });
  };
  IX.focusTrap = function (el) {
    var sel = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])';
    function handler(e) { if (e.key !== 'Tab') return; var f = $$(sel, el).filter(function (n) { return n.offsetParent !== null; }); if (!f.length) return; var first = f[0], last = f[f.length - 1]; if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); } else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); } }
    el.addEventListener('keydown', handler); return function () { el.removeEventListener('keydown', handler); };
  };
  IX.liveAnnounce = function (msg, politeness) {
    var id = 'ix-live-' + (politeness || 'polite'); var lr = document.getElementById(id);
    if (!lr) { lr = document.createElement('div'); lr.id = id; lr.setAttribute('aria-live', politeness || 'polite'); lr.setAttribute('aria-atomic', 'true'); lr.style.cssText = 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap'; document.body.appendChild(lr); }
    lr.textContent = ''; setTimeout(function () { lr.textContent = msg; }, 50);
  };

  // ── 17. validateForm (Constraint Validation + :user-invalid + inline на blur) ──
  IX.validateForm = function (form, opts) {
    opts = opts || {};
    $$('input,select,textarea', form).forEach(function (field) {
      field.addEventListener('blur', function () {
        var ok = field.checkValidity(); field.setAttribute('aria-invalid', !ok);
        var msgId = field.getAttribute('aria-describedby');
        if (msgId) { var m = document.getElementById(msgId); if (m) { m.textContent = ok ? '' : (opts.message ? opts.message(field) : field.validationMessage); m.setAttribute('role', ok ? '' : 'alert'); } }
      });
    });
    form.addEventListener('submit', function (e) { if (!form.checkValidity()) { e.preventDefault(); var bad = form.querySelector(':invalid'); if (bad) bad.focus(); } });
  };

  // ── 18. optimistic UI з risk-tier гейтом (тверде: незворотне → confirm) ────
  IX.optimistic = function (action, opts) {
    opts = opts || {};
    if (opts.isReversible === false) { return { requireConfirm: true, reason: T('Незворотна дія — потрібне явне підтвердження (risk-tier гейт).') }; }
    opts.apply && opts.apply();               // миттєвий UI
    return Promise.resolve().then(action).then(function (r) { opts.onOk && opts.onOk(r); return r; })
      .catch(function (err) { opts.rollback && opts.rollback(err); IX.liveAnnounce(opts.errorText || T('Не вдалося — зміни скасовано'), 'assertive'); throw err; });
  };

  root.IX = IX;
  if (typeof module !== 'undefined' && module.exports) module.exports = IX;
})(typeof window !== 'undefined' ? window : this);
