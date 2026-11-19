/* biogaz.js — карта біоенергетики. Специфіка напряму: категорії сировини,
 * пороги інфраструктури, налаштовувані ваги придатності, шар хабів.
 * Усе спільне (кластеризація, шухляда, CSV, стан у посиланні) — у yadro.js.
 */
import {
  SharTochok, SharyGIS, Shuhlyada, StanURL, blyzkist, chyslo, ekranuy, eksportCsv,
  kolirZaBalom, pokazhyMeta, stvoryKartu, tokeny, zavantazh,
  spysokYakShuhlyada
} from './yadro.js';

const KATEGORIYI = {
  A_biogas_wet: window.T('Вологі (гній) → біогаз'),
  B_solid_biomass: window.T('Суха біомаса → тверде паливо'),
  C_poultry: window.T('Пташиний послід'),
  C_landfill: window.T('Полігон ТПВ → звалищний газ'),
  C_sludge: window.T('Очисні споруди → мул'),
  FACT_EXPERT: window.T('Фабрика (експертний список)'),
  other: window.T('Інше / не визначено'),
};

// Категорії ПІДПРИЄМСТВА (ТЗ Костянтина 25.08.2026). Слаги — з
// app/services/kategoriyi.py; людські назви й ієрархія — рівно з ТЗ.
// `hrupa` збирає підтипи в три гілки ТЗ: тваринництво / переробка / рослинництво.
const KP = {
  vrh: [window.T('ВРХ'), 'tvar'], svyni: [window.T('Свинарство'), 'tvar'],
  ptytsia: [window.T('Птахівництво'), 'tvar'], tvarynnytstvo: [window.T('Тваринництво (інше/н/д)'), 'tvar'],
  zmishane: [window.T('Змішане (потребує уточнення)'), 'tvar'],
  spyrt: [window.T('Спиртовий/біоетанольний'), 'per'], tsukor: [window.T('Цукровий завод'), 'per'],
  moloko: [window.T('Молокозавод'), 'per'], miaso: [window.T('Мʼясокомбінат'), 'per'],
  oliya: [window.T('Олійноекстракційний'), 'per'], krokhmal: [window.T('Крохмальний'), 'per'],
  kombikorm: [window.T('Комбікормовий'), 'per'], pyvo: [window.T('Пивоварня'), 'per'],
  kharch: [window.T('Інше харчове'), 'per'],
  roslynnytstvo: [window.T('Рослинництво'), 'ros'],
};
const KP_HRUPY = { tvar: window.T('Тваринництво'), per: window.T('Агропереробка'), ros: window.T('Рослинництво') };

// Пороги «далеко» для нормування близькості. Беруться зі шаблону оригінальної
// карти: далі 40 км газ уже не рятує економіку, далі 15 км дорога — теж.
const MEZHI = { gaz: 40, merezha: 30, doroha: 15 };
const OPORA_KW = 500;          // кВт, за яких внесок «обсяг сировини» = 1

// Ділянка будівництва (радіус-фільтр із ТЗ): {lat, lon} або null.
let DILYANKA = null;
let sharDilyanky = null;

// Haversine — формула зі скіла geodani (geo_sanitary.py), не Піфагор:
// на радіусах 30-50 км площина вже помітно бреше.
const R_ZEMLI_KM = 6371.0088;
function haversineKm(lat1, lon1, lat2, lon2) {
  const p = Math.PI / 180;
  const a = Math.sin((lat2 - lat1) * p / 2) ** 2
    + Math.cos(lat1 * p) * Math.cos(lat2 * p) * Math.sin((lon2 - lon1) * p / 2) ** 2;
  return 2 * R_ZEMLI_KM * Math.asin(Math.sqrt(a));
}

const $ = id => document.getElementById(id);
let T = tokeny();
let ROWS = [], HABY = [], META = {};
let shar = null, sharHabiv = null, shuhlyada = null, map = null, gis = null;

const vahy = () => ({
  gaz: +$('wGas').value, merezha: +$('wPow').value,
  doroha: +$('wRoad').value, syrovyna: +$('wFeed').value,
});

/** Живий бал придатності 0–100 за поточними вагами. */
function bal(r) {
  const w = vahy();
  const suma = w.gaz + w.merezha + w.doroha + w.syrovyna || 1;
  // Потужність невідома → внесок сировини 0, а не «як у нуля»: це різні стани,
  // і саме тому такі обʼєкти живуть окремим класом «потенційні»
  const syr = r.kw ? Math.min(1, r.kw / OPORA_KW) : 0;
  return Math.round(100 * (
    w.gaz * blyzkist(r.d_gaz, MEZHI.gaz) +
    w.merezha * blyzkist(r.d_merezha, MEZHI.merezha) +
    w.doroha * blyzkist(r.d_doroha, MEZHI.doroha) +
    w.syrovyna * syr) / suma);
}

const kategoriya = r => KATEGORIYI[r.kat] ? r.kat : 'other';
const potencijna = r => r.kw == null || r.kw <= 0;
const hruba = r => ['oblast', 'raion', ''].includes(r.tochnist);

