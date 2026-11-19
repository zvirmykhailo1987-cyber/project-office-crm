"""Шаблони, підстановка, відмінювання, потік узгодження, реєстр листів (ТЗ §4a)."""
from datetime import date, timedelta

from app.models import Document, Outreach, utcnow
from app.services import docgen


# ── підстановка ──────────────────────────────────────────────────────────────

def test_known_placeholders_are_filled():
    r = docgen.fill("Вітаємо, [назва] громада! — [Проєктний офіс]",
                    {"hromada": "Борсуківська", "office": "Проєктний офіс"})
    assert "Борсуківська громада" in r.text
    assert "[назва]" not in r.text and r.unresolved == []


def test_unknown_placeholder_is_kept_and_reported():
    """Нічого не вигадуємо: невідоме лишається видимим і потрапляє у перелік."""
    r = docgen.fill("Кому: [посада, ПІБ] · Сума: [сума угоди]", {})
    assert "[сума угоди]" in r.text
    assert "сума угоди" in r.unresolved
    assert not r.ready


def test_links_and_checkboxes_are_not_placeholders():
    r = docgen.fill("Див. [http://example.com] і чекбокс [ ] та [x]", {})
    assert r.unresolved == []


def test_no_invented_values_when_context_empty():
    body = "Громада [назва], область [область], дата [дата]"
    r = docgen.fill(body, {})
    assert r.text == body
    assert set(r.unresolved) == {"назва", "область", "дата"}


# ── відмінювання назв громад ─────────────────────────────────────────────────

def test_declension_genitive():
    assert docgen.decline_f("Борсуківська", "родовий") == "Борсуківської"
    assert docgen.decline_f("Білобожницька", "родовий") == "Білобожницької"


def test_declension_dative():
    assert docgen.decline_f("Гусятинська", "давальний") == "Гусятинській"


def test_declension_multiword():
    assert docgen.decline_f("Тернопільська міська", "родовий") == "Тернопільської міської"


def test_non_adjectival_name_left_alone():
    """Краще називний відмінок, ніж зіпсована власна назва."""
    for n in ("Бучач", "Заліщики", "Микулинці"):
        assert docgen.decline_f(n, "родовий") == n


def test_case_chosen_by_following_word():
    ctx = {"hromada": "Борсуківська"}
    assert "рада Борсуківської громади" in docgen.fill("рада [назва] громади", ctx).text
    assert "у Борсуківській громаді" in docgen.fill("у [назва] громаді", ctx).text
    assert "Борсуківська громада" in docgen.fill("[назва] громада", ctx).text


# ── пошук плейсхолдерів ──────────────────────────────────────────────────────

def test_find_placeholders_dedupes():
    assert docgen.find_placeholders("[назва] і ще [назва] та [область]") == \
        ["назва", "область"]


# ── рендер ───────────────────────────────────────────────────────────────────

def test_docx_is_created(tmp_path):
    out = docgen.to_docx("# Заголовок\n\n**жирний** текст\n\n- пункт",
                         tmp_path / "t.docx")
    assert out.exists() and out.stat().st_size > 5000


def test_draft_mark_not_duplicated(tmp_path):
    """Шаблони вже несуть мітку — вгорі другу не додаємо."""
    from docx import Document as Docx
    docgen.to_docx("> 🟠 AI-чернетка — перевірте\n\nтекст", tmp_path / "a.docx")
    text = "\n".join(p.text for p in Docx(str(tmp_path / "a.docx")).paragraphs)
    assert text.count("AI-чернетка") == 1


def test_draft_mark_added_when_absent(tmp_path):
    from docx import Document as Docx
    docgen.to_docx("просто текст", tmp_path / "b.docx")
    text = "\n".join(p.text for p in Docx(str(tmp_path / "b.docx")).paragraphs)
    assert "AI-чернетка" in text


def test_bold_markers_are_consumed_in_quotes(tmp_path):
    """Раніше в цитаті лишались сирі `**зірочки**` — у діловому листі це брак."""
    from docx import Document as Docx
    docgen.to_docx("> Відправник: **Проєктний офіс**", tmp_path / "c.docx")
    text = "\n".join(p.text for p in Docx(str(tmp_path / "c.docx")).paragraphs)
    assert "**" not in text and "Проєктний офіс" in text


# ── потік узгодження й реєстр ────────────────────────────────────────────────

def test_new_version_keeps_group_and_increments(s):
    g = "tpl:COM-06:1"
    for v in (1, 2, 3):
        s.add(Document(title="MOU", kind="COM-06", doc_group_uid=g, version=v))
    s.commit()
    rows = list(s.exec(__import__("sqlmodel").select(Document)
                       .where(Document.doc_group_uid == g)))
    assert len(rows) == 3 and {r.version for r in rows} == {1, 2, 3}


def test_outreach_overdue_logic(s):
    o = Outreach(subject="COM-01", sent_at=utcnow(),
                 reply_due_date=date.today() - timedelta(days=1))
    assert o.is_overdue
    o.reply_status = "otrymano"
    assert not o.is_overdue


def test_outreach_records_which_version_was_sent(s):
    d = Document(title="MOU", kind="COM-06", doc_group_uid="g", version=4)
    s.add(d)
    s.commit()
    o = Outreach(document_id=d.id, document_version=d.version, sent_at=utcnow(),
                 recipient="Голова громади")
    s.add(o)
    s.commit()
    assert o.document_version == 4      # фіксується версія, а не «останній файл»
