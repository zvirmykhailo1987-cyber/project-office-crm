"""Генерація документів із шаблонів (ТЗ §4a, модуль M8/M14).

Тверді правила:
1. Плейсхолдер, для якого немає даних, НЕ вигадується — лишається як є і потрапляє
   в перелік «заповнити руками». Порожнє поле в діловому листі краще за вигадане.
2. Результат — завжди чернетка з міткою «AI-чернетка — перевірте» (домашній стандарт).
3. Надсилає людина; система лише готує файл і фіксує факт відправлення.
"""
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .. import config

PLACEHOLDER_RE = re.compile(r"\[([^\[\]\n]{1,60})\]")

# Плейсхолдер → ключ у контексті. Одне значення може мати кілька написань
# (укр./англ., різний регістр) — шаблони писали руками, тож нормалізуємо тут.
MAPPING = {
    "назва": "hromada", "Назва": "hromada", "назва громади": "hromada",
    "Community": "hromada_en", "territory": "hromada_en",
    "область": "oblast",
    "ПІБ": "recipient", "посада, ПІБ": "recipient",
    "Recipient name, title": "recipient_en", "Name": "recipient_en",
    "дата": "today", "date": "today_en",
    "Проєктний офіс": "office", "Project Office": "office_en",
    "контакти": "contacts",
    "Проєктний офіс — контакти": "office_contacts",
    "Проєктний офіс — назва / контакти": "office_contacts",
    "Проєктний офіс — підпис, контакти": "office_signature",
    "Project Office — name / contacts": "office_contacts_en",
    "роль": "role",
}

MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
          "серпня", "вересня", "жовтня", "листопада", "грудня"]


@dataclass
class Filled:
    text: str
    resolved: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.unresolved


def build_context(*, site=None, counterparty=None, person=None,
                  settings: dict | None = None) -> dict:
    """Збирає значення з карток. Нічого не вигадує: чого немає — того немає."""
    s = settings or {}
    today = date.today()
    ctx = {
        "office": s.get("office.name") or "",
        "office_en": s.get("office.name_en") or "",
        "contacts": s.get("office.contacts") or "",
        "office_contacts": s.get("office.contacts") or "",
        "office_contacts_en": s.get("office.contacts") or "",
        "office_signature": s.get("office.signature") or "",
        "today": f"{today.day} {MONTHS[today.month - 1]} {today.year} р.",
        "today_en": today.strftime("%d %B %Y"),
    }
    if site is not None:
        ctx["hromada"] = site.hromada or site.name or ""
        ctx["hromada_en"] = site.hromada or site.name or ""
        ctx["oblast"] = site.oblast or ""
    if counterparty is not None:
        ctx.setdefault("hromada", counterparty.hromada or counterparty.name or "")
        ctx.setdefault("oblast", counterparty.oblast or "")
    if person is not None:
        ctx["recipient"] = ", ".join(x for x in [person.name, person.role] if x)
        ctx["recipient_en"] = person.name or ""
        ctx["role"] = person.role or ""
    return {k: v for k, v in ctx.items() if v}


# Назви громад — прикметникові жіночого роду («Борсуківська»), відмінюються регулярно.
# Без цього виходить «рада Борсуківська громади» — видно, що лист згенерований машиною.
_ADJ_F_ENDINGS = ("ська", "цька", "зька", "чна", "вська", "ївська", "на", "я", "а")
_CASES = {
    "родовий": {"а": "ої", "я": "ьої"},
    "давальний": {"а": "ій", "я": "ій"},
    "знахідний": {"а": "у", "я": "ю"},
    "орудний": {"а": "ою", "я": "ьою"},
}
# «громади» → родовий, «громаді» → давальний, «громаду» → знахідний, «громадою» → орудний
_FOLLOW_CASE = {
    "громади": "родовий", "громадою": "орудний", "громаді": "давальний",
    "громаду": "знахідний", "громад": "родовий",
}


def decline_f(name: str, case: str) -> str:
    """Відмінює прикметникову назву ж.р. Неприкметникову лишає як є — краще
    залишити називний, ніж зіпсувати назву власного імені."""
    n = (name or "").strip()
    if not n or case not in _CASES:
        return n

    def one(word: str) -> str:
        if not word.endswith(_ADJ_F_ENDINGS) or word[-1] not in _CASES[case]:
            return word
        return word[:-1] + _CASES[case][word[-1]]

    # відмінюємо КОЖНЕ прикметникове слово: «Тернопільська міська» → «Тернопільської міської»
    words = n.split()
    out = [one(w) for w in words]
    return " ".join(out) if any(a != b for a, b in zip(words, out, strict=False)) else n


