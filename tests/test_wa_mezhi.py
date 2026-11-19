# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""WhatsApp import limits: a zip bomb, a broken zip or a forged header must stop before
memory is spent; month-first exports and 12-hour clocks are read correctly; the collected
JSON format and the import report say honestly what was skipped."""
import io
import json
import zipfile
from datetime import datetime

import pytest

from app.services import wa_import, wa_parser
from app.services.wa_parser import ArkhivVidhyleno, rozibraty


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


CHAT = "04.08.2026, 11:25 - Петро: привіт\n".encode()


def test_bytyi_zip_vidkhyleno():
    with pytest.raises(ArkhivVidhyleno, match="zip"):
        rozibraty("chat.zip", b"PK-not-really")


def test_zabahato_fayliv_vidkhyleno(monkeypatch):
    monkeypatch.setattr(wa_parser, "ZIP_MAX_FAYLIV", 2)
    with pytest.raises(ArkhivVidhyleno, match="понад 2"):
        rozibraty("chat.zip", _zip({"a.txt": CHAT, "b.jpg": b"1", "c.jpg": b"2"}))


def test_zaiavlenyi_rozmir_perevireno_do_chytannya(monkeypatch):
    monkeypatch.setattr(wa_parser, "ZIP_MAX_FAYL", 10)
    with pytest.raises(ArkhivVidhyleno, match="більший"):
        rozibraty("chat.zip", _zip({"a.txt": CHAT, "big.bin": b"x" * 11}))
    monkeypatch.setattr(wa_parser, "ZIP_MAX_FAYL", 10_000)
    monkeypatch.setattr(wa_parser, "ZIP_MAX_RAZOM", 100)
    with pytest.raises(ArkhivVidhyleno, match="розпаковується"):
        rozibraty("chat.zip", _zip({"a.txt": CHAT, "m1.bin": b"x" * 60, "m2.bin": b"y" * 60}))


def test_spravzhni_rozpakovani_bayty_liczatsya_a_ne_zahlovok():
    """The header can lie: the reader counts the bytes it actually inflates."""
    z = zipfile.ZipFile(io.BytesIO(_zip({"a.txt": b"z" * 5000})))
    info = z.infolist()[0]
    with pytest.raises(ArkhivVidhyleno):
        wa_parser._prochytaty_obmezheno(z, info, zalyshok=100)
    assert wa_parser._prochytaty_obmezheno(z, info, zalyshok=10_000) == b"z" * 5000


def test_zip_bez_perepysky_i_z_media():
    assert rozibraty("chat.zip", _zip({"photo.jpg": b"1"})).format == "zip без .txt"
    r = rozibraty("chat.zip", _zip({"chat.txt": CHAT, "small.txt": b"x", "photo.jpg": b"img"}))
    assert len(r.povidomlennya) == 1
    assert r.media_v_arhivi == {"small.txt": b"x", "photo.jpg": b"img"}


def test_poshkodzhenyi_chlen_arkhivu_ce_vidmova_a_ne_500(monkeypatch):
    def zlamano(*a, **k):
        raise EOFError
    monkeypatch.setattr(wa_parser, "_prochytaty_obmezheno", zlamano)
    with pytest.raises(ArkhivVidhyleno, match="Пошкоджений"):
        rozibraty("chat.zip", _zip({"chat.txt": CHAT}))


def test_misyats_pershym_i_12_hodynnyi_hodynnyk():
    txt = ("8/13/26, 1:05 PM - Ann: after lunch\n"
           "8/14/26, 12:30 AM - Ann: midnight\n"
           "8/15/26, 12:10 PM - Ann: noon\n")
    r = rozibraty("chat.txt", txt.encode())
    assert not r.den_pershyi and not r.dvoznachna_data
    assert [m.when for m in r.povidomlennya] == [datetime(2026, 8, 13, 13, 5), datetime(2026, 8, 14, 0, 30),
                                                 datetime(2026, 8, 15, 12, 10)]


def test_nemozhlyva_data_i_systemni_ryadky_ne_staiut_povidomlennyamy():
    txt = ("31.02.2026, 10:00 - Петро: такої дати немає\n"
           "13.08.2026, 10:01 - Петро змінив тему групи\n"
           "13.08.2026, 10:02 - Петро: справжнє\n"
           "рядок без заголовка в самому кінці ще належить попередньому\n")
    r = rozibraty("chat.txt", txt.encode())
    assert len(r.povidomlennya) == 1 and "в самому кінці" in r.povidomlennya[0].text
    assert r.nerozibranyh == 1 and r.systemnyh >= 1


def test_nevidomyi_format_pokazuye_zrazky():
    r = rozibraty("chat.txt", "просто текст\nбез дат\n\n".encode())
    assert r.format == "невідомий" and r.nerozibranyh == 2 and r.zrazky_nerozibranyh == ["просто текст", "без дат"]


def test_zibranyi_json():
    obj = {"z_datoyu": [
        {"kod": "a", "data": "13.08.2026", "chas": "10:00", "avtor": " Богдан ", "text": "звіт.pdf (файл долучено)"},
        {"kod": "a", "data": "погана", "chas": "10:00", "text": "x"},
        {"kod": "a", "data": "13.08.2026", "chas": "10:01", "text": "  "},
        {"kod": "b", "data": "13.08.2026", "chas": "10:02", "text": "інший чат"},
    ]}
    r = wa_parser.rozibraty_json(obj, kod_chatu="a")
    assert len(r.povidomlennya) == 1 and r.povidomlennya[0].avtor == "Богдан"
    assert r.povidomlennya[0].vkladennya == ["звіт.pdf"]
    assert (r.nerozibranyh, r.systemnyh) == (1, 1)
    assert rozibraty("x.json", json.dumps(obj).encode()).format == "zibranyi-json"
    assert rozibraty("x.json", b"{broken").format == "зіпсований JSON"


def test_zvit_importu_nazyvaye_vse_propushchene():
    z = wa_import.Zvit(kimnata="Архів", nova_kimnata=True, novyh=2, vzhe_bulo=1, media_dodano=1,
                       media_vidhyleno=["run.exe"], media_bez_baytiv=3, systemnyh=4, nerozibranyh=5,
                       dvoznachna_data=True)
    t = z.yak_tekst()
    for part in ("(створено)", "нових 2", "файлів 1", "без байтів 3", "відхилено 1", "системних пропущено 4",
                 "НЕ РОЗІБРАНО 5", "недоказовий"):
        assert part in t
    assert not z.idempotentno and wa_import.Zvit().idempotentno
