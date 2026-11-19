// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Mykhailo Zvir

/* charts.js — SVG-генератори діаграм із ЖОРСТКИМ контрактом осей (dyzayn-ux/biblioteka).
 *
 * НАВІЩО. Відповідь на 10 класів діаграмного примітивізму, які система ловила
 * в реальних артефактах (смужки-div без осей, «сітка без чисел», водоспади з
 * підписами-сиротами, лог-шкала що бреше в HPL_dossier, Canva-чарти зі сміттям):
 * тут погана діаграма НЕ РЕНДЕРИТЬСЯ — генератор кидає виняток зі зрозумілим
 * текстом замість тихого малювання неправди.
 *
 * КОНТРАКТ ОСІ (незламний):
 *   1. Кожна вісь = {domain:[min,max], unit:"...", ticks:[...]} — без будь-якої
 *      з трьох частин → виняток, не рендер. Кожна лінія сітки має число
 *      («сітка без чисел» неможлива за побудовою).
 *   2. Кожен стовпчик/сегмент/крок — підпис значення; для дрібних — підпис
 *      виноситься назовні (bar/tornado/gantt самі обирають всередині/зовні).
 *   3. funnel — нормування НА ВХІД (перша стадія = 100 %) + конверсії МІЖ
 *      стадіями; waterfall — вісь Y + значення на кожному сегменті + звірка
 *      «сума сегментів = підсумок» (розбіжність → виняток).
 *   4. Легенда ОБОВʼЯЗКОВА, щойно кольорових категорій > 1 (параметр legend).
 *   5. benchmark-лінія опційна, але тільки іменована: {value, label}.
 *   6. Стовпчики починаються з нуля (domain[0] === 0), бо обрізаний стовпчик
 *      бреше довжиною; для відхилень від бази є CH.tornado.
 *   7. Нормування на максимум — ЛИШЕ явним normalize:"max" (з приміткою на
 *      полотні); значення поза domain → виняток, а не тихе стискання.
 *   8. Повороти підписів заборонені: довгий підпис переноситься у 2 рядки
 *      (wrap2), API повороту не існує.
 *   9. Всі числа — tabular-nums (style="font-variant-numeric:tabular-nums").
 *  10. Площинні порівняння (nestedTSS) — лінійна шкала площ із підписом
 *      кратності; логарифмічного режиму немає взагалі.
 *
 * ДЖЕРЕЛА-ПАТЕРНИ (узагальнено): grant_trening_ternopil/scripts/diagrams.py
 * (11 генераторів: direct-labeling, ортогональні конектори, wrap-замість-повороту),
 * biodyzel_kilgan/scripts/design_system.py (водоспад зі злиттям дрібних статей,
 * сегментна смуга з лідер-лініями), антиприклади hpl_zavod_lviv/output/HPL_dossier.html.
 *
 * API (кожен повертає self-contained SVG-рядок; кольори — CSS-токени var(--ch-*)
 * з фолбеками, тому працює і без жодного зовнішнього CSS):
 *   CH.bar · CH.waterfall · CH.funnel · CH.lineDual · CH.tornado ·
 *   CH.timelineGantt · CH.nestedTSS · CH.scatterPareto
 * Чисті помічники для тестів: CH._konversii, CH._paretoFront, CH._fmt.
 *
 * Selftest:  "C:\Program Files\nodejs\node.exe" charts.js --selftest
 */
'use strict';

/* ── Жетони дизайну: var(--ch-*) з фолбеками — темується сторінкою, працює без неї ── */
const TOK = {
  ink:    'var(--ch-ink,#262B31)',      // основний текст
  muted:  'var(--ch-muted,#5A6570)',    // другорядний текст, позначки осей
  grid:   'var(--ch-grid,#E6E1D8)',     // лінії сітки (кожна МАЄ число поруч)
  osnova: 'var(--ch-axis,#9AA1A9)',     // базова лінія осі, лідер-лінії
  bar:    'var(--ch-bar,#3E4C59)',      // основний колір рядів
  accent: 'var(--ch-accent,#C05621)',   // один акцент (підсумок, рекомендація)
  ok:     'var(--ch-ok,#2F7D5B)',       // додатний внесок
  okM:    'var(--ch-ok-m,#6BA188)',     // додатний сегмент (мʼякший)
  neg:    'var(--ch-neg,#B23A3A)',      // відʼємний внесок
  negM:   'var(--ch-neg-m,#C4756E)',    // відʼємний сегмент (мʼякший)
  bench:  'var(--ch-bench,#8A6D3B)',    // іменована benchmark-лінія
  paper:  'var(--ch-paper,#FFFFFF)',    // текст на темних заливках
  a1:     'var(--ch-area1,#E8EDF2)',    // вкладені площини (TAM)
  a2:     'var(--ch-area2,#C9D5DE)',    // (SAM)
  a3:     'var(--ch-area3,#F0C9AF)',    // (SOM)
};
/* Категорійна палітра (порядок = порядок категорій у даних) */
const PALITRA = [TOK.bar, TOK.accent, 'var(--ch-c3,#6B7F94)',
                 'var(--ch-c4,#8A6D3B)', 'var(--ch-c5,#4C7A6E)', 'var(--ch-c6,#7A5C86)'];

/* ── Дрібні помічники ── */
function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function r1(v) { return Math.round(v * 10) / 10; }

/* Число по-українськи: кома в дробовій, вузький пробіл у тисячах, «−» замість «-».
 * Без dec — до 2 знаків із відкиданням хвостових нулів (22.51 → «22,51», 43.8 → «43,8»). */
function fmt(v, dec) {
  if (!isFinite(v)) return '—';
  let s = dec == null ? String(Math.round(v * 100) / 100) : Number(v).toFixed(dec);
  let znak = '';
  if (s[0] === '-') { znak = '\u2212'; s = s.slice(1); }
  const ch = s.split('.');
  return znak + ch[0].replace(/\B(?=(\d{3})+(?!\d))/g, '\u00A0') + (ch[1] ? ',' + ch[1] : '');
}

/* Консервативна оцінка ширини тексту (для розведення підписів БЕЗ повороту) */
function estW(t, size) { return String(t).length * size * 0.62 + 8; }

/* Перенос у щонайбільше 2 рядки — ЄДИНА дозволена реакція на довгий підпис.
 * Повороту >0° в бібліотеці не існує (заборона №8). */
function wrap2(s, maxc) {
  s = String(s);
  if (s.length <= maxc) return [s];
  const slova = s.split(/\s+/);
  if (slova.length === 1) return [s];
  let r1s = '', i = 0;
  while (i < slova.length && (r1s + ' ' + slova[i]).trim().length <= maxc) {
    r1s = (r1s + ' ' + slova[i]).trim(); i++;
  }
  if (!r1s) { r1s = slova[0]; i = 1; }
  const r2s = slova.slice(i).join(' ');
  return r2s ? [r1s, r2s] : [r1s];
}

/* <text>: y = базова лінія; riadky — рядок або масив (2 рядки через tspan).
 * tabular-nums ставиться на КОЖЕН текст — правило №9 виконане за побудовою. */
function tekst(x, y, riadky, o) {
  o = o || {};
  const rr = Array.isArray(riadky) ? riadky : [riadky];
  const size = o.size || 14;
  let atr = 'x="' + r1(x) + '" y="' + r1(y) + '" font-size="' + size + '" fill="' + (o.fill || TOK.ink) + '"';
  if (o.weight) atr += ' font-weight="' + o.weight + '"';
  if (o.anchor) atr += ' text-anchor="' + o.anchor + '"';
  atr += ' data-styl="font-variant-numeric:tabular-nums"';
  if (rr.length === 1) return '<text ' + atr + '>' + esc(rr[0]) + '</text>';
  return '<text ' + atr + '>' + rr.map((r, i) =>
    '<tspan x="' + r1(x) + '" dy="' + (i ? Math.round(size * 1.25) : 0) + '">' + esc(r) + '</tspan>'
  ).join('') + '</text>';
}

function obhortka(w, h, tilo, aria) {
  return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + w + ' ' + h + '" ' +
    'role="img" aria-label="' + esc(aria || T('діаграма')) + '" ' +
    'data-styl="max-width:100%;height:auto;display:block;' +
    "font-family:var(--ch-font,'Segoe UI',system-ui,sans-serif)" + '">' + tilo + '</svg>';
}

/* ── КОНТРАКТ ОСІ: перевірки, що НЕ рендерять, а кидають ── */
function perevirytyVis(vis, hto, imya) {
  const pref = T('КОНТРАКТ ОСІ (') + hto + (imya ? '.' + imya : '.vis') + '): ';
  if (!vis || typeof vis !== 'object')
    throw new Error(pref + T('вісь не задана. Потрібно {domain:[min,max], unit:"…", ticks:[…]} — ') +
      T('діаграма без осі не рендериться.'));
  const d = vis.domain;
  if (!Array.isArray(d) || d.length !== 2 || !isFinite(d[0]) || !isFinite(d[1]) || d[0] >= d[1])
    throw new Error(pref + T('domain має бути [min, max] із min < max. Отримано: ') + JSON.stringify(d) + '.');
  if (typeof vis.unit !== 'string' || !vis.unit.trim())
    throw new Error(pref + T('unit відсутня. Число без одиниці — не дані. Приклад: unit:"млн €" або unit:"%".'));
  if (!Array.isArray(vis.ticks) || vis.ticks.length < 2 || vis.ticks.some(t => !isFinite(t)))
    throw new Error(pref + T('ticks відсутні або нечислові (потрібно ≥2 позначок). ') +
      T('«Сітка без чисел» заборонена — кожна лінія сітки мусить мати число.'));
  const poza = vis.ticks.filter(t => t < d[0] - 1e-9 || t > d[1] + 1e-9);
  if (poza.length)
    throw new Error(pref + T('позначки поза domain: ') + poza.join(', ') + '.');
  return vis;
}

