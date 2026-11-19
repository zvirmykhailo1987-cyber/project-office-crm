// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mykhailo Zvir

/* elevatory.js — карта елеваторів: громади-кандидати, наявні елеватори,
 * індустріальні парки. Шкали тут ІНШІ, ніж у біогазі (га зерна й тонни
 * зберігання, а не кВт), тому спільний лише рушій у yadro.js.
 *
 * ВАЖЛИВО про бали: у базі живуть ДВІ моделі оцінки громад — `hromada_screening`
 * (критерії C1–C9 з ваг EIFO) і `elevator_ranking` (скоринг 225 громад за
 * зерновими землями в радіусі 35 км). Це різні шкали з різних методик, тому
 * порівнювати бал громади з одного набору з балом іншого не можна — і карта
 * про це прямо каже, а не мовчки змішує.
 */
import {
  SharTochok, Shuhlyada, StanURL, chyslo, ekranuy, eksportCsv,
  pokazhyMeta, stvoryKartu, tokeny, zavantazh,
  spysokYakShuhlyada
} from './yadro.js';

const TYPY = {
  hromada_lokatsiya: window.T('Громада-кандидат'),
  elevator_isnuyuchyi: window.T('Наявний елеватор'),
  industrialnyi_park: window.T('Індустріальний парк'),
};

const $ = id => document.getElementById(id);
let T = tokeny();
let ROWS = [], META = {};
let shar = null, shuhlyada = null, map = null;

const hruba = r => ['oblast', 'raion', ''].includes(r.tochnist);

function kolir(r) {
  if (r.typ === 'elevator_isnuyuchyi') return T.prygl;   // конкурент/партнер — нейтрально
  if (r.typ === 'industrialnyi_park') return T.uvaha;    // майданчик — сигнал уваги
  return T.akcent;                                       // кандидат — головна дія
}

function radius(r) {
  if (r.typ === 'elevator_isnuyuchyi') {
    // розмір = місткість; немає даних → мінімальний, а не «нуль тонн»
    return r.mistkist_t ? Math.max(6, Math.min(20, 5 + Math.sqrt(r.mistkist_t) / 45)) : 5;
  }
  if (r.typ === 'industrialnyi_park') return 7;
  return r.zerno_ha ? Math.max(5, Math.min(18, 4 + Math.sqrt(r.zerno_ha) / 28)) : 5;
}

// ── фільтрація ─────────────────────────────────────────────────────────────
function vidibrani() {
  const q = $('q').value.trim().toLowerCase();
  const typOff = new Set([...document.querySelectorAll('[data-typ]:not(:checked)')]
    .map(x => x.dataset.typ));
  const oblOff = new Set([...document.querySelectorAll('[data-obl]:not(:checked)')]
    .map(x => x.dataset.obl));
  const minZerno = +$('minZerno').value, minVid = +$('minVid').value;
  const showHrubi = $('showHrubi').checked;

  return ROWS.filter(r => {
    if (!showHrubi && hruba(r)) return false;
    if (typOff.has(r.typ)) return false;
    if (oblOff.has(r.oblast || '')) return false;
    // Пороги стосуються лише кандидатів: у елеватора немає «зернових земель»
    if (r.typ === 'hromada_lokatsiya') {
      if (minZerno && (r.zerno_ha || 0) < minZerno) return false;
      if (minVid && (r.do_elevatora == null || r.do_elevatora < minVid)) return false;
    }
    if (q && !(r.nazva || '').toLowerCase().includes(q)) return false;
    return true;
  });
}

function peremaluy() {
  const rows = vidibrani();
  shar.onovyty(rows);
  pokazhyMeta($('meta'), META, rows.length);
  spysok(rows);
  zberehtyStan();
}

