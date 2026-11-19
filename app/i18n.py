# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""UI localisation: English (default) and Ukrainian.

Source strings are written in Ukrainian (the product's working language) and
looked up in ``app/locale/en.json`` when the interface language is English.
A missing entry falls back to the source string, and ``tests/test_ekrany.py``
fails the build if any template string has no English translation.

Numbers in a string are matched through ``{n}``: the catalogue key
``"{n} угод"`` translates ``"5 угод"`` to ``"5 deals"``. Strings built from data
use ``{*}`` wildcards: ``"Програму закрито: {*}"`` → ``"Programme closed: {*}"``.
"""
from __future__ import annotations

import json
import os
import re
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

LANGS = {"en": "English", "uk": "Українська"}
# English for the public demo; the test-suite pins Ukrainian (its assertions are in Ukrainian).
DEFAULT = os.getenv("UI_DEFAULT_LANG", "en") if os.getenv("UI_DEFAULT_LANG") in ("en", "uk") else "en"
COOKIE = "lang"

_lang: ContextVar[str] = ContextVar("lang", default=DEFAULT)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_CATALOG_PATH = Path(__file__).resolve().parent / "locale" / "en.json"


@lru_cache(maxsize=1)
def catalog() -> dict[str, str]:
    if not _CATALOG_PATH.exists():
        return {}
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if isinstance(v, str) and v}


@lru_cache(maxsize=1)
def _js_keys() -> tuple[str, ...]:
    p = _CATALOG_PATH.parent / "js_keys.json"
    return tuple(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else ()


def js_catalog() -> dict[str, str]:
    """Strings used by client-side scripts, already in the current language."""
    if _lang.get() != "en":
        return {}
    cat = catalog()
    return {k: cat[k] for k in _js_keys() if k in cat}


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[re.Pattern[str], str]]:
    """Catalogue keys with {*} / {n} placeholders, compiled once (longest first)."""
    out = []
    for k, v in catalog().items():
        if "{*}" not in k:
            continue
        rx = re.escape(k).replace(re.escape("{*}"), "(.+?)").replace(re.escape("{n}"), r"(\d+(?:[.,]\d+)?)")
        out.append((re.compile("^" + rx + "$", re.S), v))
    out.sort(key=lambda p: -len(p[0].pattern))
    return out


def _fill(template: str, values: list[str]) -> str:
    it = iter(values)
    return re.sub(r"\{\*\}|\{n\}", lambda _m: next(it, ""), template)


def current() -> str:
    return _lang.get()


def set_lang(lang: str) -> None:
    _lang.set(lang if lang in LANGS else DEFAULT)


def pick(cookie_value: str | None, accept_language: str = "") -> str:
    if cookie_value in LANGS:
        return str(cookie_value)
    return DEFAULT


def translate(text: str, lang: str | None = None) -> str:
    """Translate one UI string into the current (or given) language."""
    if not text or (lang or _lang.get()) != "en":
        return text
    cat = catalog()
    hit = cat.get(text)
    if hit is not None:
        return hit
    key = text.strip()
    if key != text and (hit := cat.get(key)) is not None:
        return text.replace(key, hit)
    nums = _NUM.findall(key)
    if nums:
        hit = cat.get(_NUM.sub("{n}", key))
        if hit is not None:
            return text.replace(key, _fill(hit, nums))
    for rx, tr in _patterns():
        m = rx.match(key)
        if m:
            return text.replace(key, _fill(tr, [translate(g) for g in m.groups()]))
    if ", " in key:                       # a list of labels: "Agro, Waste sorting"
        parts = key.split(", ")
        done = [translate(x) for x in parts]
        if done != parts:
            return text.replace(key, ", ".join(done))
    return text


_CYR = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")


def finalize(value: object) -> object:
    """Jinja `finalize` hook: every `{{ value }}` that is exactly a catalogue string
    (a label from a Python dictionary, a seeded stage name) is shown translated.
    Markup and non-Cyrillic values pass through untouched."""
    if type(value) is str and _lang.get() == "en" and _CYR.search(value):
        return translate(value)
    return value


def _(text: str) -> str:
    return translate(text)


def plural_en(n: int, one: str, many: str) -> str:
    return one if abs(int(n or 0)) == 1 else many
