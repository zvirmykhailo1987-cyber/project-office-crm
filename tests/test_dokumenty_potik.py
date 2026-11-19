# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Document workflow through HTTP: who may move a document, what an upload may be, which
files a download may read, and the outreach register that follows a sent letter.

The approval rules are the point of the workflow: "agreed" is a decision by someone else,
"sent" is recorded by whoever keeps the register, and a stored path in the database must
never turn into an arbitrary read from the disk.
"""
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app import config
from app.models import (
    AuditEntry,
    Counterparty,
    Deal,
    DocTemplate,
    Document,
    Outreach,
    Person,
    Pipeline,
    Program,
    Setting,
    Site,
    Stage,
    Task,
    User,
)
from app.seed_pipelines import seed


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
    c = tmp_path / "chat"
    c.mkdir()
    monkeypatch.setattr(config, "CHAT_DIR", c)
    return d


def _yak(user):
    from app.main import app
    from app.web.deps import current_user
    app.dependency_overrides[current_user] = lambda: user


def _dok(s, **kw):
    d = Document(title=kw.pop("title", "Док"), kind=kw.pop("kind", "KP"), **kw)
    s.add(d)
    s.commit()
    s.refresh(d)
    return d


def _stan(s, doc_id):
    s.expire_all()
    return s.get(Document, doc_id).workflow_status


def _tpl(s, **kw):
    t = DocTemplate(code=kw.pop("code", "KP"), title="Комерційна пропозиція", group="BIO",
                    body_md="# КП\n\nДля [назва] громади від [Проєктний офіс]", output_formats="md", **kw)
    s.add(t)
    s.commit()
    s.refresh(t)
    return t


# ── templates and generation ────────────────────────────────────────────────

def test_shablony_filtr_za_hrupoyu(klient, s):
    _tpl(s)
    s.add(DocTemplate(code="INV-1", title="Інвестору", group="INV", body_md="x"))
    s.commit()
    r = klient.get("/shablony", params={"group": "INV"})
    assert r.status_code == 200 and "INV-1" in r.text and "Комерційна пропозиція" not in r.text


def test_kartka_shablonu_z_kontekstom_i_bez_shablonu(klient, s):
    t = _tpl(s)
    cp = Counterparty(name="ТОВ Контрагент")
    s.add(cp)
    s.commit()
    hromada = Site(name="Борсуківська", site_type="hromada_lokatsiya")
    person = Person(name="Іван Петренко", counterparty_id=cp.id)
    s.add(hromada)
    s.add(person)
    s.commit()
    r = klient.get(f"/shablony/{t.id}", params={"site_id": hromada.id, "counterparty_id": cp.id,
                                                "person_id": person.id})
    assert r.status_code == 200 and "Борсуківськ" in r.text
    assert klient.get("/shablony/9999").headers["location"] == "/shablony"


def test_heneratsiya_stvoryuye_chernetku_i_versiyi(klient, s, docs_dir):
    t = _tpl(s)
    cp = Counterparty(name="ТОВ Власник")
    s.add(cp)
    s.commit()
    hromada = Site(name="Сокальська", site_type="hromada_lokatsiya", counterparty_id=cp.id)
    s.add(hromada)
    s.commit()
    for _ in range(2):
        r = klient.post(f"/shablony/{t.id}/generate", data={"site_id": str(hromada.id)})
        assert r.status_code == 303
    docs = list(s.exec(select(Document).order_by(Document.version)))
    assert [d.version for d in docs] == [1, 2]
    assert all(d.workflow_status == "chernetka" and not d.is_final for d in docs)
    assert docs[0].counterparty_id == cp.id                  # owner taken from the site
    assert len({d.filename for d in docs}) == 2              # v2 never overwrites v1
    assert all((docs_dir / d.filename).exists() for d in docs)
    assert klient.post("/shablony/9999/generate").headers["location"] == "/shablony"


def test_heneratsiya_bez_adresata_i_z_kontrahentom(klient, s, docs_dir):
    t = _tpl(s)
    cp = Counterparty(name="ТОВ Адресат")
    s.add(cp)
    s.commit()
    klient.post(f"/shablony/{t.id}/generate", data={"title": "Загальний лист"})
    klient.post(f"/shablony/{t.id}/generate", data={"counterparty_id": str(cp.id)})
    groups = {d.doc_group_uid for d in s.exec(select(Document))}
    assert groups == {f"tpl:{t.code}:general", f"tpl:{t.code}:counterparty:{cp.id}"}
    assert s.exec(select(Document).where(Document.title == "Загальний лист")).first()


# ── list, card, download ────────────────────────────────────────────────────

def test_spysok_filtruye_za_stanom_i_vydom(klient, s):
    _dok(s, title="Чернетка-А", workflow_status="chernetka", kind="KP")
    _dok(s, title="Узгоджений-Б", workflow_status="uzhodzheno", kind="LOI")
    r = klient.get("/dokumenty", params={"status": "uzhodzheno", "kind": "LOI"})
    assert "Узгоджений-Б" in r.text and "Чернетка-А" not in r.text


def test_kartka_dokumenta_pokazuye_tekst_i_kontakty(klient, s, docs_dir):
    cp = Counterparty(name="ТОВ Картка")
    s.add(cp)
    s.commit()
    s.add(Person(name="Олена Коваль", counterparty_id=cp.id))
    s.commit()
    (docs_dir / "lyst.md").write_text("Шановна [посада]", encoding="utf-8")
    d = _dok(s, filename="lyst.docx", counterparty_id=cp.id, workflow_status="do_vidpravlennya")
    r = klient.get(f"/dokumenty/{d.id}")
    assert r.status_code == 200 and "[посада]" in r.text
    assert 'value="Олена Коваль"' in r.text                 # recipient prefilled from the contacts
    assert klient.get("/dokumenty/9999").headers["location"] == "/dokumenty"


def test_zavantazhennya_chytaye_lyshe_z_dozvolenykh_koreniv(klient, s, docs_dir, tmp_path):
    """`stored_path` comes from the database: outside DOCS_DIR/CHAT_DIR it is ignored."""
    sekret = tmp_path / "secret.txt"
    sekret.write_text("do not serve", encoding="utf-8")
    d = _dok(s, filename="nemaye.docx", stored_path=str(sekret))
    r = klient.get(f"/dokumenty/{d.id}/file")
    assert r.status_code == 303 and "do not serve" not in r.text

    (docs_dir / "ok.docx").write_bytes(b"docx-bytes")
    (docs_dir / "ok.pdf").write_bytes(b"pdf-bytes")
    d2 = _dok(s, filename="ok.docx", stored_path=str(docs_dir / "ok.docx"))
    assert klient.get(f"/dokumenty/{d2.id}/file").content == b"docx-bytes"
    assert klient.get(f"/dokumenty/{d2.id}/file", params={"fmt": "pdf"}).content == b"pdf-bytes"
    assert klient.get(f"/dokumenty/{d2.id}/file", params={"fmt": "exe"}).content == b"docx-bytes"
    assert klient.get("/dokumenty/9999/file").headers["location"] == "/dokumenty"


def test_fayl_z_chatu_viddayetsya_z_korenya_chatu(klient, s, docs_dir):
    f = config.CHAT_DIR / "z_chatu.pdf"
    f.write_bytes(b"chat-pdf")
    d = _dok(s, filename="z_chatu.pdf", stored_path=str(f))
    assert klient.get(f"/dokumenty/{d.id}/file").content == b"chat-pdf"


# ── upload ──────────────────────────────────────────────────────────────────

def test_zavantazhennya_vidkydaye_vykonuvani_i_bez_imeni(klient, s, docs_dir):
    r = klient.post("/dokumenty/zavantazhyty", files={"fayl": ("run.exe", b"MZ", "application/octet-stream")})
    assert r.headers["location"].endswith("err=rozshyrennya")
    r = klient.post("/dokumenty/zavantazhyty", files={"fayl": ("", b"x", "text/plain")})
    assert r.status_code in (303, 422)
    assert list(docs_dir.iterdir()) == [] and s.exec(select(Document)).first() is None


def test_zavantazhennya_zavelykoho_ne_pyshe_dokument(klient, s, docs_dir, monkeypatch):
    from app.web import dokumenty
    monkeypatch.setattr(dokumenty, "MAX_ROZMIR_FAYLU", 10)
    r = klient.post("/dokumenty/zavantazhyty", files={"fayl": ("big.pdf", b"x" * 11, "application/pdf")})
    assert r.headers["location"].endswith("err=zavelykyi")
    assert s.exec(select(Document)).first() is None


def test_zavantazhennya_vidrizaye_shlyakh_i_versiyuye(klient, s, docs_dir):
    for _ in range(2):
        r = klient.post("/dokumenty/zavantazhyty",
                        data={"kind": "prezentatsiya", "deal_id": "", "counterparty_id": "7"},
                        files={"fayl": ("../../Презентація.PPTX", b"pptx", "application/octet-stream")})
        assert r.status_code == 303
    docs = list(s.exec(select(Document).order_by(Document.version)))
    assert [d.version for d in docs] == [1, 2] and docs[0].counterparty_id == 7
    assert all(p.parent == docs_dir and p.suffix == ".pptx" for p in docs_dir.iterdir())
    assert docs[0].title == "Презентація" and docs[0].workflow_status == "chernetka"


# ── workflow transitions: who may decide ────────────────────────────────────

def test_nevidomyi_abo_toi_samyi_stan_nichoho_ne_robyt(klient, s, lyudy):
    d = _dok(s, created_by="Оксана")
    klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "zlamano"})
    klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "chernetka"})
    assert _stan(s, d.id) == "chernetka"
    assert not s.exec(select(AuditEntry).where(AuditEntry.action == "document.workflow")).first()


def test_uzhodzheno_lyshe_zatverdzhuvach_i_ne_avtor(klient, s, lyudy):
    d = _dok(s, created_by="Оксана", workflow_status="na_pravtsi")
    _yak(lyudy["oksana"])
    r = klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "uzhodzheno"})
    assert r.headers["location"].endswith("err=lyshe-zatverdzhuvach")
    _yak(lyudy["marko"])                                   # admin is not an approver
    r = klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "uzhodzheno"})
    assert r.headers["location"].endswith("err=lyshe-zatverdzhuvach")
    assert _stan(s, d.id) == "na_pravtsi"
    _yak(lyudy["bohdan"])
    klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "uzhodzheno"})
    assert _stan(s, d.id) == "uzhodzheno"


def test_avtor_inshoyi_versiyi_tezh_ne_zatverdzhuye(klient, s, lyudy):
    _dok(s, created_by="Богдан", doc_group_uid="g1", version=1)
    v2 = _dok(s, created_by="Оксана", doc_group_uid="g1", version=2, workflow_status="na_pravtsi")
    _yak(lyudy["bohdan"])
    r = klient.post(f"/dokumenty/{v2.id}/status", data={"workflow_status": "uzhodzheno"})
    assert r.headers["location"].endswith("err=samozatverdzhennya")


def test_skasuvaty_pohodzhennya_lyshe_zatverdzhuvach_abo_avtor(klient, s, lyudy):
    d = _dok(s, created_by="Оксана", workflow_status="uzhodzheno")
    _yak(lyudy["halyna"])
    r = klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "na_pravtsi"})
    assert r.headers["location"].endswith("err=prava")
    assert _stan(s, d.id) == "uzhodzheno"
    _yak(lyudy["oksana"])                                   # the author may take it back
    klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "na_pravtsi"})
    assert _stan(s, d.id) == "na_pravtsi"


def test_do_vidpravlennya_ne_dlya_storonnih(klient, s, lyudy):
    d = _dok(s, created_by="Богдан", workflow_status="uzhodzheno")
    _yak(lyudy["oksana"])
    r = klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "do_vidpravlennya"})
    assert r.headers["location"].endswith("err=prava")
    _yak(lyudy["halyna"])                                   # keeps the register: may queue it
    klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "do_vidpravlennya"})
    assert _stan(s, d.id) == "do_vidpravlennya"
    klient.post(f"/dokumenty/{d.id}/status", data={"workflow_status": "nadislano"})
    assert _stan(s, d.id) == "nadislano"
    entry = s.exec(select(AuditEntry).where(AuditEntry.action == "document.workflow")
                   .order_by(AuditEntry.id.desc())).first()
    assert entry.actor == "Галина"


# ── finalisation: irreversible, opens the gate, moves the deal ───────────────

def test_finalizatsiya_nevidomoho_dokumenta(klient, lyudy):
    _yak(lyudy["bohdan"])
    assert klient.post("/dokumenty/9999/final", data={"confirm": "yes"}).headers["location"] == "/dokumenty"


def test_finalizatsiya_zapuskaye_avtoperekhid_ugody(klient, s, lyudy):
    seed(s)
    prog = s.exec(select(Program).where(Program.code == "biogaz")).one()
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one()
    stages = {x.code: x for x in s.exec(select(Stage).where(Stage.pipeline_id == pipe.id))}
    cur = next(x for x in sorted(stages.values(), key=lambda x: x.order) if x.required_list)
    nxt = next(x for x in sorted(stages.values(), key=lambda x: x.order) if x.order > cur.order)
    cur.avto_umova, cur.avto_do_id = "dokument", nxt.id
    s.add(cur)
    deal = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=cur.id, title="Авто")
    s.add(deal)
    s.commit()
    docs = [_dok(s, kind=k, deal_id=deal.id, created_by="Оксана") for k in cur.required_list]
    _yak(lyudy["bohdan"])
    for d in docs[:-1]:
        klient.post(f"/dokumenty/{d.id}/final", data={"confirm": "yes"})
        s.expire_all()
        assert s.get(Deal, deal.id).stage_id == cur.id          # not all required documents yet
    klient.post(f"/dokumenty/{docs[-1].id}/final", data={"confirm": "yes"})
    s.expire_all()
    assert s.get(Deal, deal.id).stage_id == nxt.id
    assert s.exec(select(AuditEntry).where(AuditEntry.action == "deal.stage.auto")).first()


def test_nekorektna_data_pidpysu_400(klient, s, lyudy):
    d = _dok(s, created_by="Оксана")
    _yak(lyudy["bohdan"])
    r = klient.post(f"/dokumenty/{d.id}/final", data={"confirm": "yes", "signed_at": "31.12.2026"})
    assert r.status_code == 400
    s.expire_all()
    assert not s.get(Document, d.id).is_final


# ── new version ─────────────────────────────────────────────────────────────

def test_nova_versiya_zavantazhenoho_fayla_kopiyuye_oryhinal(klient, s, docs_dir, lyudy):
    orig = docs_dir / "prez_v1.pptx"
    orig.write_bytes(b"original-pptx")
    d = _dok(s, filename=orig.name, stored_path=str(orig), doc_group_uid="g-up", created_by="Оксана")
    _yak(lyudy["bohdan"])
    r = klient.post(f"/dokumenty/{d.id}/nova-versiya", data={"note": "правки"})
    nd = s.get(Document, int(r.headers["location"].rsplit("/", 1)[1]))
    assert nd.version == 2 and nd.workflow_status == "na_pravtsi" and nd.created_by == "Оксана"
    assert (docs_dir / nd.filename).read_bytes() == b"original-pptx"
    assert orig.read_bytes() == b"original-pptx"                # the original is untouched


def test_nova_versiya_tekstovoho_perehenerovuye(klient, s, docs_dir):
    (docs_dir / "lyst_v1.md").write_text("# Лист\n\nтекст", encoding="utf-8")
    d = _dok(s, filename="lyst_v1.md", doc_group_uid="g-md")
    r = klient.post(f"/dokumenty/{d.id}/nova-versiya")
    nd = s.get(Document, int(r.headers["location"].rsplit("/", 1)[1]))
    assert nd.version == 2 and (docs_dir / nd.filename).exists()
    assert klient.post("/dokumenty/9999/nova-versiya").headers["location"] == "/dokumenty"


def test_nova_versiya_lyshe_z_pravom_redahuvaty_chuzhe(klient, s, lyudy):
    d = _dok(s, doc_group_uid="g")
    _yak(lyudy["oksana"])
    assert klient.post(f"/dokumenty/{d.id}/nova-versiya").status_code == 403


# ── outreach register ───────────────────────────────────────────────────────

def test_lyst_pislya_pohodzhennya_staye_nadislanym(klient, s, lyudy):
    d = _dok(s, workflow_status="do_vidpravlennya", counterparty_id=None, title="КП Сокаль")
    _yak(lyudy["halyna"])
    klient.post("/lysty/nadislano", data={"document_id": str(d.id), "recipient": "голова",
                                          "channel": "pigeon", "reply_due_date": "2026-12-01"})
    o = s.exec(select(Outreach)).one()
    assert o.channel == "email" and o.subject == "КП Сокаль" and o.reply_due_date == date(2026, 12, 1)
    assert _stan(s, d.id) == "nadislano"


def test_neuzhodzhenyi_dokument_ne_staye_nadislanym(klient, s, lyudy):
    d = _dok(s, workflow_status="chernetka")
    _yak(lyudy["halyna"])
    klient.post("/lysty/nadislano", data={"document_id": str(d.id)})
    assert _stan(s, d.id) == "chernetka"
    assert s.exec(select(Outreach)).one().document_id == d.id


def test_lyst_bez_dokumenta_vede_na_kontrahenta_i_typovyi_strok(klient, s, lyudy):
    cp = Counterparty(name="ТОВ Лист")
    s.add(cp)
    s.add(Setting(key="outreach.reply_days", value="10"))
    s.commit()
    _yak(lyudy["halyna"])
    klient.post("/lysty/nadislano", data={"counterparty_id": str(cp.id), "subject": "Запит"})
    o = s.exec(select(Outreach)).one()
    assert o.reply_due_date == date.today() + timedelta(days=10)
    e = s.exec(select(AuditEntry).where(AuditEntry.action == "outreach.sent")).one()
    assert (e.entity, e.entity_id) == ("counterparty", cp.id)


def test_fiksuvaty_lyst_lyshe_z_pravom_reyestru(klient, lyudy):
    _yak(lyudy["oksana"])
    assert klient.post("/lysty/nadislano", data={}).status_code == 403


def test_vidpovid_zakryvaye_zavdannya_kontrolyu(klient, s, lyudy):
    o = Outreach(subject="Запит", sent_by="Галина", reply_due_date=date.today())
    s.add(o)
    s.commit()
    t = Task(title="Перевірити відповідь", source="outreach_due", outreach_id=o.id, status="open")
    s.add(t)
    s.commit()
    _yak(lyudy["halyna"])
    assert klient.post(f"/lysty/{o.id}/vidpovid", data={"reply_status": "nonsense"}).headers["location"] == "/lysty"
    klient.post(f"/lysty/{o.id}/vidpovid", data={"reply_status": "otrymano", "reply_note": " так "})
    s.expire_all()
    assert s.get(Outreach, o.id).reply_note == "так"
    assert s.get(Task, t.id).status == "done" and s.get(Task, t.id).done_by == "Галина"


def test_reyestr_lystiv_rozdilyaye_prostrocheni(klient, s, docs_dir):
    d = _dok(s, workflow_status="do_vidpravlennya", title="У черзі")
    cp = Counterparty(name="ТОВ Мовчить")
    s.add(cp)
    s.commit()
    s.add(Outreach(subject="Давно", document_id=d.id, counterparty_id=cp.id,
                   reply_due_date=date.today() - timedelta(days=3)))
    s.add(Outreach(subject="Свіжий", reply_due_date=date.today() + timedelta(days=3)))
    s.commit()
    r = klient.get("/lysty", params={"tab": "reyestr"})
    assert r.status_code == 200 and "Давно" in r.text and "Свіжий" in r.text


# ── office settings: admin only, audited ────────────────────────────────────

def test_nalashtuvannya_lyshe_admin_i_z_audytom(klient, s, lyudy):
    s.add(Setting(key="office.name", value="Офіс", label="Назва"))
    s.commit()
    assert klient.get("/nalashtuvannya").status_code == 200
    klient.post("/nalashtuvannya", data={"office.name": "Офіс"})       # unchanged → no audit row
    assert not s.exec(select(AuditEntry).where(AuditEntry.action == "settings.update")).first()
    klient.post("/nalashtuvannya", data={"office.name": "Новий офіс"})
    s.expire_all()
    assert s.exec(select(Setting)).one().value == "Новий офіс"
    assert s.exec(select(AuditEntry).where(AuditEntry.action == "settings.update")).one().detail == "office.name"
    _yak(lyudy["bohdan"])
    assert klient.post("/nalashtuvannya", data={"office.name": "x"}).status_code == 403
    assert klient.get("/nalashtuvannya").status_code == 403
