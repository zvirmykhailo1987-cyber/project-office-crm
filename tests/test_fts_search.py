"""Пошук довідника: кирилиця, регістр, префікс, пошук по особах (ТЗ §4b)."""
from app.fts import fts_upsert, rebuild_all, search_ids
from app.models import Counterparty, Person


def _seed(s):
    cps = [
        Counterparty(name="ТОВ «Нива»", edrpou="12345678", oblast="Тернопільська",
                     city="Чортків", hromada="Білобожницька"),
        Counterparty(name="ПАП Зоряна Долина", edrpou="99758892", oblast="Тернопільська",
                     city="Білобожниця"),
        Counterparty(name="Стрийський молокозавод", oblast="Львівська", city="Стрий"),
    ]
    for cp in cps:
        s.add(cp)
    s.commit()
    s.add(Person(counterparty_id=cps[0].id, name="Іван Коваль", role="Директор",
                 phone="+380001112233"))
    s.commit()
    rebuild_all(s)
    s.commit()
    return cps


def test_search_by_name_case_insensitive(s):
    cps = _seed(s)
    assert cps[0].id in search_ids(s, "нива")
    assert cps[0].id in search_ids(s, "НИВА")


def test_search_by_edrpou(s):
    cps = _seed(s)
    assert search_ids(s, "99758892") == [cps[1].id]


def test_search_by_prefix(s):
    cps = _seed(s)
    assert cps[2].id in search_ids(s, "молоко")


def test_search_by_city_and_hromada(s):
    cps = _seed(s)
    assert cps[0].id in search_ids(s, "чортків")
    assert cps[0].id in search_ids(s, "білобожницька")


def test_search_by_person_name(s):
    cps = _seed(s)
    assert cps[0].id in search_ids(s, "коваль")


def test_empty_query_returns_nothing(s):
    _seed(s)
    assert search_ids(s, "") == []
    assert search_ids(s, "   ") == []


def test_index_updates_after_edit(s):
    cps = _seed(s)
    cp = s.get(Counterparty, cps[2].id)
    cp.name = "Стрийський сирзавод"
    s.add(cp)
    s.commit()
    fts_upsert(s, cp)
    s.commit()
    assert cps[2].id in search_ids(s, "сирзавод")
    assert cps[2].id not in search_ids(s, "молокозавод")


def test_special_chars_do_not_break_query(s):
    _seed(s)
    for q in ['ТОВ "Нива"', "нива-2", "а.б.в", "*", '"']:
        search_ids(s, q)   # не має падати
