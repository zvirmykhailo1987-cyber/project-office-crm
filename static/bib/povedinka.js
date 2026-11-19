/* ═══ POVEDINKA — шар поведінки бібліотеки ══════════════════════════════════
 * Надбудова над `interaktyvnist/scripts/interactions.js` (`window.IX`).
 * Нічого з IX не переписує: там уже є `optimistic` з risk-tier гейтом,
 * `dialogController`, `focusTrap`, `rovingTabindex`, `liveAnnounce`,
 * `validateForm`, `flipReorder`. Тут — те, чого над ними бракує продукту.
 *
 * ЩО ЦЕ ЗАКРИВАЄ (вимір робочої CRM 08.08.2026):
 *   нуль тостів — помилки через `alert()`, блокуючий діалог ОС;
 *   нуль undo — жодна дія не має вікна відкату;
 *   нуль підтверджень на незворотних діях;
 *   нуль drag&drop — канбан виглядає дошкою, поводиться як список посилань;
 *   нуль вибору рядків і масових дій на 46 149 записах;
 *   нуль гарячих клавіш, крім ⌘K.
 *
 * ТРИ ТВЕРДІ ПРАВИЛА ШАРУ
 * 1. **Оборотне отримує undo, незворотне — підтвердження.** Не навпаки й не
 *    обидва. `IX.optimistic` уже відмовляє оптимізму незворотному; тут це
 *    правило доводиться до кінця.
 * 2. **Кожен вказівниковий жест має клавіатурний шлях.** Перетягування без
 *    клавіатури — не функція, а половина функції.
 * 3. **Частковий результат називається чесно.** «94 зі 100 · 6 не вдалося»,
 *    а не «готово».
 *
 * Без залежностей, без eval, без інлайнових обробників — працює під строгим CSP.
 * Рух поважає `--ruh` (0 = без анімації) і `prefers-reduced-motion`.
 * ═══════════════════════════════════════════════════════════════════════════ */