function perevirytyDilianku(v, vis, hto, mitka) {
  if (!isFinite(v) || v < vis.domain[0] - 1e-9 || v > vis.domain[1] + 1e-9)
    throw new Error(T('КОНТРАКТ ОСІ (') + hto + T('): значення «') + mitka + '» = ' + v +
      T(' поза domain [') + vis.domain.join(', ') + T(']. Розширте domain явно — ') +
      T('тихого нормування немає (явне нормування: normalize:"max").'));
}

function perevirytyLegendu(katehorii, legend, hto) {
  if (katehorii.length > 1 && !legend)
    throw new Error(hto + ': ' + katehorii.length + T(' кольорові категорії (') + katehorii.join(', ') +
      T(') без легенди. Передайте legend:{"категорія":"Підпис", …} — колір без розшифровки заборонений.'));
}

function perevirytyBenchmark(b, hto) {
  if (b == null) return null;
  if (!isFinite(+b.value) || typeof b.label !== 'string' || !b.label.trim())
    throw new Error(hto + T(': benchmark мусить бути іменованим — {value, label}. ') +
      T('Безіменна лінія на графіку — це шум, а не орієнтир.'));
  return b;
}

/* Щільність позначок: якщо підписи зіллються — це вже не вісь, а шум */
function perevirytyShchilnist(pozytsii, hto) {
  const p = pozytsii.slice().sort((a, b) => a - b);
  for (let i = 1; i < p.length; i++)
    if (p[i] - p[i - 1] < 15)
      throw new Error(T('КОНТРАКТ ОСІ (') + hto + T('): позначки надто щільні (') +
        Math.round(p[i] - p[i - 1]) + T(' px < 15 px) — підписи зіллються. Розрідіть ticks.'));
}

/* Вісь Y (вертикальні діаграми): сітка ЛИШЕ на позначках, кожна з числом; unit згори */
function malyuvatyOsY(vis, L, xKin, sy, T, hto) {
  perevirytyShchilnist(vis.ticks.map(sy), hto);
  let s = '';
  vis.ticks.forEach(t => {
    const y = sy(t);
    s += '<line x1="' + L + '" y1="' + r1(y) + '" x2="' + xKin + '" y2="' + r1(y) +
         '" stroke="' + TOK.grid + '" stroke-width="1"/>';
    s += tekst(L - 10, y + 4.5, fmt(t), { size: 13, fill: TOK.muted, anchor: 'end' });
  });
  s += tekst(L - 10, T - 18, vis.unit, { size: 13, fill: TOK.muted, anchor: 'end' });
  s += '<line x1="' + L + '" y1="' + r1(sy(vis.domain[0])) + '" x2="' + xKin + '" y2="' +
       r1(sy(vis.domain[0])) + '" stroke="' + TOK.osnova + '" stroke-width="1.5"/>';
  return s;
}

/* Легенда: справа наліво від xPravo, прямокутник-зразок + підпис */
function malyuvatyLegendu(elementy, xPravo, yBaza) {
  let s = '', x = xPravo;
  for (let i = elementy.length - 1; i >= 0; i--) {
    const e = elementy[i], wt = estW(e.label, 13) - 8;
    x -= wt;
    s += tekst(x, yBaza, e.label, { size: 13, fill: TOK.muted, anchor: 'start' });
    x -= 17;
    s += '<rect x="' + r1(x) + '" y="' + r1(yBaza - 10) + '" width="12" height="12" rx="2" fill="' + e.kolir + '"/>';
    x -= 24;
  }
  return s;
}

/* ══════════════ 1. CH.bar — стовпчикова ══════════════
 * opts: { dani:[{label, value, cat?, aktsent?}], vis:{domain,unit,ticks},
 *         legend?, benchmark?:{value,label}, normalize?:'max', w?, h? } */
function bar(o) {
  const hto = 'CH.bar';
  o = o || {};
  const vis = perevirytyVis(o.vis, hto);
  if (vis.domain[0] !== 0)
    throw new Error(hto + T(': стовпчик, що починається не з нуля, бреше довжиною — ') +
      T('domain[0] мусить бути 0. Для відхилень від бази є CH.tornado.'));
  if (!Array.isArray(o.dani) || !o.dani.length)
    throw new Error(hto + T(': dani порожні — потрібно [{label, value}, …].'));
  if (o.normalize != null && o.normalize !== 'max')
    throw new Error(hto + T(': normalize приймає лише явне значення "max" — тихого нормування не існує.'));

  let dani = o.dani.map(d => ({ label: String(d.label), value: +d.value, cat: d.cat, aktsent: !!d.aktsent }));
  let prymNorm = '';
  if (o.normalize === 'max') {
    const mx = Math.max.apply(null, dani.map(d => d.value));
    dani = dani.map(d => Object.assign({}, d, { value: d.value / mx * 100 }));
    prymNorm = T('нормовано на максимум (normalize:"max")');
  }
  const katehorii = [...new Set(dani.filter(d => d.cat != null).map(d => d.cat))];
  perevirytyLegendu(katehorii, o.legend, hto);
  const bench = perevirytyBenchmark(o.benchmark, hto);
  dani.forEach(d => {
    if (!isFinite(d.value) || d.value < 0)
      throw new Error(hto + T(': значення «') + d.label + '» = ' + d.value +
        T(' — стовпчик вимагає скінченного невідʼємного числа.'));
    perevirytyDilianku(d.value, vis, hto, d.label);
  });
  if (bench) perevirytyDilianku(+bench.value, vis, hto, bench.label);

  const w = o.w || 960, h = o.h || 420, L = 76, R = 28, T = 56, B = 66;
  const sy = v => T + (vis.domain[1] - v) / (vis.domain[1] - vis.domain[0]) * (h - T - B);
  let s = malyuvatyOsY(vis, L, w - R, sy, T, hto);

  const n = dani.length, banda = (w - L - R) / n, bw = Math.min(88, banda * 0.62);
  const maxc = Math.max(8, Math.floor(banda / 8));
  dani.forEach((d, i) => {
    const x = L + i * banda + (banda - bw) / 2;
    const y1 = sy(d.value), y0 = sy(0);
    const kolir = d.aktsent ? TOK.accent
      : (d.cat != null && katehorii.length > 1 ? PALITRA[katehorii.indexOf(d.cat) % PALITRA.length] : TOK.bar);
    s += '<rect x="' + r1(x) + '" y="' + r1(y1) + '" width="' + r1(bw) + '" height="' +
         r1(Math.max(0, y0 - y1)) + '" rx="2" fill="' + kolir + '"/>';
    /* правило №2: кожен стовпчик — підпис значення (завжди зовні, над стовпчиком) */
    s += tekst(x + bw / 2, y1 - 9, fmt(d.value), { size: 15, weight: 600, anchor: 'middle' });
    s += tekst(x + bw / 2, h - B + 22, wrap2(d.label, maxc), { size: 13.5, fill: TOK.muted, anchor: 'middle' });
  });
  if (bench) {
    const yb = sy(+bench.value);
    s += '<line x1="' + L + '" y1="' + r1(yb) + '" x2="' + (w - R) + '" y2="' + r1(yb) +
         '" stroke="' + TOK.bench + '" stroke-width="2" stroke-dasharray="6 4"/>';
    s += tekst(w - R, yb - 7, bench.label + ' — ' + fmt(+bench.value),
               { size: 13, fill: TOK.bench, anchor: 'end', weight: 600 });
  }
  if (katehorii.length > 1)
    s += malyuvatyLegendu(katehorii.map((k, i) => ({ label: String(o.legend[k] || k), kolir: PALITRA[i % PALITRA.length] })), w - R, 24);
  if (prymNorm)
    s += tekst(w - R, h - 8, prymNorm, { size: 12.5, fill: TOK.muted, anchor: 'end' });
  return obhortka(w, h, s, T('Стовпчикова діаграма; вісь Y: ') + vis.unit + ', ' + n + T(' стовпчиків, кожен підписаний значенням'));
}

/* ══════════════ 2. CH.waterfall — водоспад ══════════════
 * opts: { start:{label,value}, kroky:[{label, delta}], pidsumok:{label, value?},
 *         vis:{domain,unit,ticks}, w?, h? }
 * Звірка «сума сегментів = підсумок» — розбіжність кидає виняток. */
