/* yadro.js — спільний рушій карт ОФІС (біоенергетика й елеватори).
 *
 * ЧОМУ власна кластеризація, а не Leaflet.markercluster: 21 тисяча точок сидить
 * лише в ~7.4 тис. унікальних координат (усі підприємства населеного пункту мають
 * ОДНАКОВІ координати — до 304 в одній точці). Тобто головна задача не «згрупувати
 * близькі», а «розкрити накладені». Сітка за пікселями робить це на ~90 рядках і
 * не тягне 45 КБ зовнішнього коду з CDN, який у проєкті заборонений.
 *
 * ГРУБІ КООРДИНАТИ. 4 857 обʼєктів (23 %) мають точність лише «область» і фізично
 * стоять у 5 точках — центрах областей. Показувати їх як звичайні маркери означає
 * малювати скупчення, якого не існує. Тому вони: вимкнені за замовчуванням,
 * малюються ІНШОЮ ФОРМОЮ (пунктирний ромб), і виключені з теплокарти й зон збору.
 * Джитер (розкидати випадково) заборонений — це вигадування даних.
 */

// ── токени теми: кольори беремо з CSS, щоб карта жила в обох темах ──────────
export function tokeny() {
  const cs = getComputedStyle(document.documentElement);
  const t = n => cs.getPropertyValue(n).trim();
  return {
    akcent: t('--akcent'), uvaha: t('--uvaha'), dobre: t('--dobre'),
    krytych: t('--krytych'), prygl: t('--prygl'), tilo: t('--tilo'),
    mezha: t('--mezha'), poverhnya: t('--poverhnya'), tlo: t('--tlo'),
  };
}

export function stvoryKartu(el, { tsentr = [49.0, 27.0], zum = 7 } = {}) {
  // preferCanvas: тисячі кіл на SVG гальмують, на canvas — ні
  const map = L.map(el, { preferCanvas: true, zoomControl: true }).setView(tsentr, zum);
  const pidlozhka = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    { maxZoom: 18, attribution: '© OpenStreetMap' });
  pidlozhka.addTo(map);
  map.pidlozhka = pidlozhka;
  return map;
}

// ── дані: колонковий формат → обʼєкти ──────────────────────────────────────
export function rozpakuy({ polya, ryadky }) {
  return ryadky.map(r => {
    const o = {};
    polya.forEach((k, i) => { o[k] = r[i]; });
    return o;
  });
}

export async function zavantazh(url) {
  const vidpovid = await fetch(url, { headers: { 'Accept': 'application/json' } });
  if (!vidpovid.ok) throw new Error(T('не вдалося завантажити дані: ') + vidpovid.status);
  const dani = await vidpovid.json();
  return { rows: rozpakuy(dani), meta: dani.meta || {} };
}

// ── рядок чесності ─────────────────────────────────────────────────────────
export function pokazhyMeta(el, meta, vydymo) {
  if (!el) return;
  // Перше число — скільки видно ЗАРАЗ, після фільтрів. Показувати серверне
  // «завантажено» означало б, що лічильник не збігається з тим, що на екрані.
  const n = vydymo ?? meta.pokazano ?? 0;
  const shovano = (meta.pokazano ?? 0) - n;
  const chastyny = [];
  if (shovano > 0) chastyny.push(`${shovano} ${T("сховали фільтри")}`);
  if (meta.prychyna) chastyny.push(meta.prychyna);
  el.innerHTML = `${T("на карті")} <b>${n.toLocaleString('uk-UA')}</b>${T("&nbsp;з&nbsp;")}` +
    `<b>${(meta.vsoho ?? 0).toLocaleString('uk-UA')}</b>` +
    (chastyny.length ? ` <span class="chomu">· ${chastyny.join(' · ')}</span>` : '');
}

// ── власна кластеризація за пікселями ──────────────────────────────────────
const KROK_PX = 46;          // сторона комірки; менше — купи дрібніші, але їх більше
const MEZHA_ROZKRYTTYA = 12; // з такого зуму показуємо окремі точки, а не купи

export class SharTochok {
  /** @param opts {kolir, radius, forma, pidpys, naKlik} */
  constructor(map, opts = {}) {
    this.map = map;
    this.opts = opts;
    this.grupa = L.layerGroup().addTo(map);
    this.rows = [];
    this._perem = () => this.peremaluy();
    map.on('zoomend moveend', this._perem);
  }

  onovyty(rows) {
    this.rows = rows;
    this.peremaluy();
  }

  znyshchyty() {
    this.map.off('zoomend moveend', this._perem);
    this.grupa.remove();
  }

