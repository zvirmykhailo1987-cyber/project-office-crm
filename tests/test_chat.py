"""Чат офісу (Ф10) — насамперед МЕЖІ БЕЗПЕКИ.

Чат — єдине місце системи, куди людина кладе довільний файл, а система віддає його
іншим людям. Тому тут перевіряється не «повідомлення надсилається», а те, що не можна
доставити командою виконуваний файл, вичерпати диск недописаними шматками чи вийти
з каталогу вкладень.

Роути викликаємо як звичайні функції (`user` і `s` напряму) — так само, як у
`test_collab.py`: перевіряємо правила, а не транспорт.
"""
import asyncio
import io
from urllib.parse import unquote

import pytest
from sqlmodel import select
from starlette.datastructures import UploadFile

from app import config
from app.models import (ChatAttachment, ChatMessage, ChatRead, Document,
                        Notification, Task, User)
from app.web import chat as web


# ── фікстури ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def chat_dir(tmp_path, monkeypatch):
    """Вкладення пишемо у тимчасовий каталог, а не в робочі дані."""
    d = tmp_path / "chat"
    d.mkdir()
    monkeypatch.setattr(config, "CHAT_DIR", d)
    return d


@pytest.fixture()
def lyudy(s):
    u = {
        "marko": User(name="Марко", login="marko", role="rozrobnyk"),
        "kostyantyn": User(name="Богдан", login="kostyantyn",
                           role="perehovorschyk_dok"),
        "oksana": User(name="Оксана", login="oksana", role="perehovory_kontakt"),
    }
    for x in u.values():
        s.add(x)
    s.commit()
    return u


def _fayl(imya: str, danci: bytes = b"vmist-faylu") -> UploadFile:
    return UploadFile(filename=imya, file=io.BytesIO(danci))


def _nadislaty(s, user, text="", fayly=None, reply_to=""):
    """Синхронна обгортка над async-роутом — щоб не тягнути pytest-asyncio."""
    return asyncio.run(web.send(request=None, text=text, reply_to=reply_to,
                                fayly=fayly or [], user=user, s=s))


def _msgs(s):
    return list(s.exec(select(ChatMessage).order_by(ChatMessage.id)))


# ── 1. розширення файлів ─────────────────────────────────────────────────────

def test_podviyne_rozshyrennya_vidhyleno(s, lyudy, chat_dir):
    """«звіт.pdf.exe» має читатись як .exe — перевірка йде за ОСТАННІМ суфіксом."""
    r = _nadislaty(s, lyudy["marko"], fayly=[_fayl("звіт.pdf.exe")])
    assert ".exe" in unquote(r.headers["location"])
    assert _msgs(s) == []                      # повідомлення не створено
    assert list(chat_dir.iterdir()) == []      # на диску нічого


def test_arhiv_pryimayetsya(s, lyudy, chat_dir):
    """Архіви дозволені свідомо: zip із базою має ходити тут, а не через WhatsApp."""
    _nadislaty(s, lyudy["marko"], text="база", fayly=[_fayl("kontakty.zip", b"PK\x03\x04dani")])
    a = s.exec(select(ChatAttachment)).one()
    assert a.original_name == "kontakty.zip"
    assert (chat_dir / a.filename).read_bytes() == b"PK\x03\x04dani"
    assert a.sha256 and a.size == 8


def test_nevidome_rozshyrennya_vidhyleno(s, lyudy, chat_dir):
    r = _nadislaty(s, lyudy["marko"], fayly=[_fayl("shos.xyz")])
    assert "xyz" in unquote(r.headers["location"])
    assert _msgs(s) == []


# ── 2. розмір ────────────────────────────────────────────────────────────────