function waterfall(o) {
  const hto = 'CH.waterfall';
  o = o || {};
  const vis = perevirytyVis(o.vis, hto);
  if (vis.domain[0] !== 0)
    throw new Error(hto + T(': водоспад міряє висоти від нуля — domain[0] мусить бути 0.'));
  if (!o.start || !isFinite(+o.start.value))
    throw new Error(hto + T(': потрібен start:{label, value} — стартова колона з числом.'));
  if (!Array.isArray(o.kroky) || !o.kroky.length)
    throw new Error(hto + T(': потрібні kroky:[{label, delta}, …] — водоспад без кроків не є водоспадом.'));
  const kroky = o.kroky.map(k => ({ label: String(k.label), delta: +k.delta }));
  kroky.forEach(k => {
    if (!isFinite(k.delta)) throw new Error(hto + T(': крок «') + k.label + T('» без числового delta.'));
  });
  let bih = +o.start.value;
  const bihy = [bih];
  kroky.forEach(k => { bih += k.delta; bihy.push(bih); });
  const pidsumok = bih;
  perevirytyDilianku(+o.start.value, vis, hto, String(o.start.label));
  bihy.forEach((v, i) => perevirytyDilianku(v, vis, hto, T('проміжний підсумок №') + i));
  if (o.pidsumok && o.pidsumok.value != null &&
      Math.abs(pidsumok - o.pidsumok.value) > Math.max(1e-9, Math.abs(pidsumok) * 0.005))
    throw new Error(hto + T(': сума сегментів не сходиться з підсумком: ') + (+o.start.value) +
      T(' + Σ(кроки) = ') + (Math.round(pidsumok * 100) / 100) + T(' ≠ заявлених ') + o.pidsumok.value +
      T('. Виправте дані — діаграма не малюватиме неправду.'));
  const nazvaPid = (o.pidsumok && o.pidsumok.label) || T('Разом');

  const w = o.w || 960, h = o.h || 440, L = 76, R = 28, T = 64, B = 66;
  const sy = v => T + (vis.domain[1] - v) / (vis.domain[1] - vis.domain[0]) * (h - T - B);
  let s = malyuvatyOsY(vis, L, w - R, sy, T, hto);

  const n = kroky.length + 2, banda = (w - L - R) / n, bw = Math.min(80, banda * 0.62);
  const maxc = Math.max(8, Math.floor(banda / 8));
  const xc = i => L + i * banda + (banda - bw) / 2;

  /* стартова колона */
  s += '<rect x="' + r1(xc(0)) + '" y="' + r1(sy(bihy[0])) + '" width="' + r1(bw) + '" height="' +
       r1(sy(0) - sy(bihy[0])) + '" rx="2" fill="' + TOK.bar + '"/>';
  s += tekst(xc(0) + bw / 2, sy(bihy[0]) - 9, fmt(bihy[0]), { size: 15, weight: 700, anchor: 'middle' });
  s += tekst(xc(0) + bw / 2, h - B + 22, wrap2(String(o.start.label), maxc), { size: 13.5, fill: TOK.muted, anchor: 'middle' });

  /* кроки: сегмент + значення на ньому + пунктирний конектор рівня */
  kroky.forEach((k, i) => {
    const pered = bihy[i], pislia = bihy[i + 1];
    const yTop = sy(Math.max(pered, pislia)), yNiz = sy(Math.min(pered, pislia));
    const x = xc(i + 1);
    s += '<line x1="' + r1(xc(i) + bw) + '" y1="' + r1(sy(pered)) + '" x2="' + r1(x) + '" y2="' +
         r1(sy(pered)) + '" stroke="' + TOK.osnova + '" stroke-width="1" stroke-dasharray="3 3"/>';
    s += '<rect x="' + r1(x) + '" y="' + r1(yTop) + '" width="' + r1(bw) + '" height="' +
         r1(Math.max(2, yNiz - yTop)) + '" rx="2" fill="' + (k.delta >= 0 ? TOK.okM : TOK.negM) + '"/>';
    s += tekst(x + bw / 2, yTop - 7, (k.delta >= 0 ? '+' : '\u2212') + fmt(Math.abs(k.delta)),
               { size: 14, weight: 600, anchor: 'middle', fill: k.delta >= 0 ? TOK.ok : TOK.neg });
    s += tekst(x + bw / 2, h - B + 22, wrap2(k.label, maxc), { size: 13.5, fill: TOK.muted, anchor: 'middle' });
  });

  /* підсумкова колона */
  const xP = xc(n - 1);
  s += '<line x1="' + r1(xc(n - 2) + bw) + '" y1="' + r1(sy(pidsumok)) + '" x2="' + r1(xP) + '" y2="' +
       r1(sy(pidsumok)) + '" stroke="' + TOK.osnova + '" stroke-width="1" stroke-dasharray="3 3"/>';
  s += '<rect x="' + r1(xP) + '" y="' + r1(sy(pidsumok)) + '" width="' + r1(bw) + '" height="' +
       r1(sy(0) - sy(pidsumok)) + '" rx="2" fill="' + TOK.accent + '"/>';
  s += tekst(xP + bw / 2, sy(pidsumok) - 9, fmt(pidsumok), { size: 15, weight: 700, anchor: 'middle', fill: TOK.accent });
  s += tekst(xP + bw / 2, h - B + 22, wrap2(nazvaPid, maxc), { size: 13.5, fill: TOK.muted, anchor: 'middle' });

  return obhortka(w, h, s, T('Водоспад: ') + String(o.start.label) + T(' плюс ') + kroky.length +
    T(' кроків дає ') + nazvaPid + ' ' + fmt(pidsumok) + ' ' + vis.unit);
}

/* ══════════════ 3. CH.funnel — воронка ══════════════
 * opts: { stadii:[{label, value}], unit, w? }
 * Нормування НА ВХІД (перша стадія = 100 %) + конверсія між сусідніми стадіями. */
function _konversii(znachennia) {
  const out = [];
  for (let i = 1; i < znachennia.length; i++)
    out.push(Math.round(znachennia[i] / znachennia[i - 1] * 1000) / 10);
  return out;
}
function funnel(o) {
  const hto = 'CH.funnel';
  o = o || {};
  if (typeof o.unit !== 'string' || !o.unit.trim())
    throw new Error(T('КОНТРАКТ (') + hto + T('): unit відсутня — «100» без одиниці не є даними. Приклад: unit:"од.".'));
  if (!Array.isArray(o.stadii) || o.stadii.length < 2)
    throw new Error(hto + T(': потрібно ≥2 стадій [{label, value}, …].'));
  const st = o.stadii.map(d => ({ label: String(d.label), value: +d.value }));
  st.forEach(d => {
    if (!isFinite(d.value) || d.value <= 0)
      throw new Error(hto + T(': стадія «') + d.label + '» = ' + d.value + T(' — потрібне додатне число.'));
  });
  const vhid = st[0].value;
  const konv = _konversii(st.map(d => d.value));

  const w = o.w || 960, L = 210, R = 170, T = 64, rH = 46, gap = 38;
  const h = T + st.length * rH + (st.length - 1) * gap + 30;
  const maxBw = w - L - R;
  let s = tekst(L, 34, T('нормовано на вхід — перша стадія = 100\u00A0%'), { size: 13, fill: TOK.muted });

  st.forEach((d, i) => {
    const y = T + i * (rH + gap), yc = y + rH / 2;
    const bw = maxBw * d.value / vhid;
    const chastka = Math.round(d.value / vhid * 1000) / 10;
    s += '<rect x="' + L + '" y="' + r1(y) + '" width="' + r1(Math.max(2, bw)) + '" height="' + rH +
         '" rx="3" fill="' + TOK.bar + '"' + (i ? ' opacity="' + (1 - i * 0.14) + '"' : '') + '/>';
    s += tekst(L - 14, yc + 5, d.label, { size: 15, anchor: 'end' });
    s += tekst(L + Math.max(2, bw) + 12, yc + 5,
               fmt(d.value) + '\u00A0' + o.unit + ' · ' + fmt(chastka) + '\u00A0%', { size: 14, weight: 600 });
    if (i < st.length - 1) {
      /* стрілка конверсії — намальована фігурою, а не текстовим символом-іконкою */
      const ys = y + rH + 6, yk = y + rH + gap - 6, xs = L + 22;
      s += '<line x1="' + xs + '" y1="' + r1(ys) + '" x2="' + xs + '" y2="' + r1(yk - 5) +
           '" stroke="' + TOK.accent + '" stroke-width="2"/>';
      s += '<path d="M' + (xs - 4) + ' ' + r1(yk - 6) + ' L' + (xs + 4) + ' ' + r1(yk - 6) +
           ' L' + xs + ' ' + r1(yk) + ' Z" fill="' + TOK.accent + '"/>';
      s += tekst(xs + 14, y + rH + gap / 2 + 4, T('конверсія ') + fmt(konv[i]) + '\u00A0%',
                 { size: 13.5, weight: 600, fill: TOK.accent });
    }
  });
  return obhortka(w, h, s, T('Воронка з ') + st.length + T(' стадій, нормована на вхід, з конверсіями між стадіями'));
}

/* ══════════════ 4. CH.lineDual — дві лінії на двох осях ══════════════
 * opts: { x:{domain,unit,ticks}, seriesL:{name, vis, dani:[{x,y}], kolir?},
 *         seriesR:{…}, benchmark?:{value,label} (на ЛІВІЙ шкалі), w?, h? }
 * Легенда обовʼязкова й малюється сама з name обох рядів (2 кольори = 2 підписи). */
