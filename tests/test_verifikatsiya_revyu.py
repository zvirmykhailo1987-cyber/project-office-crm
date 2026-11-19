# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Regression tests for the verification round of the independent review."""
from datetime import date

import pytest
from sqlmodel import Session, select

from app import config
from app.models import AuditEntry, ChatMessage, ChatRoom, Document, User
from app.security import KIT


def test_chat_import_keeps_its_own_body_limit():
    assert KIT.body_limits["/chat/perenesty"] > 100 * 1024 * 1024
    assert KIT.max_body < KIT.body_limits["/chat/perenesty"]


def test_deleted_chat_text_does_not_reach_the_audit_trail(engine, klient):
    with Session(engine) as s:
        room = ChatRoom(code="r1", title="room")
        s.add(room)
        s.commit()
        s.refresh(room)
        m = ChatMessage(room_id=room.id, author=klient.korystuvach.name, text="very private words")
        s.add(m)
        s.commit()
        mid = m.id
    klient.post(f"/chat/{mid}/vydalyty", follow_redirects=False)
    with Session(engine) as s:
        assert all("private" not in (e.detail or "") for e in s.exec(select(AuditEntry)).all())


def test_fmt_parameter_is_a_whitelist(engine, klient):
    with Session(engine) as s:
        d = Document(title="x", filename="x.md", stored_path="")
        s.add(d)
        s.commit()
        did = d.id
    r = klient.get(f"/dokumenty/{did}/file", params={"fmt": "a/b"}, follow_redirects=False)
    assert r.status_code != 500


def test_demo_seed_refuses_a_database_with_accounts(engine, monkeypatch):
    from app import db, seed_demo
    monkeypatch.setattr(config, "IS_PROD", False)
    db.bootstrap(engine)
    with Session(engine) as s:
        s.add(User(name="Real admin", login="realadmin", role="rozrobnyk", password_hash="$argon2id$x"))
        s.commit()
        assert seed_demo.seed(s) == {"skipped": True}


def test_finalising_records_a_signing_date(engine, monkeypatch):
    """Gates need is_final AND signed_at: a final document without a date would never open one."""
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app
    from app.web.deps import current_user
    from tests.conftest import z_csrf
    with Session(engine) as s:
        avtor = User(name="Author", login="a1", role="rozrobnyk")
        appr = User(name="Approver", login="a2", role="perehovorschyk_dok")
        s.add(avtor); s.add(appr); s.commit(); s.refresh(appr)
        d = Document(title="Teaser", filename="t.md", created_by="Author", uploaded_by="Author")
        s.add(d); s.commit(); did = d.id
        s.refresh(appr); s.expunge(appr)

    def _sess():
        with Session(engine) as sess:
            yield sess
    app.dependency_overrides[get_session] = _sess
    app.dependency_overrides[current_user] = lambda: appr
    try:
        c = z_csrf(TestClient(app, follow_redirects=False))
        r = c.post(f"/dokumenty/{did}/final", data={"confirm": "yes"})
        assert r.status_code in (303, 200)
    finally:
        app.dependency_overrides.clear()
    with Session(engine) as s:
        d = s.get(Document, did)
        assert d.is_final is True
        assert d.signed_at == date.today()


@pytest.mark.parametrize("rol,ochik_zmina", [("perehovory_kontakt", False), ("perehovorschyk_dok", True)])
def test_undoing_an_approval_needs_the_right(engine, rol, ochik_zmina):
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app
    from app.web.deps import current_user
    from tests.conftest import z_csrf
    with Session(engine) as s:
        u = User(name=f"U {rol}", login=f"u_{rol}", role=rol)
        s.add(u); s.commit(); s.refresh(u)
        d = Document(title="Doc", filename="d.md", workflow_status="uzhodzheno", created_by="Someone")
        s.add(d); s.commit(); did = d.id
        s.refresh(u); s.expunge(u)

    def _sess():
        with Session(engine) as sess:
            yield sess
    app.dependency_overrides[get_session] = _sess
    app.dependency_overrides[current_user] = lambda: u
    try:
        c = z_csrf(TestClient(app, follow_redirects=False))
        c.post(f"/dokumenty/{did}/status", data={"workflow_status": "na_pravtsi"})
    finally:
        app.dependency_overrides.clear()
    with Session(engine) as s:
        assert (s.get(Document, did).workflow_status == "na_pravtsi") is ochik_zmina


