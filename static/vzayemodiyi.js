// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mykhailo Zvir

/* ═══ ВЗАЄМОДІЇ ════════════════════════════════════════════════════════════
   Командна палітра · перемикачі напряму/теми/щільності/руху · рейка ·
   анімовані числа. Без бібліотек: Chrome 148 має все потрібне рідно.
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  var D = document, R = D.documentElement;

  /* ── Налаштування вигляду ────────────────────────────────────────────────
     Читаються ще в <head> (щоб не блимало), тут — лише перемикання. */
  function zberehty(klyuch, znach) {
    try { localStorage.setItem("ofis-" + klyuch, znach); } catch (e) {}
  }
  function vstanovyty(atr, znach) {
    R.dataset[atr] = znach;
    zberehty(atr, znach);
    D.dispatchEvent(new CustomEvent("vyhlyad-zmineno", { detail: { atr: atr, znach: znach } }));
  }
  window.ofisVstanovyty = vstanovyty;

  D.addEventListener("click", function (e) {
    var b = e.target.closest("[data-vstanovyty]");
    if (!b) return;
    e.preventDefault();
    var p = b.dataset.vstanovyty.split(":");
    vstanovyty(p[0], p[1]);
    // Сегментований перемикач: підсвітити активну кнопку одразу, не чекаючи
    // перезавантаження — інакше натискання виглядає як «не спрацювало».
    // ОБИДВІ ШКІРИ: `studiya` групує кнопки в `.segment`, `hlybyna` — у
    // `.k-hrupa` бібліотеки. Селектор знав лише перший клас, тож у новій шкірі
    // натискання профілю мовчки нічого не підсвічувало (знайдено 09.08.2026
    // при переході на `hlybyna` як типову).
    var grupa = b.closest(".segment, .k-hrupa");
    if (grupa) grupa.querySelectorAll("button[data-vstanovyty]").forEach(function (x) {
      x.setAttribute("aria-pressed", String(x === b));
    });
  });

  /* Звірка при завантаженні. Шаблон друкує `aria-pressed` статично, а чинний
     вибір живе в localStorage — тож без цього кроку кнопки показують ЧУЖИЙ
     стан: людина бачить «Виразно» натиснутим, хоча в неї «Лише 3D». Стан, який
     бреше читачеві екрана, гірший за відсутній. */
  D.querySelectorAll("[data-vstanovyty]").forEach(function (b) {
    var p = b.dataset.vstanovyty.split(":");
    b.setAttribute("aria-pressed", String(R.dataset[p[0]] === p[1]));
  });

  /* ── Фільтр застосовується одразу ────────────────────────────────────────
     Шість фільтр-форм сабмітились із `onchange="this.form.submit()"` на КОЖНОМУ
     полі. Позначка переїхала на саму форму: намір «фільтр діє одразу» належить
     формі, а не кожному `select` окремо — і додаючи сьоме поле, вже не треба
     памʼятати про атрибут. Заразом зникає останній інлайновий обробник, тобто
     остання причина тримати `unsafe-inline` у CSP. */
  D.addEventListener("change", function (e) {
    var forma = e.target.closest("form[data-sabmit-na-zminu]");
    if (!forma) return;
    // `requestSubmit`, а не `submit()`: перший проходить валідацію й породжує
    // подію `submit` — саме ту, на якій тримається відгук «зберігається».
    if (forma.requestSubmit) forma.requestSubmit();
    else forma.submit();
  });

  /* ── Розкрити й навести фокус ────────────────────────────────────────────
     До 06.08.2026 це робили сім інлайнових `onclick`, кожен зі своїм способом
     дістатись потрібного вузла. Три біди одразу: кожен такий атрибут — дірка
     під CSP (проєкт увесь self-contained, це була єдина причина тримати
     `unsafe-inline`); машинний аудит не бачить у них дії й рахує кнопку
     мертвою; і сім варіантів одного наміру розповзаються при першій же правці.

     Конвенція замість цього:
       data-vidkryty="<селектор>"  — розкрити <details> (з data-perem — перемкнути)
       data-fokus="<селектор>"     — навести фокус
       data-fokus-v="<селектор>"   — шукати поле ВСЕРЕДИНІ цього предка, а не по
                                     всій сторінці: блок співпраці є на кожній
                                     картці, і глобальний пошук навів би фокус
                                     у чужий екземпляр
       data-fokus-klik             — не фокус, а клік (діалог вибору файлу) */
  D.addEventListener("click", function (e) {
    var b = e.target.closest("[data-vidkryty],[data-fokus]");
    if (!b) return;

    var vidkr = b.dataset.vidkryty;
    var vuzol = null;
    if (vidkr) {
      vuzol = D.querySelector(vidkr);
      if (vuzol) vuzol.open = "perem" in b.dataset ? !vuzol.open : true;
    }

    var sel = b.dataset.fokus;
    if (!sel) return;
    // Розкриття щойно змінило розкладку — фокус ставимо наступним тактом,
    // інакше браузер наводить його на ще прихований елемент і мовчки губить.
    setTimeout(function () {
      var korin = D;
      if (b.dataset.fokusV) korin = b.closest(b.dataset.fokusV)
                                 || D.querySelector(b.dataset.fokusV) || D;
      var cil = korin.querySelector(sel);
      if (!cil) return;
      if ("fokusKlik" in b.dataset) cil.click();
      else { cil.focus(); cil.scrollIntoView({ block: "nearest" }); }
    }, 0);
  });

  /* ── Тема: перемикання циклом світла → темна ─────────────────────────────── */
  D.addEventListener("click", function (e) {
    if (!e.target.closest("#tema")) return;
    e.preventDefault();
    vstanovyty("tema", R.dataset.tema === "dark" ? "light" : "dark");
    // карта тримає кольори маркерів у JS — без події лишиться в старій темі
    D.dispatchEvent(new Event("tema-zminena"));
  });

  /* ── Рейка: згортання й висувний режим ──────────────────────────────────── */
  var shell = D.getElementById("shell");
  if (shell) {
    if (R.dataset.rejka === "zgornuto") shell.classList.add("zgornuto");
    var zg = D.getElementById("zgornuty");
    if (zg) zg.addEventListener("click", function () {
      var z = shell.classList.toggle("zgornuto");
      R.dataset.rejka = z ? "zgornuto" : "";
      zberehty("rejka", z ? "zgornuto" : "rozgornuto");
      this.setAttribute("aria-label", z ? T("Розгорнути рейку") : T("Згорнути рейку"));
    });

    var menyu = D.getElementById("menyu");
    function zakrytyRejku() {
      shell.classList.remove("vidkryto");
      if (menyu) menyu.setAttribute("aria-expanded", "false");
      var z = D.querySelector(".zatemnennya");
      if (z) z.remove();
    }
    if (menyu) menyu.addEventListener("click", function () {
      var v = shell.classList.toggle("vidkryto");
      this.setAttribute("aria-expanded", String(v));
      if (!v) return zakrytyRejku();
      var z = D.createElement("div");
      z.className = "zatemnennya";
      z.addEventListener("click", zakrytyRejku);
      shell.appendChild(z);
      var p = D.querySelector(".rejka a");
      if (p) p.focus();
    });
    D.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && shell.classList.contains("vidkryto")) {
        zakrytyRejku();
        if (menyu) menyu.focus();
      }
    });
  }

  /* ── КОМАНДНА ПАЛІТРА ⌘K ─────────────────────────────────────────────────
     Popover API бере на себе верхній шар, фокус-пастку й закриття по Esc —
     тобто рівно те, заради чого раніше тягнули бібліотеку. Нам лишається
     клавіатурна навігація по списку. */
  var pal = D.getElementById("palitra");
  if (pal) {
    var pole = D.getElementById("palitra-pole");
    var rez = D.getElementById("palitra-rez");

    function punkty() { return [].slice.call(rez.querySelectorAll("[data-palitra-punkt]")); }
    function obranyi() { return rez.querySelector('[aria-selected="true"]'); }
    function obraty(el) {
      punkty().forEach(function (p) { p.removeAttribute("aria-selected"); });
      if (!el) return;
      el.setAttribute("aria-selected", "true");
      el.scrollIntoView({ block: "nearest" });
    }
    function pershyi() { obraty(punkty()[0]); }

    function vidkryty() {
      if (pal.matches(":popover-open")) return;
      pal.showPopover();
      pole.value = "";
      pole.focus();
      // Порожній запит показує дії й екрани — палітра ніколи не буває порожньою
      if (window.htmx) window.htmx.trigger(pole, "palitra-onovyty");
    }

    D.addEventListener("keydown", function (e) {
      var mod = e.metaKey || e.ctrlKey;
      if (mod && (e.key === "k" || e.key === "K" || e.key === "л" || e.key === "Л")) {
        e.preventDefault();
        pal.matches(":popover-open") ? pal.hidePopover() : vidkryty();
        return;
      }
      if (!pal.matches(":popover-open")) return;
      var p = punkty();
      if (!p.length) return;
      var i = p.indexOf(obranyi());
      if (e.key === "ArrowDown") { e.preventDefault(); obraty(p[(i + 1) % p.length]); }
      else if (e.key === "ArrowUp") { e.preventDefault(); obraty(p[(i - 1 + p.length) % p.length]); }
      else if (e.key === "Home") { e.preventDefault(); obraty(p[0]); }
      else if (e.key === "End") { e.preventDefault(); obraty(p[p.length - 1]); }
      else if (e.key === "Enter") {
        var o = obranyi() || p[0];
        if (o) { e.preventDefault(); pal.hidePopover(); location.href = o.href; }
      }
    });

    D.addEventListener("click", function (e) {
      if (e.target.closest("[data-vidkryty-palitru]")) { e.preventDefault(); vidkryty(); }
    });
    // Після кожної підміни списку виділяємо перший рядок: Enter має спрацьовувати
    // одразу, без жодної стрілки — це і є сенс палітри.
    D.body.addEventListener("htmx:afterSwap", function (e) {
      if (e.detail && e.detail.target && e.detail.target.id === "palitra-rez") pershyi();
    });
    rez.addEventListener("mousemove", function (e) {
      var a = e.target.closest("[data-palitra-punkt]");
      if (a && a !== obranyi()) obraty(a);
    });
  }

  /* ── Стан «зберігається» на КОЖНІЙ формі ─────────────────────────────────
     До 06.08.2026 жодна з 58 форм не давала відгуку на сабміт: людина тисне
     «Зберегти», сторінка ще думає, і незрозуміло, чи натиснулось узагалі. Типова
     реакція — натиснути ще раз, тобто відправити двічі.

     Один делегований обробник замість правки 58 шаблонів. Ловимо на фазі
     ЗАХОПЛЕННЯ: інакше HTMX встигає обробити подію раніше.

     Що НЕ робимо: не блокуємо саму форму (`disabled` на полях зняв би їх зі
     сабміту) і не ставимо `disabled` на кнопку до відправлення — браузер тоді
     не передасть її `name`/`value`, а частина форм на цьому тримається
     (`name="kudy"` у порядку стадій). Тому кнопка глушиться ПІСЛЯ, у
     мікрозадачі, коли дані вже пішли. */
  D.addEventListener("submit", function (e) {
    var forma = e.target;
    if (!(forma instanceof HTMLFormElement) || forma.dataset.bezVidhuku) return;

    var knopka = forma.querySelector(
      'button[type=submit],input[type=submit],button:not([type])');

    // Шість фільтр-форм кнопки не мають узагалі: вони сабмітяться з `onchange`
    // самого `select`. Глушити там нічого, подвійна відправка неможлива — але
    // відгук потрібен так само: людина міняє фільтр і секунду дивиться на старі
    // рядки, не знаючи, чи запит пішов. Тому позначаємо саму форму, а смужку
    // під нею малює CSS.
    if (!knopka) {
      forma.setAttribute("aria-busy", "true");
      return;
    }
    if (knopka.dataset.zaynyata) return;

    knopka.dataset.zaynyata = "1";
    setTimeout(function () {
      // `aria-busy` — на КНОПЦІ: CSS уже має під неї спінер
      // (`komponenty.css:236` — текст прозорий, обертається кільце).
      // Це працює й на іконкових кнопках, на відміну від підміни тексту,
      // і читач екрана отримує той самий сигнал, що й око.
      knopka.setAttribute("aria-busy", "true");
      knopka.disabled = true;
    }, 0);

    // HTMX не перезавантажує сторінку — там кнопку треба відпустити самим.
    // Для звичайного сабміту цього не робимо: сторінка й так поїде.
    if (forma.hasAttribute("hx-post") || forma.hasAttribute("hx-get")) {
      forma.addEventListener("htmx:afterRequest", function vidpustyty() {
        knopka.removeAttribute("aria-busy");
        knopka.disabled = false;
        delete knopka.dataset.zaynyata;
        forma.removeEventListener("htmx:afterRequest", vidpustyty);
      });
    }
  }, true);

  /* Повернення «назад» показує сторінку з bfcache — разом із заглушеною
     кнопкою. Без цього форма лишається мертвою після Alt+←. */
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    D.querySelectorAll("[data-zaynyata]").forEach(function (k) {
      k.removeAttribute("aria-busy");
      k.disabled = false;
      delete k.dataset.zaynyata;
    });
  });

  /* ── Оголошення для читача екрана ────────────────────────────────────────
     Стрічка (60 с), лічильники (30 с) і чат (5 с) оновлюються полінгом: на
     екрані число просто змінюється, і незрячий користувач не дізнається про це
     ніколи. Регіон один на систему — `#ogoloshennya` у `base.html`.

     Оголошуємо лише те, що СТАЛО ІНШИМ. Полінг щохвилини повертає переважно ту
     саму розмітку; якщо озвучувати кожну відповідь, читач екрана перетвориться
     на дзижчання, і людина його вимкне — тобто вийде гірше, ніж було. */
  var region = D.getElementById("ogoloshennya");
  function skazaty(tekst) {
    if (!region || !tekst) return;
    // Той самий текст поспіль читач екрана може проігнорувати як «без змін» —
    // тому спершу чистимо, і лише потім кладемо новий.
    // Таймер, а НЕ `requestAnimationFrame`: у фоновій (неотрендереній) вкладці
    // кадри не малюються взагалі, і оголошення не пролунало б ніколи — а саме
    // фонова вкладка й є типовим станом вікна, куди приходить полінг.
    region.textContent = "";
    setTimeout(function () { region.textContent = tekst; }, 50);
  }
  window.ofisSkazaty = skazaty;

  if (region) {
    var buloChyslo = {};   // id цілі → останнє озвучене число
    D.body.addEventListener("htmx:afterSwap", function (e) {
      var cil = e.detail && e.detail.target;
      if (!cil) return;

      // Лічильники: у розмітці число живе в `.n`, а його зміст — в `aria-label`
      // або `title` пункту. Беремо число, бо саме воно є новиною.
      var lich = cil.matches && cil.matches("#lich,[hx-get*='lichylnyk']")
        ? cil : null;
      if (lich) {
        var n = lich.querySelector(".n");
        var znach = n ? n.textContent.trim() : "0";
        var klyuch = lich.id || lich.getAttribute("hx-get");
        if (buloChyslo[klyuch] === znach) return;
        buloChyslo[klyuch] = znach;
        var pidpys = (lich.querySelector(".nz") || {}).textContent || T("Сповіщення");
        skazaty(znach === "0"
          ? pidpys + T(": нічого нового")
          : pidpys + ": " + znach + T(" нових"));
        return;
      }

      // Стрічка й потік чату: рахуємо рядки, що додались.
      if (cil.id === "strichka" || cil.id === "potik" || cil.id === "hvist") {
        var kilkist = cil.children.length;
        var bulo = buloChyslo[cil.id];
        buloChyslo[cil.id] = kilkist;
        if (bulo === undefined || kilkist <= bulo) return;
        var dodano = kilkist - bulo;
        skazaty(dodano === 1 ? T("Один новий запис") : dodano + T(" нових записів"));
      }
    });
  }

  /* ── Skeleton на HTMX-цілях ──────────────────────────────────────────────
     `.skelet` існував лише у вітрині `/dyzayn` і не був підключений ніде.
     Ставимо його на час запиту — але ЛИШЕ там, де підміняється вміст блоку
     (палітра, стрічка, довідник), і НЕ на полінгу: фонове оновлення раз на
     хвилину не має мигати сірими смугами під носом у людини, яка читає. */
  D.body.addEventListener("htmx:beforeRequest", function (e) {
    var cil = e.detail && e.detail.target;
    if (!cil || !cil.matches || !cil.matches("[data-skelet]")) return;
    // Полінг видно по тригеру `every` — його пропускаємо.
    var tryger = (e.detail.elt && e.detail.elt.getAttribute("hx-trigger")) || "";
    if (tryger.indexOf("every") !== -1) return;
    if (cil.dataset.skeletAktyvnyi) return;
    cil.dataset.skeletAktyvnyi = "1";
    cil.dataset.skeletBulo = cil.innerHTML;
    cil.innerHTML =
      '<div class="skelet" aria-hidden="true"><i></i><i></i><i></i></div>';
  });
  D.body.addEventListener("htmx:afterSwap", function (e) {
    var cil = e.detail && e.detail.target;
    if (cil && cil.dataset && cil.dataset.skeletAktyvnyi) {
      delete cil.dataset.skeletAktyvnyi;
      delete cil.dataset.skeletBulo;
    }
  });
  // Запит упав — підміни не буде, і скелет лишився б назавжди.
  D.body.addEventListener("htmx:responseError", vidnovyty);
  D.body.addEventListener("htmx:sendError", vidnovyty);
  function vidnovyty(e) {
    var cil = e.detail && e.detail.target;
    if (!cil || !cil.dataset || !cil.dataset.skeletAktyvnyi) return;
    cil.innerHTML = cil.dataset.skeletBulo || "";
    delete cil.dataset.skeletAktyvnyi;
    delete cil.dataset.skeletBulo;
    skazaty(T("Не вдалося оновити — перевірте зʼєднання"));
  }

  /* ── Анімовані числа ─────────────────────────────────────────────────────
     Саме число малює CSS (@property --chyslo). JS лише задає кінцеве значення
     після появи елемента у вьюпорті — щоб лічильник не «відкрутився» у фоні,
     поки на нього ніхто не дивиться. */
  var chysla = D.querySelectorAll("[data-chyslo]");

  /* Форматування — те саме, що у фільтра `num` на сервері: нерозривний пробіл
     через кожні три розряди. Раніше число малював CSS-лічильник (`counter(n)`),
     а він групувати не вміє — і в одній картці стояло «46149» великим числом і
     «51 416 осіб» підписом просто під ним. Два формати одного роду в одному
     блоці; помітно з першого погляду, але жоден гейт такого не бачить. */
  function rozdilyty(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  /* Число малює СЕРВЕР, тут лише відкручування на очах. Порядок саме такий:
     елемент, що чекає на JS, лишається порожнім скрізь, де спостерігач не
     спрацює — нижче згину, у прихованій рамці, при вимкненому JS. Виявлено
     07.08.2026: у зонді велике число було пусте, тоді як читач екрана чув
     правильні «1 200». Зміст первинний, анімація — надбудова. */
  function vidkrutyty(el) {
    var cil = parseInt(el.dataset.chyslo, 10) || 0;
    // Приглушений рух — одразу кінцеве значення: лічильник, що біжить, тут
    // саме той рух, від якого людині зле.
    var ruh = parseFloat(getComputedStyle(R).getPropertyValue("--ruh")) || 0;
    if (!ruh || cil < 2) { el.textContent = rozdilyty(cil); return; }

    var tryv = parseFloat(getComputedStyle(R).getPropertyValue("--t-slow")) || 320;
    var pochatok = performance.now();
    (function krok(teper) {
      var chastka = Math.min(1, (teper - pochatok) / tryv);
      // те саме сповільнення на виході, що в решти руху системи
      var m = 1 - Math.pow(1 - chastka, 3);
      el.textContent = rozdilyty(Math.round(cil * m));
      if (chastka < 1) requestAnimationFrame(krok);
    })(pochatok);
  }

  if (chysla.length && "IntersectionObserver" in window) {
    var sposter = new IntersectionObserver(function (zapysy) {
      zapysy.forEach(function (z) {
        if (!z.isIntersecting) return;
        vidkrutyty(z.target);
        sposter.unobserve(z.target);
      });
    }, { rootMargin: "0px 0px -10% 0px" });
    chysla.forEach(function (el) { sposter.observe(el); });
  }
  // Спостерігача немає — нічого не робимо: число вже стоїть у розмітці.
})();
