"""Картка контрагента = вузол CRM, а не глухий кут довідника (25.08.2026).

Правило: з картки компанії видно її угоди, обʼєкти, документи й завдання —
дані, що вже лежать у базі за FK, але доти не рендерились.
"""
from app.models import (Counterparty, Deal, Program, Pipeline, Stage,
                        Site, Task)


def test_kartka_pokazuye_ugody_kompaniyi(s, klient):
    cp = Counterparty(name="Агрофірма", edrpou="10000001"); s.add(cp); s.commit()
    prog = Program(code="bio", title="Біо"); s.add(prog); s.commit()
    pipe = Pipeline(program_id=prog.id, code="test", title="В"); s.add(pipe); s.commit()
    st = Stage(pipeline_id=pipe.id, code="new", title="Новий", position=1)
    s.add(st); s.commit()
    d = Deal(title="Угода А", counterparty_id=cp.id, program_id=prog.id,
             pipeline_id=pipe.id, stage_id=st.id, owner="Іра", status="active")
    s.add(d); s.commit()

    html = klient.get(f"/dovidnyk/{cp.id}").text
    assert "Угода А" in html, "угода компанії не показана на картці"
    assert f"/voronky/{d.id}" in html, "немає переходу в угоду"
    assert "Новий" in html, "стадія угоди не показана"


def test_kartka_pokazuye_obyekty(s, klient):
    cp = Counterparty(name="Ферма", edrpou="10000002"); s.add(cp); s.commit()
    site = Site(site_type="majdanchyk", name="свиноферма — Ферма",
                counterparty_id=cp.id, city="с. Тест", lat=49.0, lon=25.0)
    s.add(site); s.commit()
    html = klient.get(f"/dovidnyk/{cp.id}").text
    assert "свиноферма" in html and f"/obyekty/{site.id}" in html


def test_kartka_pokazuye_zavdannya_cherez_ugodu(s, klient):
    cp = Counterparty(name="К", edrpou="10000003"); s.add(cp); s.commit()
    prog = Program(code="p", title="П"); s.add(prog); s.commit()
    pipe = Pipeline(program_id=prog.id, code="test", title="В"); s.add(pipe); s.commit()
    st = Stage(pipeline_id=pipe.id, code="n", title="Н", position=1); s.add(st); s.commit()
    d = Deal(title="У", counterparty_id=cp.id, program_id=prog.id,
             pipeline_id=pipe.id, stage_id=st.id); s.add(d); s.commit()
    s.add(Task(title="Передзвонити", deal_id=d.id, status="open", assignee="Іра"))
    s.commit()
    html = klient.get(f"/dovidnyk/{cp.id}").text
    assert "Передзвонити" in html


def test_porozhnya_kartka_proponuye_stvoryty_ugodu(s, klient):
    cp = Counterparty(name="Нова", edrpou="10000004"); s.add(cp); s.commit()
    html = klient.get(f"/dovidnyk/{cp.id}").text
    assert f"/voronky/nova?counterparty_id={cp.id}" in html
