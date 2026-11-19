# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Витяг структурованих даних із файлів (ТЗ §6, модуль M11).

Шість твердих правил ТЗ, реалізованих тут дослівно:
1. Результат — ЧЕРНЕТКА з міткою «AI-чернетка — перевірте» (ставить веб-шар).
2. LLM не рахує гроші: суми перераховує `check_arithmetic()`, розбіжність показується.
3. Strict-output: відповідь валідується `jsonschema` перед записом.
4. Fallback: без LLM працює детермінований екстрактор — система не зупиняється.
5. Кожен прогін → `AiRun`.
6. Нічого не вигадуємо: чого немає в тексті, лишається `null`.
"""
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import ValidationError, validate

from . import llm

# ── схеми (strict-output) ────────────────────────────────────────────────────

INVOICE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["postachalnyk", "pozytsii"],
    "properties": {
        "postachalnyk": {"type": ["string", "null"]},
        "edrpou": {"type": ["string", "null"]},
        "nomer": {"type": ["string", "null"]},
        "data": {"type": ["string", "null"]},
        "valyuta": {"type": ["string", "null"]},
        "pozytsii": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "nazva": {"type": ["string", "null"]},
                    "kilkist": {"type": ["number", "null"]},
                    "tsina": {"type": ["number", "null"]},
                    "suma": {"type": ["number", "null"]},
                },
            },
        },
        "suma_bez_pdv": {"type": ["number", "null"]},
        "pdv": {"type": ["number", "null"]},
        "vsogo": {"type": ["number", "null"]},
        "incoterms": {"type": ["string", "null"]},
    },
}

ANKETA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hromada": {"type": ["string", "null"]},
        "c1_grain_t_yr": {"type": ["number", "null"]},
        "c2_underserved_ha": {"type": ["number", "null"]},
        "c3_ind_park": {"type": ["number", "null"]},
        "c4_rail": {"type": ["number", "null"]},
        "c5_road": {"type": ["number", "null"]},
        "c6_power_mw": {"type": ["number", "null"]},
        "c7_guarantee_ready": {"type": ["number", "null"]},
        "c8_ahc_ready": {"type": ["number", "null"]},
        "c9_transparency": {"type": ["number", "null"]},
    },
}

CONTACTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["organizatsii"],
    "properties": {
        "organizatsii": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "nazva": {"type": ["string", "null"]},
                    "edrpou": {"type": ["string", "null"]},
                    "misto": {"type": ["string", "null"]},
                    "telefon": {"type": ["string", "null"]},
                    "email": {"type": ["string", "null"]},
                    "kerivnyk": {"type": ["string", "null"]},
                },
            },
        },
    },
}

SCHEMAS = {"rahunok": INVOICE_SCHEMA, "anketa": ANKETA_SCHEMA,
           "kontakty": CONTACTS_SCHEMA}

PROMPTS = {
    "rahunok": (
        "Ти витягуєш дані з рахунку або комерційної пропозиції. Відповідай ЛИШЕ JSON "
        "за схемою. Чого немає в тексті — став null. НЕ рахуй і НЕ вгадуй суми: "
        "переписуй тільки те, що прямо написано. Числа — без пробілів, крапка як "
        "десятковий роздільник."),
    "anketa": (
        "Ти витягуєш відповіді анкети скринінгу громади (критерії C1–C9). Відповідай "
        "ЛИШЕ JSON за схемою. Питання без відповіді — null (не 0: нуль означає "
        "«перевірили, немає», null означає «не питали»). Для «так/ні» став 1 або 0."),
    "kontakty": (
        "Ти витягуєш список організацій із тексту. Відповідай ЛИШЕ JSON за схемою. "
        "Нічого не вигадуй: якщо ЄДРПОУ чи телефону немає — null."),
}


# ── читання файлів ───────────────────────────────────────────────────────────

@dataclass
class SourceText:
    text: str
    pages: int = 0
    note: str = ""

    @property
    def empty(self) -> bool:
        return len(self.text.strip()) < 20


def read_file(path: Path) -> SourceText:
    """Дістає текст із PDF/XLSX/DOCX/TXT. Скан без текстового шару — чесно кажемо."""
    suf = path.suffix.lower()

    if suf == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            parts = [p.get_text() for p in doc]
            n = doc.page_count
            doc.close()
        except Exception:
            from pypdf import PdfReader
            r = PdfReader(str(path))
            parts = [(p.extract_text() or "") for p in r.pages]
            n = len(r.pages)
        text = "\n".join(parts)
        if len(text.strip()) < 20:
            return SourceText("", n, "PDF без текстового шару (скан або фото). "
                                     "Розпізнавання зображень не встановлено — "
                                     "внесіть дані вручну або надайте текстовий PDF.")
        return SourceText(text, n)

    if suf in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(str(path), read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            for r in ws.iter_rows(values_only=True):
                cells = [str(c) for c in r if c is not None]
                if cells:
                    rows.append(" | ".join(cells))
        wb.close()
        return SourceText("\n".join(rows), len(rows))

    if suf == ".docx":
        from docx import Document as Docx
        d = Docx(str(path))
        parts = [p.text for p in d.paragraphs]
        for tbl in d.tables:
            for row in tbl.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return SourceText("\n".join(parts))

    if suf in (".txt", ".md", ".csv"):
        return SourceText(path.read_text(encoding="utf-8", errors="replace"))

    if suf in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
        return SourceText("", 0, "Це зображення. Розпізнавання тексту (OCR) не "
                                 "встановлено — витяг із фото недоступний. "
                                 "Надішліть PDF з текстом або внесіть дані вручну.")

    return SourceText("", 0, f"Формат {suf or '—'} не підтримується.")


# ── детермінований екстрактор (fallback і водночас перевірка) ────────────────

_NUM = r"[-+]?\d[\d  ']*(?:[.,]\d+)?"


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(" ", "").replace("'", "").replace(" ", "").replace(",", ".")
    s = re.sub(r"[^\d.\-+]", "", s)
    if not s or s in ("-", "+", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_invoice_deterministic(text: str) -> dict:
    """Регулярні вирази по типових українських формулюваннях рахунку."""
    out: dict = {"postachalnyk": None, "edrpou": None, "nomer": None, "data": None,
                 "valyuta": None, "pozytsii": [], "suma_bez_pdv": None, "pdv": None,
                 "vsogo": None, "incoterms": None}

    m = re.search(r"(?:ЄДРПОУ|ЕДРПОУ|код)\D{0,12}(\d{8,10})", text, re.I)
    if m:
        out["edrpou"] = m.group(1)
    # Номер беремо ЛИШЕ після «№»: інакше з «Рахунок-фактура» витягалось «-фактура»
    m = re.search(r"(?:рахунок|інвойс|invoice|counter)[^\n№]{0,30}№\s*([\w\-/]+)",
                  text, re.I)
    if m:
        out["nomer"] = m.group(1)

    # Постачальник: перший «організаційний» рядок згори — з ОПФ або в лапках
    for line in text.splitlines()[:12]:
        t = line.strip()
        if len(t) < 4 or _SKIP_LINE.match(t):
            continue
        if re.search(r"(ТОВ|ПП|ФГ|ПрАТ|ПАТ|ДП|КП|LLC|GmbH|Ltd)\b|[«\"']", t):
            out["postachalnyk"] = t[:150]
            break
    m = re.search(r"(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})", text)
    if m:
        out["data"] = m.group(1)
    for cur, pat in (("UAH", r"грн|UAH|₴"), ("EUR", r"EUR|€|євро"), ("USD", r"USD|\$")):
        if re.search(pat, text, re.I):
            out["valyuta"] = cur
            break
    m = re.search(r"\b(EXW|FCA|FOB|CIF|CIP|CPT|DAP|DDP|DPU)\b", text)
    if m:
        out["incoterms"] = m.group(1)

    # Підсумки читаємо ПО РЯДКАХ: інакше «ПДВ» чіпляється за рядок «Сума без ПДВ»
    # і в поле ПДВ потрапляє сума без ПДВ.
    for line in text.splitlines():
        low = line.lower()
        nums = re.findall(_NUM, line)
        if not nums:
            continue
        val = _num(nums[-1])
        if val is None:
            continue
        if "без пдв" in low or "сума без" in low:
            out["suma_bez_pdv"] = out["suma_bez_pdv"] or val
        elif re.search(r"\bпдв\b", low):
            # «ПДВ 20%: 891.00» — беремо останнє число, а не відсоток
            out["pdv"] = out["pdv"] or val
        elif re.search(r"всього|разом|усього|до сплати|total", low):
            out["vsogo"] = out["vsogo"] or val

    out["pozytsii"] = _positions(text)
    return out


# Рядок таблиці має РОЗДІЛЬНИКИ колонок: «|», табуляція або 2+ пробіли.
# Без цієї вимоги за позицію приймався заголовок «Рахунок № СФ-00123 від 12.03.2026».
_SEP = re.compile(r"\s*\|\s*|\t+|\s{2,}")
_SKIP_LINE = re.compile(
    r"^\s*(?:рахунок|інвойс|invoice|сума|пдв|всього|разом|усього|до сплати|total"
    r"|єдрпоу|адреса|тел|iban|п/р|дата)", re.I)


def _positions(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if _SKIP_LINE.match(line) or len(line.strip()) < 5:
            continue
        cells = [c.strip() for c in _SEP.split(line) if c.strip()]
        if len(cells) < 3:
            continue                      # не схоже на рядок таблиці
        name = cells[0]
        if len(re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ]", name)) < 3:
            continue                      # перша колонка має бути назвою
        vals = [_num(c) for c in cells[1:]]
        vals = [v for v in vals if v is not None]
        if len(vals) < 3:
            continue
        q, price, s = vals[-3:]
        rows.append({"nazva": name[:120], "kilkist": q, "tsina": price, "suma": s})
    return rows


ANKETA_PATTERNS = {
    "c1_grain_t_yr": r"(?:C1|зерн\w*)[^\d\n]{0,60}?(" + _NUM + r")",
    "c2_underserved_ha": r"(?:C2|га|земл\w*)[^\d\n]{0,60}?(" + _NUM + r")",
    "c3_ind_park": r"(?:C3|індустріальн\w* парк)[^\d\n]{0,60}?(" + _NUM + r")",
    "c4_rail": r"(?:C4|залізни\w*)[^\d\n]{0,60}?(" + _NUM + r")",
    "c5_road": r"(?:C5|дорог\w*)[^\d\n]{0,60}?(" + _NUM + r")",
    "c6_power_mw": r"(?:C6|потужн\w*|МВт)[^\d\n]{0,60}?(" + _NUM + r")",
    "c7_guarantee_ready": r"(?:C7|гарант\w*)[^\d\n]{0,60}?(" + _NUM + r")",
    "c8_ahc_ready": r"(?:C8|комунальн\w*|AHC|АНС)[^\d\n]{0,60}?(" + _NUM + r")",
    "c9_transparency": r"(?:C9|прозор\w*|аудит)[^\d\n]{0,60}?(" + _NUM + r")",
}

_YES = re.compile(r"\b(так|є|наявн\w*|yes)\b", re.I)
_NO = re.compile(r"\b(ні|нема\w*|відсутн\w*|no)\b", re.I)


def extract_anketa_deterministic(text: str) -> dict:
    out: dict = {"hromada": None}
    m = re.search(r"([А-ЯІЇЄҐ][а-яіїєґ']+ська)\s+громад", text)
    if m:
        out["hromada"] = m.group(1)
    for key, pat in ANKETA_PATTERNS.items():
        out[key] = None
        for line in text.splitlines():
            m = re.search(pat, line, re.I)
            if m:
                out[key] = _num(m.group(1))
                break
            # для «так/ні» критеріїв
            if key in ("c3_ind_park", "c4_rail", "c5_road", "c7_guarantee_ready",
                       "c8_ahc_ready", "c9_transparency"):
                head = re.search(pat.split("[^")[0], line, re.I)
                if head:
                    if _YES.search(line):
                        out[key] = 1.0
                        break
                    if _NO.search(line):
                        out[key] = 0.0
                        break
    return out


def extract_contacts_deterministic(text: str) -> dict:
    orgs = []
    for line in text.splitlines():
        if len(line.strip()) < 5:
            continue
        edrpou = re.search(r"\b(\d{8})\b", line)
        phone = re.search(r"(\+?38\s?0\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|0\d{9})",
                          line)
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", line)
        if not (edrpou or phone or email):
            continue
        name = re.split(r"[|;,\t]", line)[0].strip()
        orgs.append({"nazva": name[:150] or None,
                     "edrpou": edrpou.group(1) if edrpou else None,
                     "misto": None,
                     "telefon": phone.group(1) if phone else None,
                     "email": email.group(0) if email else None,
                     "kerivnyk": None})
    return {"organizatsii": orgs}


DETERMINISTIC = {
    "rahunok": extract_invoice_deterministic,
    "anketa": extract_anketa_deterministic,
    "kontakty": extract_contacts_deterministic,
}


# ── детермінована перевірка арифметики (ТЗ §6, п.2) ──────────────────────────

@dataclass
class ArithmeticCheck:
    ok: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        return "; ".join(self.notes)


def check_arithmetic(inv: dict, tol: float = 0.02) -> ArithmeticCheck:
    """Перераховує суми КОДОМ. LLM лише переписала числа — рахуємо ми.

    Перевіряє: кількість × ціна = сума по кожній позиції; Σ позицій = сума без ПДВ;
    без ПДВ + ПДВ = всього.
    """
    res = ArithmeticCheck()
    total_lines = 0.0
    for i, p in enumerate(inv.get("pozytsii") or [], 1):
        q, price, s = p.get("kilkist"), p.get("tsina"), p.get("suma")
        if q is not None and price is not None:
            calc = round(q * price, 2)
            if s is None:
                p["suma"] = calc
                s = calc
                res.notes.append(f"позиція {i}: суму не вказано, підставлено {calc}")
            elif abs(calc - s) > max(tol, abs(s) * 0.001):
                res.ok = False
                res.notes.append(
                    f"позиція {i}: {q} × {price} = {calc}, а в документі {s}")
        if s is not None:
            total_lines += s

    sub = inv.get("suma_bez_pdv")
    if inv.get("pozytsii") and sub is not None:
        if abs(round(total_lines, 2) - sub) > max(tol, abs(sub) * 0.001):
            res.ok = False
            res.notes.append(
                f"сума позицій {round(total_lines, 2)} ≠ сума без ПДВ {sub}")

    vsogo, pdv = inv.get("vsogo"), inv.get("pdv")
    if sub is not None and pdv is not None and vsogo is not None:
        calc = round(sub + pdv, 2)
        if abs(calc - vsogo) > max(tol, abs(vsogo) * 0.001):
            res.ok = False
            res.notes.append(f"{sub} + ПДВ {pdv} = {calc}, а «всього» {vsogo}")

    if not res.notes:
        res.notes.append("арифметика збігається")
    return res


# ── прогін ───────────────────────────────────────────────────────────────────

@dataclass
class Run:
    kind: str
    data: dict
    engine: str
    schema_valid: bool
    fallback_used: bool
    duration_ms: int
    arithmetic: ArithmeticCheck | None = None
    error: str = ""
    source_chars: int = 0

    @property
    def fields_total(self) -> int:
        return _count(self.data)[1]

    @property
    def fields_filled(self) -> int:
        return _count(self.data)[0]


def _count(data) -> tuple[int, int]:
    """Скільки полів заповнено з усіх — метрика «≥80%» з критерію приймання Ф6."""
    filled = total = 0
    def walk(v):
        nonlocal filled, total
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        else:
            total += 1
            if v not in (None, "", []):
                filled += 1
    walk(data)
    return filled, total


def run(kind: str, text: str) -> Run:
    """LLM (якщо є) з валідацією схеми → інакше детермінований екстрактор."""
    t0 = time.perf_counter()
    schema = SCHEMAS[kind]
    engine, valid, fallback, err = "deterministic", False, False, ""
    data: dict = {}

    if llm.available():
        try:
            data = llm.complete(PROMPTS[kind], text[:60000])
            validate(instance=data, schema=schema)
            engine, valid = "llm", True
        except llm.LLMUnavailable as e:
            err, fallback = str(e), True
        except ValidationError as e:
            # невалідна відповідь НЕ записується — це і є strict-output
            err, fallback = f"відповідь LLM не пройшла схему: {e.message}", True
    else:
        fallback = True

    if engine != "llm":
        data = DETERMINISTIC[kind](text)
        try:
            validate(instance=data, schema=schema)
            valid = True
        except ValidationError as e:      # баг у нашому екстракторі, не в LLM
            valid = False
            err = (err + "; " if err else "") + f"детермінований вихід не за схемою: {e.message}"

    arith = check_arithmetic(data) if kind == "rahunok" else None
    return Run(kind=kind, data=data, engine=engine, schema_valid=valid,
               fallback_used=fallback,
               duration_ms=int((time.perf_counter() - t0) * 1000),
               arithmetic=arith, error=err, source_chars=len(text))
