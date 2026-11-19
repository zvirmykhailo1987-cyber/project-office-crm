"""Перенесення переписки WhatsApp (Ф10): парсер і імпорт в архівні кімнати.

Головне, що тут перевіряється:
  1. обидва формати експорту (iOS і Android) читаються без втрат;
  2. неоднозначний порядок день/місяць НЕ вгадується мовчки, а позначається;
  3. повторне перенесення того самого експорту не подвоює нічого;
  4. правила щодо файлів у імпорті ТІ САМІ, що при ручному завантаженні —
     інакше імпорт став би дірою в захисті;
  5. архів особистої переписки не видно решті команди.
"""
import io
import zipfile

import pytest
from sqlmodel import select

from app import config
from app.models import ChatAttachment, ChatMessage, ChatRoom, User
from app.services import wa_import
from app.services.wa_parser import rozibraty, rozibraty_txt

IOS = (
    "‎[15.07.2026, 14:01:22] Богдан: Під сумки наради\n"
    "1) Петро надає договір\n"
    "2) Marko надає базу\n"
    "‎[16.07.2026, 9:09:03] Богдан: ‎IMG-20260716-WA0003.jpg (файл долучено)\n"
    "Супер! Вдячний за оперативність\n"
    "[17.07.2026, 13:19:00] Богдан: <Медіа не долучено>\n"
    "[18.07.2026, 10:00:00] Marko: Повідомлення й дзвінки захищені наскрізним шифруванням.\n"
)
ANDROID = (
    "04.08.2026, 11:25 - Петро Тестовий: звіт буде готовий до пʼятниці\n"
    "04.08.2026, 11:26 - Петро Тестовий: чи потрібні ще якісь таблиці?\n"
    "13.08.2026, 09:00 - Богдан: baza.zip (файл долучено)\n"
)


@pytest.fixture()
def chat_dir(tmp_path, monkeypatch):
    d = tmp_path / "chat"
    d.mkdir()
    monkeypatch.setattr(config, "CHAT_DIR", d)
    return d


@pytest.fixture()
def lyudy(s):
    u = {"marko": User(name="Марко", login="marko", role="rozrobnyk"),
         "oksana": User(name="Оксана", login="oksana", role="perehovory_kontakt")}
    for x in u.values():
        s.add(x)
    s.commit()
    return u


# ── парсер ───────────────────────────────────────────────────────────────────

def test_ios_bagatoryadkove_i_vkladennya():
    r = rozibraty_txt(IOS)
    assert r.format == "ios"
    assert r.vsoho == 3
    pershe = r.povidomlennya[0]
    assert pershe.avtor == "Богдан"
    assert "1) Петро надає договір" in pershe.text, "втрачено другий рядок"
    assert "2) Marko надає базу" in pershe.text
    druhe = r.povidomlennya[1]
    assert druhe.vkladennya == ["IMG-20260716-WA0003.jpg"]
    assert druhe.text == "Супер! Вдячний за оперативність", "мітка файлу лишилась у тексті"
    assert r.povidomlennya[2].media_vtracheno is True
    assert r.systemnyh == 1, "рядок про шифрування має бути пропущений як системний"


def test_android_i_poryadok_daty():
    r = rozibraty_txt(ANDROID)
    assert r.format == "android"
    assert r.vsoho == 3
    # 13.08 — перше число більше 12, отже порядок доказовий: день першим
    assert r.den_pershyi is True and r.dvoznachna_data is False
    assert r.povidomlennya[0].when.strftime("%d.%m.%Y %H:%M") == "04.08.2026 11:25"


def test_dvoznachna_data_ne_vydayetsya_za_vpevnenist():
    """Коли обидва числа ≤ 12, порядок недоказовий — це має бути ПОЗНАЧЕНО."""
    r = rozibraty_txt("05.08.2026, 11:25 - Богдан: тест\n")
    assert r.dvoznachna_data is True, "мовчазне вгадування дати — гірше за відмову"


def test_ampm():
    r = rozibraty_txt("8/5/26, 11:25 PM - Ruslan: files open fine\n")
    assert r.povidomlennya[0].when.hour == 23


def test_smittya_ne_stvoryuye_povidomlen():
    r = rozibraty_txt("це не експорт\nпросто текст\n")
    assert r.vsoho == 0 and r.nerozibranyh == 2
    assert r.zrazky_nerozibranyh, "треба показати, ЩО не розібралось"


def test_zip_z_media():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_chat.txt", ANDROID)
        z.writestr("baza.zip", b"PK\x03\x04dani")
    r = rozibraty("export.zip", buf.getvalue())
    assert r.vsoho == 3
    assert r.media_v_arhivi["baza.zip"] == b"PK\x03\x04dani"


# ── імпорт ───────────────────────────────────────────────────────────────────

