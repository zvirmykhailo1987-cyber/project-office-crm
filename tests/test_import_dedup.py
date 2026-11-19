# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Дедуп і ідемпотентність імпорту — головна вимога Ф1 (ТЗ, Додаток В §2)."""
from sqlmodel import select

from app.importers.common import Index, Stats, upsert, upsert_person
from app.models import Counterparty, Person


def _rows():
    return [
        {"name": "ТОВ «Нива»", "edrpou": "12345678", "oblast": "Тернопільська",
         "city": "Чортків", "phone": "0001112233"},
        {"name": "Приватне підприємство Зоряна Долина", "edrpou": "99758892",
         "oblast": "Тернопільська", "city": "Білобожниця"},
        {"name": "ФГ Маяк", "oblast": "Львівська", "city": "Стрий"},
    ]


def test_edrpou_is_primary_key(s):
    idx, st = Index(s), Stats()
    upsert(s, idx, {"name": "Нива", "edrpou": "12345678"}, "a", st)
    # та сама юрособа під іншою назвою, але з тим самим ЄДРПОУ — це ОДИН запис
    upsert(s, idx, {"name": "ТОВ НИВА", "edrpou": "12345678"}, "b", st)
    s.commit()
    assert st.new == 1 and st.unchanged + st.merged == 1


def test_edrpou_normalised_before_match(s):
    idx, st = Index(s), Stats()
    upsert(s, idx, {"name": "Нива", "edrpou": "992829"}, "a", st)
    upsert(s, idx, {"name": "Нива", "edrpou": "00992829"}, "b", st)
    s.commit()
    assert st.new == 1


def test_different_edrpou_never_merged(s):
    """Однакова назва в різних областях — РІЗНІ підприємства."""
    idx, st = Index(s), Stats()
    upsert(s, idx, {"name": "Маяк", "edrpou": "11111111"}, "a", st)
    upsert(s, idx, {"name": "Маяк", "edrpou": "22222222"}, "a", st)
    s.commit()
    assert st.new == 2


def test_fill_empty_does_not_overwrite_curated(s):
    idx, st = Index(s), Stats()
    upsert(s, idx, {"name": "Нива", "edrpou": "12345678",
                    "phone": "+380001112233", "city": ""}, "a", st)
    upsert(s, idx, {"name": "Нива", "edrpou": "12345678",
                    "phone": "+380001000000", "city": "Чортків"}, "b", st)
    s.commit()
    cp = s.exec(select(Counterparty)).one()
    assert cp.phone == "+380001112233"   # куроване не затерто
    assert cp.city == "Чортків"          # порожнє заповнено
    assert st.conflicts and st.conflicts[0]["field"] == "phone"


def test_sources_are_merged_as_set(s):
    idx, st = Index(s), Stats()
    upsert(s, idx, {"name": "Нива", "edrpou": "12345678"}, "a", st)
    upsert(s, idx, {"name": "Нива", "edrpou": "12345678"}, "b", st)
    s.commit()
    assert s.exec(select(Counterparty)).one().source == "a,b"


def test_repeat_import_is_idempotent(s):
    """Повторний прогін не створює й не змінює нічого (критерій приймання Ф1)."""
    idx = Index(s)
    st1 = Stats()
    for i, r in enumerate(_rows()):
        upsert(s, idx, r, "src", st1, src_uid=f"src:{i}")
    s.commit()

    idx2, st2 = Index(s), Stats()
    for i, r in enumerate(_rows()):
        upsert(s, idx2, r, "src", st2, src_uid=f"src:{i}")
    s.commit()

    assert st1.new == 3
    assert (st2.new, st2.merged) == (0, 0)
    assert st2.unchanged == 3


def test_ambiguous_name_inserts_new_not_wrong_merge(s):
    """Кілька кандидатів на ключ назви → новий запис, а не злиття навмання."""
    idx, st = Index(s), Stats()
    upsert(s, idx, {"name": "Маяк", "oblast": "Львівська", "city": "Стрий"}, "a", st)
    upsert(s, idx, {"name": "Маяк", "oblast": "Львівська", "city": "Дрогобич"}, "a", st)
    s.commit()
    # третій «Маяк» тієї ж області без міста — неоднозначний
    upsert(s, idx, {"name": "Маяк", "oblast": "Львівська"}, "a", st)
    s.commit()
    assert st.new == 3


def test_persons_deduped_within_counterparty(s):
    idx, st = Index(s), Stats()
    cp = upsert(s, idx, {"name": "Нива", "edrpou": "12345678"}, "a", st)
    s.commit()
    upsert_person(s, cp, {"name": "Іван Коваль", "phone": "0001112233"})
    upsert_person(s, cp, {"name": "Іван Коваль", "phone": "0001112233"})
    upsert_person(s, cp, {"name": "Марія Стець"})
    s.commit()
    assert len(s.exec(select(Person)).all()) == 2
