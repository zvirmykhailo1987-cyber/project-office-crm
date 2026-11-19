# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Живі стани інтерфейсу: відгук на дію, помилка біля поля, оголошення.

ЧОМУ ЦЕЙ ФАЙЛ ЗʼЯВИВСЯ. Вимір 06.08.2026 показав розрив між тим, як система
ВИГЛЯДАЄ, і тим, як вона ПОВОДИТЬСЯ: візуальний шар зроблено ретельно, а
поведінковий — ні. Конкретно:

  * жодна з 58 форм не давала відгуку на сабміт — людина тисне «Зберегти» й не
    знає, чи щось сталось; типова реакція — натиснути ще раз;
  * інлайнових помилок біля полів не було взагалі, лише банер угорі сторінки:
    на довгій формі банер каже ЩО сталось, але не показує ДЕ виправляти;
  * стрічка, лічильники й чат оновлювались полінгом мовчки — читач екрана про
    зміни не дізнавався ніколи.

Ці тести навмисно перевіряють РОЗМІТКУ, а не вигляд: вони гарантують, що гачки,
на яких тримається поведінка (`aria-invalid`, `aria-describedby`, `role`,
`aria-live`, `data-skelet`), не зникнуть із шаблонів при наступній правці.
Сам вигляд — на JS-гейті `ui_audit.js`; сам відгук кнопки — вимірюється в
браузері, бо це поведінка `vzayemodiyi.js`, а не сервера.
"""
import pathlib
import re

import pytest
from sqlmodel import select

from app.models import Pipeline, Program
from app.seed_pipelines import seed

KOREN = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture()
def baza(s):
    seed(s)
    s.commit()
    return s


def _kod(s):
    return s.exec(select(Program)).first().code


# ── Регіон оголошень ─────────────────────────────────────────────────────────

def test_region_ogoloshen_ye_na_kozhnomu_ekrani(klient):
    """Один регіон на всю систему — у `base.html`, тобто на кожному екрані."""
    for shlyah in ("/", "/strichka", "/dovidnyk", "/zavdannya"):
        t = klient.get(shlyah).text
        assert 'id="ogoloshennya"' in t, f"{shlyah}: немає регіону оголошень"
        assert 'aria-live="polite"' in t, f"{shlyah}: регіон без aria-live"


# ── Словник обох шкір ────────────────────────────────────────────────────────
# Той самий контракт виражений різними класами: `studiya` має власні `.err`,
# `.pole-err`, `.ava`; `hlybyna` бере компоненти бібліотеки (`k-banner krytych`,
# `.pomylka`, `.k-avatar`). Перевіряти треба намір, а не словник — інакше тест
# червоніє при зміні типової шкіри, хоча поведінка ціла (з'ясовано 09.08.2026
# при переході на `hlybyna`).
BANER_POMYLKY = ('class="err" role="alert"', 'k-banner krytych" role="alert"')
POLE_POMYLKA = ('class="pole-err"', 'class="pomylka"')
LYUDYNA = ('class="monogram', 'class="ava', 'class="k-avatar')


def _bud_yakyi(tekst, varianty):
    return any(v in tekst for v in varianty)


def _skilky(tekst, varianty):
    return sum(tekst.count(v) for v in varianty)

# ── Помилка біля поля ────────────────────────────────────────────────────────

def test_pomylka_kodu_stadiyi_bilya_polya(baza, klient):
    """`err=kod_zaynyato` має позначити САМЕ поле коду, а не лише банер угорі."""
    t = klient.get(f"/napryamy/{_kod(baza)}?err=kod_zaynyato").text
    assert 'id="e-stage-kod"' in t, "немає підпису біля поля коду"
    assert 'aria-describedby="e-stage-kod"' in t, "поле не звʼязане з підписом"
    assert 'aria-invalid="true"' in t, "поле не позначене як невалідне"
    assert _bud_yakyi(t, BANER_POMYLKY), "банер-підсумок теж має лишитись"


def test_bez_pomylky_pole_chyste(baza, klient):
    """Без помилки жодне поле не має бути позначене — інакше сигнал знецінюється."""
    t = klient.get(f"/napryamy/{_kod(baza)}").text
    assert 'aria-invalid="true"' not in t
    assert 'class="pole-err"' not in t


def test_pomylka_perenesennya_ugod_bilya_potribnoyi_stadiyi(baza, klient):
    """`err=kudy` несе `sid` — підпис має стати біля ТІЄЇ стадії, а не всіх.

    Без `sid` довелося б підсвітити кожну стадію воронки, і людина шукала б
    поглядом, яка з них насправді завинила.
    """
    from app.models import Deal
    from app.web.napryamy import _stadiyi

    prog = baza.exec(select(Program)).first()
    pipe = baza.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one()
    stadiyi = _stadiyi(baza, pipe.id)
    sid = stadiyi[0].id

    # Поле «куди перенести» існує ЛИШЕ там, де є що переносити — і сама помилка
    # `kudy` теж можлива тільки тоді. Без угоди тест перевіряв би неможливий стан.
    baza.add(Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=sid,
                  title="Угода для перевірки перенесення"))
    baza.commit()

    t = klient.get(f"/napryamy/{prog.code}?err=kudy&sid={sid}").text
    assert f'id="e-kudy-{sid}"' in t, "підпис не став біля потрібної стадії"
    # рівно один підпис на сторінку, а не по одному на кожну стадію
    assert _skilky(t, POLE_POMYLKA) == 1


def test_pomylka_vhodu_bilya_polya_parolya(klient_bez_vhodu):
    """Помилка входу — завжди про пароль: логін обирається зі списку."""
    r = klient_bez_vhodu.post("/login",
                              data={"login": "nemaye", "password": "hibne"},
                              follow_redirects=True)
    assert 'id="e-parol"' in r.text, "немає підпису біля поля пароля"
    assert 'aria-describedby="e-parol"' in r.text


# ── Стани в розмітці й у стилях ──────────────────────────────────────────────

def test_usi_banery_pomylok_ogoloshuyutsya(klient):
    """`.err` без `role="alert"` читач екрана мовчки пропускає.

    Раніше `role` мали 3 з 9 банерів — тобто дві третини помилок для незрячого
    користувача просто не існували.
    """
    bez_role = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        for i, ryad in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if 'class="err"' in ryad and 'role="alert"' not in ryad:
                bez_role.append(f"{f.relative_to(KOREN)}:{i}")
    assert not bez_role, "банери помилок без role=alert: " + ", ".join(bez_role)


def test_kozhna_forma_distane_vidhuk():
    """Гейт: жодна форма не сабмітиться мовчки.

    Шляхів до відгуку рівно три, і кожна форма мусить мати щонайменше один:
      1. кнопка сабміту — делегований обробник ставить на неї спінер;
      2. `data-sabmit-na-zminu` — фільтр без кнопки, що діє одразу; обробник
         сабмітить його через `requestSubmit()`, тобто подія `submit` настає,
         і смужка під формою малюється тим самим шляхом, що й спінер на кнопці;
      3. HTMX-форма з `hx-indicator` або з ціллю `data-skelet` — там відгук дає
         сам HTMX, а нативної події `submit` не буває взагалі, тож обробник до
         неї й не дійшов би.

    Тест рахує форми в шаблонах, а не на відрендерених сторінках: форма, яка
    показується лише певній ролі чи лише при певних даних, інакше просто не
    потрапила б у вибірку — і саме вона проскочила б повз відгук.
    """
    import re
    forma = re.compile(r"<form\b.*?</form>", re.S)
    knopka = re.compile(r'<button(?![^>]*type=["\'](?:button|reset)["\'])'
                        r'|<input[^>]*type=["\']submit["\']')
    # без кнопки — форма мусить бути позначена як «діє одразу», інакше її
    # взагалі неможливо відправити мишею, і це вже інший дефект
    sam_sabmit = re.compile(r"data-sabmit-na-zminu|requestSubmit\(")
    htmx_vidhuk = re.compile(r"hx-indicator=")

    # Цілі зі скелетом збираємо з УСІХ шаблонів: `hx-target` часто вказує на
    # блок, оголошений в іншому файлі-частині.
    skelet_cili = set()
    for f in (KOREN / "templates_v2").rglob("*.html"):
        for m in re.finditer(r'id="([^"]+)"[^>]*data-skelet', f.read_text(encoding="utf-8")):
            skelet_cili.add("#" + m.group(1))

    nimi = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in forma.finditer(t):
            blok = m.group(0)
            if knopka.search(blok) or sam_sabmit.search(blok) or htmx_vidhuk.search(blok):
                continue
            cil = re.search(r'hx-target="([^"]+)"', blok)
            if cil and cil.group(1) in skelet_cili:
                continue
            nimi.append(f"{f.relative_to(KOREN)}:{t[:m.start()].count(chr(10)) + 1}")
    assert not nimi, "форми без жодного шляху до відгуку: " + ", ".join(nimi)


def test_skelet_pidklyucheno_do_zhyvykh_tsiley(klient):
    """`.skelet` існував лише у вітрині `/dyzayn` і не працював ніде."""
    for shlyah, cil in (("/dovidnyk", 'id="rows" data-skelet'),
                        ("/", 'id="palitra-rez" data-skelet')):
        assert cil in klient.get(shlyah).text, f"{shlyah}: {cil} не підключено"


# ── Хвиля 2: мобільне, таблиці, підписи ──────────────────────────────────────

def test_zhodne_pole_ne_nime():
    """Поле без підпису — поле, яке для читача екрана не має назви.

    Плейсхолдер підписом НЕ рахуємо навмисно: він зникає щойно почали набирати,
    і озвучується непослідовно. Тому шлях лише один із трьох: `<label>`
    (обгорткою або через `for`), `aria-label` або `aria-labelledby`.
    """
    import re
    POLE = re.compile(r"<(select|input|textarea)\b[^>]*>", re.I)
    LABEL = re.compile(r"</?label\b[^>]*>", re.I)

    nimi = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        podiyi = sorted([(m.start(), "L", m.group(0)) for m in LABEL.finditer(t)] +
                        [(m.start(), "P", m.group(0)) for m in POLE.finditer(t)])
        hlyb = 0
        for poz, vyd, teg in podiyi:
            if vyd == "L":
                hlyb += 1 if not teg.startswith("</") else -1
                continue
            if hlyb > 0:
                continue
            if re.search(r'type=["\'](hidden|submit|button|image)', teg, re.I):
                continue
            if "aria-label" in teg or "aria-labelledby" in teg:
                continue
            ident = re.search(r'\bid="([^"]+)"', teg)
            if ident and f'for="{ident.group(1)}"' in t:
                continue
            nimi.append(f"{f.relative_to(KOREN)}:{t[:poz].count(chr(10)) + 1}")
    assert not nimi, f"полів без підпису: {len(nimi)} — " + ", ".join(nimi[:12])


def test_prava_panel_karty_ne_znykaye():
    """Нижче 1200px список обʼєктів стає шухлядою, а не `display:none`.

    До 06.08.2026 панель просто зникала — разом зі списком і сортуванням, тобто
    половиною сенсу екрана, і замість неї не зʼявлялось нічого.
    """
    css = (KOREN / "static" / "karta" / "karta.css").read_text(encoding="utf-8")
    assert ".k-panel.prava{display:none}" not in css.replace(" ", ""), \
        "права панель знову просто зникає"
    assert ".spysok-vidkryto .k-panel.prava" in css, "немає стану відкритої шухляди"

    karkas = (KOREN / "templates_v2" / "karta" / "_karkas.html").read_text(encoding="utf-8")
    assert 'id="spysokKnopka"' in karkas, "немає кнопки виклику списку"
    assert 'aria-controls="spysokPanel"' in karkas, "кнопка не звʼязана з панеллю"

    yadro = (KOREN / "static" / "karta" / "yadro.js").read_text(encoding="utf-8")
    assert "spysokYakShuhlyada" in yadro

    def zhyvyi_vyklyk(dzherelo):
        """Закоментований виклик — не виклик.

        Перша версія цього тесту шукала підрядок і зеленіла на
        `// spysokYakShuhlyada();`. Виявлено калібруванням: гейт, який не
        червоніє на зламаному стані, — не гейт.
        """
        return any("spysokYakShuhlyada()" in r
                   and not r.lstrip().startswith(("//", "*", "/*"))
                   for r in dzherelo.splitlines())

    for f in ("biogaz.js", "elevatory.js"):
        assert zhyvyi_vyklyk(
            (KOREN / "static" / "karta" / f).read_text(encoding="utf-8")), \
            f"{f}: шухляду не підключено — кнопка була б мертвою"


def test_shkala_vidstupiv():
    """Відступи лягають на крок 4px; 1–2px — оголошений крок-волосина.

    Вимір 06.08.2026 на шести екранах: 48.4 % обчислених відступів були поза
    кроком — рівень найгіршої з семи платформ, що я міряв. Після зведення
    лишилось 1.4 % (і ті — похідні від em/%, не літерали).

    Тест дивиться ДЖЕРЕЛО, а не сторінку: обчислені значення залежать від
    щільності, теми й шрифта, тож червоніли б від сторонніх причин. Джерело ж
    ловить рівно те, що людина вписує руками.
    """
    VLAST = re.compile(r"\b(?:margin|padding|gap|row-gap|column-gap)"
                       r"(?:-(?:top|right|bottom|left))?\s*:\s*([^;{}]+)")
    PX = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)px")
    chuzhi = []
    for f in (KOREN / "static").rglob("*.css"):
        if f.name == "leaflet.css":       # вендорний файл — не наш каскад
            continue
        t = f.read_text(encoding="utf-8")
        for m in VLAST.finditer(t):
            for x in PX.findall(m.group(1)):
                n = float(x)
                if n <= 2 or n % 4 == 0:
                    continue
                chuzhi.append(f"{f.name}:{t[:m.start()].count(chr(10)) + 1} = {x}px")
    assert not chuzhi, f"відступів поза шкалою: {len(chuzhi)} — " + ", ".join(chuzhi[:10])


# ── Знайдено перевіреними двигунами аудиту (produkt-dyzayn), 06.08.2026 ──────

def test_zhodnoho_inlaynovoho_obrobnyka():
    """`onclick` у шаблоні — три біди одразу.

    Дірка під CSP (проєкт увесь self-contained, це була єдина причина тримати
    `unsafe-inline`); машинний аудит не бачить у ньому дії й рахує кнопку
    мертвою; і сім варіантів одного наміру розповзаються при першій правці.
    Замість них — конвенція `data-vidkryty` / `data-fokus` в одному обробнику.
    """
    import re
    znaydeno = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r"\bon(click|change|input|submit|load)\s*=", t):
            znaydeno.append(f"{f.relative_to(KOREN)}:{t[:m.start()].count(chr(10)) + 1}")
    assert not znaydeno, "інлайнові обробники: " + ", ".join(znaydeno)


def test_porozhniy_stan_zavzhdy_maye_dilyu():
    """Правило «жодних глухих кутів»: порожній стан пропонує наступний крок.

    Три екрани (`/ai`, `/bazy`, `/chat`) лише пояснювали, чому порожньо, і на
    цьому все — стіна з написом «тут нічого немає». Знайшов `screen_audit.js`,
    коли його нарешті прогнали як належить, а не саморобним чеком.
    """
    import re
    bez_diyi = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r'data-state="(empty|first-run)"', t):
            # блок від позначки до кінця найближчого закриття — груба, але
            # достатня межа: дія мусить бути в тому ж вузлі, а не «десь вище»
            hvist = t[m.start():m.start() + 1400]
            if not re.search(r"<(a|button)\b", hvist):
                bez_diyi.append(f"{f.relative_to(KOREN)}:{t[:m.start()].count(chr(10)) + 1}")
    assert not bez_diyi, "порожні стани без наступної дії: " + ", ".join(bez_diyi)


def test_porozhni_stany_ne_dilyat_odyn_obraz():
    """Один знак на пів системи — екран без власного образу.

    До 07.08.2026 `p-arkush` стояв у 9 порожніх станах із 18: «Витягів ще не
    було», «Імпортів ще не було», «Нічого не горить», «Документів ще немає»,
    «Активностей ще немає» — усі показували ту саму теку. Найгірший був
    дашборд: «Нічого не горить» — це ДОБРА новина, а порожня тека брехала
    настроєм.

    Поріг свідомо мʼякий: спільний знак на двох-трьох споріднених екранах — це
    система, а не недогляд. Ловимо саме вироджений випадок.
    """
    import re
    from collections import Counter

    vzhytky = Counter()
    for f in (KOREN / "templates_v2").rglob("*.html"):
        if f.name == "_porozhno.html":          # сам спрайт — не вжиток
            continue
        for m in re.finditer(r'use href="#(p-[a-z-]+)"', f.read_text(encoding="utf-8")):
            vzhytky[m.group(1)] += 1

    assert vzhytky, "порожні стани взагалі не використовують знаків"
    usiogo = sum(vzhytky.values())
    naychastishyi, skilky = vzhytky.most_common(1)[0]
    assert skilky <= max(3, usiogo // 3), (
        f"«{naychastishyi}» стоїть у {skilky} станах із {usiogo} — "
        f"половина екранів на одному образі")


# ── Знайдено на 13 знімках робочої системи, 07.08.2026 ───────────────────────

def test_kozhen_porozhniy_stan_maye_obraz():
    """Порожній стан без образу читається як недороблений екран.

    «Затвердження» був єдиним таким на всю систему — на тлі решти, де образ є,
    він виглядав як забутий.
    """
    import re
    bez_obrazu = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r'<div class="empty" data-state="(empty|first-run)"', t):
            hvist = t[m.start():m.start() + 1200]
            if 'class="znak"' not in hvist:
                bez_obrazu.append(f"{f.relative_to(KOREN)}:{t[:m.start()].count(chr(10)) + 1}")
    assert not bez_obrazu, "порожні стани без образу: " + ", ".join(bez_obrazu)


# ── Картка угоди, знімки 07.08.2026 ──────────────────────────────────────────

def test_procherk_ne_kleyitsya_zi_znachennyam():
    """«—» означає «немає даних». Клеїти до нього справжнє значення не можна.

    На картці угоди стояло «—, Львівська», тобто «НЕМАЄ ДАНИХ, Львівська».
    Той самий рядок був на «Обʼєктах» і в «Довіднику» — три копії одного
    виразу, які ще й розійшлися б при першій правці.
    """
    from app.templating import misce

    class Zapys:
        def __init__(self, **kw):
            self.city = self.hromada = self.oblast = ""
            self.__dict__.update(kw)

    assert misce(Zapys(oblast="Львівська")) == "Львівська", "прочерк приклеївся до області"
    assert misce(Zapys(city="Львів", oblast="Львівська")) == "Львів, Львівська"
    assert misce(Zapys(hromada="Хоростківська", oblast="Тернопільська")) \
        == "Хоростківська, Тернопільська"
    assert misce(Zapys(city="Львів")) == "Львів"
    assert misce(Zapys()) == "—", "коли даних немає взагалі — саме прочерк"

    # У шаблонах не лишилось склейки прочерка з ПРОДОВЖЕННЯМ ТОГО САМОГО поля.
    # Кома — саме той випадок: «—, Львівська» читається як одне місце, де
    # частина відома. Розділювачі «·» і дужки склеюють РІЗНІ факти («кому
    # невідомо · організація відома») — це читається, тому не ловимо.
    import re
    ruchni = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r"or\s+'—'\s*\}\}.{0,40}?\{%\s*if.{0,60}?%\}\s*,", t):
            ruchni.append(f"{f.relative_to(KOREN)}:{t[:m.start()].count(chr(10)) + 1}")
    assert not ruchni, "прочерк знову склеюють комою: " + ", ".join(ruchni)


def test_znaky_berutsya_z_naboru_a_ne_z_emodzi():
    """Емодзі малює шрифт системи — своїм кольором, своєю вагою, по-різному на
    кожній ОС. Це виводить його з-під правила «колір лише семантичний» і з-під
    теми: 🔒 лишався чорно-жовтим і на світлому, і на темному.
    """
    import re
    # Ловимо саме КОЛЬОРОВІ емодзі: блоки емодзі та будь-який символ із
    # селектором VS16 (U+FE0F), який просить малювати його емодзі-шрифтом.
    # `✓ ✗ ✕` без VS16 малює ТЕКСТОВИЙ шрифт — вони слухають `currentColor`,
    # тему й вагу, тобто лишаються всередині системи. Це знаки, не емодзі,
    # і забороняти їх означало б заборонити типографіку.
    EMODZI = re.compile("[\U0001F300-\U0001FAFF]|.️")
    znaydeno = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        # коментарі не рахуємо: там емодзі можуть згадуватись як пояснення
        rozmitka = re.sub(r"\{#.*?#\}", "", t, flags=re.S)
        for m in EMODZI.finditer(rozmitka):
            znaydeno.append(f"{f.relative_to(KOREN)}: {m.group(0)!r}")
    assert not znaydeno, "емодзі в розмітці замість іконки набору: " + ", ".join(znaydeno)


def test_blok_spivpratsi_ne_zabyraye_akcent():
    """Блок співпраці повторюється на КОЖНІЙ картці системи.

    Три акцентні кнопки в ньому множаться на кожен екран: картка угоди набрала
    шість при межі ≤3, і жодна дія вже не читалась як головна. Усередині блоку
    кожна дія головна лише для свого блоку — тож вони тихі, а акцент лишається
    головній дії самого екрана.

    Виняток рівно один — «Затвердити». Це рішення, а не форма: воно зʼявляється
    лише тому, хто затверджує, і лише поки є що вирішувати; поруч стоїть тиха
    «На доопрацювання». Пара рішень, де позитивне головне, — це не надлишок
    акценту, а його правильне місце.
    """
    import re
    t = (KOREN / "templates_v2" / "_spivpratsya.html").read_text(encoding="utf-8")
    # in this skin a button is "loud" only with the accent class `holovna`
    huchni = [m.group(0) for m in re.finditer(r"<button[^>]*>", t)
              if "holovna" in m.group(0)]
    assert len(huchni) <= 1, ("більше однієї акцентної кнопки в блоці, який "
                              "повторюється на кожній картці: "
                              + ", ".join(x[:60] for x in huchni))
    if huchni:
        assert 'value="zatverdzheno"' in huchni[0], (
            "акцент дістався не рішенню затвердження: " + huchni[0][:70])


# ── Аватари команди ──────────────────────────────────────────────────────────

def test_ava_imortuyetsya_z_kontekstom():
    """`{% from ... import ava %}` БЕЗ `with context` мовчки ламає аватари.

    Імпортований макрос у Jinja контексту сторінки не бачить — `avatary`
    всередині нього порожній, і замість фото завжди малюється монограма. Нічого
    не падає, тестів це не червонить: просто фото ніколи не зʼявляється.
    Виявлено виміром 07.08.2026, коли карта аватарів була правильна, а на
    сторінці стояли монограми.
    """
    import re
    bez_kontekstu = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r'\{%\s*from\s+"_ava\.html"\s+import\s+ava(.*?)%\}', t):
            if "with context" not in m.group(1):
                bez_kontekstu.append(f"{f.relative_to(KOREN)}:{t[:m.start()].count(chr(10)) + 1}")
    assert not bez_kontekstu, ("макрос аватара імпортовано без `with context` — "
                               "фото не зʼявиться ніколи: " + ", ".join(bez_kontekstu))


def test_bez_fota_lyshayetsya_monohrama(klient):
    """Аватар не обовʼязковий: система мусить працювати з порожньою текою.

    Інакше додавання людей стало б обовʼязковим обрядом, а екран без фото —
    зламаним.
    """
    t = klient.get("/").text
    assert _bud_yakyi(t, LYUDYNA), "людину не показано взагалі"


def test_voronka_bez_napryamiv_ne_padaye(engine, klient):
    """Порожній стан, показаний як 500, — найгірший із можливих.

    `_program()` брав `progs[0]` без перевірки. Заархівувати обидва напрями на
    `/napryamy` — і `/voronky`, `/voronky/nova` та створення угоди віддавали
    помилку сервера. Людина бачить поламану систему замість «тут поки порожньо,
    ось що зробити».

    Знайдено 07.08.2026 зондом, який заводив РЕАЛЬНІ дані без напрямів. Жоден
    попередній прогін цього не бачив: тестові дані завжди створювали напрям.
    """
    r = klient.get("/voronky")
    assert r.status_code == 200, f"воронка без напрямів впала: {r.status_code}"
    assert "Немає жодного чинного напряму" in r.text
    assert "/napryamy" in r.text, "глухий кут: немає виходу до напрямів"

    # форма нової угоди теж не має падати
    assert klient.get("/voronky/nova", follow_redirects=True).status_code == 200


def test_avatar_ne_vidkladayetsya():
    """`loading="lazy"` на аватарі не ощаджує нічого, але створює клас дефектів.

    Усі аватари команди разом важать ~12 КБ. Натомість відкладене завантаження
    лишає порожнє місце всередині згорнутого <details>, за згином і в друку —
    перевірено 07.08.2026: чотири аватари в «Хто чим займається» не зʼявлялись
    навіть через 2.5 с після розкриття блоку.
    """
    t = (KOREN / "templates_v2" / "_ava.html").read_text(encoding="utf-8")
    rozmitka = re.sub(r"\{#.*?#\}", "", t, flags=re.S)
    assert 'loading="lazy"' not in rozmitka, "аватар знову відкладено"
    assert 'decoding="async"' in rozmitka, "декодування має лишатись асинхронним"