// ── фільтрація ─────────────────────────────────────────────────────────────
// Поріг за каноном Г15: «невідомо» ≠ «нуль». Відсіює лише тих, у кого дані Є
// і вони нижчі за поріг; для «немає даних» рішення приймає людина прапорцем
// `bezDanyh`, а не мовчазний фільтр.
function porih(znach, min, pokazuvatyND) {
  if (!min) return true;
  if (znach == null || znach === '') return pokazuvatyND;
  return +znach >= min;
}

function vidibrani() {
  const q = $('q').value.trim().toLowerCase();
  const katOff = new Set([...document.querySelectorAll('[data-kat]:not(:checked)')]
    .map(x => x.dataset.kat));
  const oblOff = new Set([...document.querySelectorAll('[data-obl]:not(:checked)')]
    .map(x => x.dataset.obl));
  // Категорія підприємства: МНОЖИННА логіка «будь-яка з увімкнених». Обʼєкт
  // без категорії живе під власним прапорцем — щоб 9% непокритих не зникали
  // мовчки (Г15), але їх можна було прибрати свідомо.
  const kpOff = new Set([...document.querySelectorAll('[data-kp]:not(:checked)')]
    .map(x => x.dataset.kp));
  // Реальна операція (ДПСС/поголівʼя) — окрема вісь від категорії (рішення
  // Костянтина 27.08): нею фільтруються майданчики й компанії з поголівʼям.
  const opOff = new Set([...document.querySelectorAll('[data-op]:not(:checked)')]
    .map(x => x.dataset.op));
  const stOff = new Set([...document.querySelectorAll('[data-st]:not(:checked)')]
    .map(x => x.dataset.st));
  const minkw = +$('minkw').value, maxGas = +$('maxGas').value;
  const maxPow = +$('maxPower').value;
  const minVrh = +$('minVrh').value, minSvyni = +$('minSvyni').value;
  const minPtytsia = +$('minPtytsia').value;
  const minBiometan = +$('minBiometan').value * 1e6;   // повзунок у млн м³
  const minZemlya = +$('minZemlya').value;
  const nd = $('bezDanyh').checked;
  const finOnly = $('finOnly').checked, showPot = $('showPot').checked;
  const showHrubi = $('showHrubi').checked;
  const hromada = $('hromadaSel').value;
  const vRadiusi = DILYANKA && $('dilyankaFiltr').checked
    ? +$('dilyankaR').value : 0;

  const showMajd = $('showMajd') ? $('showMajd').checked : true;

  return ROWS.filter(r => {
    if (r.m && !showMajd) return false;
    if (!showHrubi && hruba(r)) return false;
    if (katOff.has(kategoriya(r))) return false;
    if (oblOff.has(r.oblast || '')) return false;
    // Майданчик — це реальна ОПЕРАЦІЯ (без КВЕД): фільтрується осями операцій,
    // не категорій. Компанія — навпаки, за категорією (осн. КВЕД).
    if (r.m) {
      const ops = (r.operatsiyi || '').split(',').filter(Boolean);
      if (ops.length ? ops.every(k => opOff.has(k)) : opOff.has('bez_op')) return false;
    } else {
      const kats = (r.kat_pidpr || '').split(',').filter(Boolean);
      if (kats.length ? kats.every(k => kpOff.has(k)) : kpOff.has('bez')) return false;
    }
    if (stOff.size && stOff.has(r.status || '')) return false;
    if (hromada && r.hromada !== hromada) return false;
    if (potencijna(r)) { if (!showPot) return false; }
    else if (minkw && r.kw < minkw) return false;
    // Немає даних про відстань — не відсіюємо: «невідомо» ≠ «далеко»
    if (maxGas && r.d_gaz != null && r.d_gaz > maxGas) return false;
    if (maxPow && r.d_merezha != null && r.d_merezha > maxPow) return false;
    if (!porih(r.vrh, minVrh, nd)) return false;
    if (!porih(r.svyni, minSvyni, nd)) return false;
    if (!porih(r.ptytsia, minPtytsia, nd)) return false;
    if (!porih(r.biometan_m3, minBiometan, nd)) return false;
    if (!porih(r.zemlya_ha, minZemlya, nd)) return false;
    if (vRadiusi &&
        haversineKm(DILYANKA.lat, DILYANKA.lon, r.lat, r.lon) > vRadiusi) {
      return false;
    }
    if (finOnly && r.fin !== 1) return false;
    if (q && !((r.nazva || '').toLowerCase().includes(q) ||
               String(r.edrpou || '').includes(q))) return false;
    return true;
  });
}

// ── вигляд маркера ─────────────────────────────────────────────────────────
const KOLORY_KAT = () => ({
  A_biogas_wet: T.dobre, B_solid_biomass: T.uvaha, C_poultry: T.krytych,
  C_landfill: T.prygl, C_sludge: T.akcent, FACT_EXPERT: T.akcent, other: T.prygl,
});

function kolir(r) {
  if (r.m) {                              // майданчик фарбує РЕАЛЬНА операція
    const k = (r.operatsiyi || '').split(',')[0];
    return KOLORY_KP()[k] || T.akcent;
  }
  if ($('colorScore').checked) return kolirZaBalom(bal(r), T);
  return KOLORY_KAT()[kategoriya(r)] || T.prygl;
}

