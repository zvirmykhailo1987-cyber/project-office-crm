# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Tasks (who may change a task, rule-made tasks are idempotent), the AI draft → human
confirmation flow, the LLM client's failure modes and the KVED category calibration."""
import io
import json
import urllib.error

import pytest
from sqlmodel import select

from app import config
from app.models import AiRun, AuditEntry, Counterparty, Notification, Outreach, Person, Site, Task, User
from app.services import kategoriyi, llm


@pytest.fixture()
def lyudy(s):
    u = {
        "marko": User(id=1, name="Марко", login="marko", role="rozrobnyk"),
        "oksana": User(id=3, name="Оксана", login="oksana", role="perehovory_kontakt"),
        "halyna": User(id=4, name="Галина", login="halyna", role="zapyty_lysty"),
    }
    for x in u.values():
        s.add(x)
    s.commit()
    for x in u.values():
        s.refresh(x)
        s.expunge(x)
    return u


def _yak(user):
    from app.main import app
    from app.web.deps import current_user
    app.dependency_overrides[current_user] = lambda: user


# ── tasks ───────────────────────────────────────────────────────────────────

def test_zavdannya_bez_nazvy_ne_stvoryuyetsya(klient, s):
    klient.post("/zavdannya", data={"title": "   "})
    assert s.exec(select(Task)).first() is None


def test_doruchennya_koledzi_spovishchaye_yiyi(klient, s):
    klient.post("/zavdannya", data={"title": "Подзвонити", "assignee": "Оксана", "priority": "urgentno!",
                                    "due_date": "2026-12-01", "deal_id": "5", "counterparty_id": "x"})
    t = s.exec(select(Task)).one()
    assert (t.source, t.priority, t.deal_id, t.counterparty_id) == ("vid_koleghy", "normal", 5, None)
    n = s.exec(select(Notification)).one()
    assert n.user == "Оксана" and n.from_whom == "Марко"


def test_zavdannya_sobi_bez_spovishchennya(klient, s):
    klient.post("/zavdannya", data={"title": "Своє"})
    assert s.exec(select(Task)).one().source == "manual"
    assert s.exec(select(Notification)).first() is None


def test_skasuvaty_mozhe_lyshe_postanovnyk(klient, s, lyudy):
    t = Task(title="Т", assignee="Оксана", created_by="Галина")
    s.add(t)
    s.commit()
    _yak(lyudy["oksana"])
    r = klient.post(f"/zavdannya/{t.id}/status", data={"status": "cancelled"})
    assert r.headers["location"].endswith("err=prava")
    _yak(lyudy["marko"])                                     # neither assignee nor creator
    r = klient.post(f"/zavdannya/{t.id}/status", data={"status": "done"})
    assert r.headers["location"].endswith("err=prava")
    s.expire_all()
    assert s.get(Task, t.id).status == "open"
    _yak(lyudy["halyna"])
    klient.post(f"/zavdannya/{t.id}/status", data={"status": "cancelled"})
    s.expire_all()
    assert s.get(Task, t.id).status == "cancelled"


def test_vykonano_spovishchaye_postanovnyka(klient, s, lyudy):
    t = Task(title="Т", assignee="Оксана", created_by="Галина")
    rule = Task(title="Р", assignee="Оксана", created_by="правило")
    s.add(t)
    s.add(rule)
    s.commit()
    _yak(lyudy["oksana"])
    klient.post(f"/zavdannya/{t.id}/status", data={"status": "done"})
    klient.post(f"/zavdannya/{rule.id}/status", data={"status": "done"})
    klient.post(f"/zavdannya/{t.id}/status", data={"status": "bogus"})
    s.expire_all()
    assert s.get(Task, t.id).done_by == "Оксана"
    assert [n.user for n in s.exec(select(Notification))] == ["Галина"]     # the rule is nobody


def test_zavdannya_bez_vykonavtsya_mozhe_zminyty_bud_khto(klient, s, lyudy):
    t = Task(title="Нічиє", assignee="", created_by="Галина")
    s.add(t)
    s.commit()
    _yak(lyudy["oksana"])
    klient.post(f"/zavdannya/{t.id}/status", data={"status": "in_progress"})
    s.expire_all()
    assert s.get(Task, t.id).status == "in_progress"