function spysok(rows) {
  const sort = $('sort').value;
  const k = rows.slice();
  if (sort === 'bal') k.sort((a, b) => (b.bal || 0) - (a.bal || 0));
  else if (sort === 'zerno') k.sort((a, b) => (b.zerno_ha || 0) - (a.zerno_ha || 0));
  else if (sort === 'mistkist') k.sort((a, b) => (b.mistkist_t || 0) - (a.mistkist_t || 0));
  else k.sort((a, b) => (a.nazva || '').localeCompare(b.nazva || '', 'uk'));

  $('spysokN').textContent = rows.length;
  $('spysok').innerHTML = k.slice(0, 100).map(r => `
    <li tabindex="0" data-id="${r.id}">
      <div class="t">${ekranuy(r.nazva)}</div>
      <div class="d"><span>${ekranuy(TYPY[r.typ] || r.typ)}</span>
        <span>${r.mistkist_t ? chyslo(r.mistkist_t) + ' т'
              : r.zerno_ha ? chyslo(r.zerno_ha) + ' га' : ''}</span></div>
    </li>`).join('');
  $('spysokHint').textContent = rows.length > 100
    ? `${window.T("показано перші 100 зі")} ${rows.length}` : '';
}

function renderKartky(d) {
  const ryad = (t, v) => v == null || v === '' ? '' : `<dt>${t}</dt><dd>${v}</dd>`;
  const k = d.kontakt || {};
  return `
    <h3>${ekranuy(d.nazva)}</h3>
    <span class="kod">${ekranuy(d.typ)}</span>
    ${d.hruba ? `<div class="k-zastereh"><b>Координати приблизні</b> —
        ${ekranuy(d.tochnist)}. Для відстаней не використовувати.</div>` : ''}
    <dl>
      ${ryad('Місце', ekranuy(d.misce))}
      ${ryad('Місткість, т', d.mistkist_t ? chyslo(d.mistkist_t) : null)}
      ${ryad('До елеватора, км', d.do_elevatora)}
      ${ryad('До е-мережі, км', d.d_merezha)}
      ${ryad('До дороги, км', d.d_doroha)}
      ${ryad('Телефон', k.telefon ? `<a href="tel:${ekranuy(k.telefon)}">${ekranuy(k.telefon)}</a>` : null)}
      ${ryad('Нотатки', ekranuy(d.notatky))}
    </dl>
    ${d.bal ? `<h3 data-styl="font-size:14px">Бал ${d.bal.znachennya}
        <span class="chip">${ekranuy(d.bal.priorytet)}</span></h3>
      <p class="hint" data-styl="margin:0 0 8px">Модель: <span class="kod">${ekranuy(d.bal.model)}</span>.
        Бали різних моделей між собою не порівнюються.</p>
      <dl>${d.bal.skladovi.map(c => `<dt>${ekranuy(c.label)}${c.nemaye
        ? ' <span class="dim">(немає даних)</span>' : ''}</dt>
        <dd>${c.vnesok.toFixed(1)}</dd>`).join('')}</dl>`
      : '<p class="hint">Бал не рахується: для цього типу обʼєкта моделі оцінки немає.</p>'}
    <div class="acts"><a class="btn" href="${d.url}">${window.T("Відкрити картку →")}</a></div>`;
}

function pobuduyFiltry(dovidnyky) {
  const lich = {};
  ROWS.forEach(r => { lich[r.typ] = (lich[r.typ] || 0) + 1; });
  $('typRyady').innerHTML = Object.keys(TYPY).map(k => `
    <label class="k-ryad"><input type="checkbox" data-typ="${k}" checked>
      <span class="krapka" data-styl="background:${
        k === 'elevator_isnuyuchyi' ? T.prygl
        : k === 'industrialnyi_park' ? T.uvaha : T.akcent}"></span>
      <span class="nm">${TYPY[k]}</span>
      <span class="n">${chyslo(lich[k] || 0)}</span></label>`).join('');
  $('typN').textContent = Object.keys(lich).length;

  $('oblRyady').innerHTML = (dovidnyky.oblasti || []).map(o => `
    <label class="k-ryad"><input type="checkbox" data-obl="${ekranuy(o.znachennya)}" checked>
      <span class="nm">${ekranuy(o.znachennya)}</span>
      <span class="n">${chyslo(o.n)}</span></label>`).join('');
  $('oblN').textContent = (dovidnyky.oblasti || []).length;
  $('hrubiN').textContent = chyslo(ROWS.filter(hruba).length);
}