const KOLORY_KP = () => ({
  vrh: T.dobre, svyni: T.krytych, ptytsia: T.uvaha, tvarynnytstvo: T.prygl,
  zmishane: T.prygl,
});

function radius(r) {
  if (r.m) return 4;                      // майданчик: малий, форма скаже своє
  if (potencijna(r)) return 4.5;
  return Math.max(4.5, Math.min(17, 4 + Math.sqrt(r.kw) / 3.2));
}

// ── рендер ─────────────────────────────────────────────────────────────────
function peremaluy() {
  const rows = vidibrani();
  shar.onovyty(rows);
  pokazhyMeta($('meta'), META, rows.length);
  spysok(rows);
  onovyZvity(rows);
  zberehtyStan();
}

function spysok(rows) {
  const sort = $('sort').value;
  const kopiya = rows.slice();
  if (sort === 'bal') kopiya.sort((a, b) => bal(b) - bal(a));
  else if (sort === 'kw') kopiya.sort((a, b) => (b.kw || 0) - (a.kw || 0));
  else kopiya.sort((a, b) => (a.nazva || '').localeCompare(b.nazva || '', 'uk'));

  const top = kopiya.slice(0, 100);
  $('spysokN').textContent = rows.length;
  $('spysok').innerHTML = top.map(r => `
    <li tabindex="0" data-id="${r.id}">
      <div class="t">${ekranuy(r.nazva)}</div>
      <div class="d"><span>${r.kw ? chyslo(r.kw) + ' кВт' : 'потужність н/д'}</span>
        <span>${window.T("придатність")} ${bal(r)}</span></div>
    </li>`).join('');
  $('spysokHint').textContent = rows.length > 100
    ? `${window.T("показано перші 100 зі")} ${rows.length} ${window.T("— звузьте фільтри")}` : '';
}

// ── картка в шухляді ───────────────────────────────────────────────────────
function renderKartky(d) {
  const ryad = (t, v) => v == null || v === '' ? '' : `<dt>${t}</dt><dd>${v}</dd>`;
  const k = d.kontakt || {};
  const tz = d.tz || {};
  return `
    <h3>${ekranuy(d.nazva)}</h3>
    <span class="kod">${ekranuy(k.edrpou || d.typ)}</span>
    ${d.hruba ? `<div class="k-zastereh"><b>Координати приблизні</b> —
        ${ekranuy(d.tochnist)}. Для відстаней не використовувати.</div>` : ''}
    ${d.rozbizhnist ? `<div class="k-zastereh k-rozbizhnist"><b>Розбіжність джерел:</b>
        ${ekranuy(d.rozbizhnist)}</div>` : ''}
    ${(d.kat_pidpr || '').split(',').filter(Boolean).map(kk =>
      `<span class="chip">${ekranuy(KP[kk] ? KP[kk][0] : kk)}</span>`).join(' ')}
    ${(d.operatsiyi || '').split(',').filter(Boolean).length ? `<div class="op-poznachka">
      <b>Реальна операція:</b> ${(d.operatsiyi || '').split(',').filter(Boolean)
        .map(kk => `<span class="chip chip-op">${ekranuy(KP[kk] ? KP[kk][0] : kk)}</span>`)
        .join(' ')}<span class="dim"> — ДПСС/поголівʼя</span></div>` : ''}
    <dl>
      ${ryad('Місце', ekranuy(d.misce))}
      ${ryad('Керівник', ekranuy(k.kerivnyk))}
      ${ryad('Адреса', ekranuy(k.adresa))}
      ${ryad('Категорія: звідки', d.kat_dzherelo ? ekranuy(d.kat_dzherelo
        .split('; ').map(p => {
          const [slag, dzh] = p.split('←');
          return (KP[slag] ? KP[slag][0] : slag) + ' — ' + (dzh || '');
        }).join(' · ')) : null)}
      ${ryad('Сировина', ekranuy(d.syrovyna))}
      ${ryad('Сектор', ekranuy(d.sektor))}
      ${ryad('Потужність', d.kw ? chyslo(d.kw) + ' кВт' : 'немає даних')}
      ${ryad('Сировини, т/рік', d.feed_t ? chyslo(d.feed_t) : null)}
      ${ryad('ВРХ / птиця / свині', [d.vrh, d.ptytsya, d.svyni].some(x => x)
        ? [d.vrh, d.ptytsya, d.svyni].map(x => x ? chyslo(x) : '—').join(' / ') : null)}
      ${ryad('Земля, га', d.zemlya_ha ? chyslo(d.zemlya_ha) : null)}
      ${ryad('До газу', d.d_gaz != null ? d.d_gaz + ' км' : null)}
      ${ryad('До е-мережі', d.d_merezha != null ? d.d_merezha + ' км' : null)}
      ${ryad('До дороги', d.d_doroha != null ? d.d_doroha + ' км' : null)}
      ${ryad('ДПСС', ekranuy(d.dpss))}
      ${ryad('Обдзвін', ekranuy(d.status))}
      ${ryad('Телефон', k.telefon ? `<a href="tel:${ekranuy(k.telefon)}">${ekranuy(k.telefon)}</a>` : null)}
      ${ryad('Email', k.email ? `<a href="mailto:${ekranuy(k.email)}">${ekranuy(k.email)}</a>` : null)}
      ${ryad('Дані оновлено', d.dani_onovleno ? `${d.dani_onovleno} (${ekranuy(d.onovleno_z)})` : null)}
    </dl>
    ${tz.capex || tz.irr ? `<h3 data-styl="font-size:14px">Попереднє ТЕО
      <span class="chip warn">PRELIMINARY</span></h3><dl>
      ${ryad('Станція', ekranuy(tz.plant))}
      ${ryad('CAPEX, €', tz.capex ? chyslo(tz.capex) : null)}
      ${ryad('Дохід, €/рік', tz.revenue ? chyslo(tz.revenue) : null)}
      ${ryad('Окупність', tz.payback ? tz.payback + ' р.' : null)}
      ${ryad('IRR', tz.irr ? tz.irr + ' %' : null)}</dl>` : ''}
    ${d.bal ? `<h3 data-styl="font-size:14px">Бал ${d.bal.znachennya}
      <span class="chip">${ekranuy(d.bal.priorytet)}</span></h3>
      <dl>${d.bal.skladovi.map(c => `<dt>${ekranuy(c.label)}${c.nemaye
        ? ' <span class="dim">(немає даних)</span>' : ''}</dt>
        <dd>${c.vnesok.toFixed(1)}</dd>`).join('')}</dl>` : ''}
    ${(d.majdanchyky || []).length ? `<h3 data-styl="font-size:14px">Майданчики
      компанії <span class="chip">${d.majdanchyky.length}</span></h3>
      <ul class="k-spysok">${d.majdanchyky.slice(0, 12).map(m => `
        <li tabindex="0" data-id="${m.id}">
          <div class="t">${ekranuy(m.nazva)}</div>
          <div class="d"><span>${ekranuy(m.misce)}</span>
          ${(m.kat || '').split(',').filter(Boolean).slice(0, 2).map(k =>
            `<span>${ekranuy(KP[k] ? KP[k][0] : k)}</span>`).join('')}</div>
        </li>`).join('')}</ul>` : ''}
    <div class="acts">
      <a class="btn" href="${d.url}">${window.T("Відкрити картку →")}</a>
      ${k.url ? `<a class="btn-quiet" href="${k.url}">Контрагент</a>` : ''}
    </div>`;
}