def test_synk_pravyl_idempotentnyi(klient, s):
    from datetime import date
    s.add(Outreach(subject="Лист 1", sent_by="Галина", reply_due_date=date(2026, 12, 1)))
    s.add(Outreach(subject="Без строку", reply_due_date=None))
    s.add(Outreach(subject="Відповіли", reply_status="otrymano", reply_due_date=date(2026, 12, 1)))
    s.commit()
    klient.post("/zavdannya/synk")
    klient.post("/zavdannya/synk")
    tasks = list(s.exec(select(Task)))
    assert len(tasks) == 1 and tasks[0].assignee == "Галина" and tasks[0].created_by == "правило"
    assert len(list(s.exec(select(AuditEntry).where(AuditEntry.action == "task.sync")))) == 1


def test_spysok_zavdan_filtry_i_kpi(klient, s):
    from datetime import date
    s.add(Task(title="Подзвонити-А", assignee="Оксана", due_date=date(2026, 1, 1)))
    s.add(Task(title="Лист-В", assignee="Оксана"))
    s.add(Task(title="Звіт-Б", assignee="Галина", status="done"))
    s.commit()
    r = klient.get("/zavdannya", params={"assignee": "Оксана"})
    assert "Подзвонити-А" in r.text and "Лист-В" in r.text and "Звіт-Б" not in r.text
    r = klient.get("/zavdannya", params={"status": "done"})
    assert "Звіт-Б" in r.text and "Подзвонити-А" not in r.text
    assert "Звіт-Б" in klient.get("/zavdannya", params={"status": ""}).text
    assert klient.get("/kpi", params={"days": 7}).status_code == 200


# ── AI: nothing enters the working tables without a person's click ──────────

@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    from app.web import ai
    up = tmp_path / "uploads"
    up.mkdir()
    monkeypatch.setattr(ai, "UPLOAD_DIR", up)
    return up


def test_ai_nevidomyi_vyd_i_zavelykyi_fayl(klient, s, uploads, monkeypatch):
    from app.web import ai
    r = klient.post("/ai/vytyah", data={"kind": "nonsense"}, files={"file": ("a.txt", b"x", "text/plain")})
    assert r.headers["location"] == "/ai"
    monkeypatch.setattr(ai, "MAX_MB", 0)
    klient.post("/ai/vytyah", data={"kind": "rahunok"}, files={"file": ("a.txt", b"x", "text/plain")})
    run = s.exec(select(AiRun)).one()
    assert run.engine == "none" and "МБ" in run.error and list(uploads.iterdir()) == []


def test_ai_porozhnii_fayl_chesno_kazhe_shcho_tekstu_nemaye(klient, s, uploads):
    klient.post("/ai/vytyah", data={"kind": "rahunok"}, files={"file": ("empty.txt", b"   ", "text/plain")})
    run = s.exec(select(AiRun)).one()
    assert run.engine == "none" and run.error and run.output_json in ("", None)
    assert s.exec(select(AuditEntry).where(AuditEntry.action == "ai.extract.fail")).one()


def test_ai_anketa_pamyataye_hromadu_a_kartka_rendrytsya(klient, s, uploads):
    site = Site(name="Тестова", site_type="hromada_lokatsiya")
    s.add(site)
    s.commit()
    text = "Анкета громади. Валовий збір зерна 120000 т на рік. Залізниця: так. " * 3
    r = klient.post("/ai/vytyah", data={"kind": "anketa", "site_id": str(site.id)},
                    files={"file": ("anketa.txt", text.encode("utf-8"), "text/plain")})
    run = s.get(AiRun, int(r.headers["location"].rsplit("/", 1)[1]))
    assert json.loads(run.output_json)["_site_id"] == site.id
    assert run.confirmed_at is None                       # a draft, not applied
    assert klient.get(f"/ai/{run.id}").status_code == 200
    assert klient.get("/ai/9999").headers["location"] == "/ai"
    assert klient.get("/ai").status_code == 200


