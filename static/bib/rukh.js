// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mykhailo Zvir

/* ═══ RUKH.JS — хореограф ═══════════════════════════════════════════════════
 * Те, чого CSS не вміє: індекси для stagger, кути нахилу з позиції вказівника,
 * відкручування числа з групуванням розрядів, зшивання двох станів сторінки.
 *
 * НЕ ДУБЛЮЄ `interactions.js` (`window.IX`, 26 патернів) і `povedinka.js`
 * (`window.PV`). Там — ЩО станеться при натисканні; тут — ЯК воно рухається.
 * `flipReorder`, `viewTransition`, `linearEasing` уже є в IX: цей файл їх
 * ВИКЛИКАЄ, а не переписує (інакше фікс у чужій копії нікуди не доїде — Г18).
 *
 * ТРИ ПРАВИЛА
 * 1. Рух питає дозволу: `--ruh` = 0 або `prefers-reduced-motion` — і кожна
 *    функція виконує КІНЦЕВИЙ стан миттєво, а не пропускає роботу.
 * 2. Жодного `setInterval` на анімацію — лише `requestAnimationFrame`.
 * 3. Слухачі вказівника й прокрутки — `passive`, а робота в них зведена до
 *    запису змінної: обчислення йдуть у кадрі, не в обробнику.
 *
 * Без залежностей, без eval, без інлайнових обробників — під строгим CSP.
 * ═══════════════════════════════════════════════════════════════════════════ */