// ── панелі фільтрів ────────────────────────────────────────────────────────
function pobuduyFiltry(dovidnyky) {
  const kat = {};
  ROWS.forEach(r => { const k = kategoriya(r); kat[k] = (kat[k] || 0) + 1; });
  $('katRyady').innerHTML = Object.entries(kat).sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `<label class="k-ryad">
      <input type="checkbox" data-kat="${k}" checked>
      <span class="krapka" data-styl="background:${KOLORY_KAT()[k] || T.prygl}"></span>
      <span class="nm">${ekranuy(KATEGORIYI[k] || k)}</span>
      <span class="n">${chyslo(n)}</span></label>`).join('');
  $('katN').textContent = Object.keys(kat).length;

  $('oblRyady').innerHTML = (dovidnyky.oblasti || [])
    .map(o => `<label class="k-ryad">
      <input type="checkbox" data-obl="${ekranuy(o.znachennya)}" checked>
      <span class="nm">${ekranuy(o.znachennya)}</span>
      <span class="n">${chyslo(o.n)}</span></label>`).join('');
  $('oblN').textContent = (dovidnyky.oblasti || []).length;

  if ($('majdN')) $('majdN').textContent = chyslo(ROWS.filter(r => r.m).length);
  $('potN').textContent = chyslo(ROWS.filter(r => potencijna(r) && !r.m).length);
  $('hrubiN').textContent = chyslo(ROWS.filter(hruba).length);

  // ── категорія підприємства (СУГОБО осн. КВЕД): лише КОМПАНІЇ, не майданчики ──
  const kp = {};
  let bezKat = 0;
  ROWS.forEach(r => {
    if (r.m) return;                       // майданчик — операція, не категорія
    const kats = (r.kat_pidpr || '').split(',').filter(Boolean);
    if (!kats.length) { bezKat++; return; }
    kats.forEach(k => { kp[k] = (kp[k] || 0) + 1; });
  });
  const hilky = { tvar: [], per: [], ros: [] };
  Object.keys(KP).forEach(k => {
    if (kp[k]) hilky[KP[k][1]].push([k, kp[k]]);
  });
  const ryad = (k, n) => `<label class="k-ryad">
      <input type="checkbox" data-kp="${k}" checked>
      <span class="nm">${ekranuy(KP[k] ? KP[k][0] : k)}</span>
      <span class="n">${chyslo(n)}</span></label>`;
  $('kpRyady').innerHTML = Object.entries(KP_HRUPY).map(([h, nazva]) =>
    hilky[h].length ? `<div class="k-lbl" data-styl="margin-top:6px">${nazva}</div>`
      + hilky[h].map(([k, n]) => ryad(k, n)).join('') : '').join('')
    + (bezKat ? `<label class="k-ryad">
        <input type="checkbox" data-kp="bez" checked>
        <span class="nm dim">${window.T("Без категорії (КВЕД не с/г)")}</span>
        <span class="n">${chyslo(bezKat)}</span></label>` : '');
  $('kpN').textContent = Object.keys(kp).length;

  // ── реальна операція (ДПСС/поголівʼя): окрема вісь; керує майданчиками ──────
  if ($('opRyady')) {
    const opc = {};
    ROWS.forEach(r => (r.operatsiyi || '').split(',').filter(Boolean)
      .forEach(k => { opc[k] = (opc[k] || 0) + 1; }));
    const opRyad = (k, n) => `<label class="k-ryad">
        <input type="checkbox" data-op="${k}" checked>
        <span class="nm">${ekranuy(KP[k] ? KP[k][0] : k)}</span>
        <span class="n">${chyslo(n)}</span></label>`;
    $('opRyady').innerHTML = ['vrh', 'svyni', 'ptytsia', 'tvarynnytstvo']
      .filter(k => opc[k]).map(k => opRyad(k, opc[k])).join('')
      || window.T('<div class="k-lbl dim">немає даних про реальні операції</div>');
    if ($('opN')) $('opN').textContent = chyslo(Object.values(opc)
      .reduce((a, b) => a + b, 0));
  }

  // ── статус переговорів (member_status, покриття 100%) ────────────────────
  const st = {};
  ROWS.forEach(r => { const s = r.status || ''; st[s] = (st[s] || 0) + 1; });
  $('stRyady').innerHTML = Object.entries(st).sort((a, b) => b[1] - a[1])
    .map(([s, n]) => `<label class="k-ryad">
      <input type="checkbox" data-st="${ekranuy(s)}" checked>
      <span class="nm">${ekranuy(s || '(порожньо)')}</span>
      <span class="n">${chyslo(n)}</span></label>`).join('');
  $('stN').textContent = Object.keys(st).length;

  // ── громади: залежний від увімкнених областей список ─────────────────────
  onovyHromady();

  // Лічильники «без даних: N» біля порогів — щоб людина бачила, скільки
  // обʼєктів поріг НЕ зачіпає, бо їх ще не верифікували (Г15).
  const nDataN = (pole) => ROWS.filter(r => r[pole] == null || r[pole] === '').length;
  $('minVrhND').textContent = window.T('н/д: ') + chyslo(nDataN('vrh'));
  $('minSvyniND').textContent = window.T('н/д: ') + chyslo(nDataN('svyni'));
  $('minPtytsiaND').textContent = window.T('н/д: ') + chyslo(nDataN('ptytsia'));
  $('minBiometanND').textContent = window.T('н/д: ') + chyslo(nDataN('biometan_m3'));
  $('minZemlyaND').textContent = window.T('н/д: ') + chyslo(nDataN('zemlya_ha'));
}