function lineDual(o) {
  const hto = 'CH.lineDual';
  o = o || {};
  const vx = perevirytyVis(o.x, hto, 'x');
  const pary = [['seriesL', o.seriesL], ['seriesR', o.seriesR]];
  pary.forEach(([im, sr]) => {
    if (!sr || typeof sr !== 'object')
      throw new Error(hto + T(': відсутня ') + im + T(' — подвійна вісь має сенс лише з двома рядами.'));
    if (typeof sr.name !== 'string' || !sr.name.trim())
      throw new Error(hto + ': ' + im + T('.name відсутнє — легенда обовʼязкова при двох кольорах.'));
    perevirytyVis(sr.vis, hto, im + '.vis');
    if (!Array.isArray(sr.dani) || sr.dani.length < 2)
      throw new Error(hto + ': ' + im + T('.dani — потрібно ≥2 точок {x, y}.'));
    sr.dani.forEach(p => {
      perevirytyDilianku(+p.x, vx, hto, im + ' x=' + p.x);
      perevirytyDilianku(+p.y, sr.vis, hto, im + ' y=' + p.y);
    });
  });
  const sL = o.seriesL, sR = o.seriesR;
  const kL = sL.kolir || TOK.bar, kR = sR.kolir || TOK.accent;
  const bench = perevirytyBenchmark(o.benchmark, hto);
  if (bench) perevirytyDilianku(+bench.value, sL.vis, hto, bench.label);

  const w = o.w || 960, h = o.h || 440, L = 76, R = 76, T = 64, B = 58;
  const sx = v => L + (v - vx.domain[0]) / (vx.domain[1] - vx.domain[0]) * (w - L - R);
  const syL = v => T + (sL.vis.domain[1] - v) / (sL.vis.domain[1] - sL.vis.domain[0]) * (h - T - B);
  const syR = v => T + (sR.vis.domain[1] - v) / (sR.vis.domain[1] - sR.vis.domain[0]) * (h - T - B);
  perevirytyShchilnist(vx.ticks.map(sx), hto);
  perevirytyShchilnist(sL.vis.ticks.map(syL), hto);
  perevirytyShchilnist(sR.vis.ticks.map(syR), hto);

  let s = '';
  /* сітка від лівої осі — кожна лінія з числом; права вісь — риски + числа */
  sL.vis.ticks.forEach(t => {
    const y = syL(t);
    s += '<line x1="' + L + '" y1="' + r1(y) + '" x2="' + (w - R) + '" y2="' + r1(y) +
         '" stroke="' + TOK.grid + '" stroke-width="1"/>';
    s += tekst(L - 10, y + 4.5, fmt(t), { size: 13, fill: kL, anchor: 'end' });
  });
  sR.vis.ticks.forEach(t => {
    const y = syR(t);
    s += '<line x1="' + (w - R) + '" y1="' + r1(y) + '" x2="' + (w - R + 5) + '" y2="' + r1(y) +
         '" stroke="' + TOK.osnova + '" stroke-width="1.5"/>';
    s += tekst(w - R + 9, y + 4.5, fmt(t), { size: 13, fill: kR, anchor: 'start' });
  });
  s += tekst(L - 10, T - 18, sL.vis.unit, { size: 13, fill: kL, anchor: 'end', weight: 600 });
  s += tekst(w - 6, T - 18, sR.vis.unit, { size: 13, fill: kR, anchor: 'end', weight: 600 });
  /* вісь X знизу з числами та одиницею */
  s += '<line x1="' + L + '" y1="' + (h - B) + '" x2="' + (w - R) + '" y2="' + (h - B) +
       '" stroke="' + TOK.osnova + '" stroke-width="1.5"/>';
  vx.ticks.forEach(t => {
    const x = sx(t);
    s += '<line x1="' + r1(x) + '" y1="' + (h - B) + '" x2="' + r1(x) + '" y2="' + (h - B + 5) +
         '" stroke="' + TOK.osnova + '" stroke-width="1.5"/>';
    s += tekst(x, h - B + 24, fmt(t), { size: 13, fill: TOK.muted, anchor: 'middle' });
  });
  s += tekst(L + (w - L - R) / 2, h - 14, vx.unit, { size: 13, fill: TOK.muted, anchor: 'middle' });
  /* benchmark — лише іменований */
  if (bench) {
    const yb = syL(+bench.value);
    s += '<line x1="' + L + '" y1="' + r1(yb) + '" x2="' + (w - R) + '" y2="' + r1(yb) +
         '" stroke="' + TOK.bench + '" stroke-width="2" stroke-dasharray="6 4"/>';
    s += tekst(L + 8, yb - 6, bench.label + ' — ' + fmt(+bench.value), { size: 13, fill: TOK.bench, weight: 600 });
  }
  /* лінії + точки */
  [[sL, kL, syL], [sR, kR, syR]].forEach(([sr, k, syF]) => {
    const pts = sr.dani.map(p => r1(sx(+p.x)) + ',' + r1(syF(+p.y))).join(' ');
    s += '<polyline points="' + pts + '" fill="none" stroke="' + k +
         '" stroke-width="2.5" stroke-linejoin="round"/>';
    sr.dani.forEach(p => {
      s += '<circle cx="' + r1(sx(+p.x)) + '" cy="' + r1(syF(+p.y)) + '" r="3.5" fill="' + k + '"/>';
    });
  });
  s += malyuvatyLegendu([{ label: sL.name, kolir: kL }, { label: sR.name, kolir: kR }], w - 6, 24);
  return obhortka(w, h, s, T('Дві лінії на двох іменованих осях: ') + sL.name + T(' та ') + sR.name);
}

/* ══════════════ 5. CH.tornado — чутливість ══════════════
 * opts: { baza:{value, label}, vis:{domain,unit,ticks},
 *         faktory:[{label, low, high}], w?, h? }
 * Фактори сортуються за розмахом (канон торнадо); підпис значення — на кожному
 * кінці; для дуже коротких плечей підпис переходить ВСЕРЕДИНУ бруска (без повороту). */
function tornado(o) {
  const hto = 'CH.tornado';
  o = o || {};
  const vis = perevirytyVis(o.vis, hto);
  if (!o.baza || !isFinite(+o.baza.value))
    throw new Error(hto + T(': потрібна baza:{value, label} — торнадо без названої бази нечитабельне.'));
  const bazaV = +o.baza.value, bazaL = o.baza.label || T('база');
  perevirytyDilianku(bazaV, vis, hto, bazaL);
  if (!Array.isArray(o.faktory) || !o.faktory.length)
    throw new Error(hto + T(': faktory порожні — потрібно [{label, low, high}, …].'));
  const faktory = o.faktory.map(f => ({ label: String(f.label), low: +f.low, high: +f.high }));
  faktory.forEach(f => {
    perevirytyDilianku(f.low, vis, hto, f.label + '.low');
    perevirytyDilianku(f.high, vis, hto, f.label + '.high');
  });
  faktory.sort((a, b) => Math.abs(b.high - b.low) - Math.abs(a.high - a.low));

  const w = o.w || 960, L = 210, R = 46, T = 76, B = 56, rH = 54;
  const h = o.h || (T + faktory.length * rH + B);
  const sx = v => L + (v - vis.domain[0]) / (vis.domain[1] - vis.domain[0]) * (w - L - R);
  perevirytyShchilnist(vis.ticks.map(sx), hto);

  let s = '';
  vis.ticks.forEach(t => {
    const x = sx(t);
    s += '<line x1="' + r1(x) + '" y1="' + (T - 6) + '" x2="' + r1(x) + '" y2="' + (h - B + 6) +
         '" stroke="' + TOK.grid + '" stroke-width="1"/>';
    s += tekst(x, h - B + 26, fmt(t), { size: 13, fill: TOK.muted, anchor: 'middle' });
  });
  s += tekst(w - R, h - 8, vis.unit, { size: 13, fill: TOK.muted, anchor: 'end' });
  /* базова лінія — іменована за контрактом */
  const xb = sx(bazaV);
  s += '<line x1="' + r1(xb) + '" y1="' + (T - 8) + '" x2="' + r1(xb) + '" y2="' + (h - B + 6) +
       '" stroke="' + TOK.accent + '" stroke-width="2" stroke-dasharray="5 4"/>';
  s += tekst(xb, T - 16, bazaL + ': ' + fmt(bazaV) + '\u00A0' + vis.unit,
             { size: 14, weight: 600, anchor: 'middle', fill: TOK.accent });

  faktory.forEach((f, i) => {
    const yc = T + i * rH + rH / 2;
    s += tekst(L - 14, yc + 4.5, f.label, { size: 14.5, anchor: 'end' });
    [['low', f.low, TOK.negM], ['high', f.high, TOK.okM]].forEach(([, v, kolir]) => {
      const x0 = Math.min(sx(v), xb), x1 = Math.max(sx(v), xb);
      s += '<rect x="' + r1(x0) + '" y="' + r1(yc - 13) + '" width="' + r1(Math.max(1, x1 - x0)) +
           '" height="26" rx="2" fill="' + kolir + '"/>';
      /* підпис на зовнішньому кінці; закоротке плече → підпис усередину, без повороту */
      const txt = fmt(v), wt = estW(txt, 13.5);
      const zovni = sx(v) < xb;   // плече ліворуч від бази
      if (zovni) {
        if (sx(v) - 7 - wt >= L + 2)
          s += tekst(sx(v) - 7, yc + 4.5, txt, { size: 13.5, anchor: 'end' });
        else
          s += tekst(sx(v) + 7, yc + 4.5, txt, { size: 13.5, anchor: 'start', fill: TOK.paper, weight: 600 });
      } else {
        if (sx(v) + 7 + wt <= w - R - 2)
          s += tekst(sx(v) + 7, yc + 4.5, txt, { size: 13.5, anchor: 'start' });
        else
          s += tekst(sx(v) - 7, yc + 4.5, txt, { size: 13.5, anchor: 'end', fill: TOK.paper, weight: 600 });
      }
    });
  });
  return obhortka(w, h, s, T('Торнадо-діаграма чутливості навколо ') + bazaL + ' ' + fmt(bazaV) + ' ' + vis.unit +
    T(', фактори за спаданням розмаху'));
}

