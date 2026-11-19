# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""360° на картках угоди й обʼєкта (25.08.2026) — консистентність із карткою
компанії. Правило: з угоди видно інші угоди + обʼєкти контрагента; з обʼєкта
видно угоди контрагента (щоб не заводити дубль)."""
from app.models import Counterparty, Deal, Program, Pipeline, Stage, Site


def _seed(s):
    cp = Counterparty(name="Агро", edrpou="30000001"); s.add(cp); s.commit()
    prog = Program(code="b", title="Б"); s.add(prog); s.commit()
    pipe = Pipeline(program_id=prog.id, code="p", title="В"); s.add(pipe); s.commit()
    st = Stage(pipeline_id=pipe.id, code="n", title="Новий", order=0); s.add(st); s.commit()
    d1 = Deal(title="Угода-1", counterparty_id=cp.id, program_id=prog.id,
              pipeline_id=pipe.id, stage_id=st.id, status="active")
    d2 = Deal(title="Угода-2", counterparty_id=cp.id, program_id=prog.id,
              pipeline_id=pipe.id, stage_id=st.id, status="active")
    s.add(d1); s.add(d2); s.commit()
    site = Site(site_type="majdanchyk", name="свиноферма — Агро",
                counterparty_id=cp.id, city="с.Т", lat=49.0, lon=25.0)
    s.add(site); s.commit()
    return cp, d1, d2, site


def test_kartka_ugody_pokazuye_inshi_ugody_i_obyekty(s, klient):
    cp, d1, d2, site = _seed(s)
    html = klient.get(f"/voronky/{d1.id}").text
    assert "Інші угоди контрагента" in html
    assert f"/voronky/{d2.id}" in html, "інша угода контрагента не показана"
    assert "Обʼєкти контрагента" in html and f"/obyekty/{site.id}" in html


def test_kartka_obyekta_pokazuye_ugody(s, klient):
    cp, d1, d2, site = _seed(s)
    html = klient.get(f"/obyekty/{site.id}").text
    assert f"/voronky/{d1.id}" in html, "угода контрагента не показана на обʼєкті"


def test_obyekt_bez_ugod_proponuye_zavesty(s, klient):
    cp = Counterparty(name="Сам", edrpou="30000002"); s.add(cp); s.commit()
    site = Site(site_type="biogaz_object", name="точка", counterparty_id=cp.id,
                lat=49.0, lon=25.0); s.add(site); s.commit()
    html = klient.get(f"/obyekty/{site.id}").text
    assert f"/voronky/nova?counterparty_id={cp.id}" in html