def test_import_stvoryuye_arhiv_z_chasom_perepysky(s, lyudy, chat_dir):
    z = wa_import.importuvaty(s, rozibraty_txt(IOS), kod="wa-gis", nazva="ГІС Карта")
    s.commit()
    assert z.novyh == 3 and z.nova_kimnata

    room = s.exec(select(ChatRoom).where(ChatRoom.code == "wa-gis")).one()
    assert room.archived is True and room.dzherelo == "whatsapp"

    rows = list(s.exec(select(ChatMessage).order_by(ChatMessage.created_at)))
    assert rows[0].created_at.strftime("%d.%m.%Y %H:%M") == "15.07.2026 14:01", \
        "час має бути з переписки, а не час імпорту"
    assert rows[0].author == "Богдан"


def test_imena_zvodyatsya_do_komandy(s, lyudy, chat_dir):
    wa_import.importuvaty(s, rozibraty_txt(
        "[15.07.2026, 10:00:00] Marko: моє\n"
        "[15.07.2026, 10:01:00] Oksana Demo: її\n"
        "[15.07.2026, 10:02:00] Петро Тестовий: чужий\n"), kod="wa-x", nazva="X")
    s.commit()
    avtory = [m.author for m in s.exec(select(ChatMessage).order_by(ChatMessage.id))]
    assert avtory == ["Марко", "Оксана", "Петро Тестовий"], \
        "своїх зводимо, чужого лишаємо як є — вигадувати ім'я не можна"


def test_povtornyi_import_nichoho_ne_podvoyuye(s, lyudy, chat_dir):
    wa_import.importuvaty(s, rozibraty_txt(IOS), kod="wa-gis", nazva="ГІС")
    s.commit()
    z2 = wa_import.importuvaty(s, rozibraty_txt(IOS), kod="wa-gis", nazva="ГІС")
    s.commit()
    assert z2.novyh == 0 and z2.vzhe_bulo == 3 and z2.idempotentno
    assert len(list(s.exec(select(ChatMessage)))) == 3


def test_svizhishyi_eksport_dodaye_lyshe_nove(s, lyudy, chat_dir):
    wa_import.importuvaty(s, rozibraty_txt(ANDROID), kod="wa-o", nazva="Офіс")
    s.commit()
    z = wa_import.importuvaty(s, rozibraty_txt(
        ANDROID + "14.08.2026, 12:00 - Богдан: нове після експорту\n"),
        kod="wa-o", nazva="Офіс")
    s.commit()
    assert z.novyh == 1 and z.vzhe_bulo == 3


def test_media_z_arhivu_lyahaye_faylamy(s, lyudy, chat_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", ANDROID)
        zf.writestr("baza.zip", b"PK\x03\x04dani")
    z = wa_import.importuvaty(s, rozibraty("e.zip", buf.getvalue()),
                              kod="wa-o", nazva="Офіс")
    s.commit()
    assert z.media_dodano == 1
    a = s.exec(select(ChatAttachment)).one()
    assert a.original_name == "baza.zip"
    assert (chat_dir / a.filename).read_bytes() == b"PK\x03\x04dani"


def test_import_ne_obhodyt_zaboronu_vykonuvanyh(s, lyudy, chat_dir):
    """Імпорт мусить жити за тими самими правилами, що ручне завантаження."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt",
                    "04.08.2026, 11:25 - Богдан: setup.exe (файл долучено)\n")
        zf.writestr("setup.exe", b"MZ\x90\x00")
    z = wa_import.importuvaty(s, rozibraty("e.zip", buf.getvalue()),
                              kod="wa-o", nazva="Офіс")
    s.commit()
    assert z.media_dodano == 0
    assert z.media_vidhyleno == ["setup.exe"]
    assert list(s.exec(select(ChatAttachment))) == []
    assert not list(chat_dir.iterdir()), "виконуваний файл осів на диску"


def test_zgadka_pro_fayl_bez_baytiv_ne_hovayetsya(s, lyudy, chat_dir):
    """Експорт без медіа: файл згадано, байтів немає — це має бути видно."""
    z = wa_import.importuvaty(s, rozibraty_txt(ANDROID), kod="wa-o", nazva="Офіс")
    s.commit()
    assert z.media_bez_baytiv == 1
    m = s.exec(select(ChatMessage)
               .where(ChatMessage.vidsutni_fayly != "")).one()
    assert m.vidsutni_list == ["baza.zip"]


def test_osobystyi_arhiv_ne_vydymyi_reshti(s, lyudy, chat_dir):
    wa_import.importuvaty(s, rozibraty_txt(IOS), kod="wa-osob",
                          nazva="Богдан", uchasnyky="Марко")
    s.commit()
    room = s.exec(select(ChatRoom).where(ChatRoom.code == "wa-osob")).one()
    assert room.vydyma("Марко") is True
    assert room.vydyma("Оксана") is False, "особиста переписка не для всієї команди"


def test_spilnyi_arhiv_vydymyi_vsim(s, lyudy, chat_dir):
    wa_import.importuvaty(s, rozibraty_txt(IOS), kod="wa-grupa", nazva="ОФІС")
    s.commit()
    room = s.exec(select(ChatRoom).where(ChatRoom.code == "wa-grupa")).one()
    assert room.vydyma("Оксана") is True and room.vydyma("Галина") is True
