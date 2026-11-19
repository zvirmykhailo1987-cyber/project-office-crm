"""Кожна покликана іконка існує у спрайті.

ЧОМУ ЦЕ ТЕСТ, А НЕ ОКО. Посилання на неіснуючий символ (`#i-nemaye`) НЕ дає
ані помилки в консолі, ані порожнього місця з рамкою — воно дає просто нічого.
Кнопка лишається клікабельною, розмітка валідною, жоден із чотирьох браузерних
гейтів такого не бачить: для них це елемент нульового розміру.

Наслідок у продукті: іконка мовчки зникає після перейменування символу в
бібліотеці, і помічають це через тиждень на скріншоті від Марка (Г2).
"""
from __future__ import annotations

import re
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
SHABLONY = [KOREN / "templates", KOREN / "templates_v2"]
SPRAYTY = {
    "ikony.svg": KOREN / "static" / "bib" / "ikony.svg",
    "porozhni.svg": KOREN / "static" / "bib" / "porozhni.svg",
}
# Локальні спрайти старої шкіри (`_ikonky.html`, `_porozhno.html`) — inline,
# тому їхні символи збираємо з самих шаблонів.
LOKALNI = [KOREN / "templates" / "_ikonky.html",
           KOREN / "templates" / "_porozhno.html"]


def _symvoly(shlyah: Path) -> set[str]:
    if not shlyah.exists():
        return set()
    return set(re.findall(r'<symbol[^>]+id="([^"]+)"',
                          shlyah.read_text(encoding="utf-8")))


def test_usi_poklykani_ikonky_isnuyut():
    nayavni: set[str] = set()
    for p in SPRAYTY.values():
        nayavni |= _symvoly(p)
    for p in LOKALNI:
        nayavni |= _symvoly(p)
    assert nayavni, "спрайти порожні — перевір `scripts/synk_bib.py`"

    vidsutni: list[str] = []
    for koren in SHABLONY:
        if not koren.is_dir():
            continue
        for f in koren.rglob("*.html"):
            txt = f.read_text(encoding="utf-8")
            for m in re.finditer(r'<use[^>]+href="([^"]*)#([^"]+)"', txt):
                imya = m.group(2)
                # Пропускаємо посилання, зібрані в шаблоні з виразу
                # (`#{{ ikona }}`): їхнє імʼя відоме лише під час рендера, і
                # тут ми чесно кажемо «не перевірено», а не вдаємо перевірку.
                if "{" in imya or "}" in imya:
                    continue
                if imya not in nayavni:
                    vidsutni.append(f"{f.relative_to(KOREN)}: #{imya}")

    assert not vidsutni, (
        "покликано символи, яких немає у спрайтах "
        "(іконка мовчки не намалюється):\n  " + "\n  ".join(sorted(set(vidsutni))))