/* ══════════════ 6. CH.timelineGantt — стрічковий графік ══════════════
 * opts: { vis:{domain,unit,ticks, unitKor?}, riadky:[{label, start, end, cat?}],
 *         legend?, w? }
 * Числова часова вісь згори; кожен брусок підписаний тривалістю (всередині
 * або праворуч — без повороту). */
function timelineGantt(o) {
  const hto = 'CH.timelineGantt';
  o = o || {};
  const vis = perevirytyVis(o.vis, hto);
  if (!Array.isArray(o.riadky) || !o.riadky.length)
    throw new Error(hto + T(': riadky порожні — потрібно [{label, start, end}, …].'));
  const riadky = o.riadky.map(rr => ({ label: String(rr.label), start: +rr.start, end: +rr.end, cat: rr.cat }));
  riadky.forEach(rr => {
    if (!(rr.start < rr.end))
      throw new Error(hto + T(': рядок «') + rr.label + T('» має start ≥ end (') + rr.start + ' ≥ ' + rr.end + ').');
    perevirytyDilianku(rr.start, vis, hto, rr.label + '.start');
    perevirytyDilianku(rr.end, vis, hto, rr.label + '.end');
  });
  const katehorii = [...new Set(riadky.filter(rr => rr.cat != null).map(rr => rr.cat))];
  perevirytyLegendu(katehorii, o.legend, hto);
  const unitKor = o.vis.unitKor || '';

  const w = o.w || 960, L = 220, R = 36, T = 64, B = 26, rH = 46;
  const h = T + riadky.length * rH + B;
  const sx = v => L + (v - vis.domain[0]) / (vis.domain[1] - vis.domain[0]) * (w - L - R);
  perevirytyShchilnist(vis.ticks.map(sx), hto);

  let s = tekst(L, T - 34, vis.unit, { size: 13, fill: TOK.muted });
  vis.ticks.forEach(t => {
    const x = sx(t);
    s += '<line x1="' + r1(x) + '" y1="' + (T - 6) + '" x2="' + r1(x) + '" y2="' + (h - B + 4) +
         '" stroke="' + TOK.grid + '" stroke-width="1"/>';
    s += tekst(x, T - 12, fmt(t), { size: 13, fill: TOK.muted, anchor: 'middle' });
  });
  riadky.forEach((rr, i) => {
    const y = T + i * rH + (rH - 24) / 2, yc = y + 12;
    const x0 = sx(rr.start), x1 = sx(rr.end);
    const kolir = rr.cat != null && katehorii.length > 1
      ? PALITRA[katehorii.indexOf(rr.cat) % PALITRA.length] : TOK.bar;
    s += tekst(L - 14, yc + 4.5, rr.label, { size: 14, anchor: 'end' });
    s += '<rect x="' + r1(x0) + '" y="' + r1(y) + '" width="' + r1(x1 - x0) + '" height="24" rx="3" fill="' + kolir + '"/>';
    const txt = fmt(rr.end - rr.start) + (unitKor ? '\u00A0' + unitKor : '');
    if (x1 - x0 >= estW(txt, 13) + 16)  /* підпис усередині бруска */
      s += tekst((x0 + x1) / 2, yc + 4.5, txt, { size: 13, anchor: 'middle', fill: TOK.paper, weight: 600 });
    else                                 /* закороткий брусок → підпис праворуч */
      s += tekst(x1 + 8, yc + 4.5, txt, { size: 13, anchor: 'start', fill: TOK.muted, weight: 600 });
  });
  if (katehorii.length > 1)
    s += malyuvatyLegendu(katehorii.map((k, i) => ({ label: String(o.legend[k] || k), kolir: PALITRA[i % PALITRA.length] })), w - R, T - 34);
  return obhortka(w, h, s, T('Стрічковий графік, ') + riadky.length + T(' рядків; вісь: ') + vis.unit);
}

/* ══════════════ 7. CH.nestedTSS — вкладені площини TAM/SAM/SOM ══════════════
 * opts: { unit, rivni:[{korotke?, label, value}] (строго спадні, 2–4), w?, storona? }
 * Площа КОЖНОГО квадрата лінійно пропорційна значенню; кратність підписана
 * явно («20× SOM»). Логарифмічного режиму немає — це і є захист від лог-обману. */
function nestedTSS(o) {
  const hto = 'CH.nestedTSS';
  o = o || {};
  if (typeof o.unit !== 'string' || !o.unit.trim())
    throw new Error(T('КОНТРАКТ (') + hto + T('): unit відсутня — площі без одиниці не читаються. Приклад: unit:"млн €".'));
  const rv = o.rivni;
  if (!Array.isArray(rv) || rv.length < 2 || rv.length > 4)
    throw new Error(hto + T(': rivni — 2–4 рівні [{label, value}], найбільший перший (TAM → SAM → SOM).'));
  const rivni = rv.map(d => ({ korotke: d.korotke, label: String(d.label), value: +d.value }));
  rivni.forEach(d => {
    if (!isFinite(d.value) || d.value <= 0)
      throw new Error(hto + T(': рівень «') + d.label + '» = ' + d.value + T(' — потрібне додатне число.'));
  });
  for (let i = 1; i < rivni.length; i++)
    if (!(rivni[i].value < rivni[i - 1].value))
      throw new Error(hto + T(': рівні мають строго спадати (TAM > SAM > SOM) — інакше вкладені площі брешуть.'));

  const w = o.w || 960, S = o.storona || 300, L = 64, T = 64;
  const h = T + S + 56;
  const k = S / Math.sqrt(rivni[0].value);
  const yNiz = T + S;
  const zalyvky = rivni.length === 2 ? [TOK.a1, TOK.a3] : [TOK.a1, TOK.a2, TOK.a3, TOK.accent];
  const ostannii = rivni[rivni.length - 1];
  const bazovaNazva = ostannii.korotke || ostannii.label.split(/\s/)[0];

  let s = '';
  const storony = rivni.map(d => k * Math.sqrt(d.value));
  storony.forEach((side, i) => {
    s += '<rect x="' + L + '" y="' + r1(yNiz - side) + '" width="' + r1(side) + '" height="' + r1(side) +
         '" fill="' + zalyvky[i] + '" stroke="' + TOK.osnova + '" stroke-width="1"/>';
  });
  /* підписи — блоками праворуч із лідер-лініями (розведені по вертикалі ≥56 px) */
  const x0 = L + S + 96;
  const yBloky = storony.map(side => yNiz - side);
  for (let i = 1; i < yBloky.length; i++)
    if (yBloky[i] < yBloky[i - 1] + 56) yBloky[i] = yBloky[i - 1] + 56;
  rivni.forEach((d, i) => {
    const y = yBloky[i], yc = y + 16;
    const krat = Math.round(d.value / ostannii.value * 10) / 10;
    s += '<line x1="' + r1(L + storony[i] + 6) + '" y1="' + r1(yc) + '" x2="' + (x0 - 14) + '" y2="' + r1(yc) +
         '" stroke="' + TOK.osnova + '" stroke-width="1"/>';
    s += tekst(x0, y + 6, d.label, { size: 15, weight: 700 });
    s += tekst(x0, y + 26, fmt(d.value) + '\u00A0' + o.unit +
               (i < rivni.length - 1 ? ' · ' + fmt(krat) + '× ' + bazovaNazva : ''),
               { size: 14, fill: TOK.muted });
  });
  s += tekst(L, h - 14, T('Площі пропорційні значенням — лінійна шкала, без логарифмічних спотворень.'),
             { size: 13, fill: TOK.muted });
  return obhortka(w, h, s, T('Вкладені площини ') + rivni.map(d => d.korotke || d.label).join('/') +
    T(' з підписаною кратністю, лінійна шкала площ'));
}

/* ══════════════ 8. CH.scatterPareto — розсіювання з фронтом Парето ══════════════
 * opts: { visX, visY, tochky:[{label, x, y, aktsent?}],
 *         front?:{x:'min'|'max', y:'min'|'max', label?}, w?, h? }
 * Фронт МАЛЮЄТЬСЯ лінією через недоміновані точки і має підпис. */