function onovyHromady() {
  const oblOn = new Set([...document.querySelectorAll('[data-obl]:checked')]
    .map(x => x.dataset.obl));
  const hr = {};
  ROWS.forEach(r => {
    if (r.hromada && oblOn.has(r.oblast || '')) {
      hr[r.hromada] = (hr[r.hromada] || 0) + 1;
    }
  });
  const potochna = $('hromadaSel').value;
  $('hromadaSel').innerHTML = window.T('<option value="">— уся вибірка —</option>') +
    Object.entries(hr).sort((a, b) => a[0].localeCompare(b[0], 'uk'))
      .map(([h, n]) => `<option value="${ekranuy(h)}"${h === potochna ? ' selected' : ''}>
        ${ekranuy(h)} (${chyslo(n)})</option>`).join('');
}

// ── зведення: громада або коло ділянки ─────────────────────────────────────
// Пункт 2 наради 15.07: «обираємо громаду — всі агрокомпанії та їх потенціал».
// Той самий рендер обслуговує підсумок радіуса: питання одне — «скільки
// потенціалу в цій вибірці», різниться лише спосіб відбору.
//
// МЕЖА ЧЕСНОСТІ: суми рахуються ЛИШЕ по заповнених полях, і поруч стоїть
// «з них без даних: N». Сума по 4% покриття без цього напису виглядала б як
// сума по всій вибірці — і саме так народжуються фальшиві млн м³ у пітчах.
function zvedennya(rows) {
  const sum = (pole) => {
    let s = 0, ye = 0;
    rows.forEach(r => { if (r[pole] != null && r[pole] !== '') { s += +r[pole]; ye++; } });
    return { s, ye, nd: rows.length - ye };
  };
  const feed = sum('feed'), bio = sum('biometan_m3'), zem = sum('zemlya_ha');
  const kats = {};
  rows.forEach(r => (r.kat_pidpr || '').split(',').filter(Boolean)
    .forEach(k => { kats[k] = (kats[k] || 0) + 1; }));
  const ryadKat = Object.entries(kats).sort((a, b) => b[1] - a[1]).slice(0, 6)
    .map(([k, n]) => `<span>${ekranuy(KP[k] ? KP[k][0] : k)}</span><b>${chyslo(n)}</b>`)
    .join('');
  const r3 = (m, {s, ye, nd}, od) => s ? `<span>${m}</span>
    <b>${chyslo(Math.round(s))} ${od}</b>
    <span class="dim small">${window.T("по")} ${chyslo(ye)}${window.T("; без даних")} ${chyslo(nd)}</span>` : '';
  return `<dl class="k-zvit">
    <span>${window.T("Обʼєктів")}</span><b>${chyslo(rows.length)}</b><span></span>
    ${r3('Сировина', feed, 'т/рік')}
    ${r3('Біометан', bio, 'м³/рік')}
    ${r3('Земфонд', zem, 'га')}
    </dl>${ryadKat ? `<div class="k-zvit-kat">${ryadKat}</div>` : ''}`;
}