def _run(s, kind, data):
    run = AiRun(kind=kind, engine="stub", output_json=json.dumps(data, ensure_ascii=False))
    s.add(run)
    s.commit()
    s.refresh(run)
    return run


def test_pidtverdzhennya_kontaktiv_dodaye_v_dovidnyk_na_perevirku(klient, s):
    run = _run(s, "kontakty", {"organizatsii": [
        {"nazva": "ТОВ Агро", "edrpou": "12345678", "telefon": "000 123 45 67", "kerivnyk": "Іван Шевчук"},
        {"nazva": "", "edrpou": ""},
    ]})
    klient.post(f"/ai/{run.id}/pidtverdyty")
    cp = s.exec(select(Counterparty)).one()
    assert cp.name == "ТОВ Агро" and cp.data_status == "needs_check" and cp.source == f"ai:{run.id}"
    assert s.exec(select(Person)).one().name == "Іван Шевчук"
    s.expire_all()
    assert "нових 1" in s.get(AiRun, run.id).applied_to
    klient.post(f"/ai/{run.id}/pidtverdyty")               # a second click applies nothing again
    assert len(list(s.exec(select(Counterparty)))) == 1
    assert len(list(s.exec(select(AuditEntry).where(AuditEntry.action == "ai.confirm")))) == 1


def test_pidtverdzhennya_ankety_onovlyuye_kryterii_hromady(klient, s):
    site = Site(name="Анкетна", site_type="hromada_lokatsiya", c4_rail=0)
    s.add(site)
    s.commit()
    run = _run(s, "anketa", {"_site_id": site.id, "c1_grain_t_yr": 150000, "c4_rail": 1, "c5_road": None})
    klient.post(f"/ai/{run.id}/pidtverdyty")
    s.expire_all()
    site = s.get(Site, site.id)
    assert site.c1_grain_t_yr == 150000 and site.c4_rail == 1
    assert "2 критеріїв" in s.get(AiRun, run.id).applied_to


def test_pidtverdzhennya_rakhunku_nichoho_ne_platyt(klient, s):
    run = _run(s, "rahunok", {"suma": 100})
    klient.post(f"/ai/{run.id}/pidtverdyty")
    s.expire_all()
    assert "оплату виконує людина" in s.get(AiRun, run.id).applied_to
    assert klient.post("/ai/9999/pidtverdyty").status_code == 303


# ── LLM client: every failure falls back, never crashes the request ─────────

@pytest.fixture()
def llm_on(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://llm.example/v1/")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_llm_vymkneno_za_zamovchuvannyam():
    assert not llm.available()
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("s", "u")


def test_llm_vidmovlyayetsya_vid_http(llm_on, monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://llm.example/v1")
    with pytest.raises(llm.LLMUnavailable, match="https"):
        llm.complete("s", "u")


def test_llm_zapyt_i_rozbir(llm_on, monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["url"], seen["auth"], seen["body"] = req.full_url, req.headers["Authorization"], json.loads(req.data)
        return _Resp(json.dumps({"choices": [{"message": {"content": '{"a": 1}'}}]}).encode())
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    assert llm.complete("sys", "usr") == {"a": 1}
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key" and seen["body"]["temperature"] == 0


@pytest.mark.parametrize("vidpovid, pomylka", [
    (urllib.error.URLError("down"), "звʼязку"),
    (b"not json", "не JSON"),
    (json.dumps({"choices": []}).encode(), "не розібралась"),
    (json.dumps({"choices": [{"message": {"content": "not json"}}]}).encode(), "не розібралась"),
])
def test_llm_kozhna_pomylka_tse_fallback(llm_on, monkeypatch, vidpovid, pomylka):
    def fake(req, timeout):
        if isinstance(vidpovid, Exception):
            raise vidpovid
        return _Resp(vidpovid)
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)
    with pytest.raises(llm.LLMUnavailable, match=pomylka):
        llm.complete("s", "u")


# ── KVED categories: the module's calibration pairs must all hold ────────────

def test_kalibruvannya_kategorii_kved(capsys):
    assert kategoriyi._selftest() == 0, capsys.readouterr().out
