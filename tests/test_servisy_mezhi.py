# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Service-level edges: the automatic stage rule may never move a deal the gate would
stop, nor into another pipeline; who counts as "involved" in an entity; and the
"what changed since you were here" summary counts other people's work only."""
from datetime import timedelta

import pytest
from sqlmodel import select

from app import audit
from app.models import AuditEntry, Deal, Document, Pipeline, Program, Site, Stage, Task, utcnow
from app.seed_pipelines import seed
from app.services import collab, gates


@pytest.fixture()
def voronka(s):
    seed(s)
    prog = s.exec(select(Program).where(Program.code == "elevator")).one()
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one()
    stages = list(s.exec(select(Stage).where(Stage.pipeline_id == pipe.id).order_by(Stage.order)))
    cur = next(x for x in stages if x.required_list and not x.is_terminal)
    nxt = next(x for x in stages if x.order > cur.order and not x.is_terminal)
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=cur.id, title="Авто")
    s.add(d)
    s.commit()
    return d, cur, nxt


def _pidpysaty(s, deal, kinds):
    for k in kinds:
        s.add(Document(kind=k, deal_id=deal.id, is_final=True, signed_at=utcnow().date()))
    s.commit()


def test_prychyna_vidmovy_hejtu_nazyvaye_dokumenty():
    assert gates.GateResult(True, []).reason == ""
    assert "KP, LOI" in gates.GateResult(False, ["KP", "LOI"]).reason


def test_bez_pravyla_uhoda_stoyit(s, voronka):
    d, cur, nxt = voronka
    _pidpysaty(s, d, cur.required_list)
    assert gates.avtoperekhid(s, d, "dokument", "Марко") is None          # rule not set on this stage
    cur.avto_umova, cur.avto_do_id = "aktyvnist", nxt.id
    s.add(cur)
    s.commit()
    assert gates.avtoperekhid(s, d, "dokument", "Марко") is None          # a different event


def test_pravylo_ne_vede_v_chuzhu_voronku_chy_na_sebe(s, voronka):
    d, cur, nxt = voronka
    _pidpysaty(s, d, cur.required_list)
    chuzha = s.exec(select(Stage).where(Stage.pipeline_id != d.pipeline_id)).first()
    for target in (chuzha.id, cur.id, 999_999):
        cur.avto_umova, cur.avto_do_id = "dokument", target
        s.add(cur)
        s.commit()
        assert gates.avtoperekhid(s, d, "dokument", "Марко") is None
    assert s.get(Deal, d.id).stage_id == cur.id


def test_pravylo_dokument_chekaye_vsi_obovyazkovi(s, voronka):
    d, cur, nxt = voronka
    cur.avto_umova, cur.avto_do_id = "dokument", nxt.id
    s.add(cur)
    s.commit()
    _pidpysaty(s, d, cur.required_list[:-1])
    assert gates.avtoperekhid(s, d, "dokument", "Марко") is None
    _pidpysaty(s, d, cur.required_list[-1:])
    assert gates.avtoperekhid(s, d, "dokument", "Марко").id == nxt.id
    assert s.exec(select(AuditEntry).where(AuditEntry.action == "deal.stage.auto")).one().actor == "Марко"


def test_pravylo_dokument_bez_vymoh_ne_zdohaduyetsya(s, voronka):
    d, cur, nxt = voronka
    cur.required_doc_kinds = ""
    cur.avto_umova, cur.avto_do_id = "dokument", nxt.id
    s.add(cur)
    s.commit()
    assert gates.avtoperekhid(s, d, "dokument", "Марко") is None


def test_pravylo_aktyvnist_ne_obkhodyt_hejt(s, voronka):
    d, cur, nxt = voronka
    cur.avto_umova, cur.avto_do_id = "aktyvnist", nxt.id
    s.add(cur)
    s.commit()
    assert gates.avtoperekhid(s, d, "aktyvnist", "Марко") is None          # documents still missing
    assert s.get(Deal, d.id).stage_id == cur.id


# ── who is involved in an entity ────────────────────────────────────────────

def test_prychetni_do_sutnosti_bez_importu_i_pravyla(s):
    d = Deal(title="x", owner="Оксана", created_by="import", program_id=1, pipeline_id=1, stage_id=1)
    doc = Document(kind="KP", created_by="Богдан", uploaded_by="Галина")
    t = Task(title="t", assignee="Галина", created_by="правило")
    site = Site(name="s", site_type="biogaz_object", created_by="Марко")
    for x in (d, doc, t, site):
        s.add(x)
    s.commit()
    assert collab.owners_of(s, "deal", d.id) == ["Оксана"]
    assert collab.owners_of(s, "document", doc.id) == ["Богдан", "Галина"]
    assert collab.owners_of(s, "task", t.id) == ["Галина"]
    assert collab.owners_of(s, "site", site.id) == ["Марко"]
    assert collab.owners_of(s, "deal", 9999) == []
    assert collab.owners_of(s, "airun", 1) == []


# ── what changed since the last visit ───────────────────────────────────────

def test_zminy_rakhuyut_lyshe_chuzhe_i_vazhlyve(s):
    vid = utcnow() - timedelta(hours=1)
    assert collab.zminy_vid(s, "Марко", None).pershyi_vhid
    assert not collab.zminy_vid(s, "Марко", vid).je
    audit.log(s, "Оксана", "task.create", entity="task", entity_id=1)
    audit.log(s, "Оксана", "deal.stage", entity="deal", entity_id=1)
    audit.log(s, "Оксана", "approval.decide", entity="document", entity_id=1)
    audit.log(s, "Оксана", "settings.update", entity="setting")          # too small for the summary
    audit.log(s, "Марко", "task.create", entity="task", entity_id=2)     # own work is not news
    s.commit()
    z = collab.zminy_vid(s, "Марко", vid)
    assert z.je and z.vsoho == 3
    assert {(p.kod, p.skilky, p.url) for p in z.punkty} == {
        ("zavdannya", 1, "/zavdannya"), ("ugody", 1, "/voronky"), ("rishennya", 1, "/zatverdzhennya")}


def test_strichka_sutnosti_i_rishennya(s):
    audit.log(s, "Оксана", "approval.decide", entity="document", entity_id=5)
    audit.log(s, "Оксана", "comment.create", entity="document", entity_id=6)
    s.commit()
    items = collab.feed(s, entity="document", entity_id=5)
    assert len(items) == 1 and items[0].is_decision
    assert len(collab.feed(s, entity="document")) == 2
