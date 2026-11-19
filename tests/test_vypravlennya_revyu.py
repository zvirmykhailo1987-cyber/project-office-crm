"""Регресії за перевіреними знахідками ревʼю (26 пунктів).

Кожен тест названо за пунктом і йде через HTTP там, де знахідка була в роуті:
половина помилок жила саме між формою й логікою, і прямий виклик функції
її б не побачив.
"""
import io
import os
import time
import zipfile
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app import config
from app.models import (
    AiRun,
    Approval,
    AuditEntry,
    ChatAttachment,
    ChatMessage,
    ChatRoom,
    Cluster,
    Counterparty,
    Deal,
    Document,
    Outreach,
    Pipeline,
    Program,
    Score,
    Setting,
    Site,
    Stage,
    Task,
    User,
    utcnow,
)
from app.seed_pipelines import seed


# ── спільне ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def lyudy(s):
    u = {
        "marko": User(id=1, name="Марко", login="marko", role="rozrobnyk"),
        "bohdan": User(id=2, name="Богдан", login="bohdan", role="perehovorschyk_dok"),
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


@pytest.fixture()
def docs_dir(tmp_path, monkeypatch):
    d = tmp_path / "docs"
    d.mkdir()
    monkeypatch.setattr(config, "DOCS_DIR", d)
    return d


@pytest.fixture()
def chat_dir(tmp_path, monkeypatch):
    d = tmp_path / "chat"
    d.mkdir()
    monkeypatch.setattr(config, "CHAT_DIR", d)
    return d


@pytest.fixture()
def voronky(s):
    seed(s)
    return s


def _yak(user):
    """Підмінити поточного користувача для наступних запитів клієнта."""
    from app.main import app
    from app.web.deps import current_user
    app.dependency_overrides[current_user] = lambda: user


def _stadii(s, kod):
    prog = s.exec(select(Program).where(Program.code == kod)).one()
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one()
    return prog, pipe, {st.code: st for st in s.exec(
        select(Stage).where(Stage.pipeline_id == pipe.id))}


def _ugoda(s, kod, stage_code, **kw):
    prog, pipe, st = _stadii(s, kod)
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=st[stage_code].id,
             title="тест", **kw)
    s.add(d)
    s.commit()
    s.refresh(d)
    return d, st


def _dok(s, **kw):
    d = Document(title=kw.pop("title", "Док"), kind=kw.pop("kind", "KP"), **kw)
    s.add(d)
    s.commit()
    s.refresh(d)
    return d


# ── 1. шлях завантаження на AI-витяг ─────────────────────────────────────────

def test_p01_ai_imya_klienta_ne_potraplyaye_v_shlyakh(klient, s, tmp_path, monkeypatch):
    from app.web import ai
    up = tmp_path / "uploads"
    up.mkdir()
    monkeypatch.setattr(ai, "UPLOAD_DIR", up)
    r = klient.post("/ai/vytyah", data={"kind": "rahunok"},
                    files={"file": ("x\\..\\..\\pwned.txt", b"Rakhunok 1 suma 100 " * 5,
                                    "text/plain")})
    assert r.status_code == 303
    zapysani = list(up.iterdir())
    assert len(zapysani) == 1 and zapysani[0].suffix == ".txt"
    assert "pwned" not in zapysani[0].name and ".." not in zapysani[0].name
    assert not (tmp_path / "pwned.txt").exists()
    run = s.exec(select(AiRun)).one()
    assert run.source_file == "pwned.txt"            # оригінал — лише як дані

    r = klient.post("/ai/vytyah", data={"kind": "rahunok"},
                    files={"file": ("evil.exe", b"MZ" * 50, "application/octet-stream")})
    assert len(list(up.iterdir())) == 1               # .exe на диск не потрапив


# ── 2. фіналізація документа ─────────────────────────────────────────────────

