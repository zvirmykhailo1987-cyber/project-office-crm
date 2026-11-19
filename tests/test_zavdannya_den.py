"""Завдання, «мій день» під роль, KPI (ТЗ §3 О8, Додаток Б §6)."""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app.models import (
    Deal,
    Document,
    Outreach,
    Pipeline,
    Program,
    Stage,
    Task,
    User,
    utcnow,
)
from app.seed_pipelines import seed
from app.services import den

TEAM = {
    "rozrobnyk": "Марко",
    "perehovorschyk_dok": "Богдан",
    "perehovory_kontakt": "Оксана",
    "zapyty_lysty": "Галина",
}


@pytest.fixture()
def seeded(s):
    seed(s)
    for role, name in TEAM.items():
        s.add(User(name=name, login=name.lower(), role=role))
    s.commit()
    return s


def _user(s, role):
    return s.exec(select(User).where(User.role == role)).one()


def _deal(s, owner, **kw):
    prog = s.exec(select(Program).where(Program.code == "biogaz")).one()
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one()
    st = s.exec(select(Stage).where(Stage.pipeline_id == pipe.id)
                .order_by(Stage.order)).first()
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=st.id, owner=owner,
             title=kw.pop("title", "угода"), **kw)
    s.add(d)
    s.commit()
    return d


# ── стан завдання ────────────────────────────────────────────────────────────

def test_overdue_and_today_flags():
    t = Task(title="x", due_date=date.today() - timedelta(days=1))
    assert t.is_overdue and not t.is_today
    t.due_date = date.today()
    assert t.is_today and not t.is_overdue
    t.status = "done"
    assert not t.is_overdue and not t.is_today       # закрите не горить


def test_task_without_date_never_overdue():
    assert not Task(title="колись").is_overdue


# ── «мій день» під роль ──────────────────────────────────────────────────────

def test_overdue_task_goes_to_urgent(seeded):
    u = _user(seeded, "perehovory_kontakt")
    seeded.add(Task(title="передзвонити", assignee=u.name,
                    due_date=date.today() - timedelta(days=2)))
    seeded.commit()
    day = den.build_day(seeded, u)
    assert any("передзвонити" in i.title for i in day.urgent)


def test_overdue_deal_action_goes_to_urgent(seeded):
    u = _user(seeded, "perehovory_kontakt")
    _deal(seeded, u.name, title="ПАП Зоряна Долина",
          next_action_date=date.today() - timedelta(days=3),
          next_action_note="подзвонити керівнику")
    day = den.build_day(seeded, u)
    it = [i for i in day.urgent if i.title == "ПАП Зоряна Долина"]
    assert it and "подзвонити керівнику" in it[0].why


def test_deal_of_another_owner_not_in_my_day(seeded):
    _deal(seeded, "Богдан", title="чужа угода",
          next_action_date=date.today() - timedelta(days=3))
    day = den.build_day(seeded, _user(seeded, "perehovory_kontakt"))
    assert not any(i.title == "чужа угода" for i in day.urgent + day.today + day.soon)


def test_halyna_sees_send_queue_and_overdue_replies(seeded):
    t = _user(seeded, "zapyty_lysty")
    seeded.add(Document(title="COM-06 — MOU", kind="COM-06",
                        workflow_status="do_vidpravlennya"))
    seeded.add(Outreach(subject="COM-01 запрошення", sent_at=utcnow(),
                        reply_due_date=date.today() - timedelta(days=1),
                        reply_status="chekaiemo", recipient="Голова"))
    seeded.commit()
    day = den.build_day(seeded, t)
    assert any("COM-06" in i.title for i in day.today)
    assert any("COM-01" in i.title for i in day.urgent)


def test_others_do_not_see_halynas_queue(seeded):
    seeded.add(Document(title="COM-06 — MOU", kind="COM-06",
                        workflow_status="do_vidpravlennya"))
    seeded.commit()
    day = den.build_day(seeded, _user(seeded, "perehovory_kontakt"))
    assert not any("COM-06" in i.title for i in day.today)


def test_bohdan_sees_documents_awaiting_edit(seeded):
    k = _user(seeded, "perehovorschyk_dok")
    seeded.add(Document(title="ТЗ заводу", kind="TZ", workflow_status="na_pravtsi"))
    seeded.commit()
    assert any("ТЗ заводу" in i.title for i in den.build_day(seeded, k).today)


def test_marko_sees_data_quality(seeded):
    from app.models import Counterparty
    seeded.add(Counterparty(name="сумнівна", data_status="needs_check"))
    seeded.commit()
    day = den.build_day(seeded, _user(seeded, "rozrobnyk"))
    assert any(i.kind == "data" for i in day.soon)


def test_empty_day_is_empty(seeded):
    assert den.build_day(seeded, _user(seeded, "perehovorschyk_dok")).total == 0


# ── KPI ──────────────────────────────────────────────────────────────────────

def test_kpi_on_empty_base(seeded):
    k = den.kpi(seeded)
    assert k["deals_active"] == 0 and k["reply_rate"] is None


def test_kpi_counts_hygiene(seeded):
    _deal(seeded, "Оксана", title="без дії")
    _deal(seeded, "Оксана", title="прострочена",
          next_action_date=date.today() - timedelta(days=1))
    k = den.kpi(seeded)
    assert k["deals_active"] == 2
    assert k["no_next_action"] == 1 and k["overdue_actions"] == 1


def test_kpi_reply_rate(seeded):
    seeded.add(Outreach(subject="a", sent_at=utcnow(), reply_status="otrymano"))
    seeded.add(Outreach(subject="b", sent_at=utcnow(), reply_status="chekaiemo"))
    seeded.commit()
    k = den.kpi(seeded)
    assert k["outreach_sent"] == 2 and k["outreach_replied"] == 1
    assert k["reply_rate"] == 50


def test_kpi_weighted_pipeline(seeded):
    _deal(seeded, "Оксана", title="а", value=100000)
    d = seeded.exec(select(Deal)).first()
    d.probability = 60
    seeded.add(d)
    seeded.commit()
    assert den.kpi(seeded)["pipeline_value"] == 60000
