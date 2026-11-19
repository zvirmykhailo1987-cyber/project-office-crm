# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Сортування списку обʼєктів — за балом, а не «в межах сторінки».

ЧОМУ ЦЕЙ ФАЙЛ ЗʼЯВИВСЯ. `sort=score` — типове сортування екрана `/obyekty`, і воно
брехало: роут спершу відрізав сторінку (`.offset().limit()`), а потім упорядковував
її в Python. Тобто «за балом» перемішувало 50 випадкових рядків.

Виміряно на живій базі 06.08.2026: максимум на 1-й сторінці 78.9 при справжньому
83.6 — три найкращі обʼєкти не потрапляли на неї взагалі. Для екрана, чия єдина
задача — «покажи найперспективніші», це не косметика.

Тести навмисно перевіряють ВИДИМИЙ результат сторінки, а не форму SQL-запиту:
запит можна переписати, обіцянка лишається та сама.
"""
import pytest
from sqlmodel import Session

from app.models import Score, Site


@pytest.fixture()
def obyekty(engine):
    """Більше обʼєктів, ніж уміщає сторінка, і найкращий — В КІНЦІ за id.

    Порядок навмисний: якщо сортування знову стане посторінковим, найкращий
    опиниться на другій сторінці, і тест почервоніє.
    """
    from app import config
    n = config.PAGE_SIZE + 10
    with Session(engine) as s:
        for i in range(1, n + 1):
            s.add(Site(id=i, name=f"Обʼєкт {i}", site_type="biogaz_object",
                       oblast="Тернопільська"))
            # бал росте з id: найвищий у найостаннішого
            s.add(Score(site_id=i, model="fat_score", value=float(i)))
        # обʼєкт БЕЗ балу — має лишитись у списку, а не зникнути (Г15)
        s.add(Site(id=n + 1, name="Без балу", site_type="biogaz_object",
                   oblast="Тернопільська"))
        s.commit()
    return n


def _baly_storinky(klient, url):
    """Бали в тому порядку, як їх видно на сторінці."""
    import re
    r = klient.get(url)
    assert r.status_code == 200
    # назви виду «Обʼєкт N» ідуть у порядку рядків таблиці
    return [int(x) for x in re.findall(r"Обʼєкт (\d+)", r.text)]


def test_persha_storinka_mistyt_spravzhniy_maksymum(obyekty, klient):
    """Головна обіцянка екрана: зверху — найкращий у ВСІЙ вибірці."""
    poryadok = _baly_storinky(klient, "/obyekty?sort=score")
    assert poryadok, "сторінка порожня"
    assert poryadok[0] == obyekty, (
        f"зверху обʼєкт {poryadok[0]}, а найкращий — {obyekty}: "
        f"сортування знову працює лише в межах сторінки")


def test_poryadok_spadnyi(obyekty, klient):
    poryadok = _baly_storinky(klient, "/obyekty?sort=score")
    assert poryadok == sorted(poryadok, reverse=True), "порядок не спадний"


def test_obyekt_bez_balu_ne_znykaye(obyekty, klient):
    """«Немає балу» — не «нуль» і не «не існує»: запис лишається у вибірці.

    LEFT JOIN замість INNER саме для цього. З INNER-звʼязкою обʼєкти без балу
    тихо випали б зі списку, і ніхто б не помітив.
    """
    from app import config
    storinok = (obyekty + 1 + config.PAGE_SIZE - 1) // config.PAGE_SIZE
    znaydeno = False
    for p in range(1, storinok + 1):
        if "Без балу" in klient.get(f"/obyekty?sort=score&page={p}").text:
            znaydeno = True
            break
    assert znaydeno, "обʼєкт без балу зник зі списку при сортуванні за балом"


def test_sortuvannya_za_nazvoyu_ne_zlamane(obyekty, klient):
    nazvy = _baly_storinky(klient, "/obyekty?sort=name")
    assert nazvy, "сторінка порожня"
    # «Обʼєкт 1», «Обʼєкт 10», «Обʼєкт 11»… — рядкове впорядкування
    assert nazvy[0] == 1