(function (root) {
  'use strict';
  var IX = root.IX || {};
  var PV = {};

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function ruh() {
    var v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--ruh'));
    if (!isNaN(v)) return v;
    return matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1;
  }
  function kazhy(txt, naholos) {
    if (IX.liveAnnounce) IX.liveAnnounce(txt, naholos ? 'assertive' : 'polite');
  }

  /* ── §1 ТОСТИ ──────────────────────────────────────────────────────────────
     Черга не довша за три. Четвертий нікому не потрібен: людина читає
     найсвіжіший, а стос із семи стає стіною, яку закривають не читаючи.
     Помилка — `role="alert"` (перебиває читача екрана), решта — `role="status"`.
     Відлік СТОЇТЬ на наведенні й фокусі: інакше тост із кнопкою «Скасувати»
     зникає рівно тоді, коли до неї тягнуться. */
  var TOSTIV_MAX = 3;
  PV.toast = function (o) {
    o = o || {};
    var box = $('.k-tosty');
    if (!box) {
      box = document.createElement('div');
      box.className = 'k-tosty';
      document.body.appendChild(box);
    }
    while (box.children.length >= TOSTIV_MAX) box.removeChild(box.firstElementChild);

    var t = document.createElement('div');
    t.className = 'k-tost' + (o.ton ? ' ' + o.ton : '');
    t.setAttribute('role', o.ton === 'pomylka' ? 'alert' : 'status');

    var znak = document.createElement('span');
    znak.className = 'znak'; znak.setAttribute('aria-hidden', 'true');
    znak.innerHTML = o.ikona
      ? '<svg class="ik" width="15" height="15"><use href="#' + o.ikona + '"/></svg>' : '';
    var tekst = document.createElement('span');
    tekst.textContent = o.tekst || '';
    t.appendChild(znak); t.appendChild(tekst);

    if (o.diya) {
      var b = document.createElement('button');
      b.className = 'k-btn tykha shchilna';
      b.textContent = o.diya.pidpys || T('Скасувати');
      b.addEventListener('click', function () { o.diya.fn(); zakryty(); });
      t.appendChild(b);
    }
    var trymaty = o.trymaty == null ? 8000 : o.trymaty;
    if (trymaty && ruh()) {
      var v = document.createElement('span'); v.className = 'vidlik';
      var i = document.createElement('i');
      i.style.animationDuration = trymaty + 'ms';
      v.appendChild(i); t.appendChild(v);
    }
    box.appendChild(t);
    kazhy(o.tekst || '', o.ton === 'pomylka');

    var tid = null;
    function zakryty() {
      if (tid) clearTimeout(tid);
      if (t.parentNode) t.parentNode.removeChild(t);
    }
    function pusk() { if (trymaty) tid = setTimeout(zakryty, trymaty); }
    function stop() { if (tid) { clearTimeout(tid); tid = null; } }
    t.addEventListener('mouseenter', stop);
    t.addEventListener('focusin', stop);
    t.addEventListener('mouseleave', pusk);
    t.addEventListener('focusout', pusk);
    pusk();
    return { zakryty: zakryty, element: t };
  };

  /* ── §2 UNDO ──────────────────────────────────────────────────────────────
     Вікно відкату на ОБОРОТНІ дії: зміна статусу, призначення, архівація.
     Видалення undo НЕ отримує — там `PV.pidtverdyty` (див. §3).

     Чому не «підтвердити кожну дію»: у реєстрі на 46 тисяч записів
     підтвердження на кожен рух перетворює роботу на клацання «так». Undo
     дешевший за підтвердження й безпечніший за тишу. */
  PV.undo = function (o) {
    o = o || {};
    if (o.oborotna === false) {
      return Promise.reject(new Error(T('Незворотна дія не може мати undo — потрібне підтвердження')));
    }
    var skasovano = false;
    o.zastosuvaty && o.zastosuvaty();             // оптимістично: UI змінюється одразу
    var t = PV.toast({
      tekst: o.tekst || T('Зроблено'), ton: o.ton, ikona: o.ikona,
      trymaty: o.vikno || 8000,
      diya: { pidpys: T('Скасувати'), fn: function () {
        skasovano = true;
        o.vidkotyty && o.vidkotyty();
        kazhy(T('Скасовано'));
      } }
    });
    return new Promise(function (rozvyaz) {
      setTimeout(function () {
        if (skasovano) return rozvyaz({ skasovano: true });
        Promise.resolve().then(o.zafiksuvaty).then(function (r) {
          rozvyaz({ skasovano: false, rezultat: r });
        }).catch(function (e) {
          o.vidkotyty && o.vidkotyty();
          t.zakryty();
          PV.toast({ tekst: o.pomylkaTekst || T('Не вдалось зберегти — зміни скасовано'),
                     ton: 'pomylka', trymaty: 0 });
          rozvyaz({ skasovano: true, pomylka: e });
        });
      }, o.vikno || 8000);
    });
  };

  /* ── §3 ПІДТВЕРДЖЕННЯ НЕЗВОРОТНОГО ────────────────────────────────────────
     Нативний `<dialog>`: фокус-пастка, Esc і повернення фокуса на тригер —
     усе це вже робить браузер. Своє означало б відтворити те саме гірше.
     Текст мусить називати НАСЛІДКИ («разом із ним зникнуть 3 угоди»), а не
     питати «ви впевнені?» — впевненість не є інформацією. */
  PV.pidtverdyty = function (o) {
    o = o || {};
    var d = document.createElement('dialog');
    d.className = 'k-dialoh' + (o.ruynivna === false ? '' : ' ruynivna');
    var h = document.createElement('div'); h.className = 'shapka';
    h.textContent = o.zaholovok || T('Підтвердити дію');
    var p = document.createElement('div');
    p.textContent = o.naslidky || '';
    var diyi = document.createElement('div'); diyi.className = 'diyi';
    var ni = document.createElement('button');
    ni.className = 'k-btn tykha'; ni.textContent = o.ni || T('Скасувати');
    var tak = document.createElement('button');
    tak.className = 'k-btn ' + (o.ruynivna === false ? 'holovna' : 'nebezpechna');
    tak.textContent = o.tak || T('Підтвердити');
    diyi.appendChild(ni); diyi.appendChild(tak);
    d.appendChild(h); if (o.naslidky) d.appendChild(p); d.appendChild(diyi);
    document.body.appendChild(d);

    return new Promise(function (rozvyaz) {
      function zakryty(v) {
        if (d.open) d.close();
        if (d.parentNode) d.parentNode.removeChild(d);
        rozvyaz(v);
      }
      ni.addEventListener('click', function () { zakryty(false); });
      tak.addEventListener('click', function () {
        if (!o.diya) return zakryty(true);
        d.setAttribute('data-stan', 'zaynyata');
        tak.setAttribute('aria-busy', 'true');
        Promise.resolve().then(o.diya).then(function () { zakryty(true); })
          .catch(function (e) {
            d.setAttribute('data-stan', 'pomylka');
            tak.removeAttribute('aria-busy');
            p.textContent = (e && e.message) || T('Не вдалось виконати. Спробуйте ще раз.');
          });
      });
      d.addEventListener('cancel', function () { zakryty(false); });
      d.showModal();
      ni.focus();                                  // фокус на БЕЗПЕЧНІЙ дії
    });
  };

  /* ── §4 ВИБІР РЯДКІВ ──────────────────────────────────────────────────────
     Shift-діапазон, «усі на сторінці» з проміжним станом, клавіатура.
     Без проміжного стану прапорця «обрати всі» бреше: коли обрано частину,
     галочка каже «обрано все». */
  PV.vybir = function (tab, o) {
    o = o || {};
    var vsi = $('[data-vybir-vsi]', tab);
    var ostannij = null;
    function ryadky() { return $$('tbody tr', tab); }
    function pole(tr) { return $('[data-vybir]', tr); }
    function obrani() { return ryadky().filter(function (tr) { var p = pole(tr); return p && p.checked; }); }

    function onovyty() {
      var n = obrani().length, vsyoho = ryadky().length;
      ryadky().forEach(function (tr) {
        var p = pole(tr);
        if (p) tr.setAttribute('aria-selected', p.checked ? 'true' : 'false');
      });
      if (vsi) {
        vsi.checked = n > 0 && n === vsyoho;
        vsi.indeterminate = n > 0 && n < vsyoho;    // саме те, без чого чекбокс бреше
      }
      o.onZmina && o.onZmina(obrani(), n, vsyoho);
      kazhy(n ? (T('обрано ') + n + T(' із ') + vsyoho) : T('вибір знято'));
    }

    tab.addEventListener('click', function (e) {
      var p = e.target.closest && e.target.closest('[data-vybir]');
      if (!p) return;
      var tr = p.closest('tr'), spysok = ryadky();
      if (e.shiftKey && ostannij) {                // діапазон
        var a = spysok.indexOf(ostannij), b = spysok.indexOf(tr);
        spysok.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(function (r) {
          var q = pole(r); if (q) q.checked = p.checked;
        });
      }
      ostannij = tr;
      onovyty();
    });
    if (vsi) vsi.addEventListener('change', function () {
      ryadky().forEach(function (tr) { var p = pole(tr); if (p) p.checked = vsi.checked; });
      onovyty();
    });
    onovyty();
    return { obrani: obrani, onovyty: onovyty, znyaty: function () {
      ryadky().forEach(function (tr) { var p = pole(tr); if (p) p.checked = false; });
      onovyty();
    } };
  };

  /* ── §5 МАСОВІ ДІЇ ────────────────────────────────────────────────────────
     Панель дій над вибраними + ЧЕСНИЙ частковий результат. Операція на сто
     записів майже ніколи не проходить рівно: «94 зі 100 · 6 не вдалося —
     показати» — це правда, а «готово» — ні. */
  PV.masovi = function (o) {
    o = o || {};
    var elementy = o.elementy || [];
    var vyjshlo = 0, vpaly = [];
    return elementy.reduce(function (lanc, el) {
      return lanc.then(function () {
        return Promise.resolve().then(function () { return o.diya(el); })
          .then(function () { vyjshlo++; })
          .catch(function (e) { vpaly.push({ element: el, pomylka: e }); });
      });
    }, Promise.resolve()).then(function () {
      var vsyoho = elementy.length;
      if (!vpaly.length) {
        PV.toast({ tekst: (o.uspikhTekst || T('Готово')) + ' · ' + vyjshlo + T(' з ') + vsyoho,
                   ton: 'uspikh' });
      } else {
        PV.toast({
          tekst: vyjshlo + T(' з ') + vsyoho + ' · ' + vpaly.length + T(' не вдалося'),
          ton: 'uvaha', trymaty: 0,
          diya: o.pokazatyVpaly ? { pidpys: T('Показати'), fn: function () { o.pokazatyVpaly(vpaly); } } : null
        });
      }
      return { vyjshlo: vyjshlo, vpaly: vpaly };
    });
  };

  /* ── §6 ПЕРЕТЯГУВАННЯ З КЛАВІАТУРНИМ ШЛЯХОМ ───────────────────────────────
     Вказівник — рідний HTML5 drag&drop; порядок анімується через
     `IX.flipReorder`, якщо він є.

     КЛАВІАТУРА ОБОВʼЯЗКОВА: Пробіл — узяти, стрілки — перенести, Пробіл —
     покласти, Esc — повернути. Кожен крок оголошується. Дошка, якою не можна
     керувати з клавіатури, недоступна цілому класу людей — і це не крайній
     випадок, а звичайна робота при травмі руки. */
  PV.dnd = function (doshka, o) {
    o = o || {};
    var vzyato = null;
    function kolonky() { return $$('[data-kolonka]', doshka); }

    doshka.addEventListener('dragstart', function (e) {
      var k = e.target.closest && e.target.closest('[data-kartka]');
      if (!k) return;
      vzyato = k; k.setAttribute('data-stan', 'peretyah');
      e.dataTransfer.effectAllowed = 'move';
    });
    doshka.addEventListener('dragover', function (e) {
      var kol = e.target.closest && e.target.closest('[data-kolonka]');
      if (!kol || !vzyato) return;
      e.preventDefault(); kol.setAttribute('data-stan', 'tsil');
    });
    doshka.addEventListener('dragleave', function (e) {
      var kol = e.target.closest && e.target.closest('[data-kolonka]');
      if (kol) kol.removeAttribute('data-stan');
    });
    doshka.addEventListener('drop', function (e) {
      var kol = e.target.closest && e.target.closest('[data-kolonka]');
      if (!kol || !vzyato) return;
      e.preventDefault(); kol.removeAttribute('data-stan');
      perenesty(vzyato, kol);
    });
    doshka.addEventListener('dragend', function () {
      if (vzyato) vzyato.removeAttribute('data-stan');
      kolonky().forEach(function (k) { k.removeAttribute('data-stan'); });
      vzyato = null;
    });

    function perenesty(kartka, kol) {
      var zvidky = kartka.closest('[data-kolonka]');
      if (zvidky === kol) return;
      var zrobyty = function () { kol.appendChild(kartka); };
      // Підпис IX.flipReorder — (контейнер, mutate, opts), а НЕ (контейнер,
      // селектор, fn): перша версія передавала рядок другим аргументом і падала
      // з «mutate is not a function». Гейт-6 спіймав це першим прогоном
      // 08.08.2026 — саме тому поведінку теж треба міряти, а не оглядати.
      if (IX.flipReorder && ruh()) {
        IX.flipReorder(doshka, zrobyty, { itemSelector: '[data-kartka]' });
      } else zrobyty();
      kartka.removeAttribute('data-stan');
      kazhy(T('перенесено в ') + (kol.getAttribute('aria-label') || kol.dataset.kolonka));
      o.onPerenos && o.onPerenos(kartka, kol, zvidky);
    }

    // Клавіатурний шлях
    doshka.addEventListener('keydown', function (e) {
      var k = e.target.closest && e.target.closest('[data-kartka]');
      if (!k) return;
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        if (!vzyato) {
          vzyato = k; k.setAttribute('data-stan', 'peretyah');
          kazhy(T('взято. Стрілки — перенести, пробіл — покласти, Esc — повернути'));
        } else { vzyato.removeAttribute('data-stan'); vzyato = null; kazhy(T('покладено')); }
        return;
      }
      if (e.key === 'Escape' && vzyato) {
        vzyato.removeAttribute('data-stan'); vzyato = null; kazhy(T('повернено')); return;
      }
      if (vzyato && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault();
        var ks = kolonky(), i = ks.indexOf(vzyato.closest('[data-kolonka]'));
        var j = Math.max(0, Math.min(ks.length - 1, i + (e.key === 'ArrowRight' ? 1 : -1)));
        if (j !== i) { perenesty(vzyato, ks[j]); vzyato.focus(); }
      }
    });
  };

  /* ── §7 ВАРТОВИЙ НЕЗБЕРЕЖЕНОГО ────────────────────────────────────────────
     Найчастіша втрата даних у CRM із повним POST — піти зі сторінки з
     заповненою формою. Браузерне попередження працює лише на закриття вкладки,
     тому внутрішні переходи ловимо самі. */
  PV.vartovyi = function (forma, o) {
    o = o || {};
    var brudno = false;
    forma.addEventListener('input', function () { brudno = true; });
    forma.addEventListener('submit', function () { brudno = false; });
    addEventListener('beforeunload', function (e) {
      if (!brudno) return;
      e.preventDefault(); e.returnValue = '';
    });
    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[href]');
      if (!a || !brudno || a.target === '_blank') return;
      e.preventDefault();
      PV.pidtverdyty({
        zaholovok: T('Піти без збереження?'),
        naslidky: o.tekst || T('У формі є незбережені зміни. Якщо піти зараз, вони зникнуть.'),
        tak: T('Піти'), ni: T('Лишитись'), ruynivna: true
      }).then(function (ok) { if (ok) { brudno = false; location.href = a.href; } });
    });
    return { chysto: function () { brudno = false; }, brudno: function () { return brudno; } };
  };

  /* ── §8 ГАРЯЧІ КЛАВІШІ + ШПАРГАЛКА ───────────────────────────────────────
     Правило: клавіші не працюють, поки фокус у полі вводу — інакше «j»
     у назві контрагента гортає список. Шпаргалка на «?» — бо клавіші, про
     які ніхто не знає, не існують. */
  PV.klavishi = function (mapa, o) {
    o = o || {};
    var poslidovnist = '', tid = null;
    function vPoli(t) {
      return t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable);
    }
    document.addEventListener('keydown', function (e) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (vPoli(e.target)) return;
      if (e.key === '?') { e.preventDefault(); return PV.shpargalka(mapa); }

      poslidovnist += e.key.toLowerCase();
      if (tid) clearTimeout(tid);
      tid = setTimeout(function () { poslidovnist = ''; }, 700);

      var kluchi = Object.keys(mapa);
      for (var i = 0; i < kluchi.length; i++) {
        if (poslidovnist.slice(-kluchi[i].length) === kluchi[i]) {
          e.preventDefault(); poslidovnist = '';
          mapa[kluchi[i]].fn ? mapa[kluchi[i]].fn() : mapa[kluchi[i]]();
          return;
        }
      }
    });
  };
  /* Шпаргалка. Будується як звичайний діалог, а не поверх `pidtverdyty`:
     перша версія намагалась дописати вміст у вже відкрите вікно через
     `:last-of-type` і оператор кому — не працювала й читалась як хитрість. */
  PV.shpargalka = function (mapa) {
    var d = document.createElement('dialog');
    d.className = 'k-dialoh';
    var h = document.createElement('div');
    h.className = 'shapka'; h.textContent = T('Гарячі клавіші');
    d.appendChild(h);
    var tabl = document.createElement('div');
    tabl.style.cssText = 'display:grid;grid-template-columns:auto 1fr;' +
                         'gap:var(--sp2,8px) var(--sp4,16px);align-items:baseline';
    Object.keys(mapa).forEach(function (k) {
      var kb = document.createElement('kbd');
      kb.textContent = k;
      kb.style.cssText = 'font-family:var(--f-kod,monospace);border:1px solid var(--mezha-m);' +
                         'border-radius:var(--r-xs,4px);padding:1px 6px;font-size:.85em';
      var op = document.createElement('span');
      op.textContent = mapa[k].opys || '';
      tabl.appendChild(kb); tabl.appendChild(op);
    });
    d.appendChild(tabl);
    var diyi = document.createElement('div'); diyi.className = 'diyi';
    var ok = document.createElement('button');
    ok.className = 'k-btn'; ok.textContent = T('Закрити');
    diyi.appendChild(ok); d.appendChild(diyi);
    document.body.appendChild(d);
    function zakryty() {
      if (d.open) d.close();
      if (d.parentNode) d.parentNode.removeChild(d);
    }
    ok.addEventListener('click', zakryty);
    d.addEventListener('cancel', zakryty);
    d.showModal(); ok.focus();
    return { zakryty: zakryty };
  };

  /* ── §9 ВІРТУАЛІЗАЦІЯ ────────────────────────────────────────────────────
     Вікно над довгим списком. Сервер віддає першу сторінку сам — без JS
     таблиця лишається таблицею; віртуалізація лише прибирає з DOM те, чого
     зараз не видно. На 46 149 рядках це різниця між роботою й підвисанням. */
  PV.virtual = function (spysok, o) {
    o = o || {};
    var vysota = o.vysotaRyadka || 36, zapas = o.zapas || 8;
    var vsi = o.dani || [], malyuvaty = o.malyuvaty;
    if (!malyuvaty) throw new Error(T('PV.virtual: потрібна функція malyuvaty(element, i)'));

    var rozporka = document.createElement('div');
    rozporka.style.cssText = 'position:relative;inline-size:100%';
    rozporka.style.blockSize = (vsi.length * vysota) + 'px';
    var vikno = document.createElement('div');
    vikno.style.cssText = 'position:absolute;inset-inline:0;inset-block-start:0';
    rozporka.appendChild(vikno);
    spysok.innerHTML = ''; spysok.appendChild(rozporka);

    function ramka() {
      var t = spysok.scrollTop, h = spysok.clientHeight;
      var vid = Math.max(0, Math.floor(t / vysota) - zapas);
      var do_ = Math.min(vsi.length, Math.ceil((t + h) / vysota) + zapas);
      vikno.style.transform = 'translateY(' + (vid * vysota) + 'px)';
      vikno.innerHTML = '';
      for (var i = vid; i < do_; i++) {
        var el = document.createElement('div');
        el.style.blockSize = vysota + 'px';
        malyuvaty(el, i, vsi[i]);
        vikno.appendChild(el);
      }
    }
    var chekaye = false;
    spysok.addEventListener('scroll', function () {
      if (chekaye) return;
      chekaye = true;
      requestAnimationFrame(function () { chekaye = false; ramka(); });
    });
    ramka();
    return { onovyty: function (novi) {
      vsi = novi; rozporka.style.blockSize = (vsi.length * vysota) + 'px'; ramka();
    } };
  };

  /* ── §10 МІСТ ДО HTMX ────────────────────────────────────────────────────
     Оптимізм і HTMX мусять бути ОДНИМ шляхом, інакше в продукті зʼявляться два
     різні способи показати «зайнято» і два різні способи впасти. Перехід форм
     на `hx-post` — лише за явним `data-optym` і лише для оборотних дій. */
  PV.htmxMist = function () {
    if (!root.htmx) return false;
    document.body.addEventListener('htmx:beforeRequest', function (e) {
      var t = e.detail.elt; t.setAttribute('aria-busy', 'true');
    });
    document.body.addEventListener('htmx:afterRequest', function (e) {
      e.detail.elt.removeAttribute('aria-busy');
    });
    ['htmx:responseError', 'htmx:sendError'].forEach(function (podiya) {
      document.body.addEventListener(podiya, function (e) {
        e.detail.elt.removeAttribute('aria-busy');
        PV.toast({
          tekst: podiya === 'htmx:sendError'
            ? T('Немає звʼязку з сервером — зміни не збережено')
            : T('Сервер відповів помилкою — зміни не збережено'),
          ton: 'pomylka', trymaty: 0
        });
      });
    });
    return true;
  };

  PV.versiya = '1.0.0';
  root.PV = PV;
  if (typeof module !== 'undefined' && module.exports) module.exports = PV;
})(typeof window !== 'undefined' ? window : this);