@pytest.mark.parametrize("rol", ["zapyty_lysty", "perehovory_kontakt"])
def test_leaving_ready_to_send_backwards_needs_the_right(engine, rol):
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app
    from app.web.deps import current_user
    from tests.conftest import z_csrf
    with Session(engine) as s:
        u = User(name=f"V {rol}", login=f"v_{rol}", role=rol)
        s.add(u); s.commit()
        d = Document(title="Doc", filename="d.md", workflow_status="do_vidpravlennya", created_by="Someone")
        s.add(d); s.commit(); did = d.id
        s.refresh(u); s.expunge(u)

    def _sess():
        with Session(engine) as sess:
            yield sess
    app.dependency_overrides[get_session] = _sess
    app.dependency_overrides[current_user] = lambda: u
    try:
        c = z_csrf(TestClient(app, follow_redirects=False))
        c.post(f"/dokumenty/{did}/status", data={"workflow_status": "na_pravtsi"})
    finally:
        app.dependency_overrides.clear()
    with Session(engine) as s:
        assert s.get(Document, did).workflow_status == "do_vidpravlennya"



# ── final independent review ─────────────────────────────────────────────────

def _jak(engine, u):
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app
    from app.web.deps import current_user
    from tests.conftest import z_csrf

    def _sess():
        with Session(engine) as sess:
            yield sess
    app.dependency_overrides[get_session] = _sess
    app.dependency_overrides[current_user] = lambda: u
    return z_csrf(TestClient(app, follow_redirects=False))


def _korystuvach(engine, name, login, role):
    with Session(engine) as s:
        u = User(name=name, login=login, role=role)
        s.add(u); s.commit(); s.refresh(u); s.expunge(u)
    return u


def test_sent_mark_does_not_skip_the_approval(engine):
    from app.main import app
    u = _korystuvach(engine, "Sender", "snd", "zapyty_lysty")
    with Session(engine) as s:
        d = Document(title="Draft", filename="d.md", workflow_status="chernetka", created_by="Someone")
        s.add(d); s.commit(); did = d.id
    try:
        _jak(engine, u).post("/lysty/nadislano", data={"document_id": str(did), "recipient": "x@demo.example"})
    finally:
        app.dependency_overrides.clear()
    with Session(engine) as s:
        assert s.get(Document, did).workflow_status == "chernetka"


def test_only_the_task_people_change_its_status(engine):
    from app.main import app
    from app.models import Task
    chuzhyi = _korystuvach(engine, "Outsider", "outs", "perehovory_kontakt")
    with Session(engine) as s:
        t = Task(title="Call", assignee="Doer", created_by="Boss", status="open")
        s.add(t); s.commit(); tid = t.id
    try:
        c = _jak(engine, chuzhyi)
        c.post(f"/zavdannya/{tid}/status", data={"status": "cancelled"})
        c.post(f"/zavdannya/{tid}/status", data={"status": "done"})
    finally:
        app.dependency_overrides.clear()
    with Session(engine) as s:
        assert s.get(Task, tid).status == "open"


def test_return_from_lost_stage_still_passes_the_gates(engine):
    from app.models import Deal, Pipeline, Program, Stage
    from app.services import gates
    with Session(engine) as s:
        prog = Program(code="tprog", title="Test")
        s.add(prog); s.commit(); s.refresh(prog)
        p = Pipeline(program_id=prog.id, code="tp", title="Test")
        s.add(p); s.commit(); s.refresh(p)
        st = {}
        for i, (code, req, term, won) in enumerate([("kontakt", "", False, False), ("kp", "KP", False, False),
                                                     ("perehovory", "", False, False), ("uhoda", "", True, True),
                                                     ("vidmova", "", True, False)], start=1):
            st[code] = Stage(pipeline_id=p.id, code=code, title=code, order=i, required_doc_kinds=req,
                             is_terminal=term, is_won=won)
            s.add(st[code])
        s.commit()
        deal = Deal(title="D", program_id=prog.id, pipeline_id=p.id, stage_id=st["vidmova"].id)
        s.add(deal); s.commit(); s.refresh(deal)
        assert not gates.check(s, deal, st["perehovory"]).allowed          # KP was never produced
        assert gates.check(s, deal, st["kontakt"]).allowed                 # restarting from the top is fine


def test_author_of_any_version_cannot_approve_the_document(engine):
    """A colleague's new version (or a chat file promoted by someone else) keeps the author."""
    from app.web.collab import _avtory_sutnosti
    with Session(engine) as s:
        v1 = Document(title="KP", filename="a.md", doc_group_uid="g-1", version=1, created_by="Author")
        v2 = Document(title="KP", filename="b.md", doc_group_uid="g-1", version=2,
                      created_by="Author", uploaded_by="Colleague")
        s.add(v1); s.add(v2); s.commit()
        assert {"Author", "Colleague"} <= _avtory_sutnosti(s, "document", v2.id)
        assert "Author" in _avtory_sutnosti(s, "document", v1.id)
