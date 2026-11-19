# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Pipeline routes through HTTP: the stage gate is enforced where a person clicks.

`tests/test_voronky_gates.py` proves `gates.check` itself; this file proves the route
asks it. A stage move that skips the gate would make the pipeline report a state the
paperwork does not support, so the move is tried both ways and the stored stage checked.
"""
from datetime import date

import pytest
from sqlmodel import select

from app.models import Activity, AuditEntry, Counterparty, Deal, Document, Notification, Pipeline, Program, Site, Stage, Task
from app.seed_pipelines import seed


@pytest.fixture()
def voronka(s):
    seed(s)
    prog = s.exec(select(Program).where(Program.code == "elevator")).one()
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one()
    stages = list(s.exec(select(Stage).where(Stage.pipeline_id == pipe.id).order_by(Stage.order)))
    return prog, pipe, stages


def _deal(s, prog, pipe, stage, **kw):
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=stage.id, title=kw.pop("title", "Угода"), **kw)
    s.add(d)
    s.commit()
    s.refresh(d)
    return d


def _stage_now(s, deal_id):
    s.expire_all()
    return s.get(Deal, deal_id).stage_id


def _gated(stages):
    """First working stage that requires documents, and the next working stage after it."""
    cur = next(x for x in stages if x.required_list and not x.is_terminal)
    nxt = next(x for x in stages if x.order > cur.order and not x.is_terminal)
    return cur, nxt


def test_hejt_blokuye_perekhid_bez_pidpysanykh_dokumentiv(klient, s, voronka):
    prog, pipe, stages = voronka
    cur, nxt = _gated(stages)
    d = _deal(s, prog, pipe, cur)
    r = klient.post(f"/voronky/{d.id}/stage", data={"stage_id": nxt.id})
    assert r.headers["location"] == f"/voronky/{d.id}?gate=1"
    assert _stage_now(s, d.id) == cur.id
    assert not s.exec(select(AuditEntry).where(AuditEntry.action == "deal.stage")).first()


def test_chernetka_dokumenta_ne_vidkryvaye_hejt(klient, s, voronka):
    prog, pipe, stages = voronka
    cur, nxt = _gated(stages)
    d = _deal(s, prog, pipe, cur)
    for k in cur.required_list:                         # recorded through the deal card: a draft
        klient.post(f"/voronky/{d.id}/document", data={"kind": k, "is_final": "on", "signed_at": "2026-11-01"})
    klient.post(f"/voronky/{d.id}/stage", data={"stage_id": nxt.id})
    assert _stage_now(s, d.id) == cur.id


def test_pidpysani_dokumenty_vidkryvayut_hejt_i_stavlyat_status(klient, s, voronka):
    prog, pipe, stages = voronka
    cur, nxt = _gated(stages)
    d = _deal(s, prog, pipe, cur)
    for k in cur.required_list:
        s.add(Document(title=k, kind=k, deal_id=d.id, is_final=True, signed_at=date(2026, 11, 1)))
    s.commit()
    klient.post(f"/voronky/{d.id}/stage", data={"stage_id": nxt.id})
    s.expire_all()
    moved = s.get(Deal, d.id)
    assert moved.stage_id == nxt.id and moved.probability == nxt.default_probability
    assert moved.status == nxt.status_ugody
    assert s.exec(select(AuditEntry).where(AuditEntry.action == "deal.stage")).one().detail.endswith(nxt.title)


def test_stadiya_chuzhoyi_voronky_400(klient, s, voronka):
    prog, pipe, stages = voronka
    other = s.exec(select(Stage).where(Stage.pipeline_id != pipe.id)).first()
    d = _deal(s, prog, pipe, stages[0])
    assert klient.post(f"/voronky/{d.id}/stage", data={"stage_id": other.id}).status_code == 400
    assert _stage_now(s, d.id) == stages[0].id
    assert klient.post("/voronky/9999/stage", data={"stage_id": other.id}).headers["location"] == "/voronky"


def test_nazad_zavzhdy_mozhna(klient, s, voronka):
    prog, pipe, stages = voronka
    cur, nxt = _gated(stages)
    d = _deal(s, prog, pipe, nxt)
    klient.post(f"/voronky/{d.id}/stage", data={"stage_id": cur.id})
    assert _stage_now(s, d.id) == cur.id


def test_aktyvnist_iz_rezultatom_ruhaye_lyshe_cherez_hejt(klient, s, voronka):
    prog, pipe, stages = voronka
    cur, nxt = _gated(stages)
    cur.avto_umova, cur.avto_do_id = "aktyvnist", nxt.id
    s.add(cur)
    s.commit()
    d = _deal(s, prog, pipe, cur)
    klient.post(f"/voronky/{d.id}/activity", data={"type": "Дзвінок", "outcome": "домовились"})
    assert _stage_now(s, d.id) == cur.id                # the rule does not bypass the gate
    for k in cur.required_list:
        s.add(Document(title=k, kind=k, deal_id=d.id, is_final=True, signed_at=date(2026, 11, 1)))
    s.commit()
    klient.post(f"/voronky/{d.id}/activity", data={"type": "Нотатка", "outcome": "просто нотатка"})
    assert _stage_now(s, d.id) == cur.id                # a note is not a contact
    klient.post(f"/voronky/{d.id}/activity", data={"type": "Email", "outcome": "відповіли"})
    assert _stage_now(s, d.id) == nxt.id
    assert {a.type for a in s.exec(select(Activity))} == {"Дзвінок", "Нотатка", "Email"}
    assert klient.post("/voronky/9999/activity", data={"type": "Дзвінок"}).headers["location"] == "/voronky"


def test_nastupna_diya_i_nekorektna_data(klient, s, voronka):
    prog, pipe, stages = voronka
    d = _deal(s, prog, pipe, stages[0])
    klient.post(f"/voronky/{d.id}/next-action", data={"next_action_date": "2026-12-05", "next_action_note": " дзвінок "})
    s.expire_all()
    assert s.get(Deal, d.id).next_action_date == date(2026, 12, 5)
    assert s.get(Deal, d.id).next_action_note == "дзвінок"
    assert klient.post(f"/voronky/{d.id}/next-action", data={"next_action_date": "завтра"}).status_code == 400
    assert klient.post("/voronky/9999/next-action", data={}).headers["location"] == "/voronky"


def test_nova_uhoda_z_doruchennyam_i_bez_napryamu(klient, s, voronka):
    prog, pipe, stages = voronka
    cp = Counterparty(name="ТОВ Клієнт")
    s.add(cp)
    s.commit()
    r = klient.post("/voronky/nova", data={"program": prog.code, "counterparty_id": str(cp.id),
                                           "doruchyty_komu": "Оксана", "doruchyty_scho": "презентація",
                                           "doruchyty_koly": "2026-12-01"})
    d = s.get(Deal, int(r.headers["location"].rsplit("/", 1)[1]))
    assert d.title == "ТОВ Клієнт" and d.stage_id == stages[0].id and d.owner == "Марко"
    t = s.exec(select(Task)).one()
    assert t.assignee == "Оксана" and t.deal_id == d.id and t.due_date == date(2026, 12, 1)
    assert s.exec(select(Notification)).one().user == "Оксана"
    assert klient.post("/voronky/nova", data={"doruchyty_koly": "31.12"}).status_code == 400
    assert len(list(s.exec(select(Deal)))) == 1                   # a bad date leaves no half-deal


def test_bez_aktyvnykh_napryamiv_porozhnii_stan(klient, s, voronka):
    for p in s.exec(select(Program)):
        p.active = False
        s.add(p)
    s.commit()
    assert klient.get("/voronky").status_code == 200
    assert klient.get("/voronky/nova").headers["location"] == "/voronky"
    assert klient.post("/voronky/nova", data={"title": "x"}).headers["location"] == "/voronky"


def test_doshka_filtr_vlasnyka_i_kartka_360(klient, s, voronka):
    prog, pipe, stages = voronka
    cp = Counterparty(name="ТОВ 360")
    s.add(cp)
    s.commit()
    s.add(Site(name="Майданчик-360", site_type="biogaz", counterparty_id=cp.id))
    d = _deal(s, prog, pipe, stages[0], title="Головна-угода", owner="Оксана", counterparty_id=cp.id)
    _deal(s, prog, pipe, stages[1], title="Друга-угода", owner="Галина", counterparty_id=cp.id)
    r = klient.get("/voronky", params={"program": prog.code, "owner": "Оксана"})
    assert "Головна-угода" in r.text and "Друга-угода" not in r.text
    r = klient.get(f"/voronky/{d.id}")
    assert "Друга-угода" in r.text and "Майданчик-360" in r.text
    assert klient.get("/voronky/9999").headers["location"] == "/voronky"
    assert klient.get("/voronky/nova", params={"program": prog.code, "counterparty_id": cp.id}).status_code == 200