function _paretoFront(tochky, nap) {
  nap = nap || { x: 'min', y: 'max' };
  const dominuye = (a, b) => {
    const kx = nap.x === 'min' ? a.x <= b.x : a.x >= b.x;
    const ky = nap.y === 'min' ? a.y <= b.y : a.y >= b.y;
    const strohe = (nap.x === 'min' ? a.x < b.x : a.x > b.x) || (nap.y === 'min' ? a.y < b.y : a.y > b.y);
    return kx && ky && strohe;
  };
  return tochky.filter(p => !tochky.some(q => dominuye(q, p)));
}
function scatterPareto(o) {
  const hto = 'CH.scatterPareto';
  o = o || {};
  const vX = perevirytyVis(o.visX, hto, 'visX');
  const vY = perevirytyVis(o.visY, hto, 'visY');
  if (!Array.isArray(o.tochky) || o.tochky.length < 2)
    throw new Error(hto + T(': tochky — потрібно ≥2 точок [{label, x, y}, …].'));
  const tochky = o.tochky.map(p => ({ label: String(p.label), x: +p.x, y: +p.y, aktsent: !!p.aktsent }));
  tochky.forEach(p => {
    perevirytyDilianku(p.x, vX, hto, p.label + '.x');
    perevirytyDilianku(p.y, vY, hto, p.label + '.y');
  });
  const nap = o.front || { x: 'min', y: 'max' };
  const frontLabel = nap.label || T('фронт Парето');

  const w = o.w || 960, h = o.h || 460, L = 76, R = 36, T = 56, B = 64;
  const sx = v => L + (v - vX.domain[0]) / (vX.domain[1] - vX.domain[0]) * (w - L - R);
  const sy = v => T + (vY.domain[1] - v) / (vY.domain[1] - vY.domain[0]) * (h - T - B);
  perevirytyShchilnist(vX.ticks.map(sx), hto);
  perevirytyShchilnist(vY.ticks.map(sy), hto);

  let s = '';
  vY.ticks.forEach(t => {
    const y = sy(t);
    s += '<line x1="' + L + '" y1="' + r1(y) + '" x2="' + (w - R) + '" y2="' + r1(y) +
         '" stroke="' + TOK.grid + '" stroke-width="1"/>';
    s += tekst(L - 10, y + 4.5, fmt(t), { size: 13, fill: TOK.muted, anchor: 'end' });
  });
  vX.ticks.forEach(t => {
    const x = sx(t);
    s += '<line x1="' + r1(x) + '" y1="' + T + '" x2="' + r1(x) + '" y2="' + (h - B) +
         '" stroke="' + TOK.grid + '" stroke-width="1"/>';
    s += tekst(x, h - B + 24, fmt(t), { size: 13, fill: TOK.muted, anchor: 'middle' });
  });
  s += tekst(L - 10, T - 18, vY.unit, { size: 13, fill: TOK.muted, anchor: 'end' });
  s += tekst(w - R, h - 12, vX.unit, { size: 13, fill: TOK.muted, anchor: 'end' });

  /* фронт: лінія через недоміновані точки + іменований підпис-шапка */
  const front = _paretoFront(tochky, nap).slice().sort((a, b) => a.x - b.x);
  if (front.length >= 2) {
    s += '<polyline points="' + front.map(p => r1(sx(p.x)) + ',' + r1(sy(p.y))).join(' ') +
         '" fill="none" stroke="' + TOK.accent + '" stroke-width="2" opacity="0.85"/>';
  }
  s += '<line x1="' + L + '" y1="' + (T - 20) + '" x2="' + (L + 26) + '" y2="' + (T - 20) +
       '" stroke="' + TOK.accent + '" stroke-width="2"/>';
  s += tekst(L + 34, T - 16, frontLabel, { size: 13, fill: TOK.muted });

  /* точки + підписи з жадібним уникненням колізій (замість повороту чи тісноти) */
  const mezhi = { x0: L + 2, y0: T + 2, x1: w - R - 2, y1: h - B - 2 };
  const zayniato = [];
  const peretyn = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  tochky.forEach(p => {
    const px = sx(p.x), py = sy(p.y);
    s += '<circle cx="' + r1(px) + '" cy="' + r1(py) + '" r="5.5" fill="' +
         (p.aktsent ? TOK.accent : TOK.bar) + '"/>';
    const wt = estW(p.label, 13.5), ht = 15;
    const kandydaty = [[9, 4, 'start'], [9, -9, 'start'], [-9, 4, 'end'], [0, -12, 'middle'],
                       [0, 19, 'middle'], [9, 17, 'start'], [-9, -9, 'end']];
    let misce = null;
    for (const [dx, dy, an] of kandydaty) {
      const bx = an === 'start' ? px + dx : an === 'end' ? px + dx - wt : px - wt / 2;
      const b = { x: bx, y: py + dy - ht + 3, w: wt, h: ht };
      if (b.x < mezhi.x0 || b.x + b.w > mezhi.x1 || b.y < mezhi.y0 || b.y + b.h > mezhi.y1) continue;
      if (zayniato.some(q => peretyn(q, b))) continue;
      misce = { x: px + dx, y: py + dy, anchor: an, b };
      break;
    }
    if (!misce) {  /* остання гавань: зсув донизу до вільного місця */
      let yv = py + 19;
      while (zayniato.some(q => peretyn(q, { x: px - wt / 2, y: yv - ht + 3, w: wt, h: ht }))) yv += 16;
      misce = { x: px, y: yv, anchor: 'middle', b: { x: px - wt / 2, y: yv - ht + 3, w: wt, h: ht } };
    }
    zayniato.push(misce.b);
    s += tekst(misce.x, misce.y, p.label, { size: 13.5, anchor: misce.anchor,
               fill: p.aktsent ? TOK.accent : TOK.ink, weight: p.aktsent ? 600 : 400 });
  });
  return obhortka(w, h, s, T('Діаграма розсіювання ') + vX.unit + T(' проти ') + vY.unit +
    T(' з намальованим і підписаним фронтом Парето'));
}

/* ── Спарклайн: мікро-тренд «як слово» (Tufte) — для плиток дашборда ──
   Навмисно БЕЗ осі (це визначення спарклайна), але з ОБОВʼЯЗКОВИМ підписом
   останнього значення + unit: «число-без-чарта» тут закрите самим підписом. */
function sparkline(o) {
  const hto = 'CH.sparkline'; o = o || {};
  if (!Array.isArray(o.tochky) || o.tochky.length < 2)
    throw new Error(hto + T(': tochky — потрібно ≥2 числа (мікро-тренд).'));
  const p = o.tochky.map(Number);
  if (p.some(v => !isFinite(v))) throw new Error(hto + T(': нечислове значення в tochky.'));
  if (typeof o.unit !== 'string' || !o.unit.trim())
    throw new Error(hto + T(': unit відсутня — останнє значення підписується з одиницею.'));
  const w = o.w || 132, h = o.h || 34, P = 3, lblW = o.bezPidpysu ? 0 : 40;
  const mn = Math.min.apply(null, p), mx = Math.max.apply(null, p), rng = (mx - mn) || 1;
  const iw = w - lblW - P * 2;
  const sx = i => P + i / (p.length - 1) * iw, sy = v => P + (mx - v) / rng * (h - P * 2);
  let d = ''; p.forEach((v, i) => d += (i ? 'L' : 'M') + r1(sx(i)) + ' ' + r1(sy(v)));
  let s = '<path d="' + d + '" fill="none" stroke="' + TOK.bar +
          '" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>';
  const lx = sx(p.length - 1), ly = sy(p[p.length - 1]);
  s += '<circle cx="' + r1(lx) + '" cy="' + r1(ly) + '" r="2.4" fill="' + TOK.accent + '"/>';
  if (lblW) s += tekst(w - P, h / 2 + 4.5, fmt(p[p.length - 1]) + ' ' + o.unit,
                       { size: 12, weight: 600, anchor: 'end' });
  return obhortka(w, h, s, T('Спарклайн-тренд, ') + p.length + T(' точок, останнє ') +
    fmt(p[p.length - 1]) + ' ' + o.unit);
}

/* ── Малі кратні (small multiples): решітка міні-стовпчиків на СПІЛЬНІЙ шкалі.
   Порівняння форм між панелями — коли одна шкала для всіх (Tufte). ── */