def test_zavelykyi_fayl_ne_lyshaye_smittya(s, lyudy, chat_dir, monkeypatch):
    """Перевищення ліміту → помилка І порожній каталог: недописане прибираємо."""
    monkeypatch.setattr(web, "MAX_ROZMIR", 1024)
    monkeypatch.setattr(web, "SHMAT", 256)
    r = _nadislaty(s, lyudy["marko"], fayly=[_fayl("velykyi.zip", b"x" * 5000)])
    assert "МБ" in unquote(r.headers["location"])
    assert list(chat_dir.iterdir()) == [], "недописаний файл лишився на диску"
    assert _msgs(s) == []


def test_porozhniy_fayl_vidhyleno(s, lyudy, chat_dir):
    r = _nadislaty(s, lyudy["marko"], fayly=[_fayl("porozhniy.pdf", b"")])
    assert "орожн" in unquote(r.headers["location"])
    assert list(chat_dir.iterdir()) == []


def test_zaboroneny_v_pachtsi_skasovuye_vsi(s, lyudy, chat_dir):
    """Три файли, третій заборонений — перші два НЕ мають осісти окремим повідомленням."""
    r = _nadislaty(s, lyudy["marko"], text="пачка",
                   fayly=[_fayl("a.pdf"), _fayl("b.zip"), _fayl("c.bat")])
    assert ".bat" in unquote(r.headers["location"])
    assert _msgs(s) == []
    assert list(chat_dir.iterdir()) == []


# ── 3. шлях ──────────────────────────────────────────────────────────────────

def test_path_traversal_znieshkodzheno(s, lyudy, chat_dir):
    """Імʼя з «../» не має вивести файл за межі каталогу вкладень."""
    _nadislaty(s, lyudy["marko"], fayly=[_fayl("../../secrets.env.txt", b"tayemnytsya")])
    a = s.exec(select(ChatAttachment)).one()
    shlyah = chat_dir / a.filename
    assert shlyah.exists()
    assert shlyah.resolve().parent == chat_dir.resolve()
    assert "secrets" in a.original_name          # показуємо як назвала людина
    assert ".." not in a.filename                # але на диску — наше імʼя


# ── 4. віддача ───────────────────────────────────────────────────────────────

def test_ne_kartynka_iде_yak_zavantazhennya(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["marko"], fayly=[_fayl("baza.zip", b"PK")])
    a = s.exec(select(ChatAttachment)).one()
    resp = web.download(a.id, user=lyudy["marko"], s=s)
    assert resp.headers["content-disposition"].startswith("attachment")
    assert resp.media_type == "application/octet-stream"
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_kartynka_inline_ale_z_nosniff(s, lyudy, chat_dir):
    """Скріншот правок має бути видно в стрічці — але тип віддаємо СВІЙ, не від клієнта."""
    _nadislaty(s, lyudy["marko"], fayly=[_fayl("slayd.png", b"\x89PNG")])
    a = s.exec(select(ChatAttachment)).one()
    resp = web.download(a.id, user=lyudy["marko"], s=s)
    assert resp.headers["content-disposition"].startswith("inline")
    assert resp.media_type == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_svg_zaboroneno(s, lyudy, chat_dir):
    """SVG — картинка, що вміє виконати скрипт при відкритті. Не приймаємо."""
    r = _nadislaty(s, lyudy["marko"], fayly=[_fayl("shema.svg", b"<svg/>")])
    assert "svg" in unquote(r.headers["location"])


# ── 5. згадки ────────────────────────────────────────────────────────────────

def test_zgadka_shle_spovishchennya_ale_ne_sobi(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["marko"], text="@oksana глянь, і @marko теж")
    komu = [n.user for n in s.exec(select(Notification))]
    assert komu == ["Оксана"], "сповіщення собі — це шум, а не сигнал"
    # у полі фіксуємо ВСІХ згаданих (це факт тексту), а відсіює себе вже `notify`
    assert set(s.exec(select(ChatMessage)).one().mention_list) == {"oksana", "marko"}


# ── 6. мʼяке видалення ───────────────────────────────────────────────────────

