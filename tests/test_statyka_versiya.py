"""Штамп версії статики (`?v=` на CSS).

Навіщо тест: правки CSS не доходили до людей — браузер тримав стару таблицю навіть
після оновлення сторінки, і зміну бачив лише той, хто робив hard-reload. Штамп це
лікує, але тільки поки він справді змінюється разом із файлом. Мовчазна константа
(наприклад, «0» через помилку в шляху) виглядає як робочий механізм і при цьому
заморожує кеш назавжди — саме це й ловимо.

Два випадки, які реально трапились під час переробки дизайну 06.08.2026 і через
які тут стало на два тести більше:
  1. система розʼїхалась на два файли (`dosye.css` + `app.css`), а штамп рахувався
     лише з одного — правка токенів не скидала кеш;
  2. штамп обчислювався ОДИН раз на старті, а `uvicorn --reload` стежить лише за
     `.py` — тож у розробці правка CSS не доходила взагалі, поки не чіпнеш пайтон.
"""
import pathlib
import time

from app.web.deps import STATYKA_V, _statyka_teper, _versiya_statyky

KOREN = pathlib.Path(__file__).resolve().parents[1]
STYLI = ("yadro.css", "komponenty.css", "komponenty2.css",
         "vzayemodiyi.js", "karta/karta.css")
NAPRYAMY_DIR = "napryamy"


def test_shtamp_ne_konstanta_zaglushka():
    assert STATYKA_V not in ("", "0"), "фолбек спрацював — шлях до CSS не знайдено"
    assert STATYKA_V.isdigit()


def test_shtamp_bere_naysvizhishyi_z_usih_fayliv():
    """Не лише один файл: правка будь-якого css/js має скидати кеш.

    Перелік файлів тут НЕ ведеться руками — саме так тест і застарів
    08.08.2026, коли в `static/` зʼявилась вендорована бібліотека `bib/`:
    штамп її бачив, а тест про неї не знав, і 341 зелений тест зупинився
    на розбіжності, якої в продукті не було. Тому дзеркалимо контракт
    `_versiya_statyky()` — усе дерево, крім `vendor/`.
    """
    chasy = [int(f.stat().st_mtime)
             for f in (KOREN / "static").rglob("*")
             if f.is_file() and f.suffix in (".css", ".js") and "vendor" not in f.parts]
    assert chasy, "у static/ немає жодного css/js — штамп нема з чого рахувати"
    assert _versiya_statyky() == str(max(chasy))


def test_shtamp_bachyt_biblioteku():
    """Правка вендорованої бібліотеки мусить скидати кеш нарівні з рештою.

    Інакше оновлення `bib/` приїжджало б у прод при старому CSS у браузері —
    той самий клас аварії, через який штамп і зʼявився.
    """
    bib = KOREN / "static" / "bib"
    if not bib.exists():
        return
    fayly = [f for f in bib.rglob("*.css")]
    assert fayly, "у static/bib немає css — бібліотека не синхронізована"
    naysvizhishyi = max(int(f.stat().st_mtime) for f in fayly)
    assert int(_versiya_statyky()) >= naysvizhishyi, \
        "штамп статики не враховує static/bib — правка бібліотеки не скине кеш"


def test_u_rozrobtsi_shtamp_rahuyetsya_shchorazu(monkeypatch):
    """У розробці штамп має бути живим, бо reload не стежить за CSS."""
    from app import config
    monkeypatch.setattr(config, "ENV", "dev")
    assert _statyka_teper() == _versiya_statyky()


def test_u_prodi_shtamp_kesuyetsya(monkeypatch):
    """У проді — константа зі старту: файли між релізами не міняються."""
    from app import config
    monkeypatch.setattr(config, "ENV", "production")
    assert _statyka_teper() == STATYKA_V


def test_render_kladeh_shtamp_u_kontekst():
    """Штамп має приходити з `render()`, а не лише існувати як константа."""
    import inspect

    from app.web import deps
    dzherelo = inspect.getsource(deps.render)
    assert 'ctx.setdefault("statyka_v"' in dzherelo
    assert "_statyka_teper()" in dzherelo, \
        "render має брати ЖИВИЙ штамп, інакше в розробці CSS не оновлюється"


# ── Знайдено передрелізним гейтом 06.08.2026 ─────────────────────────────────

def test_shtamp_bachyt_kozhen_svii_fail():
    """Штамп мусить реагувати на БУДЬ-ЯКИЙ наш css/js, а не на список руками.

    Перелік файлів вівся вручну й устиг застаріти: у ньому не було жодного
    `karta/*.js`, хоча саме вони змінились. У проді це дало б стилі шухляди без
    коду, який її створює, — стан «майже працює», причину якого шукають годинами.

    Тест торкається кожного файла по черзі: перелік, який відстає, червоніє тут,
    а не в браузері користувача.
    """
    import os
    from app.web.deps import _statyka_teper

    statyka = KOREN / "static"
    nashi = [f for f in statyka.rglob("*")
             if f.is_file() and f.suffix in (".css", ".js") and "vendor" not in f.parts]
    assert len(nashi) >= 8, f"підозріло мало файлів статики: {len(nashi)}"

    slipi = []
    for f in nashi:
        do = _statyka_teper()
        st = f.stat()
        try:
            os.utime(f, (st.st_atime, time.time() + 9))
            if _statyka_teper() == do:
                slipi.append(str(f.relative_to(KOREN)))
        finally:
            os.utime(f, (st.st_atime, st.st_mtime))
    assert not slipi, "штамп не бачить змін у: " + ", ".join(slipi)


def test_shablony_ne_trymayut_versiyu_ruchno():
    """`?v=9` у шаблоні — це кеш, який ніколи не скинеться.

    Саме так три файли карти простояли з першого коміту: `karta.css?v=9`,
    `biogaz.js?v=9`, `elevatory.js?v=9`. Число не міняли жодного разу, бо
    міняти його вручну ніхто не памʼятає — на те й був спільний штамп.
    """
    import re
    ruchni = []
    for f in (KOREN / "templates_v2").rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r'\?v=(\d+)\b', t):
            ruchni.append(f"{f.relative_to(KOREN)}:{t[:m.start()].count(chr(10)) + 1} = ?v={m.group(1)}")
    assert not ruchni, "версія статики зашита в шаблоні: " + ", ".join(ruchni)