def test_p02_finalizatsiya_lyshe_zatverdzhuvach_ne_avtor_z_pidtverdzhennyam(
        klient, s, lyudy):
    d = _dok(s, created_by="Марко", uploaded_by="Марко")
    # адміністратор (rozrobnyk) не затверджує
    assert klient.post(f"/dokumenty/{d.id}/final", data={"confirm": "yes"}).status_code == 403
    # затверджувач — автор документа
    vlasnyi = _dok(s, created_by="Богдан", uploaded_by="Богдан")
    _yak(lyudy["bohdan"])
    assert klient.post(f"/dokumenty/{vlasnyi.id}/final",
                       data={"confirm": "yes"}).status_code == 403
    # без підтвердження — 428
    assert klient.post(f"/dokumenty/{d.id}/final", data={}).status_code == 428
    r = klient.post(f"/dokumenty/{d.id}/final",
                    data={"confirm": "yes", "signed_at": "2026-11-01"})
    assert r.status_code == 303
    s.expire_all()
    assert s.get(Document, d.id).is_final
    # повторно — відмова
    assert klient.post(f"/dokumenty/{d.id}/final", data={"confirm": "yes"}).status_code == 409


def test_p02_dodaty_dokument_ugody_ne_stvoryuye_finalnyi(klient, s, voronky):
    d, _ = _ugoda(s, "biogaz", "kp")
    klient.post(f"/voronky/{d.id}/document",
                data={"kind": "KP", "title": "КП", "is_final": "on",
                      "signed_at": "2026-11-01"})
    doc = s.exec(select(Document).where(Document.deal_id == d.id)).one()
    assert not doc.is_final and doc.workflow_status == "chernetka"
    assert not s.exec(select(AuditEntry).where(
        AuditEntry.action == "document.finalize")).first()


# ── 3. переходи стану документа ──────────────────────────────────────────────

def test_p03_tablytsya_perekhodiv_i_finalnyi_nezminnyi(klient, s, lyudy):
    d = _dok(s, created_by="Оксана")
    r = klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "nadislano"})
    assert "err=perekhid" in r.headers["location"]

    f = _dok(s, created_by="Оксана", is_final=True, workflow_status="uzhodzheno")
    r = klient.post(f"/dokumenty/{f.id}/status", data={"workflow_status": "chernetka"})
    assert "err=finalnyi" in r.headers["location"]

    gotovyi = _dok(s, created_by="Оксана", workflow_status="do_vidpravlennya")
    _yak(lyudy["bohdan"])                             # затверджувач, але без CAP_SEND
    r = klient.post(f"/dokumenty/{gotovyi.id}/status",
                    data={"workflow_status": "nadislano"})
    assert "err=prava" in r.headers["location"]

    zavantazhenyi = _dok(s, created_by="", uploaded_by="Богдан")
    r = klient.post(f"/dokumenty/{zavantazhenyi.id}/status",
                    data={"workflow_status": "uzhodzheno"})
    assert "err=samozatverdzhennya" in r.headers["location"]

    s.expire_all()
    assert s.get(Document, d.id).workflow_status == "chernetka"
    assert s.get(Document, f.id).workflow_status == "uzhodzheno"
    assert s.get(Document, gotovyi.id).workflow_status == "do_vidpravlennya"
    assert s.get(Document, zavantazhenyi.id).workflow_status == "chernetka"


# ── 4. затвердження: автор сутності не вирішує ───────────────────────────────

def test_p04_zatverdzhuvach_avtor_dokumenta_ne_vyrishuye(klient, s, lyudy):
    d = _dok(s, created_by="Богдан", uploaded_by="Богдан")
    klient.post("/zatverdzhennya/podaty",
                data={"entity": "document", "entity_id": str(d.id)})   # подав Марко
    a = s.exec(select(Approval)).one()
    assert a.submitted_by == "Марко"
    _yak(lyudy["bohdan"])
    r = klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "zatverdzheno"})
    assert r.status_code == 403
    s.expire_all()
    assert s.get(Approval, a.id).status == "na_rozglyadi"


# ── 5. закриті архіви чату ───────────────────────────────────────────────────

def test_p05_zakryta_kimnata_ne_vydyma_cherez_pryami_zapyty(klient, s, chat_dir):
    room = ChatRoom(code="wa-osobyste", title="Особисте", is_default=False,
                    archived=True, uchasnyky="Оксана")
    s.add(room)
    s.commit()
    m = ChatMessage(room_id=room.id, author="Оксана", text="секрет до завтра")
    s.add(m)
    s.commit()
    (chat_dir / "f.txt").write_bytes(b"secret")
    a = ChatAttachment(message_id=m.id, filename="f.txt", original_name="f.txt", size=6)
    s.add(a)
    s.commit()

    assert klient.get(f"/chat/fayl/{a.id}").status_code == 404
    assert klient.post(f"/chat/{m.id}/u-zavdannya", data={}).status_code == 404
    assert klient.post(f"/chat/fayl/{a.id}/v-dokumenty", data={}).status_code == 404
    assert klient.post(f"/chat/{m.id}/redahuvaty", data={"text": "x"}).status_code == 404
    assert klient.post(f"/chat/{m.id}/vydalyty").status_code == 404
    # відповідь із кімнати офісу на повідомлення закритого архіву
    assert klient.post("/chat", data={"text": "ок", "reply_to": str(m.id)}).status_code == 400
    assert not s.exec(select(Task)).first()
    assert not s.exec(select(Document)).first()