function onovyZvity(rows) {
  const hromada = $('hromadaSel').value;
  $('hromadaZvit').hidden = !hromada;
  if (hromada) $('hromadaZvit').innerHTML = zvedennya(rows);

  const zvit = $('dilyankaZvit');
  if (!DILYANKA) { zvit.hidden = true; return; }
  const rKm = +$('dilyankaR').value;
  // Підсумок кола рахується від ПОТОЧНОЇ вибірки: фільтри вже застосовані,
  // тож «у радіусі 20 км» означає «з того, що зараз видно на карті».
  const vKoli = rows.filter(r =>
    haversineKm(DILYANKA.lat, DILYANKA.lon, r.lat, r.lon) <= rKm);
  zvit.hidden = false;
  zvit.innerHTML = `<div class="k-lbl" data-styl="margin-top:6px">${window.T("У радіусі")} ${rKm} ${window.T("км")}</div>`
    + zvedennya(vKoli);
}

// ── ділянка будівництва ────────────────────────────────────────────────────
function maluyDilyanku() {
  if (sharDilyanky) { sharDilyanky.remove(); sharDilyanky = null; }
  if (!DILYANKA) return;
  const rKm = +$('dilyankaR').value;
  sharDilyanky = L.layerGroup([
    L.circle([DILYANKA.lat, DILYANKA.lon], {
      radius: rKm * 1000, color: T.akcent, weight: 2,
      fillColor: T.akcent, fillOpacity: 0.06, dashArray: '6 4',
    }),
    L.circleMarker([DILYANKA.lat, DILYANKA.lon], {
      radius: 6, color: T.akcent, weight: 3, fillColor: '#fff', fillOpacity: 1,
    }).bindTooltip(window.T('Ділянка будівництва'), { direction: 'top' }),
  ]).addTo(map);
}

// ── стан у посиланні ───────────────────────────────────────────────────────
function zberehtyStan() {
  const c = map.getCenter();
  StanURL.zberehty({
    z: map.getZoom(), lat: c.lat.toFixed(4), lon: c.lng.toFixed(4),
    q: $('q').value, minkw: $('minkw').value, gas: $('maxGas').value,
    pow: $('maxPower').value, fin: $('finOnly').checked ? 1 : '',
    pot: $('showPot').checked ? '' : 0, hrubi: $('showHrubi').checked ? 1 : '',
    ws: [$('wGas').value, $('wPow').value, $('wRoad').value, $('wFeed').value].join(','),
    // Ділянка — в URL: посилання з колом можна скинути колезі у WhatsApp,
    // і в нього відкриється та сама точка з тим самим радіусом.
    dil: DILYANKA
      ? `${DILYANKA.lat.toFixed(4)},${DILYANKA.lon.toFixed(4)},${$('dilyankaR').value}`
      : '',
    hrom: $('hromadaSel').value,
  });
}

function vidnovyStan() {
  const s = StanURL.prochytaty();
  if (s.q) $('q').value = s.q;
  if (s.minkw) $('minkw').value = s.minkw;
  if (s.gas) $('maxGas').value = s.gas;
  if (s.pow) $('maxPower').value = s.pow;
  if (s.fin) $('finOnly').checked = true;
  if (s.pot === '0') $('showPot').checked = false;
  if (s.hrubi) $('showHrubi').checked = true;
  if (s.ws) {
    const [g, p, r, f] = s.ws.split(',');
    $('wGas').value = g; $('wPow').value = p; $('wRoad').value = r; $('wFeed').value = f;
  }
  if (s.dil) {
    const [la, lo, r] = s.dil.split(',');
    if (isFinite(+la) && isFinite(+lo)) {
      DILYANKA = { lat: +la, lon: +lo };
      if (r) $('dilyankaR').value = r;
      $('dilyankaFiltr').disabled = false;
      $('dilyankaFiltr').checked = true;
      maluyDilyanku();
    }
  }
  if (s.hrom) $('hromadaSel').value = s.hrom;
  if (s.lat && s.lon && s.z) map.setView([+s.lat, +s.lon], +s.z);
  onovyPidpysy();
}