function zberehtyStan() {
  const c = map.getCenter();
  StanURL.zberehty({
    z: map.getZoom(), lat: c.lat.toFixed(4), lon: c.lng.toFixed(4),
    q: $('q').value, zerno: $('minZerno').value, vid: $('minVid').value,
    hrubi: $('showHrubi').checked ? 1 : '',
  });
}

function onovyPidpysy() {
  $('minZernoV').textContent = chyslo(+$('minZerno').value);
  $('minVidV').textContent = $('minVid').value;
}

(async function () {
  map = stvoryKartu($('map'), { tsentr: [49.4, 26.0], zum: 7 });
  shuhlyada = new Shuhlyada($('shuhlyada'));
  // Права панель нижче 1200px — висувна; на широкому виклик нічого не робить.
  spysokYakShuhlyada();
  shar = new SharTochok(map, {
    kolir, radius,
    forma: r => hruba(r) ? 'hruba' : 'zvychayna',
    pidpys: r => `${r.nazva} — ${TYPY[r.typ] || r.typ}`,
    naKlik: r => shuhlyada.vidkryty(r.id, renderKartky),
  });

  const [dani, dov] = await Promise.all([
    zavantazh('/api/karta/tochky?karta=elevatory'),
    fetch('/api/karta/dovidnyky?karta=elevatory').then(r => r.json()),
  ]);
  ROWS = dani.rows;
  META = dani.meta;

  pobuduyFiltry(dov);
  const s = StanURL.prochytaty();
  if (s.q) $('q').value = s.q;
  if (s.zerno) $('minZerno').value = s.zerno;
  if (s.vid) $('minVid').value = s.vid;
  if (s.hrubi) $('showHrubi').checked = true;
  if (s.lat && s.lon && s.z) map.setView([+s.lat, +s.lon], +s.z);
  onovyPidpysy();
  peremaluy();

  let tymer;
  document.querySelector('.k-panel.liva').addEventListener('input', () => {
    onovyPidpysy();
    clearTimeout(tymer);
    tymer = setTimeout(peremaluy, 120);
  });
  $('sort').addEventListener('change', () => spysok(vidibrani()));
  map.on('moveend', zberehtyStan);

  const vidkryty = e => {
    const li = e.target.closest('li[data-id]');
    if (li) shuhlyada.vidkryty(+li.dataset.id, renderKartky);
  };
  $('spysok').addEventListener('click', vidkryty);
  $('spysok').addEventListener('keydown', e => { if (e.key === 'Enter') vidkryty(e); });

  $('expBtn').addEventListener('click', () => eksportCsv(vidibrani(), [
    { pidpys: window.T('Назва'), znach: r => r.nazva },
    { pidpys: window.T('Тип'), znach: r => TYPY[r.typ] || r.typ },
    { pidpys: window.T('Область'), znach: r => r.oblast },
    { pidpys: window.T('Бал'), znach: r => r.bal ?? '' },
    { pidpys: window.T('Зернові землі, га'), znach: r => r.zerno_ha ?? '' },
    { pidpys: window.T('Місткість, т'), znach: r => r.mistkist_t ?? '' },
    { pidpys: window.T('До елеватора, км'), znach: r => r.do_elevatora ?? '' },
    { pidpys: window.T('Джерело/методика'), znach: r => r.metodyka ?? '' },
  ], 'elevatory-karta.csv'));

  $('shareBtn').addEventListener('click', async () => {
    await navigator.clipboard.writeText(location.href);
    $('shareBtn').textContent = window.T('скопійовано ✓');
    setTimeout(() => { $('shareBtn').textContent = window.T('Копіювати вид'); }, 1600);
  });

  document.addEventListener('tema-zminena', () => { T = tokeny(); peremaluy(); });
})();