def fill(body_md: str, ctx: dict) -> Filled:
    """Підставляє відоме, решту лишає видимою — щоб людина побачила, що дозаповнити."""
    resolved, unresolved = {}, []

    def repl(m: re.Match) -> str:
        raw = m.group(1)
        key = MAPPING.get(raw) or MAPPING.get(raw.strip())
        val = ctx.get(key) if key else None
        if val:
            # «[назва] громади» → відмінюємо за наступним словом
            if key == "hromada":
                nxt = re.match(r"\s*([а-яіїєґА-ЯІЇЄҐ]+)", body_md[m.end():])
                case = _FOLLOW_CASE.get(nxt.group(1).lower()) if nxt else None
                if case:
                    val = decline_f(val, case)
            resolved[raw] = val
            return val
        if raw.lower().startswith("http") or re.match(r"^[\sxX]*$", raw):
            return m.group(0)          # посилання й чекбокси — не плейсхолдери
        if raw not in unresolved:
            unresolved.append(raw)
        return m.group(0)

    return Filled(PLACEHOLDER_RE.sub(repl, body_md), resolved, unresolved)


def find_placeholders(body_md: str) -> list[str]:
    out = []
    for m in PLACEHOLDER_RE.finditer(body_md):
        raw = m.group(1)
        if raw.lower().startswith("http") or re.match(r"^[\sxX]*$", raw):
            continue
        if raw not in out:
            out.append(raw)
    return out


# ── рендер у файли ───────────────────────────────────────────────────────────

DRAFT_MARK = "🟠 AI-чернетка — перевірте"


def _inline(p, text: str, *, italic_all: bool = False):
    """**жирний** і *курсив* у будь-якому абзаці, включно з цитатою.

    Раніше цитата вставлялася сирим рядком — у готовому листі було видно `**зірочки**`.
    """
    from docx.shared import Pt

    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            r = p.add_run(part[2:-2])
            r.bold = True
        elif part.startswith("*") and part.endswith("*"):
            r = p.add_run(part[1:-1])
            r.italic = True
        else:
            r = p.add_run(part)
        if italic_all:
            r.italic = True
        r.font.size = Pt(11)


def _add_md_line(doc, line: str):
    """Мінімальний markdown → DOCX: заголовки, списки, жирний, цитата, розділювач."""
    t = line.rstrip()
    if not t.strip():
        doc.add_paragraph("")
        return
    if t.startswith("---"):
        doc.add_paragraph("─" * 40)
        return
    if t.startswith("#"):
        lvl = len(t) - len(t.lstrip("#"))
        doc.add_heading(t.lstrip("# ").strip(), level=min(lvl, 4))
        return
    if t.startswith(">"):
        p = doc.add_paragraph()
        _inline(p, t.lstrip("> ").strip(), italic_all=True)
        return
    style = None
    if re.match(r"^\s*[-*•]\s+", t):
        t, style = re.sub(r"^\s*[-*•]\s+", "", t), "List Bullet"
    elif re.match(r"^\s*\d+[.)]\s+", t):
        t, style = re.sub(r"^\s*\d+[.)]\s+", "", t), "List Number"

    p = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    _inline(p, t)


def to_docx(md_text: str, out_path: Path, *, title: str = "",
            draft: bool = True) -> Path:
    """DOCX через python-docx. Шрифт Segoe UI — щоб не «поїхало» на чужій машині."""
    from docx import Document as Docx
    from docx.shared import Pt, RGBColor

    doc = Docx()
    style = doc.styles["Normal"]
    style.font.name = "Segoe UI"
    style.font.size = Pt(11)

    # шаблони вже несуть мітку чернетки — не дублюємо її вгорі документа
    if draft and "AI-чернетка" not in md_text:
        p = doc.add_paragraph()
        r = p.add_run(DRAFT_MARK)
        r.bold = True
        r.font.color.rgb = RGBColor(0xB4, 0x61, 0x0A)

    for line in md_text.splitlines():
        _add_md_line(doc, line)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def soffice_path() -> str | None:
    for c in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "soffice", "libreoffice"):
        if Path(c).exists() or shutil.which(c):
            return c
    return None


def to_pdf(docx_path: Path) -> Path | None:
    """PDF через LibreOffice. Немає LibreOffice — немає PDF, і про це кажемо прямо."""
    exe = soffice_path()
    if not exe:
        return None
    try:
        # fixed argv, our own file, no shell
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir",  # noqa: S603  # nosec B603
                        str(docx_path.parent), str(docx_path)],
                       check=True, capture_output=True, timeout=180)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    pdf = docx_path.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def render(md_text: str, *, basename: str, title: str = "",
           formats=("docx", "pdf"), draft: bool = True) -> dict[str, Path]:
    """Повертає {формат: шлях}. Формат, який не вдалося зробити, у словник не потрапляє."""
    out: dict[str, Path] = {}
    folder = config.DOCS_DIR
    folder.mkdir(parents=True, exist_ok=True)

    md_path = folder / f"{basename}.md"
    md_path.write_text(md_text, encoding="utf-8")
    out["md"] = md_path

    if "docx" in formats:
        out["docx"] = to_docx(md_text, folder / f"{basename}.docx", title=title,
                              draft=draft)
        if "pdf" in formats:
            pdf = to_pdf(out["docx"])
            if pdf:
                out["pdf"] = pdf
    return out