function onovyPidpysy() {
  $('minkwV').textContent = $('minkw').value;
  $('maxGasV').textContent = +$('maxGas').value ? $('maxGas').value + ' км' : window.T('будь-яка');
  $('maxPowerV').textContent = +$('maxPower').value ? $('maxPower').value + ' км' : window.T('будь-яка');
  ['wGas', 'wPow', 'wRoad', 'wFeed'].forEach(id => {
    $(id + 'V').textContent = $(id).value;
  });
  // Пороги ТЗ: «—» = поріг вимкнено, число = діє
  const p = (id, txt) => {
    $(id + 'V').textContent = +$(id).value ? txt(+$(id).value) : '—';
  };
  p('minVrh', v => chyslo(v) + window.T(' голів'));
  p('minSvyni', v => chyslo(v) + window.T(' голів'));
  p('minPtytsia', v => chyslo(v) + window.T(' голів'));
  p('minBiometan', v => v + window.T(' млн м³/рік'));
  p('minZemlya', v => chyslo(v) + window.T(' га'));
  $('dilyankaRV').textContent = $('dilyankaR').value;
}

// ── шар хабів ──────────────────────────────────────────────────────────────
async function perekluchHaby() {
  if (!$('lyrHaby').checked) {
    if (sharHabiv) { sharHabiv.remove(); sharHabiv = null; }
    return;
  }
  if (!HABY.length) {
    const { rows } = await zavantazh('/api/karta/haby');
    HABY = rows;
    $('habyN').textContent = chyslo(HABY.length);
  }
  sharHabiv = L.layerGroup(HABY.map(h => {
    const m = L.circleMarker([h.lat, h.lon], {
      radius: Math.max(8, Math.min(26, 6 + (h.mw || 0) * 0.7)),
      color: T.akcent, weight: 2, fillColor: T.akcent, fillOpacity: 0.14,
    });
    m.bindPopup(`<b>${window.T("Хаб")} ${ekranuy(h.kod)}</b><br>${ekranuy(h.typ)}<br>` +
      `${chyslo(h.mw)} ${window.T("МВт ·")} ${chyslo(h.donoriv)} ${window.T("донорів")}<br>` +
      (h.irr ? `IRR ${h.irr} %<br>` : '') +
      `<span class="dim">${ekranuy(h.dzherelo)}</span><br>` +
      `<a href="/haby/${h.id}">${window.T("відкрити хаб →")}</a>`);
    return m;
  })).addTo(map);
}

