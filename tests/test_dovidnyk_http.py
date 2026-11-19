# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Directory screens through HTTP: filters run in SQL, inline edits accept only
whitelisted fields, normalise identifiers, and a contact with no outcome is refused."""
from sqlmodel import select

from app.models import Activity, AuditEntry, Counterparty, Deal, Task
from app import fts


def _cp(s, **kw):
    cp = Counterparty(**kw)
    s.add(cp)
    s.commit()
    s.refresh(cp)
    fts.fts_upsert(s, cp)
    s.commit()
    return cp


def test_filtry_dovidnyka(klient, s):
    _cp(s, name="Перший-Агро", oblast="Львівська", raion="Сокальський", hromada="Сокальська",
        category="agro", roles="client", source="csv", data_status="ok", phone="+380001112233")
    _cp(s, name="Другий-Мовчун", oblast="Львівська", email="", phone="")
    _cp(s, name="Третій-Пошта", email="t@example.test")
    r = klient.get("/dovidnyk", params={"oblast": "Львівська", "raion": "Сокальський", "hromada": "Сокальська",
                                        "category": "agro", "role": "client", "source": "csv",
                                        "data_status": "ok", "has": "phone"})
    assert "Перший-Агро" in r.text and "Другий-Мовчун" not in r.text
    r = klient.get("/dovidnyk", params={"has": "email"})
    assert "Третій-Пошта" in r.text and "Перший-Агро" not in r.text
    r = klient.get("/dovidnyk", params={"has": "no_contact"})
    assert "Другий-Мовчун" in r.text and "Третій-Пошта" not in r.text


def test_poshuk_bez_zbihiv_ne_pokazuye_vsikh(klient, s):
    _cp(s, name="Єдиний-Контрагент")
    r = klient.get("/dovidnyk", params={"q": "абвгдежзнеіснує"})
    assert r.status_code == 200 and "Єдиний-Контрагент" not in r.text
    assert "Єдиний-Контрагент" in klient.get("/dovidnyk", params={"q": "Єдиний"}).text


def test_aktyvnist_bez_rezultatu_vidkhylyayetsya(klient, s):
    cp = _cp(s, name="ТОВ Дзвінок")
    r = klient.post(f"/dovidnyk/{cp.id}/activity", data={"type": "Дзвінок", "outcome": "  "})
    assert r.headers["location"].endswith("err=outcome")
    klient.post(f"/dovidnyk/{cp.id}/activity", data={"type": "Телепатія", "outcome": "передзвонять"})
    a = s.exec(select(Activity)).one()
    assert a.type == "Нотатка" and a.counterparty_id == cp.id
    assert klient.post("/dovidnyk/9999/activity", data={"outcome": "x"}).headers["location"] == "/dovidnyk"


def test_kartka_pokazuye_zavdannya_i_aktyvnosti_cherez_uhody(klient, s):
    cp = _cp(s, name="ТОВ Повна")
    d = Deal(title="Угода-повна", counterparty_id=cp.id, program_id=1, pipeline_id=1, stage_id=1)
    s.add(d)
    s.commit()
    s.add(Task(title="Передзвонити-по-угоді", deal_id=d.id))
    s.add(Activity(type="Дзвінок", deal_id=d.id, outcome="Домовились-про-зустріч"))
    s.commit()
    r = klient.get(f"/dovidnyk/{cp.id}")
    assert "Передзвонити-по-угоді" in r.text and "Домовились-про-зустріч" in r.text
    assert klient.get("/dovidnyk/9999").headers["location"] == "/dovidnyk"


def test_pravka_polya_lyshe_z_bilogo_spysku(klient, s):
    cp = _cp(s, name="ТОВ Поле")
    assert klient.post(f"/dovidnyk/{cp.id}/field", data={"field": "id", "value": "5"}).json()["ok"] is False
    assert klient.post(f"/dovidnyk/{cp.id}/field", data={"field": "created_by", "value": "x"}).json()["ok"] is False
    assert klient.post("/dovidnyk/9999/field", data={"field": "name", "value": "x"}).json()["ok"] is False


def test_pravka_normalizuye_i_audytuye(klient, s):
    cp = _cp(s, name="ТОВ Норма")
    r = klient.post(f"/dovidnyk/{cp.id}/field", data={"field": "phone", "value": "000 111 22 33"}).json()
    assert r == {"ok": True, "value": "+380001112233", "changed": True}
    r = klient.post(f"/dovidnyk/{cp.id}/field", data={"field": "phone", "value": "+380001112233"}).json()
    assert r["changed"] is False
    r = klient.post(f"/dovidnyk/{cp.id}/field", data={"field": "website", "value": " https://agro.example "}).json()
    assert r["value"] == "https://agro.example"
    r = klient.post(f"/dovidnyk/{cp.id}/field", data={"field": "edrpou", "value": "1234567"}).json()
    assert r["value"] == "01234567"
    assert len(list(s.exec(select(AuditEntry).where(AuditEntry.action == "counterparty.edit")))) == 3


def test_hronka_edrpou_vidlovlena_indeksom_bazy(klient, s, monkeypatch):
    """Two edits racing for one tax code: the database index is the last line of defence."""
    _cp(s, name="А", edrpou="12345678")
    b = _cp(s, name="Б")
    from sqlalchemy import text

    from app.web import dovidnyk
    s.exec(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_cp_edrpou ON counterparty(edrpou) WHERE edrpou != ''"))
    s.commit()
    # Simulate the race: the application-level pre-check sees no other holder of the code.
    real_select = dovidnyk.select
    monkeypatch.setattr(dovidnyk, "select",
                        lambda *a: real_select(*a).where(Counterparty.id == -1) if len(a) == 1 and a[0] is Counterparty.id else real_select(*a))
    r = klient.post(f"/dovidnyk/{b.id}/field", data={"field": "edrpou", "value": "12345678"}).json()
    assert r["ok"] is False and "ЄДРПОУ" in r["error"]
    s.expire_all()
    assert s.get(Counterparty, b.id).edrpou == ""
