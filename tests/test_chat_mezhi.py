# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Chat boundaries through HTTP: a private archive is invisible to non-participants on
every route that takes a message or file id, an import never merges a personal archive
into a shared one, and files missing from disk are reported instead of served."""
from datetime import timedelta
from urllib.parse import unquote

import pytest
from sqlmodel import select

from app import config
from app.models import KIMNATA_OFIS, AuditEntry, ChatAttachment, ChatMessage, ChatRead, ChatRoom, Task, User, utcnow


@pytest.fixture()
def chat_dir(tmp_path, monkeypatch):
    d = tmp_path / "chat"
    d.mkdir()
    monkeypatch.setattr(config, "CHAT_DIR", d)
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setattr(config, "DOCS_DIR", docs)
    return d


@pytest.fixture()
def kimnaty(s):
    office = ChatRoom(code=KIMNATA_OFIS)
    private = ChatRoom(code="wa-osobyste", title="Особисте", archived=True, uchasnyky="Оксана,Богдан")
    s.add(office)
    s.add(private)
    s.add(User(id=3, name="Оксана", login="oksana", role="perehovory_kontakt"))
    s.commit()
    return office, private


def _msg(s, room, author="Оксана", text="текст", **kw):
    m = ChatMessage(room_id=room.id, author=author, text=text, **kw)
    s.add(m)
    s.commit()
    s.refresh(m)
    return m


def _att(s, msg, chat_dir, name="plan.pdf", on_disk=True):
    a = ChatAttachment(message_id=msg.id, filename=f"f{msg.id}_{name}", original_name=name, size=3)
    if on_disk:
        (chat_dir / a.filename).write_bytes(b"pdf")
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


def _err(r):
    return unquote(r.headers["location"])


# ── private archive: 404 on every route that takes an id ────────────────────

def test_chuzhyi_arkhiv_ne_vidkryvayetsya(klient, kimnaty):
    r = klient.get("/chat", params={"kimnata": "wa-osobyste"})
    assert "не видима" in _err(r)


def test_chuzhyi_arkhiv_ne_viddaye_niyakyi_id(klient, s, kimnaty, chat_dir):
    _, private = kimnaty
    m = _msg(s, private, text="особисте")
    a = _att(s, m, chat_dir)
    assert klient.get(f"/chat/fayl/{a.id}").status_code == 404
    assert klient.post(f"/chat/fayl/{a.id}/v-dokumenty").status_code == 404
    assert klient.post(f"/chat/{m.id}/u-zavdannya").status_code == 404
    assert klient.post(f"/chat/{m.id}/redahuvaty", data={"text": "x"}).status_code == 404
    assert klient.post(f"/chat/{m.id}/vydalyty").status_code == 404
    s.expire_all()
    assert s.get(ChatMessage, m.id).text == "особисте" and s.exec(select(Task)).first() is None


def test_vidpovid_lyshe_na_povidomlennya_tsiyeyi_kimnaty(klient, s, kimnaty):
    _, private = kimnaty
    m = _msg(s, private)
    r = klient.post("/chat", data={"text": "цитую", "reply_to": str(m.id)})
    assert r.status_code == 400
    r = klient.post("/chat", data={"text": "цитую", "reply_to": "9999"})
    assert r.status_code == 400


def test_fayl_bez_imeni_abo_rozshyrennya(klient, s, kimnaty, chat_dir):
    r = klient.post("/chat", data={"text": "x"}, files={"fayly": ("README", b"abc", "text/plain")})
    assert "без розширення" in _err(r)
    assert s.exec(select(ChatMessage)).first() is None
    from app.web import chat
    with pytest.raises(chat.FaylVidhyleno):
        chat._perevir_imya("..")


# ── files ───────────────────────────────────────────────────────────────────

def test_fayl_zhyknuv_z_dysku(klient, s, kimnaty, chat_dir):
    office, _ = kimnaty
    m = _msg(s, office)
    a = _att(s, m, chat_dir, on_disk=False)
    assert "не знайдено" in _err(klient.get(f"/chat/fayl/{a.id}"))
    assert "не знайдено" in _err(klient.post(f"/chat/fayl/{a.id}/v-dokumenty"))
    assert klient.get("/chat/fayl/9999").headers["location"] == "/chat"
    assert klient.post("/chat/fayl/9999/v-dokumenty").headers["location"] == "/chat"


# ── editing, deleting, tasks ────────────────────────────────────────────────

def test_redahuvaty_lyshe_svoye_i_ne_porozhnim(klient, s, kimnaty):
    office, _ = kimnaty
    chuzhe = _msg(s, office, author="Оксана", text="оригінал")
    svoye = _msg(s, office, author="Марко", text="моє")
    klient.post(f"/chat/{chuzhe.id}/redahuvaty", data={"text": "підміна"})
    klient.post(f"/chat/{svoye.id}/redahuvaty", data={"text": "  "})
    klient.post(f"/chat/{svoye.id}/redahuvaty", data={"text": "моє, виправлене @oksana"})
    s.expire_all()
    assert s.get(ChatMessage, chuzhe.id).text == "оригінал"
    fixed = s.get(ChatMessage, svoye.id)
    assert fixed.text.startswith("моє, виправлене") and fixed.edited_at and fixed.mentions == "oksana"


def test_vydalyty_chuzhe_bez_prava_ne_mozhna(klient, s, kimnaty, monkeypatch):
    office, _ = kimnaty
    m = _msg(s, office, author="Богдан")
    klient.korystuvach.role = "perehovory_kontakt"
    try:
        assert "автор або адміністратор" in _err(klient.post(f"/chat/{m.id}/vydalyty"))
    finally:
        klient.korystuvach.role = "rozrobnyk"
    s.expire_all()
    assert not s.get(ChatMessage, m.id).is_deleted
    klient.post(f"/chat/{m.id}/vydalyty")
    klient.post(f"/chat/{m.id}/vydalyty")                       # twice: one audit entry
    assert len(list(s.exec(select(AuditEntry).where(AuditEntry.action == "chat.delete")))) == 1


def test_u_zavdannya_avtor_povidomlennya_ce_postanovnyk(klient, s, kimnaty):
    office, _ = kimnaty
    m = _msg(s, office, author="Оксана", text="зробити звіт до пʼятниці")
    klient.post(f"/chat/{m.id}/u-zavdannya", data={"assignee": "Оксана", "due_date": "2026-12-04"})
    t = s.exec(select(Task)).one()
    assert t.created_by == "Оксана" and t.assignee == "Оксана" and t.title.startswith("зробити звіт")
    porozhnie = _msg(s, office, text="  ")
    assert klient.post(f"/chat/{porozhnie.id}/u-zavdannya").headers["location"] == "/chat"


# ── reading the room ────────────────────────────────────────────────────────

def test_poshuk_za_imenem_fayla_i_storinky_ranishe(klient, s, kimnaty, chat_dir):
    office, _ = kimnaty
    base = utcnow() - timedelta(days=1)
    msgs = [_msg(s, office, text=f"репліка {i}", created_at=base + timedelta(minutes=i)) for i in range(5)]
    _att(s, msgs[1], chat_dir, name="кошторис.xlsx")
    r = klient.get("/chat", params={"q": "кошторис"})
    assert "репліка 1" in r.text and "репліка 3" not in r.text
    r = klient.get("/chat", params={"do": msgs[3].id})
    assert "репліка 2" in r.text and "репліка 4" not in r.text
    assert klient.get("/chat", params={"do": 999999}).status_code == 200


def test_poling_novykh_posuvaye_mitku_prochytanoho(klient, s, kimnaty):
    office, _ = kimnaty
    m1 = _msg(s, office)
    assert klient.get("/chat/novi", params={"after": m1.id}).status_code == 200
    assert s.exec(select(ChatRead)).first() is None              # nothing new: nothing written
    m2 = _msg(s, office, text="нове")
    r = klient.get("/chat/novi", params={"after": m1.id})
    assert "нове" in r.text
    assert s.exec(select(ChatRead)).one().last_read_message_id == m2.id
    assert klient.get("/chat/lichylnyk", headers={"HX-Current-URL": "http://t/chat"}).status_code == 200


def test_lichylnyk_bez_kimnaty_nul(s):
    from app.web import chat
    assert chat.neprochytani(s, "Марко") == 0


def test_kimnata_stvoryuyetsya_yakshcho_yiyi_nemaye(klient, s):
    assert klient.get("/chat").status_code == 200
    assert s.exec(select(ChatRoom).where(ChatRoom.code == KIMNATA_OFIS)).one()


# ── WhatsApp import ─────────────────────────────────────────────────────────

ANDROID = "04.08.2026, 11:25 - Петро: звіт буде\n04.08.2026, 11:26 - Петро: ще таблиці?\n".encode()


def test_perenesennya_vidkhylyaye_nevirni_fayly(klient, s, kimnaty, chat_dir, monkeypatch):
    assert "Очікую .txt" in _err(klient.post("/chat/perenesty", files={"fayl": ("chat.exe", b"x")}))
    assert "zip-архів" in _err(klient.post("/chat/perenesty", files={"fayl": ("chat.zip", b"PKjunk")}))
    assert "жодного повідомлення" in _err(klient.post("/chat/perenesty", files={"fayl": ("chat.txt", b"no dates")}))
    from app.web import chat
    monkeypatch.setattr(chat, "MAX_ROZMIR", 10)
    assert "більший" in _err(klient.post("/chat/perenesty", files={"fayl": ("chat.txt", ANDROID)}))
    assert s.exec(select(ChatRoom).where(ChatRoom.archived == True)).all() == [kimnaty[1]]  # noqa: E712


def test_osobystyi_arkhiv_ne_zlyvayetsya_zi_spilnym(klient, s, kimnaty, chat_dir):
    r = klient.post("/chat/perenesty", data={"nazva": "Нарада", "osobystyi": "on"},
                    files={"fayl": ("chat.txt", ANDROID)})
    assert "kimnata=wa-" in r.headers["location"]
    room = s.exec(select(ChatRoom).where(ChatRoom.title == "Нарада")).one()
    assert room.uchasnyky == "Марко"
    entry = s.exec(select(AuditEntry).where(AuditEntry.action == "chat.import")).one()
    assert "Нарада" not in entry.detail                              # the feed is visible to all
    r = klient.post("/chat/perenesty", data={"nazva": "Нарада"}, files={"fayl": ("chat.txt", ANDROID)})
    assert "іншою видимістю" in _err(r)
    assert len(list(s.exec(select(ChatMessage).where(ChatMessage.room_id == room.id)))) == 2