# ── 6. zip-бомба ─────────────────────────────────────────────────────────────

def _zip(chleny: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in chleny.items():
            z.writestr(n, b)
    return buf.getvalue()


def test_p06_zip_mezhi_rozpakuvannya(monkeypatch):
    from app.services import wa_parser
    monkeypatch.setattr(wa_parser, "ZIP_MAX_FAYLIV", 3)
    with pytest.raises(wa_parser.ArkhivVidhyleno):
        wa_parser.rozibraty_zip(_zip({f"{i}.jpg": b"x" for i in range(5)}))

    monkeypatch.setattr(wa_parser, "ZIP_MAX_FAYLIV", 2000)
    monkeypatch.setattr(wa_parser, "ZIP_MAX_RAZOM", 1024 * 1024)
    bomba = _zip({"chat.txt": b"a", "big.bin": b"\0" * (3 * 1024 * 1024)})
    assert len(bomba) < 50 * 1024                     # стиснуте — крихітне
    with pytest.raises(wa_parser.ArkhivVidhyleno):
        wa_parser.rozibraty_zip(bomba)
    with pytest.raises(wa_parser.ArkhivVidhyleno):
        wa_parser.rozibraty_zip(b"PK\x03\x04 not really a zip")


def test_p06_perenesty_vidhylyaye_bombu_bez_500(klient, monkeypatch):
    from app.services import wa_parser
    monkeypatch.setattr(wa_parser, "ZIP_MAX_FAYLIV", 3)
    r = klient.post("/chat/perenesty", data={"nazva": "x"},
                    files={"fayl": ("e.zip", _zip({f"{i}.jpg": b"x" for i in range(5)}),
                                    "application/zip")})
    assert r.status_code == 303 and "err=" in r.headers["location"]


# ── 7. невдалий вхід не пише в стрічку ───────────────────────────────────────

def test_p07_nevdalyi_vhid_ne_v_strichtsi(klient_bez_vhodu, s):
    from app.services import collab
    zlyi = "<b>ПІДРОБЛЕНО</b> " + "x" * 80
    klient_bez_vhodu.post("/login", data={"login": zlyi, "password": "nope"})
    e = s.exec(select(AuditEntry).where(AuditEntry.action == "auth.login_failed")).one()
    assert e.actor == "anonymous"
    assert len(e.detail) < 50
    assert not [i for i in collab.feed(s, limit=50) if i.action.startswith("auth.")]


# ── 8. сайт контрагента — лише http(s) ───────────────────────────────────────

def test_p08_website_lyshe_http(klient, s):
    cp = Counterparty(name="Тест")
    s.add(cp)
    s.commit()
    r = klient.post(f"/dovidnyk/{cp.id}/field",
                    data={"field": "website", "value": "javascript:alert(1)"})
    assert r.status_code == 400 and r.json()["ok"] is False
    r = klient.post(f"/dovidnyk/{cp.id}/field",
                    data={"field": "website", "value": "https://agro.example.ua"})
    assert r.json()["ok"] is True

    cp.website = "javascript:alert(document.cookie)"      # прийшло з імпорту
    s.add(cp)
    s.commit()
    assert 'href="javascript:' not in klient.get(f"/dovidnyk/{cp.id}").text


# ── 9. виграшна стадія вимагає свої документи ────────────────────────────────

def test_p09_vygrash_bez_dogovoru_zablokovano(s, voronky):
    from app.services import gates
    d, st = _ugoda(s, "biogaz", "peremovyny")
    res = gates.check(s, d, st["ugoda"])
    assert not res.allowed and "dogovir" in res.missing
    s.add(Document(deal_id=d.id, kind="dogovir", is_final=True, signed_at=date.today()))
    s.commit()
    assert gates.check(s, d, st["ugoda"]).allowed

    e, est = _ugoda(s, "elevator", "s7")
    s.add(Document(deal_id=e.id, kind="COM-07", is_final=True, signed_at=date.today()))
    s.commit()
    res = gates.check(s, e, est["s8"])
    assert res.missing == ["COM-09"]


# ── 10. стадія іншої воронки ─────────────────────────────────────────────────

def test_p10_perekhid_na_chuzhu_voronku_vidhyleno(klient, s, voronky):
    d, _ = _ugoda(s, "biogaz", "novyi")
    _, _, est = _stadii(s, "elevator")
    r = klient.post(f"/voronky/{d.id}/stage", data={"stage_id": str(est["s1"].id)})
    assert r.status_code == 400
    s.expire_all()
    assert s.get(Deal, d.id).pipeline_id != est["s1"].pipeline_id
    assert s.get(Deal, d.id).stage_id != est["s1"].id


# ── 11. автоперехід іде через гейт ───────────────────────────────────────────

def test_p11_avtoperekhid_ne_obkhodyt_geyt(klient, s, voronky):
    d, st = _ugoda(s, "biogaz", "dzvonyty")
    dz = st["dzvonyty"]
    assert dz.avto_umova == "aktyvnist"
    dz.required_doc_kinds = "KP"
    s.add(dz)
    s.commit()
    klient.post(f"/voronky/{d.id}/activity",
                data={"type": "Дзвінок", "outcome": "домовились", "note": ""})
    s.expire_all()
    assert s.get(Deal, d.id).stage_id == dz.id


# ── 12. імена файлів і ключі груп ────────────────────────────────────────────

def test_p12_hrupy_z_typom_sutnosti_i_rizni_imena(klient, s, docs_dir):
    pdf = ("p.pdf", b"%PDF-1.4 test", "application/pdf")
    klient.post("/dokumenty/zavantazhyty", data={"kind": "KP", "deal_id": "3"},
                files={"fayl": pdf})
    klient.post("/dokumenty/zavantazhyty", data={"kind": "KP", "counterparty_id": "3"},
                files={"fayl": pdf})
    a, b = list(s.exec(select(Document).order_by(Document.id)))
    assert a.doc_group_uid == "upload:KP:deal:3"
    assert b.doc_group_uid == "upload:KP:counterparty:3"
    assert a.filename != b.filename and a.version == b.version == 1
    assert len(list(docs_dir.iterdir())) == 2


# ── 13. нова версія завантаженого файлу ──────────────────────────────────────

def test_p13_nova_versiya_kopiyuye_oryginal(klient, s, docs_dir):
    bayty = b"%PDF-1.4 realna prezentatsiya"
    klient.post("/dokumenty/zavantazhyty", data={"kind": "prezentatsiya", "deal_id": "5"},
                files={"fayl": ("Презентація.pdf", bayty, "application/pdf")})
    d = s.exec(select(Document)).one()
    r = klient.post(f"/dokumenty/{d.id}/nova-versiya", data={"note": "правка"})
    assert r.status_code == 303
    nd = s.exec(select(Document).where(Document.version == 2)).one()
    assert nd.filename.endswith(".pdf") and nd.filename != d.filename
    assert (docs_dir / nd.filename).read_bytes() == bayty
    assert nd.doc_group_uid == d.doc_group_uid


# ── 14–15. завдання хабу й «в роботі» ────────────────────────────────────────

def test_p14_doruchennya_z_kartky_khabu(klient, s):
    c = Cluster(site_code="H-1", type="agro", oblast="Тернопільська")
    s.add(c)
    s.commit()
    r = klient.post("/doruchennya", data={"entity": "cluster", "entity_id": str(c.id),
                                          "title": "Зібрати донорів хабу",
                                          "assignee": "Оксана"})
    assert r.status_code == 303
    t = s.exec(select(Task)).one()
    assert t.cluster_id == c.id
    assert "Зібрати донорів хабу" in klient.get(f"/haby/{c.id}").text


def test_p15_zavdannya_v_roboti_vydno_na_kartkakh(klient, s):
    c = Cluster(site_code="H-2", type="agro")
    cp = Counterparty(name="Агро")
    s.add(c)
    s.add(cp)
    s.commit()
    s.add(Task(title="Хабове в роботі", cluster_id=c.id, status="in_progress"))
    s.add(Task(title="Компанії в роботі", counterparty_id=cp.id, status="in_progress"))
    s.commit()
    assert "Хабове в роботі" in klient.get(f"/haby/{c.id}").text
    assert "Компанії в роботі" in klient.get(f"/dovidnyk/{cp.id}").text


# ── 16. курсор «раніших» у чаті ──────────────────────────────────────────────

def test_p16_ranishi_za_chasom_a_ne_za_id(klient, s, chat_dir):
    room = ChatRoom(code="ofis", title="Офіс", is_default=True)
    s.add(room)
    s.commit()
    baza = utcnow()
    # id зростає, а час СПАДАЄ — як в імпортованому архіві, де свіжіший
    # експорт приніс старіші репліки
    for i in range(70):
        s.add(ChatMessage(room_id=room.id, author="Оксана", text=f"репліка {i}",
                          created_at=baza - timedelta(minutes=i)))
    s.commit()
    ids = [m.id for m in s.exec(select(ChatMessage).order_by(ChatMessage.id))]
    persha = klient.get("/chat").text
    assert f'id="m{ids[0]}"' in persha and f'id="m{ids[69]}"' not in persha
    # найстаріше на першій сторінці — ids[59]; «раніші» — ids[60..69]
    druha = klient.get(f"/chat?do={ids[59]}").text
    assert all(f'id="m{x}"' in druha for x in ids[60:])
    assert f'id="m{ids[10]}"' not in druha


# ── 17. автоумова «документ» ─────────────────────────────────────────────────

def test_p17_avtoperekhid_za_dokumentom(klient, s, voronky, lyudy):
    d, st = _ugoda(s, "biogaz", "kp")
    kp = st["kp"]
    kp.avto_umova, kp.avto_do_id = "dokument", st["peremovyny"].id
    s.add(kp)
    s.commit()
    doc = _dok(s, kind="KP", deal_id=d.id, created_by="Марко", uploaded_by="Марко")
    _yak(lyudy["bohdan"])
    klient.post(f"/dokumenty/{doc.id}/final",
                data={"confirm": "yes", "signed_at": "2026-11-10"})
    s.expire_all()
    assert s.get(Deal, d.id).stage_id == st["peremovyny"].id


# ── 18. дубль ЄДРПОУ ─────────────────────────────────────────────────────────

def test_p18_dubl_edrpou_ne_500(klient, s):
    a = Counterparty(name="А", edrpou="12345678")
    b = Counterparty(name="Б")
    s.add(a)
    s.add(b)
    s.commit()
    r = klient.post(f"/dovidnyk/{b.id}/field", data={"field": "edrpou", "value": "12345678"})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"]
    s.expire_all()
    assert s.get(Counterparty, b.id).edrpou == ""


# ── 19. палітра шукає перекладені підписи ────────────────────────────────────

def test_p19_palitra_anhliiskoyu(klient):
    klient.cookies.set("lang", "en")
    r = klient.get("/api/palitra", params={"q": "Tasks"})
    assert r.status_code == 200 and "/zavdannya" in r.text


# ── 20. усі критерії стерто — балу немає ─────────────────────────────────────

def test_p20_bez_kryteriiv_bal_ne_zberigayetsya(klient, s):
    site = Site(site_type="hromada_lokatsiya", name="Громада", c1_grain_t_yr=50000)
    s.add(site)
    s.commit()
    klient.post(f"/obyekty/{site.id}/kryterii", data={"c1_grain_t_yr": "60000"})
    assert s.exec(select(Score).where(Score.site_id == site.id)).first()
    klient.post(f"/obyekty/{site.id}/kryterii", data={})
    s.expire_all()
    assert not s.exec(select(Score).where(Score.site_id == site.id)).first()


# ── 21. видалення стадії й зміна результату ──────────────────────────────────

def test_p21_vydalennya_stadii_potrebuye_pidtverdzhennya(klient, s, voronky):
    _, _, st = _stadii(s, "biogaz")
    sid = st["zustrich"].id
    r = klient.post(f"/napryamy/biogaz/stadiya/{sid}/prybraty", data={})
    assert r.status_code == 428
    s.expire_all()
    assert s.get(Stage, sid) is not None


def test_p21_zmina_rezultatu_onovlyuye_status_ugod(klient, s, voronky):
    d, st = _ugoda(s, "biogaz", "kvalifikovanyi")
    x = st["kvalifikovanyi"]
    klient.post(f"/napryamy/biogaz/stadiya/{x.id}", data={
        "title": x.title, "goal": x.goal, "gate_criteria": x.gate_criteria,
        "required_doc_kinds": x.required_doc_kinds, "key_docs": x.key_docs,
        "default_probability": str(x.default_probability), "rezultat": "vidkladeno",
        "avto_umova": "", "avto_do_id": "", "color": x.color, "confirm": ""})
    s.expire_all()
    assert s.get(Deal, d.id).status == "paused"


# ── 22. період KPI і стадії за id ────────────────────────────────────────────

def test_p22_kpi_period_i_stadii_za_id(s, voronky):
    from app.services import den
    s.add(Outreach(subject="давно", sent_at=utcnow() - timedelta(days=60)))
    s.add(Outreach(subject="щойно", sent_at=utcnow()))
    s.commit()
    assert den.kpi(s, 30)["outreach_sent"] == 1
    assert den.kpi(s, 90)["outreach_sent"] == 2

    _, _, est = _stadii(s, "elevator")
    est["s3"].title = "Контакт встановлено"         # та сама назва, що в біогазі
    s.add(est["s3"])
    s.commit()
    _ugoda(s, "biogaz", "kontakt")
    _ugoda(s, "elevator", "s3")
    by_stage = den.kpi(s)["by_stage"]
    ryadky = [r for r in by_stage.values() if r["title"] == "Контакт встановлено"]
    assert len(ryadky) == 2 and all(r["n"] == 1 for r in ryadky)
    assert {r["prog"] for r in ryadky} == {"Біоенергетика", "Елеватори"}


# ── 23. відповідь закриває завдання контролю ─────────────────────────────────

def test_p23_vidpovid_zakryvaye_zavdannya(klient, s):
    o = Outreach(subject="запит", sent_at=utcnow(), reply_due_date=date.today())
    s.add(o)
    s.commit()
    t = Task(title="Перевірити відповідь", source="outreach_due", outreach_id=o.id)
    s.add(t)
    s.commit()
    klient.post(f"/lysty/{o.id}/vidpovid", data={"reply_status": "otrymano"})
    s.expire_all()
    assert s.get(Task, t.id).status == "done"


# ── 24. зіпсований ввід — не 500 ─────────────────────────────────────────────

def test_p24_zipsovana_data_400(klient, s, voronky):
    assert klient.post("/zavdannya", data={"title": "x", "due_date": "31.12.2026"}
                       ).status_code == 400
    d, _ = _ugoda(s, "biogaz", "novyi")
    assert klient.post(f"/voronky/{d.id}/next-action",
                       data={"next_action_date": "завтра"}).status_code == 400
    assert klient.post("/doruchennya", data={"title": "x", "assignee": "Оксана",
                                             "due_date": "2026-13-45"}).status_code == 400


def test_p24_zipsovane_nalashtuvannya_dniv(klient, s):
    s.add(Setting(key="outreach.reply_days", value="два тижні"))
    s.commit()
    r = klient.post("/lysty/nadislano", data={"subject": "лист", "recipient": "громада"})
    assert r.status_code == 303
    o = s.exec(select(Outreach)).one()
    assert o.reply_due_date == date.today() + timedelta(days=14)


# ── 25. ETag карти бачить бали ───────────────────────────────────────────────

def test_p25_etag_karty_zminyuyetsya_z_balom(klient, s):
    s.add(Site(site_type="biogaz_object", name="Ферма", lat=49.5, lon=25.6))
    s.commit()
    e1 = klient.get("/api/karta/tochky?karta=biogaz").headers["etag"]
    site = s.exec(select(Site)).one()
    s.add(Score(site_id=site.id, model="fat_score", value=70))
    s.commit()
    e2 = klient.get("/api/karta/tochky?karta=biogaz").headers["etag"]
    assert e1 != e2
    cp = Counterparty(name="Нова")
    s.add(cp)
    s.commit()
    assert klient.get("/api/karta/tochky?karta=biogaz").headers["etag"] != e2


# ── 26. штамп статики бачить static_v2 ───────────────────────────────────────

def test_p26_shtamp_bachyt_static_v2():
    from pathlib import Path

    from app.web.deps import _versiya_statyky
    f = Path(__file__).resolve().parents[1] / "static_v2" / "ofis.css"
    if not f.exists():
        pytest.skip("static_v2/ofis.css відсутній")
    st = f.stat()
    do = _versiya_statyky()
    try:
        os.utime(f, (st.st_atime, time.time() + 3600))
        assert _versiya_statyky() != do
    finally:
        os.utime(f, (st.st_atime, st.st_mtime))