function smallMultiples(o) {
  const hto = 'CH.smallMultiples'; o = o || {};
  const vis = perevirytyVis(o.vis, hto);
  if (vis.domain[0] !== 0)
    throw new Error(hto + T(': спільна вісь стовпчиків мусить починатися з 0 (domain[0]=0).'));
  if (!Array.isArray(o.panels) || !o.panels.length)
    throw new Error(hto + T(': panels порожні — [{label, dani:[{label,value}]}].'));
  const cols = o.cols || Math.min(4, o.panels.length), pw = o.pw || 210, ph = o.ph || 132, gap = 18, headerH = 22;
  const rows = Math.ceil(o.panels.length / cols);
  const w = cols * pw + (cols - 1) * gap + 8, h = headerH + rows * ph + (rows - 1) * gap + 8;
  let s = tekst(6, 15, vis.unit + T(' · спільна шкала (') + o.panels.length + T(' панелей)'),
                { size: 12.5, weight: 600, fill: TOK.muted });
  o.panels.forEach((pan, idx) => {
    if (!Array.isArray(pan.dani) || !pan.dani.length)
      throw new Error(hto + T(': панель «') + pan.label + T('» без dani.'));
    const cx = (idx % cols) * (pw + gap) + 4, cy = headerH + Math.floor(idx / cols) * (ph + gap) + 4;
    const L = 8, R = 8, T = 24, B = 22, iw = pw - L - R, ih = ph - T - B;
    const sy = v => cy + T + (vis.domain[1] - v) / (vis.domain[1] - vis.domain[0]) * ih;
    s += tekst(cx + L, cy + 15, pan.label, { size: 12.5, weight: 600, fill: TOK.ink });
    s += '<line x1="' + (cx + L) + '" y1="' + r1(sy(0)) + '" x2="' + (cx + pw - R) + '" y2="' +
         r1(sy(0)) + '" stroke="' + TOK.osnova + '" stroke-width="1"/>';
    const n = pan.dani.length, banda = iw / n, bw = Math.min(30, banda * 0.6);
    pan.dani.forEach((d, i) => {
      const v = +d.value; perevirytyDilianku(v, vis, hto, pan.label + '/' + d.label);
      const x = cx + L + i * banda + (banda - bw) / 2, y1 = sy(v);
      s += '<rect x="' + r1(x) + '" y="' + r1(y1) + '" width="' + r1(bw) + '" height="' +
           r1(Math.max(0, sy(0) - y1)) + '" rx="1.5" fill="' + (d.aktsent ? TOK.accent : TOK.bar) + '"/>';
    });
  });
  return obhortka(w, h, s, T('Малі кратні: ') + o.panels.length + T(' панелей на спільній шкалі ') + vis.unit);
}

/* ── Слоупграф (Tufte): «було → стало» лініями; нахил = зміна. ── */
function slopegraph(o) {
  const hto = 'CH.slopegraph'; o = o || {};
  const vis = perevirytyVis(o.vis, hto);
  if (!Array.isArray(o.items) || !o.items.length)
    throw new Error(hto + T(': items порожні — [{label, vid, do}].'));
  const mVid = o.mitkaVid || T('Було'), mDo = o.mitkaDo || T('Стало');
  const w = o.w || 520, h = o.h || 420, T = 50, B = 34, Lx = 190, Rx = w - 190;
  const sy = v => T + (vis.domain[1] - v) / (vis.domain[1] - vis.domain[0]) * (h - T - B);
  let s = tekst(Lx, T - 20, mVid, { size: 13, weight: 600, fill: TOK.muted, anchor: 'end' }) +
          tekst(Rx, T - 20, mDo, { size: 13, weight: 600, fill: TOK.muted, anchor: 'start' }) +
          '<line x1="' + Lx + '" y1="' + T + '" x2="' + Lx + '" y2="' + (h - B) + '" stroke="' + TOK.grid + '"/>' +
          '<line x1="' + Rx + '" y1="' + T + '" x2="' + Rx + '" y2="' + (h - B) + '" stroke="' + TOK.grid + '"/>' +
          tekst(Lx - 10, h - 12, vis.unit, { size: 12, fill: TOK.muted, anchor: 'end' });
  o.items.forEach(it => {
    const v1 = +it.vid, v2 = +it.do;
    perevirytyDilianku(v1, vis, hto, it.label + '/' + mVid);
    perevirytyDilianku(v2, vis, hto, it.label + '/' + mDo);
    const y1 = sy(v1), y2 = sy(v2);
    const kol = it.aktsent ? TOK.accent : (v2 >= v1 ? TOK.ok : TOK.neg);
    s += '<line x1="' + Lx + '" y1="' + r1(y1) + '" x2="' + Rx + '" y2="' + r1(y2) +
         '" stroke="' + kol + '" stroke-width="1.6" opacity="0.85"/>' +
         '<circle cx="' + Lx + '" cy="' + r1(y1) + '" r="2.6" fill="' + kol + '"/>' +
         '<circle cx="' + Rx + '" cy="' + r1(y2) + '" r="2.6" fill="' + kol + '"/>';
    s += tekst(Lx - 12, y1 + 4.5, it.label + '  ' + fmt(v1), { size: 12.5, anchor: 'end' });
    s += tekst(Rx + 12, y2 + 4.5, fmt(v2), { size: 12.5, weight: 600, anchor: 'start' });
  });
  return obhortka(w, h, s, T('Слоупграф: ') + o.items.length + T(' рядів «') + mVid + '»→«' + mDo + T('», вісь ') + vis.unit);
}

/* ── Експорт ── */
const CH = {
  bar, waterfall, funnel, lineDual, tornado, timelineGantt, nestedTSS, scatterPareto,
  sparkline, smallMultiples, slopegraph,
  _konversii, _paretoFront, _fmt: fmt, TOKENY: TOK, PALITRA,
};
if (typeof module !== 'undefined' && module.exports) module.exports = CH;
if (typeof window !== 'undefined') window.CH = CH;

/* ══════════════ SELFTEST (node charts.js --selftest) ══════════════
 * Правило системи (Г16): спершу перевірка на ВІДОМИХ значеннях, потім довіра. */
