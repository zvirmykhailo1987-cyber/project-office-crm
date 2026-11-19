"""AI-витяг: шість правил ТЗ §6 — перевіряємо кожне окремо."""

import pytest
from jsonschema import validate

from app.services import extract, llm

INVOICE_TEXT = """
ТОВ «НАСОС-СЕРВІС»
ЄДРПОУ: 99758892
Рахунок-фактура № СФ-00123 від 12.03.2026

Насос відцентровий NM-40 | 3 | 1200.00 | 3600.00
Фільтр грубої очистки     | 10 | 85.50 | 855.00

Сума без ПДВ: 4455.00 грн
ПДВ 20%: 891.00 грн
Всього до сплати: 5346.00 грн
Умови постачання: FCA
"""

BROKEN_TEXT = INVOICE_TEXT.replace("| 3 | 1200.00 | 3600.00", "| 3 | 1200.00 | 3900.00")

ANKETA_TEXT = """
Анкета скринінгу — Борсуківська громада
C1 Обсяг зерна від фермерів: 150000 т/рік
C2 Недообслуговувані землі: 14000 га
C4 Залізниця: так
C5 Дороги: ні
"""

CONTACTS_TEXT = """
ПАП «Зоряна Долина» | 99758892 | +380001771405 | dolyna@example.com
ФГ «Нива» | 12345678 | 0001112233 |
Просто рядок без даних
"""


# ── правило 2: LLM не рахує гроші ────────────────────────────────────────────

def test_arithmetic_catches_wrong_line_sum():
    r = extract.check_arithmetic({"pozytsii": [
        {"nazva": "Насос", "kilkist": 3, "tsina": 1200, "suma": 3900}]})
    assert not r.ok and "3600" in r.note


def test_arithmetic_catches_wrong_total():
    r = extract.check_arithmetic({"pozytsii": [], "suma_bez_pdv": 100,
                                  "pdv": 20, "vsogo": 130})
    assert not r.ok and "130" in r.note


def test_arithmetic_catches_sum_of_lines_mismatch():
    r = extract.check_arithmetic({
        "pozytsii": [{"kilkist": 2, "tsina": 50, "suma": 100}],
        "suma_bez_pdv": 500})
    assert not r.ok


def test_arithmetic_fills_missing_line_sum():
    inv = {"pozytsii": [{"kilkist": 4, "tsina": 25, "suma": None}]}
    r = extract.check_arithmetic(inv)
    assert inv["pozytsii"][0]["suma"] == 100 and r.ok


def test_arithmetic_ok_on_correct_invoice():
    r = extract.run("rahunok", INVOICE_TEXT)
    assert r.arithmetic.ok, r.arithmetic.note


def test_arithmetic_flags_tampered_invoice():
    r = extract.run("rahunok", BROKEN_TEXT)
    assert not r.arithmetic.ok


# ── правило 3: strict-output ─────────────────────────────────────────────────

@pytest.mark.parametrize("kind,text", [("rahunok", INVOICE_TEXT),
                                       ("anketa", ANKETA_TEXT),
                                       ("kontakty", CONTACTS_TEXT)])
def test_output_matches_schema(kind, text):
    r = extract.run(kind, text)
    validate(instance=r.data, schema=extract.SCHEMAS[kind])
    assert r.schema_valid


def test_invalid_llm_answer_is_rejected(monkeypatch):
    """Невалідна відповідь моделі НЕ записується — падаємо на детермінований шлях."""
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete",
                        lambda *a, **k: {"вигадане_поле": 1})   # не за схемою
    r = extract.run("rahunok", INVOICE_TEXT)
    assert r.engine == "deterministic" and r.fallback_used
    assert "схему" in r.error


# ── правило 4: fallback обовʼязковий ─────────────────────────────────────────

def test_works_without_llm():
    assert not llm.available()               # у тестах ключа немає
    r = extract.run("rahunok", INVOICE_TEXT)
    assert r.engine == "deterministic" and r.data["edrpou"] == "99758892"


def test_llm_failure_does_not_break_run(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    def boom(*a, **k):
        raise llm.LLMUnavailable("немає звʼязку")
    monkeypatch.setattr(llm, "complete", boom)
    r = extract.run("rahunok", INVOICE_TEXT)
    assert r.engine == "deterministic" and r.data["vsogo"] == 5346.0


def test_llm_result_used_when_valid(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: {
        "postachalnyk": "ТОВ Тест", "pozytsii": [], "vsogo": 100.0})
    r = extract.run("rahunok", INVOICE_TEXT)
    assert r.engine == "llm" and r.data["postachalnyk"] == "ТОВ Тест"


# ── правило 6: нічого не вигадуємо ───────────────────────────────────────────

def test_missing_anketa_answers_stay_none():
    r = extract.run("anketa", ANKETA_TEXT)
    assert r.data["c1_grain_t_yr"] == 150000
    for key in ("c3_ind_park", "c6_power_mw", "c7_guarantee_ready", "c9_transparency"):
        assert r.data[key] is None, f"{key} вигадано"


def test_zero_and_none_are_different():
    """«ні» → 0 (перевірили, немає); питання не було → None (не питали)."""
    r = extract.run("anketa", ANKETA_TEXT)
    assert r.data["c5_road"] == 0.0          # у тексті «ні»
    assert r.data["c3_ind_park"] is None     # питання немає взагалі


def test_empty_text_yields_empty_result():
    r = extract.run("rahunok", "")
    assert r.data["vsogo"] is None and not r.data["pozytsii"]


def test_contacts_skip_lines_without_data():
    r = extract.run("kontakty", CONTACTS_TEXT)
    assert len(r.data["organizatsii"]) == 2
    assert r.data["organizatsii"][0]["edrpou"] == "99758892"


# ── витяг тексту з файлів ────────────────────────────────────────────────────

def test_txt_is_read(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text(INVOICE_TEXT, encoding="utf-8")
    assert not extract.read_file(p).empty


def test_image_reports_no_ocr(tmp_path):
    p = tmp_path / "scan.jpg"
    p.write_bytes(b"\xff\xd8\xff")
    src = extract.read_file(p)
    assert src.empty and "OCR" in src.note


def test_unknown_format_is_reported(tmp_path):
    p = tmp_path / "a.zip"
    p.write_bytes(b"PK")
    assert "не підтримується" in extract.read_file(p).note


def test_xlsx_is_read(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.append(["ПАП Зоряна Долина", "99758892", "+380001771405"])
    p = tmp_path / "b.xlsx"
    wb.save(str(p))
    src = extract.read_file(p)
    assert "99758892" in src.text


# ── метрика заповнення (критерій приймання Ф6) ───────────────────────────────

def test_fill_rate_counted():
    r = extract.run("rahunok", INVOICE_TEXT)
    assert r.fields_total > 0 and r.fields_filled > 0
    assert r.fields_filled <= r.fields_total
