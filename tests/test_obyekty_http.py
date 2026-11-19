# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Site screens through HTTP: the questionnaire keeps "no data" apart from zero, and the
stored score follows the criteria (saved decomposed, removed when nothing is left)."""
from sqlmodel import select

from app.models import AuditEntry, Cluster, ClusterMember, Counterparty, Deal, Score, ScoreComponent, Site, Task


def _hromada(s, **kw):
    site = Site(name=kw.pop("name", "Громада-Тест"), site_type="hromada_lokatsiya", **kw)
    s.add(site)
    s.commit()
    s.refresh(site)
    return site


def _score(s, site_id):
    return s.exec(select(Score).where(Score.site_id == site_id, Score.model == "hromada_screening")).first()


def test_anketa_porozhnie_pole_ce_nemaye_danykh_a_ne_nul(klient, s):
    site = _hromada(s)
    klient.post(f"/obyekty/{site.id}/kryterii", data={"c1_grain_t_yr": "120 000".replace(" ", ""),
                                                     "c4_rail": "1", "c6_power_mw": "2,5"})
    s.expire_all()
    site = s.get(Site, site.id)
    assert site.c1_grain_t_yr == 120000 and site.c6_power_mw == 2.5
    assert site.c2_underserved_ha is None and site.c9_transparency is None        # not asked: None, not 0
    sc = _score(s, site.id)
    assert sc is not None
    assert s.exec(select(ScoreComponent).where(ScoreComponent.score_id == sc.id)).first() is not None


def test_smittya_v_poli_ignoruyetsya_a_ne_staye_nulem(klient, s):
    site = _hromada(s, c1_grain_t_yr=50000)
    klient.post(f"/obyekty/{site.id}/kryterii", data={"c1_grain_t_yr": "багато"})
    s.expire_all()
    assert s.get(Site, site.id).c1_grain_t_yr == 50000


def test_sterty_vse_prybyraye_bal_a_ne_zberihaye_nul(klient, s):
    site = _hromada(s)
    klient.post(f"/obyekty/{site.id}/kryterii", data={"c1_grain_t_yr": "90000", "c4_rail": "1"})
    assert _score(s, site.id) is not None
    klient.post(f"/obyekty/{site.id}/kryterii", data={})
    s.expire_all()
    assert _score(s, site.id) is None
    assert len(list(s.exec(select(AuditEntry).where(AuditEntry.action == "site.criteria")))) == 2


def test_bez_zmin_nichoho_ne_zapysuye(klient, s):
    site = _hromada(s)
    klient.post(f"/obyekty/{site.id}/kryterii", data={})
    assert s.exec(select(AuditEntry)).first() is None
    assert klient.post("/obyekty/9999/kryterii", data={}).headers["location"] == "/obyekty"


def test_status_obdzvonu_lyshe_z_kanonu(klient, s):
    site = _hromada(s)
    klient.post(f"/obyekty/{site.id}/status", data={"member_status": "Зацікавлений"})
    klient.post(f"/obyekty/{site.id}/status", data={"member_status": "Вигадка"})
    s.expire_all()
    assert s.get(Site, site.id).member_status == "Зацікавлений"
    assert len(list(s.exec(select(AuditEntry).where(AuditEntry.action == "site.status")))) == 1


def test_spysok_filtry_i_sortuvannya(klient, s):
    s.add(Site(name="Альфа-обʼєкт", site_type="biogaz_object", oblast="Львівська", est_capacity_kw_el=900,
               member_status="Новий", cluster_id=None))
    s.add(Site(name="Бета-обʼєкт", site_type="biogaz_object", oblast="Тернопільська", est_capacity_kw_el=100))
    s.commit()
    r = klient.get("/obyekty", params={"q": "Альфа", "oblast": "Львівська", "member_status": "Новий"})
    assert "Альфа-обʼєкт" in r.text and "Бета-обʼєкт" not in r.text
    for sort in ("name", "kw", "score"):
        assert klient.get("/obyekty", params={"sort": sort, "page": 0}).status_code == 200
    assert klient.get("/obyekty", params={"cluster_id": 77}).status_code == 200


def test_kartka_obyekta_z_uhodamy_kontrahenta(klient, s):
    cp = Counterparty(name="ТОВ Власник")
    s.add(cp)
    s.commit()
    cl = Cluster(site_code="Хаб-1", oblast="Львівська")
    s.add(cl)
    s.commit()
    site = Site(name="Обʼєкт-К", site_type="biogaz_object", counterparty_id=cp.id, cluster_id=cl.id)
    s.add(site)
    s.add(Deal(title="Угода-власника", counterparty_id=cp.id, program_id=1, pipeline_id=1, stage_id=1))
    s.commit()
    r = klient.get(f"/obyekty/{site.id}")
    assert "Угода-власника" in r.text and "Хаб-1" in r.text
    assert klient.get("/obyekty/9999").headers["location"] == "/obyekty"
    tip_bez_modeli = Site(name="Парк", site_type="industrialnyi_park")
    s.add(tip_bez_modeli)
    s.commit()
    assert klient.get(f"/obyekty/{tip_bez_modeli.id}").status_code == 200


def test_haby_kartka_i_stan(klient, s):
    cl = Cluster(site_code="Хаб-Захід", oblast="Волинська", total_mw=3.0)
    s.add(cl)
    s.commit()
    site = Site(name="Член-хабу", site_type="biogaz_object")
    s.add(site)
    s.commit()
    s.add(ClusterMember(cluster_id=cl.id, site_id=site.id))
    s.add(ClusterMember(cluster_id=cl.id, site_id=None))
    s.add(Task(title="Завдання-хабу", cluster_id=cl.id))
    s.commit()
    assert "Хаб-Захід" in klient.get("/haby", params={"oblast": "Волинська"}).text
    r = klient.get(f"/haby/{cl.id}")
    assert "Член-хабу" in r.text and "Завдання-хабу" in r.text
    assert klient.get("/haby/9999").headers["location"] == "/haby"
    klient.post(f"/haby/{cl.id}/stan", data={"stage": "Pre-FS", "owner": " Оксана "})
    klient.post(f"/haby/{cl.id}/stan", data={"stage": "Вигадка", "owner": "Оксана"})
    s.expire_all()
    assert (s.get(Cluster, cl.id).stage, s.get(Cluster, cl.id).owner) == ("Pre-FS", "Оксана")
    assert klient.post("/haby/9999/stan", data={}).headers["location"] == "/haby"
