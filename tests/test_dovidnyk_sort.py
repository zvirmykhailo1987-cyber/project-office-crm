# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Сортування довідника кліком по шапці.

ЧОМУ ЦЕЙ ФАЙЛ ЗʼЯВИВСЯ. Довідник — найдовший список у системі, і до 06.08.2026
його не можна було впорядкувати ніяк: порядок завжди за назвою. Питання «хто в
базі найгірше заповнений» відповіді не мало взагалі.

Два місця, де таке сортування зазвичай ламається, і кожне має тут свій тест:

  1. **Сортування після пагінації.** Впорядкувати вже відрізану сторінку —
     значить перемішати 50 випадкових рядків і назвати це «за повнотою». Ця
     помилка вже була на `/obyekty` (`test_obyekty_sort.py`).
  2. **Дві формули одного числа.** «Повнота» рахується властивістю Python, а
     сортувати треба в SQL — тобто той самий бал складено двічі. Розійдуться —
     і колонка почне суперечити числу, яке сама ж показує.
"""
import re

import pytest
from sqlmodel import Session

from app.models import Counterparty


@pytest.fixture()
def baza(engine):
    """Порядок за назвою НАВМИСНО протилежний порядку за повнотою.

    Інакше тест зеленів би й на зламаному сортуванні: вибірка й так уже стоїть
    у потрібному порядку, і перевіряти було б нічого.
    """
    with Session(engine) as s:
        # `activity_text` — поле ПОЗА білим списком сортування; значення різні
        # й у тому самому порядку, що назви, щоб сортування за ним спадно давало
        # порядок, відмінний від типового. Однакові значення тут зробили б
        # нічию, і тест зеленів би на зламаному білому списку.
        s.add(Counterparty(id=1, name="Аграрна компанія",     # порожня картка
                           edrpou="", phone="", email="", address="",
                           city="", oblast="", kved="", activity_text="а"))
        s.add(Counterparty(id=2, name="Бета сервіс",           # половина
                           edrpou="12345678", phone="+380001112233",
                           email="", address="", city="Тернопіль",
                           oblast="Тернопільська", kved="", activity_text="б"))
        s.add(Counterparty(id=3, name="Вега пром",             # повна
                           edrpou="87654321", phone="+380001112244",
                           email="v@example.com", address="вул. Руська, 1",
                           city="Тернопіль", oblast="Тернопільська",
                           kved="01.11", lat=49.55, lon=25.59, activity_text="в"))
        # NULL, а не порожній рядок. Вираз у SQL порівнює `!= ""`, і на NULL
        # умова дає NULL (не «істина») → CASE іде в ELSE → 0. Властивість
        # Python на `bool(None)` теж дає 0. Тобто збігаються — але це саме те
        # місце, де «немає даних» найлегше перетворюється на «нуль» непомітно
        # (Г15), тому воно має бути прибите тестом, а не міркуванням.
        s.add(Counterparty(id=4, name="Гамма НУЛЬ", edrpou=None, phone=None,
                           email=None, address=None, city=None, oblast=None,
                           kved=None, activity_text="г"))
        s.commit()
    return engine


def _nazvy(klient, url):
    r = klient.get(url)
    assert r.status_code == 200, f"{url} → {r.status_code}"
    return re.findall(r'href="/dovidnyk/\d+">([^<]+)</a>', r.text)


def test_povnota_zbihayetsya_z_vlastyvistyu(baza, klient):
    """Бал у SQL і бал у Python мають давати ОДНЕ число — включно з NULL.

    Це головний тест файлу: розбіжність тут не падає й не ламає екран — вона
    тихо показує один порядок і інші числа, і помітити це можна лише очима.
    """
    from sqlalchemy import select as sa_select
    from app.web.dovidnyk import _POVNOTA

    with Session(baza) as s:
        z_sql = dict(s.exec(sa_select(Counterparty.id, _POVNOTA)).all())
        for cp in s.exec(sa_select(Counterparty)).scalars():
            ochik = round(100 * z_sql[cp.id] / 8)
            assert cp.completeness == ochik, (
                f"{cp.name}: властивість каже {cp.completeness}, "
                f"а SQL — {ochik}: формули розійшлися")


def test_sortuvannya_za_povnotoyu(baza, klient):
    spadno = _nazvy(klient, "/dovidnyk?sort=povnota&dir=desc")
    assert spadno[0] == "Вега пром", f"зверху мала бути найповніша картка, а там {spadno}"
    assert spadno[1] == "Бета сервіс"
    # Знизу — дві порожні картки: одна з порожніми рядками, друга з NULL.
    # Вони мусять стояти поруч: «немає даних» і «порожньо» — однакова повнота.
    assert set(spadno[-2:]) == {"Аграрна компанія", "Гамма НУЛЬ"}

    zrostayucho = _nazvy(klient, "/dovidnyk?sort=povnota&dir=asc")
    assert zrostayucho[-1] == "Вега пром"
    assert set(zrostayucho[:2]) == {"Аграрна компанія", "Гамма НУЛЬ"}


def test_sortuvannya_za_nazvoyu_v_obydva_boky(baza, klient):
    assert _nazvy(klient, "/dovidnyk?sort=name&dir=asc")[0] == "Аграрна компанія"
    assert _nazvy(klient, "/dovidnyk?sort=name&dir=desc")[0] == "Гамма НУЛЬ"


def test_nevidome_pole_ne_lamaye_ekran(baza, klient):
    """`?sort=` — це адресний рядок, тобто вхід від будь-кого.

    Білий список замість `getattr` саме тому: інакше довільна назва летить у
    модель, і адреса стає керуванням запитом.
    """
    for zle in ("password", "id); DROP TABLE counterparty;--", ""):
        r = klient.get("/dovidnyk", params={"sort": zle})
        assert r.status_code == 200
    # і тихо повертаємось до типового порядку, а не до порожнечі
    assert _nazvy(klient, "/dovidnyk?sort=nemaye")[0] == "Аграрна компанія"

    # Головне тут — не «не впало», а «не спрацювало». Перша версія цього тесту
    # перевіряла лише код 200 і зеленіла на `getattr`-фолбеку: справжня НАЗВА
    # колонки все одно ставала ключем сортування, тобто адресний рядок керував
    # запитом. Тому беремо реальне поле моделі, якого НЕМАЄ у білому списку.
    assert hasattr(Counterparty, "activity_text"), "поле перейменували — оновіть тест"
    poza_spyskom = _nazvy(klient, "/dovidnyk?sort=activity_text&dir=desc")
    typovyi = _nazvy(klient, "/dovidnyk")
    assert poza_spyskom == typovyi, (
        "сортування за полем поза білим списком спрацювало: "
        f"{poza_spyskom} замість {typovyi}")


def test_shapka_povidomlyaye_stan_sortuvannya(baza, klient):
    """`aria-sort` — не оздоба: без нього читач екрана не скаже ні що колонка
    сортована, ні в який бік."""
    t = klient.get("/dovidnyk?sort=povnota&dir=desc").text
    assert 'aria-sort="descending"' in t, "напрям сортування не оголошено"
    assert t.count('aria-sort="none"') >= 3, "решта колонок мають бути позначені як несортовані"
    t2 = klient.get("/dovidnyk?sort=name&dir=asc").text
    assert 'aria-sort="ascending"' in t2


def test_posylannya_shapky_ne_dublyuye_sort(baza, klient):
    """У посиланні шапки має бути РІВНО один `sort`.

    Якщо базовий рядок запиту вже несе сортування, а шапка додає своє, в адресі
    опиняється два — і виграє випадковий, залежно від розбору.
    """
    t = klient.get("/dovidnyk?sort=povnota&dir=desc&oblast=Тернопільська").text
    posylannya = re.findall(r'href="(/dovidnyk\?[^"]*sort=[^"]*)"', t)
    assert posylannya, "у шапці немає посилань сортування"
    for u in posylannya:
        assert u.count("sort=") == 1, f"два сортування в одному посиланні: {u}"
        assert "oblast=" in u, f"посилання шапки загубило фільтр: {u}"


# ── Історія контактів на картці контрагента ──────────────────────────────────

def test_istoriya_zbyraye_i_pryami_i_cherez_ugody(baza, klient):
    """До 06.08.2026 тут стояло «Зʼявиться у фазі Ф2».

    Бракувало не даних, а виводу: `Activity` існує й пишеться з картки угоди від
    самого початку. Ключове — ДВІ гілки: менеджер записує дзвінок у картці
    угоди, і без другої гілки історія контрагента виглядала б порожньою саме
    там, де роботи найбільше.
    """
    from app.models import Activity, Deal, Pipeline, Program, Stage

    with Session(baza) as s:
        prog = Program(code="test", title="Тестовий напрям")
        s.add(prog); s.commit(); s.refresh(prog)
        pipe = Pipeline(program_id=prog.id, code="test", title="Воронка")
        s.add(pipe); s.commit(); s.refresh(pipe)
        st = Stage(pipeline_id=pipe.id, code="persha", title="Перша", position=1)
        s.add(st); s.commit(); s.refresh(st)
        d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=st.id,
                 counterparty_id=3, title="Угода з Вегою")
        s.add(d); s.commit(); s.refresh(d)

        s.add(Activity(type="Дзвінок", counterparty_id=3, author="Марко",
                       outcome="Записано просто в картці контрагента"))
        s.add(Activity(type="Зустріч", deal_id=d.id, counterparty_id=None,
                       author="Марко", outcome="Записано в картці угоди"))
        s.commit()

    t = klient.get("/dovidnyk/3").text
    assert "Записано просто в картці контрагента" in t, "пряма активність не показана"
    assert "Записано в картці угоди" in t, \
        "активність з угоди не потрапила в історію контрагента"
    assert "фазі Ф2" not in t, "обіцянка лишилась на місці"


def test_zapys_bez_rezultatu_ne_prohodyt(baza, klient):
    """Порожній результат — не запис: через місяць «Дзвінок · 14:20» не підкаже,
    домовились чи ні. Помилка має вести до ПОЛЯ, а не лише до банера."""
    r = klient.post("/dovidnyk/3/activity",
                    data={"type": "Дзвінок", "outcome": "  ", "note": "щось"},
                    follow_redirects=True)
    assert 'id="e-outcome"' in r.text, "немає підпису біля поля результату"
    assert 'aria-describedby="e-outcome"' in r.text