def test_myake_vydalennya_lyshaye_nytku(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["marko"], text="перше")
    pershe = _msgs(s)[0]
    _nadislaty(s, lyudy["oksana"], text="відповідь", reply_to=str(pershe.id))

    web.delete(pershe.id, user=lyudy["marko"], s=s)
    s.expire_all()
    rows = _msgs(s)
    assert len(rows) == 2, "рядок має лишитись, інакше рветься гілка відповідей"
    assert rows[0].is_deleted and rows[0].deleted_by == "Марко"
    assert rows[1].reply_to_id == pershe.id


def test_chuzhe_ne_vydalyaye_bez_prava(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["marko"], text="моє")
    m = _msgs(s)[0]
    web.delete(m.id, user=lyudy["oksana"], s=s)      # у ролі Оксани немає CAP_DELETE
    s.expire_all()
    assert not _msgs(s)[0].is_deleted


# ── 7. полінг ────────────────────────────────────────────────────────────────

def test_after_povertaye_lyshe_novi(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["marko"], text="раз")
    _nadislaty(s, lyudy["marko"], text="два")
    mezha = _msgs(s)[-1].id
    _nadislaty(s, lyudy["oksana"], text="три")

    novi = list(s.exec(select(ChatMessage).where(ChatMessage.id > mezha)))
    assert [m.text for m in novi] == ["три"], "полінг не має повертати вже показане"


def test_lichylnyk_ne_rahuye_svoye(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["oksana"], text="від Оксани")
    _nadislaty(s, lyudy["marko"], text="від Марка")
    assert web.neprochytani(s, "Марко") == 1
    assert web.neprochytani(s, "Оксана") == 1


def test_vidkryttya_kimnaty_znymaye_lichylnyk(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["oksana"], text="привіт")
    assert web.neprochytani(s, "Марко") == 1
    ostannii = _msgs(s)[-1].id
    r = s.exec(select(ChatRead)).first()
    assert r is None or r.last_read_message_id < ostannii


# ── 8. перенесення в документи ───────────────────────────────────────────────

def test_perenesennya_v_dokumenty_idempotentne(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["marko"], text="КП", fayly=[_fayl("kp.pdf", b"%PDF-1.4")])
    a = s.exec(select(ChatAttachment)).one()

    web.promote(a.id, kind="KP", title="КП для американців",
                user=lyudy["marko"], s=s)
    web.promote(a.id, kind="KP", title="КП для американців",
                user=lyudy["marko"], s=s)

    docs = list(s.exec(select(Document)))
    assert len(docs) == 1, "повторне перенесення не має створювати другий документ"
    s.expire_all()
    assert s.get(ChatAttachment, a.id).promoted_document_id == docs[0].id
    # байти не копіюємо — документ дивиться в те саме сховище чату
    assert docs[0].stored_path == str(chat_dir / a.filename)


# ── 9. повідомлення → завдання ───────────────────────────────────────────────

def test_povidomlennya_u_zavdannya(s, lyudy, chat_dir):
    """Те, чого бракувало: «зроби X до Y» з чату не зникає між репліками."""
    _nadislaty(s, lyudy["kostyantyn"], text="Нагадати Петру про звіт по хабах")
    m = _msgs(s)[0]

    web.to_task(m.id, assignee="Марко", due_date="2026-08-06",
                user=lyudy["kostyantyn"], s=s)

    t = s.exec(select(Task)).one()
    assert t.assignee == "Марко"
    assert t.created_by == "Богдан", "доручення в чаті дав автор повідомлення"
    assert str(t.due_date) == "2026-08-06"
    assert "з чату" in t.note and "Богдан" in t.note
    assert [n.user for n in s.exec(select(Notification))] == ["Марко"]


def test_porozhnye_povidomlennya_ne_stvoryuyetsya(s, lyudy, chat_dir):
    _nadislaty(s, lyudy["marko"], text="   ")
    assert _msgs(s) == []