  peremaluy() {
    this.grupa.clearLayers();
    if (!this.rows.length) return;
    const map = this.map;
    const zum = map.getZoom();
    const mezhi = map.getBounds().pad(0.25);
    const vydymi = this.rows.filter(r => mezhi.contains([r.lat, r.lon]));

    // На великому зумі малюємо все окремо — купи лише заважали б
    if (zum >= MEZHA_ROZKRYTTYA || vydymi.length <= 400) {
      vydymi.forEach(r => this.grupa.addLayer(this._marker(r)));
      return;
    }

    const komirky = new Map();
    for (const r of vydymi) {
      const p = map.latLngToContainerPoint([r.lat, r.lon]);
      const k = `${Math.floor(p.x / KROK_PX)}:${Math.floor(p.y / KROK_PX)}`;
      if (!komirky.has(k)) komirky.set(k, []);
      komirky.get(k).push(r);
    }
    for (const grupa of komirky.values()) {
      if (grupa.length === 1) this.grupa.addLayer(this._marker(grupa[0]));
      else this.grupa.addLayer(this._kupa(grupa));
    }
  }

  _marker(r) {
    const { kolir, radius, forma } = this.opts;
    const c = kolir ? kolir(r) : '#12605C';
    const rad = radius ? radius(r) : 6;
    // Груба координата = інша ФОРМА (порожній пунктирний ромб), не інший колір:
    // колір уже зайнятий семантикою, а форма читається навіть у сірому друці
    const hruba = forma ? forma(r) === 'hruba' : false;
    const m = L.circleMarker([r.lat, r.lon], {
      radius: rad, color: c, weight: hruba ? 1.4 : 1,
      fillColor: c, fillOpacity: hruba ? 0 : 0.62,
      dashArray: hruba ? '3,3' : null,
    });
    m.on('click', () => this.opts.naKlik && this.opts.naKlik(r));
    if (this.opts.pidpys) m.bindTooltip(this.opts.pidpys(r), { direction: 'top' });
    return m;
  }

  _kupa(grupa) {
    const n = grupa.length;
    const bik = n < 10 ? 30 : n < 100 ? 36 : n < 1000 ? 44 : 52;
    const lat = grupa.reduce((a, r) => a + r.lat, 0) / n;
    const lon = grupa.reduce((a, r) => a + r.lon, 0) / n;
    const pidpys = n >= 1000 ? (n / 1000).toFixed(1).replace('.0', '') + 'к' : String(n);
    const m = L.marker([lat, lon], {
      icon: L.divIcon({
        className: '', iconSize: [bik, bik], iconAnchor: [bik / 2, bik / 2],
        html: `<div class="k-kupa${n >= 100 ? ' velyka' : ''}" data-has-js ` +
              `data-styl="width:${bik}px;height:${bik}px">${pidpys}</div>`,
      }),
    });
    // Leaflet сам робить обгортку з role="button"; обробник висить на маркері,
    // тому позначаємо обгортку — інакше це виглядає як кнопка без дії
    m.on('add', () => m.getElement()?.setAttribute('data-has-js', ''));
    m.on('click', () => {
      // Купа з однаковими координатами не розкриється зумом — показуємо списком
      const rozkydani = new Set(grupa.map(r => `${r.lat},${r.lon}`)).size > 1;
      if (rozkydani && this.map.getZoom() < MEZHA_ROZKRYTTYA) {
        this.map.fitBounds(grupa.map(r => [r.lat, r.lon]), { padding: [40, 40] });
      } else if (this.opts.naKupu) {
        this.opts.naKupu(grupa);
      }
    });
    m.bindTooltip(`${n} ${T("обʼєктів — клац, щоб розкрити")}`, { direction: 'top' });
    return m;
  }
}

// ── інфраструктурні шари ───────────────────────────────────────────────────
/** Ліниве завантаження GIS: типовий сеанс не вмикає жодного шару, тому тягнути
 *  їх наперед означало б платити 0.8 МБ за те, чого ніхто не подивиться. */
export class SharyGIS {
  constructor(map, styli) {
    this.map = map;
    this.styli = styli;      // {roads:{color,weight}, power_lines:{…}, …}
    this.kesh = new Map();
  }

  async perekluchyty(imya, uvimknuty) {
    if (!uvimknuty) {
      const sh = this.kesh.get(imya);
      if (sh) sh.remove();
      return;
    }
    if (this.kesh.has(imya)) {
      this.kesh.get(imya).addTo(this.map);
      return;
    }
    const dani = await (await fetch(`/static/gis/v1/${imya}.geojson`)).json();
    const st = this.styli[imya] || {};
    const shar = L.geoJSON(dani, {
      style: () => ({ color: st.color, weight: st.weight || 1.5, opacity: 0.75 }),
      pointToLayer: (f, latlng) => L.circleMarker(latlng, {
        radius: 4, color: st.color, fillColor: st.color, fillOpacity: 0.8, weight: 1,
      }),
    });
    this.kesh.set(imya, shar);
    shar.addTo(this.map);
  }
}

