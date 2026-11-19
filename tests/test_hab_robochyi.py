# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Хаб (кластер) — робоча сутність, не лише перегляд (25.08.2026).

Правило: стадію розвитку хабу й власника можна змінити з картки, зміна
доходить до БАЗИ, канон стадій дотримано, і завдання хабу видно на картці.
"""
from app.models import Cluster, Task, CLUSTER_STAGES


def _hab(s, **kw):
    p = dict(site_code="HUB-1", type="CLUSTER_CHP", oblast="Хмельницька",
             total_mw=4.0, stage="Новий")
    p.update(kw)
    c = Cluster(**p); s.add(c); s.commit()
    return c


def test_zmina_stadiyi_dohodyt_do_bazy(s, klient):
    c = _hab(s)
    r = klient.post(f"/haby/{c.id}/stan",
                    data={"stage": "Pre-FS", "owner": "Богдан"})
    assert r.status_code in (302, 303)
    s.refresh(c)
    assert c.stage == "Pre-FS" and c.owner == "Богдан"


def test_stadiya_poza_kanonom_ignoruyetsya(s, klient):
    """Канон CLUSTER_STAGES: чуже значення не записується (Г22 — мовчазний
    дефолт на неохайному вході неприпустимий)."""
    c = _hab(s, stage="Новий")
    klient.post(f"/haby/{c.id}/stan", data={"stage": "вигадана", "owner": ""})
    s.refresh(c)
    assert c.stage == "Новий"


def test_kartka_pokazuye_zavdannya_habu(s, klient):
    c = _hab(s)
    s.add(Task(title="Підготувати Pre-FS", cluster_id=c.id, status="open",
               assignee="Богдан"))
    s.commit()
    html = klient.get(f"/haby/{c.id}").text
    assert "Підготувати Pre-FS" in html


def test_kartka_daye_selektor_stadiy(s, klient):
    c = _hab(s)
    html = klient.get(f"/haby/{c.id}").text
    for st in CLUSTER_STAGES:
        assert st in html, f"стадія {st} відсутня в селекторі"