(function (root) {
  'use strict';
  /* je-rh — ПЕРШИМ рядком, до будь-чого: IO-гілка .zjava (efekty.css) ховає
     контент ЛИШЕ під :root.je-rh — без JS сторінка мусить лишатися видимою
     («без JS не труна», доведено фікстурою io-fallback у гейті-7). */
  if (root.document && root.document.documentElement) {
    root.document.documentElement.classList.add('je-rh');
  }
  var RH = {};
  var IX = root.IX || {};

  function $$(s, r) {
    return Array.prototype.slice.call((r || document).querySelectorAll(s));
  }
  /* Єдине джерело правди про те, чи можна рухати. Читається з тієї самої
     змінної, що й у CSS, — щоб JS і стилі не розійшлися у своїх думках. */
  function ruh() {
    var v = parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue('--ruh'));
    if (!isNaN(v) && v === 0) return 0;
    return matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : (v || 1);
  }
  /* ЧАС І ПРОСТІР — РІЗНІ ВИМИКАЧІ, і плутати їх не можна.
     `--ruh` керує ТРИВАЛОСТЯМИ (переходи, лічильники, партитура входу).
     `--ampl` керує АМПЛІТУДОЮ (нахил, підйом, паралакс, глибина по Z).
     Профіль «лише 3D» ставить час у нуль і лишає простір — і якби нахил
     питав дозволу в `--ruh`, він згас би разом з анімаціями, хоча анімацією
     не є: він не грає в часі, а слідує за рукою. */
  function ampl() {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return 0;
    var v = parseFloat(getComputedStyle(document.documentElement)
      .getPropertyValue('--ampl'));
    return isNaN(v) ? 1 : v;
  }
  function ms(nazva, zapas) {
    var s = getComputedStyle(document.documentElement).getPropertyValue(nazva);
    var v = parseFloat(s);
    if (isNaN(v)) return zapas;
    return /\bs\b/.test(s.trim()) && !/ms/.test(s) ? v * 1000 : v;
  }

  /* ── STAGGER ──────────────────────────────────────────────────────────────
     Проставляє `--i` дітям. Стеля затримки живе в CSS (`--stagger-styelya`),
     тут — лише порядок: розділення навмисне, щоб бюджет входу не редагувався
     у двох місцях. */
  RH.cherha = function (kontejner, opts) {
    opts = opts || {};
    var elems = typeof kontejner === 'string' ? $$(kontejner)
      : (kontejner.length !== undefined ? Array.prototype.slice.call(kontejner)
        : $$(opts.selektor || ':scope > *', kontejner));
    elems.forEach(function (el, i) {
      el.style.setProperty('--i', i);
      if (opts.klas !== false) el.classList.add(opts.klas || 'vhid');
    });
    return elems.length;
  };

  /* ── НАХИЛ ЗА ВКАЗІВНИКОМ ─────────────────────────────────────────────────
     Стеля кута — у CSS (`--nahyl-max`, clamp). Тут навмисно НЕ обмежуємо
     вдруге: два обмежувачі розходяться, і тоді ніхто не знає, який чинний. */
  RH.nahyl = function (el, opts) {
    opts = opts || {};
    var syla = opts.syla || 7;          // градусів на пів-картки
    var kadr = null, ostanniy = null;

    function malyuy() {
      kadr = null;
      if (!ostanniy) return;
      el.style.setProperty('--nahyl-y', ostanniy.x * syla + 'deg');
      el.style.setProperty('--nahyl-x', -ostanniy.y * syla + 'deg');
      /* координати для .vidblysk (efekty.css): px відносно картки */
      el.style.setProperty('--mx', ostanniy.px + 'px');
      el.style.setProperty('--my', ostanniy.py + 'px');
    }
    function ruhaye(e) {
      if (!ampl()) return;
      var r = el.getBoundingClientRect();
      if (!r.width || !r.height) return;
      ostanniy = { x: (e.clientX - r.left) / r.width - .5,
                   y: (e.clientY - r.top) / r.height - .5,
                   px: e.clientX - r.left, py: e.clientY - r.top };
      el.dataset.vede = '1';
      if (!kadr) kadr = requestAnimationFrame(malyuy);
    }
    function pokydaye() {
      ostanniy = null;
      delete el.dataset.vede;          /* повернення — з переходом */
      el.style.setProperty('--nahyl-x', '0deg');
      el.style.setProperty('--nahyl-y', '0deg');
    }
    el.addEventListener('pointermove', ruhaye, { passive: true });
    el.addEventListener('pointerleave', pokydaye, { passive: true });
    /* Клавіатурний шлях не має «нахилу» — і не повинен його імітувати: фокус
       уже видно кільцем. Але картка, покинута під вказівником при переході на
       іншу вкладку, мусить вирівнятись. */
    el.addEventListener('blur', pokydaye, true);
    return function znyaty() {
      el.removeEventListener('pointermove', ruhaye);
      el.removeEventListener('pointerleave', pokydaye);
      pokydaye();
    };
  };

  /* ── МАГНІТ (v6.1) ────────────────────────────────────────────────────────
     Головна дія тягнеться до курсора в радіусі; зсув пише токен-доданок
     --mahnit-x/y (стеля 3px — у CSS, .mahnit). Слухач — на ДОКУМЕНТІ одним
     екземпляром на елемент: у радіусі поза кнопкою hover-подій немає, тому
     pointermove елемента не досить. rAF-тротлінг як у решти. */
  RH.mahnit = function (el, opts) {
    opts = opts || {};
    if (matchMedia('(hover: none)').matches) return function () {};
    var radius = opts.radius || 80, kadr = null, ost = null;

    function malyuy() {
      kadr = null;
      if (!ost) return;
      el.style.setProperty('--mahnit-x', ost.x + 'px');
      el.style.setProperty('--mahnit-y', ost.y + 'px');
      if (ost.blyzko) el.dataset.vede = '1'; else delete el.dataset.vede;
    }
    function ruhaye(e) {
      if (!ampl()) return;
      var r = el.getBoundingClientRect();
      if (!r.width) return;
      var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      var dx = e.clientX - cx, dy = e.clientY - cy;
      var d = Math.hypot(dx, dy);
      var mezha = radius + Math.max(r.width, r.height) / 2;
      if (d < mezha) {
        var syla = 1 - d / mezha;              /* ближче — сильніше */
        ost = { x: dx * syla * .18, y: dy * syla * .18, blyzko: true };
      } else if (ost && ost.blyzko) {
        ost = { x: 0, y: 0, blyzko: false };   /* відпустити пружиною */
      } else return;
      if (!kadr) kadr = requestAnimationFrame(malyuy);
    }
    document.addEventListener('pointermove', ruhaye, { passive: true });
    return function znyaty() {
      document.removeEventListener('pointermove', ruhaye);
      el.style.setProperty('--mahnit-x', '0px');
      el.style.setProperty('--mahnit-y', '0px');
    };
  };

  /* ── ПАРАЛАКС У МЕЖАХ КОНТЕЙНЕРА ──────────────────────────────────────────
     `will-change` вмикається лише поки контейнер видимий. Постійний
     `will-change` на десятках шарів зʼїдає памʼять GPU і робить гірше, ніж
     його відсутність. */
  RH.paralaks = function (kontejner, opts) {
    opts = opts || {};
    var shary = $$('.paralaks', kontejner);
    if (!shary.length) return function () {};
    var vydymyi = false, kadr = null;

    function malyuy() {
      kadr = null;
      if (!vydymyi || !ampl()) return;
      var r = kontejner.getBoundingClientRect();
      var chastka = (r.top + r.height / 2 - innerHeight / 2) / innerHeight;
      shary.forEach(function (sh, i) {
        var hlyb = parseFloat(sh.dataset.hlybyna) || (i + 1);
        sh.style.setProperty('--paralaks', (-chastka * hlyb * (opts.syla || 8)) + 'px');
      });
    }
    function pry() { if (!kadr) kadr = requestAnimationFrame(malyuy); }

    var sposter = new IntersectionObserver(function (zapysy) {
      vydymyi = zapysy[0].isIntersecting;
      shary.forEach(function (sh) {
        if (vydymyi) delete sh.dataset.poza; else sh.dataset.poza = '1';
      });
      if (vydymyi) pry();
    });
    sposter.observe(kontejner);
    addEventListener('scroll', pry, { passive: true });
    return function znyaty() {
      sposter.disconnect();
      removeEventListener('scroll', pry);
    };
  };

  /* ── ЛІЧИЛЬНИК ЧИСЛА ──────────────────────────────────────────────────────
     Не «ефект»: біжуче число показує, що величина ЗМІНИЛАСЬ, і на скільки.
     Форматування — `Intl`, українська локаль: пробіл-розділювач розрядів і
     кома в дробовій частині. Ручне форматування тут дало б «1,234.5». */
  RH.chyslo = function (el, kudy, opts) {
    opts = opts || {};
    var zvidky = opts.vid !== undefined ? opts.vid
      : (parseFloat(String(el.textContent).replace(/[^\d.,-]/g, '')
          .replace(/\s/g, '').replace(',', '.')) || 0);
    var fmt = new Intl.NumberFormat(opts.locale || 'uk-UA', {
      minimumFractionDigits: opts.znaky || 0,
      maximumFractionDigits: opts.znaky || 0
    });
    function pokazhy(v) {
      el.textContent = (opts.prefiks || '') + fmt.format(v) + (opts.sufiks || '');
    }
    if (!ruh()) { pokazhy(kudy); return Promise.resolve(); }

    var tryv = opts.tryvalist || ms('--d-slow', 360);
    return new Promise(function (hotovo) {
      var pochatok = null;
      el.dataset.bizhyt = '1';
      function krok(t) {
        if (pochatok === null) pochatok = t;
        var p = Math.min(1, (t - pochatok) / tryv);
        /* Гальмування наприкінці: число мусить «доїхати», а не обрубатись. */
        pokazhy(zvidky + (kudy - zvidky) * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(krok);
        else { delete el.dataset.bizhyt; pokazhy(kudy); hotovo(); }
      }
      requestAnimationFrame(krok);
    });
  };

  /* ── ПЕРЕХІД МІЖ СТАНАМИ СТОРІНКИ ─────────────────────────────────────────
     Обгортка над `IX.viewTransition`, якщо він є; інакше — власний мінімум.
     Спільні елементи отримують `view-transition-name` на час переходу й
     втрачають його після: два елементи з однаковим імʼям одночасно ламають
     перехід цілком, тому імена не лишаються висіти. */
  RH.perehid = function (zminyty, spilni) {
    var imena = [];
    (spilni || []).forEach(function (para, i) {
      var el = typeof para === 'string' ? document.querySelector(para) : para;
      if (!el) return;
      var imya = 'rh-' + i;
      el.style.viewTransitionName = imya;
      imena.push(el);
    });
    function prybery() { imena.forEach(function (el) { el.style.viewTransitionName = ''; }); }

    if (!ruh() || !document.startViewTransition) {
      var r = zminyty(); prybery(); return Promise.resolve(r);
    }
    if (IX.viewTransition) {
      return Promise.resolve(IX.viewTransition(zminyty)).then(function (v) {
        prybery(); return v;
      });
    }
    var vt = document.startViewTransition(zminyty);
    return vt.finished.then(prybery, prybery);
  };

  /* ── ПЕРЕБУДОВА СПИСКУ ────────────────────────────────────────────────────
     Делегування в IX.flipReorder — саме через його відсутність у ОФІСі дошка
     перебудовувалась стрибком. Якщо IX не завендорено, чесно повертаємо false,
     а не імітуємо: мовчазна заглушка приховала б цю ж проблему вдруге. */
  RH.perebuduva = function (kontejner, zminyty) {
    if (!ruh() || !IX.flipReorder) { zminyty(); return false; }
    IX.flipReorder(kontejner, zminyty);
    return true;
  };

  /* ── ПРОЖЕКТОР (efekty.css .prozhektor) ──────────────────────────────────
     Пише --mx/--my у px відносно елемента; сам ефект живе в CSS (::before,
     opacity × clamp(0,--ampl,1)). Дисципліна та сама, що в RH.nahyl:
     обробник лише записує координати, малює rAF-кадр; guard !ampl() у кадрі —
     вимкнений рух не пише навіть змінні. На дотикових пристроях (hover:none)
     прожектор безглуздий — чесний noop. */
  RH.prozhektor = function (el, opts) {
    if (typeof el === 'string') el = document.querySelector(el);
    if (!el || matchMedia('(hover: none)').matches) {
      return function () {};
    }
    var ostanniy = null, kadr = null;
    function malyuy() {
      kadr = null;
      if (!ostanniy || !ampl()) return;
      el.style.setProperty('--mx', ostanniy.x + 'px');
      el.style.setProperty('--my', ostanniy.y + 'px');
    }
    function ruhaye(e) {
      var r = el.getBoundingClientRect();
      if (!r.width) return;
      ostanniy = { x: Math.round(e.clientX - r.left),
                   y: Math.round(e.clientY - r.top) };
      if (!kadr) kadr = requestAnimationFrame(malyuy);
    }
    function pishov() {
      ostanniy = null;
      el.style.removeProperty('--mx');
      el.style.removeProperty('--my');
    }
    el.addEventListener('pointermove', ruhaye, { passive: true });
    el.addEventListener('pointerleave', pishov, { passive: true });
    return function znyaty() {
      el.removeEventListener('pointermove', ruhaye);
      el.removeEventListener('pointerleave', pishov);
      pishov();
    };
  };

  /* ── ЗʼЯВА (efekty.css .zjava) — IO-фолбек ────────────────────────────────
     У браузері з animation-timeline: view() — СВІДОМИЙ no-op: CSS-гілка
     робить усе сама, дубль дав би подвійний reveal. Без підтримки —
     IntersectionObserver додає клас і відпускає елемент (unobserve).
     При вимкненому русі — кінцевий стан ОДРАЗУ (не «повільніше», а миттєво). */
  RH.zjava = function (selektor, opts) {
    opts = opts || {};
    var klas = opts.klas || 'je-vydno';
    var ely = typeof selektor === 'string' ? $$(selektor) : [].slice.call(selektor);
    if (!ely.length) return function () {};
    if (typeof CSS !== 'undefined' && CSS.supports &&
        CSS.supports('animation-timeline', 'view()') && !opts.zmus) {
      return function () {};
    }
    if (!ruh()) {
      ely.forEach(function (el) { el.classList.add(klas); });
      return function () {};
    }
    var io = new IntersectionObserver(function (zapysy) {
      zapysy.forEach(function (z) {
        if (!z.isIntersecting) return;
        z.target.classList.add(klas);
        io.unobserve(z.target);
      });
    }, { threshold: opts.chastka || .15 });
    ely.forEach(function (el) { io.observe(el); });
    return function znyaty() { io.disconnect(); };
  };

  /* ── АВТОПІДКЛЮЧЕННЯ ──────────────────────────────────────────────────────
     Розмітка вмикає рух атрибутами, без жодного інлайнового скрипта. */
  RH.pidklyuchyty = function (korin) {
    korin = korin || document;
    $$('[data-cherha]', korin).forEach(function (k) { RH.cherha(k); });
    $$('[data-nahyl]', korin).forEach(function (k) {
      k.classList.add('nahyl'); RH.nahyl(k, { syla: +(k.dataset.nahyl) || undefined });
    });
    $$('[data-mahnit]', korin).forEach(function (k) {
      k.classList.add('mahnit'); RH.mahnit(k);
    });
    $$('[data-prozhektor]', korin).forEach(function (k) {
      k.classList.add('prozhektor'); RH.prozhektor(k);
    });
    RH.zjava('[data-zjava]');
    $$('[data-paralaks]', korin).forEach(function (k) { RH.paralaks(k); });
    $$('[data-chyslo]', korin).forEach(function (k) {
      RH.chyslo(k, parseFloat(k.dataset.chyslo), { znaky: +(k.dataset.znaky || 0) });
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { RH.pidklyuchyty(); });
  } else {
    RH.pidklyuchyty();
  }

  root.RH = RH;
  if (typeof module !== 'undefined' && module.exports) module.exports = RH;
})(typeof window !== 'undefined' ? window : this);
