# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Programme editor edges: a stage of one programme cannot be edited or removed through
another programme's URL, an automatic rule cannot point into a foreign pipeline, and the
programme code never changes after creation (templates and importers refer to it)."""
import pytest
from sqlmodel import select

from app.models import AuditEntry, Program, Stage
from app.seed_pipelines import seed
from tests.test_napryamy import _novyi, _pipe, _st, _stadiya


@pytest.fixture()
def baza(s):
    seed(s)
    s.commit()
    return s


def test_nazva_i_kod_obovyazkovi(baza, klient):
    assert _novyi(klient, nazva="  ").headers["location"].endswith("err=nazva")
    assert _novyi(klient, nazva="***", kod="!!!").headers["location"].endswith("err=kod")


def test_kopiya_nevidomoho_napryamu_daye_odnu_stadiyu(baza, klient):
    _novyi(klient, nazva="Копія нізвідки", kod="kopiya_x", dzherelo="kopiya", kopiya_id="9999")
    assert [st.code for st in _st(baza, "kopiya_x")] == ["novyi"]


def test_redaktor_i_pravka_nevidomoho_napryamu(baza, klient):
    for path in ("/napryamy/nemaye", ):
        assert klient.get(path).headers["location"] == "/napryamy"
    for path in ("/napryamy/nemaye/napryam", "/napryamy/nemaye/arhiv", "/napryamy/nemaye/stadiya",
                 "/napryamy/nemaye/stadiya/1", "/napryamy/nemaye/stadiya/1/porydok",
                 "/napryamy/nemaye/stadiya/1/prybraty"):
        assert klient.post(path, data={}).headers["location"] == "/napryamy", path


def test_kod_napryamu_ne_zminyuyetsya(baza, klient):
    klient.post("/napryamy/biogaz/napryam", data={"nazva": "Біоенергетика 2", "kod": "inshyi",
                                                 "model_balu": "vigadana"})
    prog, _ = _pipe(baza, "biogaz")
    assert prog.title == "Біоенергетика 2" and prog.model_balu == ""
    assert baza.exec(select(Program).where(Program.code == "inshyi")).first() is None


def test_stadiyu_chuzhoho_napryamu_ne_redahuvaty_i_ne_prybraty(baza, klient):
    chuzha = _st(baza, "elevator")[0]
    title = chuzha.title
    r = _stadiya(klient, "biogaz", chuzha, title="Підміна")
    assert r.headers["location"] == "/napryamy"
    r = klient.post(f"/napryamy/biogaz/stadiya/{chuzha.id}/prybraty", data={"confirm": "yes"})
    assert r.headers["location"] == "/napryamy"
    baza.expire_all()
    assert baza.get(Stage, chuzha.id).title == title


def test_avtoperekhid_u_chuzhu_voronku_vidkynuto(baza, klient):
    st = _st(baza, "biogaz")[0]
    chuzha = _st(baza, "elevator")[1]
    _stadiya(klient, "biogaz", st, avto_umova="aktyvnist", avto_do_id=str(chuzha.id))
    baza.expire_all()
    assert baza.get(Stage, st.id).avto_do_id is None
    _stadiya(klient, "biogaz", st, avto_umova="aktyvnist", avto_do_id=str(st.id))     # not onto itself
    baza.expire_all()
    assert baza.get(Stage, st.id).avto_do_id is None


def test_jmovirnist_obmezhena_a_smittya_ignorovane(baza, klient):
    st = _st(baza, "biogaz")[0]
    _stadiya(klient, "biogaz", st, default_probability="250")
    baza.expire_all()
    assert baza.get(Stage, st.id).default_probability == 100
    _stadiya(klient, "biogaz", st, default_probability="багато")
    baza.expire_all()
    assert baza.get(Stage, st.id).default_probability == 100
    assert baza.exec(select(AuditEntry).where(AuditEntry.action == "stage.edit")).first()


def test_kod_novoyi_stadiyi_unikalnyi(baza, klient):
    st = _st(baza, "biogaz")[0]
    r = klient.post("/napryamy/biogaz/stadiya", data={"title": "Дубль", "stage_kod": st.code})
    assert r.headers["location"].endswith("err=kod_zaynyato")


def test_perestanovka_na_krayakh_i_nevidoma_stadiya(baza, klient):
    st = _st(baza, "biogaz")
    before = [x.id for x in st]
    klient.post(f"/napryamy/biogaz/stadiya/{st[0].id}/porydok", data={"kudy": "vgoru"})
    klient.post(f"/napryamy/biogaz/stadiya/{st[-1].id}/porydok", data={"kudy": "vnyz"})
    r = klient.post("/napryamy/biogaz/stadiya/999999/porydok", data={"kudy": "vnyz"})
    assert r.headers["location"] == "/napryamy/biogaz"
    assert [x.id for x in _st(baza, "biogaz")] == before