// ── шухляда картки ─────────────────────────────────────────────────────────
export class Shuhlyada {
  constructor(el) {
    this.el = el;
    this.telo = el.querySelector('.telo');
    el.querySelector('.zakryty').addEventListener('click', () => this.zakryty());
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && this.el.hasAttribute('open')) this.zakryty();
    });
  }

  async vidkryty(id, rendery) {
    this.el.setAttribute('open', '');
    this.telo.innerHTML = T('<p class="dim">завантаження…</p>');
    try {
      const d = await (await fetch(`/api/karta/tochka/${id}`)).json();
      this.telo.innerHTML = rendery(d);
    } catch (e) {
      this.telo.innerHTML = T('<p class="warn-text">Не вдалося показати картку.</p>');
    }
  }

  pokazatySpysok(html) {
    this.el.setAttribute('open', '');
    this.telo.innerHTML = html;
  }

  zakryty() { this.el.removeAttribute('open'); }
}

// ── список обʼєктів як шухляда на вузькому екрані ──────────────────────────
/** До 06.08.2026 права панель нижче 1100px просто зникала (`display:none`), і
 *  разом із нею — список обʼєктів та сортування. Тепер це висувна панель, яку
 *  викликає кнопка. Розкладку робить CSS; сюди винесено те, чого CSS не вміє:
 *  стан `aria-expanded`, Esc, клік поза панеллю, дублювання лічильника в
 *  кнопку (інакше на закритій панелі не видно, скільки обʼєктів знайдено).
 *
 *  Повертає `null`, якщо розмітки немає, — щоб виклик у сторінках карти був
 *  безумовним і не обріс перевірками на кожній.
 */
export function spysokYakShuhlyada() {
  const knopka = document.getElementById('spysokKnopka');
  const panel = document.getElementById('spysokPanel');
  const obhortka = document.querySelector('.karta-obhortka');
  if (!knopka || !panel || !obhortka) return null;

  const vidkryta = () => obhortka.classList.contains('spysok-vidkryto');
  function perem(stan) {
    obhortka.classList.toggle('spysok-vidkryto', stan);
    knopka.setAttribute('aria-expanded', String(stan));
    if (stan) panel.querySelector('select,button,a,input')?.focus();
  }

  knopka.addEventListener('click', () => perem(!vidkryta()));
  panel.querySelector('.zakryty')?.addEventListener('click', () => {
    perem(false); knopka.focus();
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && vidkryta()) { perem(false); knopka.focus(); }
  });
  // Клік по карті = «хочу дивитись карту», тобто панель заважає.
  document.getElementById('map')?.addEventListener('pointerdown', () => {
    if (vidkryta()) perem(false);
  });

  // Лічильник у кнопці мусить іти за лічильником у панелі: код сторінок пише
  // тільки в `#spysokN`, і дублювати виклик у двох файлах карт означало б
  // забути про це в третьому.
  const dzherelo = document.getElementById('spysokN');
  const dublikat = document.getElementById('spysokNK');
  if (dzherelo && dublikat) {
    new MutationObserver(() => { dublikat.textContent = dzherelo.textContent; })
      .observe(dzherelo, { childList: true, characterData: true, subtree: true });
  }
  return { vidkryty: () => perem(true), zakryty: () => perem(false) };
}

// ── стан у посиланні: «копіювати вид» ──────────────────────────────────────
export const StanURL = {
  zberehty(stan) {
    const q = new URLSearchParams();
    Object.entries(stan).forEach(([k, v]) => {
      if (v !== '' && v != null && v !== false) q.set(k, String(v));
    });
    history.replaceState(null, '', '#' + q.toString());
  },
  prochytaty() {
    return Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
  },
};

// ── експорт CSV ────────────────────────────────────────────────────────────
export function eksportCsv(rows, kolonky, imya) {
  const esc = v => {
    const s = v == null ? '' : String(v);
    return /[",;\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const head = kolonky.map(k => esc(k.pidpys)).join(';');
  const body = rows.map(r => kolonky.map(k => esc(k.znach(r))).join(';'));
  // BOM — інакше Excel відкриває українські назви кракозябрами
  const blob = new Blob(['﻿' + [head, ...body].join('\r\n')],
                        { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = imya;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── дрібні помічники ───────────────────────────────────────────────────────
export const chyslo = v => v == null || v === '' ? '—' : Number(v).toLocaleString('uk-UA');

export function ekranuy(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Плавний перехід 3 семантичних зупинок: критично → увага → добре.
 *  Веселка з оригіналу тут не годиться — у системі колір означає стан. */
export function kolirZaBalom(bal, t) {
  const zmishaty = (a, b, k) => `color-mix(in srgb, ${b} ${Math.round(k * 100)}%, ${a})`;
  const x = Math.max(0, Math.min(100, bal || 0)) / 100;
  return x < 0.5 ? zmishaty(t.krytych, t.uvaha, x / 0.5)
                 : zmishaty(t.uvaha, t.dobre, (x - 0.5) / 0.5);
}

/** Близькість 0–1: 0 км → 1, cap і далі → 0. Немає даних → 0, а не «добре». */
export const blyzkist = (d, cap) => d == null ? 0 : Math.max(0, 1 - Math.min(d, cap) / cap);