// ── старт ──────────────────────────────────────────────────────────────────
(async function () {
  map = stvoryKartu($('map'));
  shuhlyada = new Shuhlyada($('shuhlyada'));
  // Права панель нижче 1200px — висувна; на широкому виклик нічого не робить.
  spysokYakShuhlyada();
  shar = new SharTochok(map, {
    kolir, radius,
    forma: r => r.m ? 'hruba' : (hruba(r) ? 'hruba' : 'zvychayna'),
    pidpys: r => r.nazva,
    naKlik: r => shuhlyada.vidkryty(r.id, renderKartky),
    naKupu: grupa => shuhlyada.pokazatySpysok(
      `<h3>${grupa.length} ${window.T("обʼєктів в одній точці")}</h3>
       <p class="dim small">${window.T("Однакові координати — розкрити зумом не можна.")}</p>
       <ul class="k-spysok">${grupa.slice(0, 200).map(r =>
        `<li tabindex="0" data-id="${r.id}"><div class="t">${ekranuy(r.nazva)}</div>
         <div class="d"><span>${r.kw ? chyslo(r.kw) + ' кВт' : 'потужність н/д'}</span></div>
         </li>`).join('')}</ul>`),
  });

  gis = new SharyGIS(map, {
    gas_lines: { color: T.akcent, weight: 1.6 },
    power_lines: { color: T.uvaha, weight: 1.2 },
    roads: { color: T.prygl, weight: 1.2 },
    power_nodes: { color: T.uvaha },
    gas_nodes: { color: T.akcent },
  });

  const [dani, dov] = await Promise.all([
    zavantazh('/api/karta/tochky?karta=biogaz'),
    fetch('/api/karta/dovidnyky?karta=biogaz').then(r => r.json()),
  ]);
  ROWS = dani.rows;
  META = dani.meta;

  pobuduyFiltry(dov);
  vidnovyStan();
  peremaluy();

  // ── події ──
  let tymer;
  const pererysuy = () => { clearTimeout(tymer); tymer = setTimeout(peremaluy, 120); };
  document.querySelector('.k-panel.liva').addEventListener('input', e => {
    onovyPidpysy();
    if (e.target.id === 'lyrHaby') { perekluchHaby(); return; }
    if (e.target.dataset.gis) {
      gis.perekluchyty(e.target.dataset.gis, e.target.checked);
      return;
    }
    // Область звузилась → список громад мусить піти за нею, інакше в селекті
    // висять громади, яких на карті вже немає.
    if (e.target.dataset.obl !== undefined) onovyHromady();
    if (e.target.id === 'dilyankaR') maluyDilyanku();
    pererysuy();
  });

  // ── ділянка будівництва: режим «наступний клік ставить точку» ────────────
  $('dilyankaBtn').addEventListener('click', () => {
    const rezhym = $('dilyankaBtn').getAttribute('aria-pressed') === 'true';
    if (rezhym || DILYANKA) {
      // повторне натискання = прибрати точку цілком (і оверлей, якщо людина
      // передумала, так і не клікнувши)
      const n = document.getElementById('dilyankaNakryvka');
      if (n) n.remove();
      DILYANKA = null;
      $('dilyankaBtn').setAttribute('aria-pressed', 'false');
      $('dilyankaBtn').textContent = window.T('Поставити точку на карті');
      $('dilyankaFiltr').checked = false;
      $('dilyankaFiltr').disabled = true;
      map.getContainer().style.cursor = '';
      maluyDilyanku();
      peremaluy();
      return;
    }
    $('dilyankaBtn').setAttribute('aria-pressed', 'true');
    $('dilyankaBtn').textContent = window.T('Клікніть по карті…');
    // Прозорий оверлей ЗАМІСТЬ map.on('click'): у скупченні маркери
    // інтерактивні і зʼїдають клік — людина цілиться в ділянку поруч із
    // фермою, а отримує відкриту картку ферми. Оверлей ловить клік ПЕРШИМ
    // і переводить його в координати сам.
    const nakryvka = document.createElement('div');
    nakryvka.id = 'dilyankaNakryvka';
    nakryvka.style.cssText =
      'position:absolute;inset:0;z-index:650;cursor:crosshair';
    map.getContainer().appendChild(nakryvka);
    nakryvka.addEventListener('click', e => {
      const r = map.getContainer().getBoundingClientRect();
      const ll = map.containerPointToLatLng(
        [e.clientX - r.left, e.clientY - r.top]);
      DILYANKA = { lat: ll.lat, lon: ll.lng };
      nakryvka.remove();
      $('dilyankaBtn').setAttribute('aria-pressed', 'false');
      $('dilyankaBtn').textContent = window.T('Прибрати ділянку');
      $('dilyankaFiltr').disabled = false;
      maluyDilyanku();
      peremaluy();
    }, { once: true });
  });
  $('sort').addEventListener('change', () => spysok(vidibrani()));
  $('hromadaSel').addEventListener('change', peremaluy);
  map.on('moveend', zberehtyStan);

  const vidkryty = e => {
    const li = e.target.closest('li[data-id]');
    if (li) shuhlyada.vidkryty(+li.dataset.id, renderKartky);
  };
  $('spysok').addEventListener('click', vidkryty);
  $('shuhlyada').addEventListener('click', vidkryty);
  $('spysok').addEventListener('keydown', e => {
    if (e.key === 'Enter') vidkryty(e);
  });

  $('expBtn').addEventListener('click', () => eksportCsv(vidibrani(), [
    { pidpys: window.T('Назва'), znach: r => r.nazva },
    { pidpys: window.T('ЄДРПОУ'), znach: r => r.edrpou },
    { pidpys: window.T('Область'), znach: r => r.oblast },
    { pidpys: window.T('Громада'), znach: r => r.hromada },
    { pidpys: window.T('Категорія'), znach: r => KATEGORIYI[kategoriya(r)] },
    { pidpys: window.T('Категорія підприємства'), znach: r => (r.kat_pidpr || '')
        .split(',').filter(Boolean).map(k => KP[k] ? KP[k][0] : k).join('; ') },
    { pidpys: window.T('Голів ВРХ'), znach: r => r.vrh ?? '' },
    { pidpys: window.T('Свиней'), znach: r => r.svyni ?? '' },
    { pidpys: window.T('Птиці'), znach: r => r.ptytsia ?? '' },
    { pidpys: window.T('Земфонд, га'), znach: r => r.zemlya_ha ?? '' },
    { pidpys: window.T('Біометан, м³/рік'), znach: r => r.biometan_m3 ?? '' },
    { pidpys: window.T('Статус переговорів'), znach: r => r.status ?? '' },
    { pidpys: window.T('Потужність, кВт'), znach: r => r.kw ?? window.T('немає даних') },
    { pidpys: window.T('Сировина, т/рік'), znach: r => r.feed ?? '' },
    { pidpys: window.T('До газу, км'), znach: r => r.d_gaz ?? '' },
    { pidpys: window.T('До е-мережі, км'), znach: r => r.d_merezha ?? '' },
    { pidpys: window.T('Придатність'), znach: r => bal(r) },
    { pidpys: window.T('Точність координат'), znach: r => r.tochnist },
  ], 'biogaz-karta.csv'));

  $('shareBtn').addEventListener('click', async () => {
    await navigator.clipboard.writeText(location.href);
    $('shareBtn').textContent = window.T('скопійовано ✓');
    setTimeout(() => { $('shareBtn').textContent = window.T('Копіювати вид'); }, 1600);
  });

  // Перемикач теми міняє токени — кольори маркерів мають піти за ним
  document.addEventListener('tema-zminena', () => { T = tokeny(); peremaluy(); });
})();