function _selftest() {
  const pidsumky = [];
  const t = (nazva, fn) => {
    try { fn(); pidsumky.push([true, nazva]); }
    catch (e) { pidsumky.push([false, nazva + ' — ' + e.message]); }
  };
  const mistytj = (s, frag, shcho) => {
    if (!s.includes(frag)) throw new Error(T('нема «') + shcho + T('» (шукали: ') + frag + ')');
  };
  const kydaie = (fn, frag) => {
    let bulo = false;
    try { fn(); } catch (e) {
      bulo = true;
      if (!String(e.message).includes(frag))
        throw new Error(T('виняток є, але текст без «') + frag + '»: ' + e.message);
    }
    if (!bulo) throw new Error(T('очікувався виняток із «') + frag + T('», а рендер пройшов'));
  };
  const VIS = { domain: [0, 70], unit: '%', ticks: [0, 20, 40, 60] };
  const DANI = [{ label: T('Обладнання'), value: 62 }, { label: T('Резерв'), value: 5 }];

  t(T('bar: валідний рендер — SVG з усіма ticks, unit і tabular-nums'), () => {
    const s = bar({ dani: DANI, vis: VIS });
    if (!/^<svg /.test(s)) throw new Error(T('не починається з <svg'));
    [0, 20, 40, 60].forEach(x => mistytj(s, '>' + fmt(x) + '<', T('позначка ') + x));
    mistytj(s, '>%<', 'unit «%»');
    mistytj(s, 'font-variant-numeric:tabular-nums', 'tabular-nums');
    mistytj(s, '>62<', T('підпис значення 62'));
  });
  t(T('bar: без unit → виняток контракту'), () =>
    kydaie(() => bar({ dani: DANI, vis: { domain: [0, 70], ticks: [0, 20, 40, 60] } }), 'unit'));
  t(T('bar: без ticks → виняток контракту'), () =>
    kydaie(() => bar({ dani: DANI, vis: { domain: [0, 70], unit: '%' } }), 'ticks'));
  t(T('bar: без осі взагалі → виняток контракту'), () =>
    kydaie(() => bar({ dani: DANI }), T('КОНТРАКТ ОСІ')));
  t(T('bar: значення поза domain → виняток (тихого нормування нема)'), () =>
    kydaie(() => bar({ dani: [{ label: 'X', value: 90 }], vis: VIS }), T('поза domain')));
  t(T('bar: normalize без явного "max" → виняток'), () =>
    kydaie(() => bar({ dani: DANI, vis: VIS, normalize: true }), 'normalize'));
  t(T('bar: 2 категорії без легенди → виняток'), () =>
    kydaie(() => bar({ dani: [{ label: 'A', value: 10, cat: 'a' }, { label: 'B', value: 20, cat: 'b' }], vis: VIS }), T('легенд')));
  t(T('bar: benchmark без label → виняток (лінія мусить бути іменована)'), () =>
    kydaie(() => bar({ dani: DANI, vis: VIS, benchmark: { value: 50 } }), T('іменован')));
  t(T('bar: domain[0] ≠ 0 → виняток (обрізаний стовпчик бреше)'), () =>
    kydaie(() => bar({ dani: DANI, vis: { domain: [10, 70], unit: '%', ticks: [20, 40, 60] } }), 'domain[0]'));

  t(T('funnel: 100→40→10 дає конверсії 40 % і 25 %, нормування на вхід'), () => {
    const k = _konversii([100, 40, 10]);
    if (k.length !== 2 || k[0] !== 40 || k[1] !== 25)
      throw new Error(T('конверсії [') + k.join(', ') + '] ≠ [40, 25]');
    const s = funnel({
      stadii: [{ label: T('Ліди'), value: 100 }, { label: T('Кваліфіковані'), value: 40 }, { label: T('Угоди'), value: 10 }],
      unit: T('од.'),
    });
    mistytj(s, T('конверсія 40\u00A0%'), T('конверсія 40 %'));
    mistytj(s, T('конверсія 25\u00A0%'), T('конверсія 25 %'));
    mistytj(s, '100\u00A0%', T('нормування на вхід (100 %)'));
    mistytj(s, '10\u00A0%', T('частка входу для останньої стадії (10 %)'));
  });
  t(T('funnel: без unit → виняток'), () =>
    kydaie(() => funnel({ stadii: [{ label: 'A', value: 10 }, { label: 'B', value: 5 }] }), 'unit'));

  t(T('waterfall: 100 − 30 − 20 → підсумок 50 на рендері, сегменти підписані'), () => {
    const s = waterfall({
      start: { label: T('Старт'), value: 100 },
      kroky: [{ label: T('А'), delta: -30 }, { label: T('Б'), delta: -20 }],
      pidsumok: { label: T('Разом'), value: 50 },
      vis: { domain: [0, 110], unit: T('од.'), ticks: [0, 50, 100] },
    });
    mistytj(s, '>50<', T('підсумок 50'));
    mistytj(s, '>\u221230<', T('сегмент −30'));
    mistytj(s, '>\u221220<', T('сегмент −20'));
  });
  t(T('waterfall: заявлений підсумок ≠ сумі сегментів → виняток'), () =>
    kydaie(() => waterfall({
      start: { label: T('Старт'), value: 100 },
      kroky: [{ label: T('А'), delta: -30 }],
      pidsumok: { label: T('Разом'), value: 60 },
      vis: { domain: [0, 110], unit: T('од.'), ticks: [0, 50, 100] },
    }), T('не сходиться')));

  t(T('lineDual: без vis правого ряду → виняток контракту'), () =>
    kydaie(() => lineDual({
      x: { domain: [0, 10], unit: T('х'), ticks: [0, 5, 10] },
      seriesL: { name: 'A', vis: { domain: [0, 10], unit: T('у'), ticks: [0, 5, 10] }, dani: [{ x: 0, y: 1 }, { x: 10, y: 9 }] },
      seriesR: { name: 'B', dani: [{ x: 0, y: 2 }, { x: 10, y: 5 }] },
    }), T('КОНТРАКТ ОСІ')));

  t(T('tornado: рендер із базою 22,51 і підписами обох плечей'), () => {
    const s = tornado({
      baza: { value: 22.51, label: T('база') },
      vis: { domain: [14, 30], unit: '%', ticks: [14, 18, 22, 26, 30] },
      faktory: [{ label: 'OPEX', low: 20.9, high: 24.2 }, { label: T('Ціна'), low: 15.8, high: 28.4 }],
    });
    mistytj(s, '22,51', T('база 22,51'));
    mistytj(s, '>15,8<', T('підпис плеча 15,8'));
    mistytj(s, '>28,4<', T('підпис плеча 28,4'));
  });

  t(T('nestedTSS: кратність «20× SOM» підписана; зростаючі рівні → виняток'), () => {
    const s = nestedTSS({
      unit: T('млн €'),
      rivni: [{ korotke: 'TAM', label: 'TAM', value: 500 },
              { korotke: 'SAM', label: 'SAM', value: 120 },
              { korotke: 'SOM', label: 'SOM', value: 25 }],
    });
    mistytj(s, '20× SOM', T('кратність 20× SOM'));
    mistytj(s, T('лінійна шкала'), T('примітка про лінійну шкалу'));
    kydaie(() => nestedTSS({ unit: T('млн €'), rivni: [{ label: 'A', value: 10 }, { label: 'B', value: 20 }] }), T('спадати'));
  });

  t(T('scatterPareto: фронт = лише недоміновані точки'), () => {
    const f = _paretoFront([{ x: 58, y: 18.9 }, { x: 63, y: 20.8 }, { x: 66, y: 19.5 }, { x: 70.6, y: 22.51 }],
                           { x: 'min', y: 'max' });
    if (f.length !== 3) throw new Error(T('фронт із ') + f.length + T(' точок, очікувано 3 (66;19,5 домінується)'));
    if (f.some(p => p.x === 66)) throw new Error(T('домінована точка (66; 19,5) потрапила у фронт'));
  });

  t(T('timelineGantt: рядок поза domain → виняток'), () =>
    kydaie(() => timelineGantt({
      vis: { domain: [0, 24], unit: T('міс'), ticks: [0, 12, 24] },
      riadky: [{ label: T('Х'), start: 2, end: 30 }],
    }), T('поза domain')));

  t(T('усі 8 генераторів повертають <svg …>…</svg>'), () => {
    const svgs = [
      bar({ dani: DANI, vis: VIS }),
      waterfall({ start: { label: T('С'), value: 40 }, kroky: [{ label: T('К'), delta: 10 }],
                  vis: { domain: [0, 60], unit: T('од.'), ticks: [0, 30, 60] } }),
      funnel({ stadii: [{ label: 'A', value: 10 }, { label: 'B', value: 4 }], unit: T('од.') }),
      lineDual({ x: { domain: [0, 10], unit: T('х'), ticks: [0, 5, 10] },
                 seriesL: { name: 'A', vis: { domain: [0, 10], unit: T('у1'), ticks: [0, 5, 10] }, dani: [{ x: 0, y: 1 }, { x: 10, y: 9 }] },
                 seriesR: { name: 'B', vis: { domain: [0, 100], unit: T('у2'), ticks: [0, 50, 100] }, dani: [{ x: 0, y: 20 }, { x: 10, y: 80 }] } }),
      tornado({ baza: { value: 5, label: T('б') }, vis: { domain: [0, 10], unit: '%', ticks: [0, 5, 10] },
                faktory: [{ label: T('Ф'), low: 3, high: 8 }] }),
      timelineGantt({ vis: { domain: [0, 12], unit: T('міс'), ticks: [0, 6, 12] },
                      riadky: [{ label: T('Р'), start: 0, end: 6 }] }),
      nestedTSS({ unit: T('од.'), rivni: [{ label: 'A', value: 100 }, { label: 'B', value: 20 }] }),
      scatterPareto({ visX: { domain: [0, 10], unit: T('х'), ticks: [0, 5, 10] },
                      visY: { domain: [0, 10], unit: T('у'), ticks: [0, 5, 10] },
                      tochky: [{ label: 'a', x: 2, y: 3 }, { label: 'b', x: 7, y: 8 }] }),
    ];
    svgs.forEach((s, i) => {
      if (!/^<svg [^]*<\/svg>$/.test(s)) throw new Error(T('генератор №') + (i + 1) + T(' повернув не SVG'));
    });
  });

  /* ── нові типи (Феррарі-2): sparkline · smallMultiples · slopegraph ── */
  t(T('sparkline: ≥2 точки → SVG із підписом останнього значення + unit'), () => {
    const s = sparkline({ tochky: [62, 78, 71, 96, 110], unit: T('М€') });
    if (!/^<svg /.test(s)) throw new Error(T('не <svg'));
    mistytj(s, T('>110 М€<'), T('підпис останнього значення'));
  });
  t(T('sparkline: 1 точка → виняток (мікро-тренд потребує ≥2)'), () =>
    kydaie(() => sparkline({ tochky: [5], unit: '%' }), 'tochky'));
  t(T('sparkline: без unit → виняток (число без одиниці)'), () =>
    kydaie(() => sparkline({ tochky: [1, 2] }), 'unit'));
  t(T('smallMultiples: спільна шкала, кожна панель у SVG'), () => {
    const s = smallMultiples({ vis: VIS, panels: [
      { label: 'Q1', dani: [{ label: 'a', value: 62 }, { label: 'b', value: 20 }] },
      { label: 'Q2', dani: [{ label: 'a', value: 40 }, { label: 'b', value: 55 }] }] });
    if (!/^<svg /.test(s)) throw new Error(T('не <svg'));
    mistytj(s, T('спільна шкала'), T('позначка спільної шкали'));
  });
  t(T('smallMultiples: значення поза domain → виняток контракту'), () =>
    kydaie(() => smallMultiples({ vis: VIS, panels: [{ label: 'X', dani: [{ label: 'a', value: 900 }] }] }), T('поза domain')));
  t(T('slopegraph: «було→стало», підписи значень обабіч'), () => {
    const s = slopegraph({ vis: VIS, items: [
      { label: T('Показник'), vid: 20, do: 60 }, { label: T('Інший'), vid: 55, do: 30 }] });
    if (!/^<svg /.test(s)) throw new Error(T('не <svg'));
    mistytj(s, '>60<', T('праве значення'));
  });
  t(T('slopegraph: без осі → виняток контракту'), () =>
    kydaie(() => slopegraph({ items: [{ label: 'a', vid: 1, do: 2 }] }), T('вісь не задана')));

  /* підсумок */
  const zeleni = pidsumky.filter(p => p[0]).length;
  const Z = '\u001b[32m', C = '\u001b[31m', R0 = '\u001b[0m';
  pidsumky.forEach(([ok, nazva]) => console.log((ok ? Z + '  \u2713 ' : C + '  \u2717 ') + nazva + R0));
  if (zeleni === pidsumky.length) {
    console.log(Z + '\nSELFTEST PASS \u2014 ' + zeleni + '/' + pidsumky.length + ' \u043f\u0435\u0440\u0435\u0432\u0456\u0440\u043e\u043a \u0437\u0435\u043b\u0435\u043d\u0456' + R0);
    return 0;
  }
  console.log(C + '\nSELFTEST FAIL \u2014 ' + zeleni + '/' + pidsumky.length + R0);
  return 1;
}

if (typeof process !== 'undefined' && typeof require === 'function' &&
    typeof module !== 'undefined' && require.main === module) {
  if (process.argv.includes('--selftest')) process.exit(_selftest());
  console.log(T('charts.js — бібліотека, не CLI. Прогін перевірок: node charts.js --selftest'));
}
