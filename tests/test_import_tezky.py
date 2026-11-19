# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Import without a tax code: the name key may merge only records that do not contradict
each other. Common farm names ("Маяк", "Нива") in different places are different firms."""
from sqlmodel import select

from app.importers.common import Index, Stats, save_conflicts, upsert, upsert_person
from app.models import Counterparty, ImportConflict, ImportRun, Person


def _cp(s, **kw):
    cp = Counterparty(**kw)
    s.add(cp)
    s.commit()
    s.refresh(cp)
    return cp


def test_nazva_misto_oblast_zbihayutsya_ce_toi_samyi(s):
    old = _cp(s, name="ФГ Маяк", oblast="Тернопільська", city="Бучач")
    st = Stats()
    got = upsert(s, Index(s), {"name": "ФГ «Маяк»", "oblast": "Тернопільська", "city": "м. Бучач",
                               "email": "mayak@example.test"}, "csv", st)
    assert got.id == old.id and st.merged == 1 and got.email == "mayak@example.test"


def test_tezka_z_inshoyi_oblasti_ne_zlyvayetsya(s):
    _cp(s, name="ФГ Нива", oblast="Львівська")
    st = Stats()
    got = upsert(s, Index(s), {"name": "ФГ Нива", "oblast": "Волинська"}, "csv", st)
    assert st.new == 1 and len(list(s.exec(select(Counterparty)))) == 2 and got.oblast == "Волинська"


def test_tezka_z_inshoho_mista_tiyeyi_zh_oblasti_ne_zlyvayetsya(s):
    _cp(s, name="ФГ Нива", oblast="Львівська", city="Сокаль")
    st = Stats()
    upsert(s, Index(s), {"name": "ФГ Нива", "oblast": "Львівська", "city": "Жовква"}, "csv", st)
    assert st.new == 1


def test_bez_imeni_ne_porozhnii_zapys(s):
    st = Stats()
    cp = upsert(s, Index(s), {"name": "", "edrpou": "12345670"}, "csv", st)
    assert cp.name == "—" and cp.edrpou == "12345670"


def test_konflikty_zberihayutsya_dlya_lyudyny(s):
    old = _cp(s, name="ТОВ Агро", edrpou="12345678", phone="+380001112233")
    st = Stats()
    upsert(s, Index(s), {"name": "ТОВ Агро", "edrpou": "12345678", "phone": "0009998877"}, "csv", st)
    s.refresh(old)
    assert old.phone == "+380001112233"                   # the curated value stays
    assert st.as_dict()["rows_conflict"] == 1
    run = ImportRun(source_code="csv")
    s.add(run)
    s.commit()
    save_conflicts(s, run.id, st)
    s.commit()
    c = s.exec(select(ImportConflict)).one()
    assert (c.field, c.existing_value, c.import_run_id) == ("phone", "+380001112233", run.id)
    st.dodaty("sites")
    st.dodaty("sites", 2)
    assert st.extra == {"sites": 3} and "sites" not in st.as_dict()


def test_osoby_dedup_za_imenem_telefonom_email(s):
    cp = _cp(s, name="ТОВ Люди")
    first = upsert_person(s, cp, {"name": "Іван Коваль", "phone": "000 111 22 33"})
    s.commit()
    assert upsert_person(s, cp, {"name": "", "phone": "", "email": ""}) is None
    assert upsert_person(s, cp, {"name": "іван коваль"}).id == first.id
    assert upsert_person(s, cp, {"name": "І. Коваль", "phone": "+380001112233"}).id == first.id
    second = upsert_person(s, cp, {"name": "Олена", "email": "olena@example.test"})
    s.commit()
    assert upsert_person(s, cp, {"name": "О. П.", "email": "OLENA@example.test"}).id == second.id
    assert len(list(s.exec(select(Person)))) == 2
