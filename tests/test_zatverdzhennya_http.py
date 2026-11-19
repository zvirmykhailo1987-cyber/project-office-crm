# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Approvals, comments and notifications through HTTP.

The approval rules (no self-approval, not even through a colleague who submits on the
author's behalf; "needs work" only with a comment) are what makes the process real, so
they are pinned at the route, where a form could otherwise slip past them.
"""
import pytest
from sqlmodel import select

from app.models import Approval, Comment, Deal, Document, Notification, Task, User


@pytest.fixture()
def lyudy(s):
    u = {
        "marko": User(id=1, name="Марко", login="marko", role="rozrobnyk"),
        "bohdan": User(id=2, name="Богдан", login="bohdan", role="perehovorschyk_dok"),
        "oksana": User(id=3, name="Оксана", login="oksana", role="perehovory_kontakt"),
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


def _podaty(klient, s, entity, entity_id, hto, note=""):
    _yak(hto)
    klient.post("/zatverdzhennya/podaty", data={"entity": entity, "entity_id": entity_id, "note": note})
    return s.exec(select(Approval).where(Approval.entity == entity, Approval.entity_id == entity_id)
                  .order_by(Approval.id.desc())).first()


def _dok(s, **kw):
    d = Document(title="Док", kind="KP", **kw)
    s.add(d)
    s.commit()
    s.refresh(d)
    return d


# ── submission ──────────────────────────────────────────────────────────────

def test_podannya_spovishchaye_lyshe_zatverdzhuvachiv(klient, s, lyudy):
    d = _dok(s, created_by="Оксана")
    a = _podaty(klient, s, "document", d.id, lyudy["oksana"], note="гляньте")
    assert a.is_open
    assert [n.user for n in s.exec(select(Notification))] == ["Богдан"]
    _podaty(klient, s, "document", d.id, lyudy["oksana"])               # already open: no duplicate
    assert len(list(s.exec(select(Approval)))) == 1


def test_nevidoma_sutnist_400(klient, lyudy):
    r = klient.post("/zatverdzhennya/podaty", data={"entity": "user", "entity_id": 1})
    assert r.status_code == 400
    r = klient.post("/komentar", data={"entity": "deal", "entity_id": 0, "text": "x"})
    assert r.status_code == 400


# ── decision ────────────────────────────────────────────────────────────────

def test_zatverdzhennya_dokumenta_synkhronizuye_stan_i_lyshaye_komentar(klient, s, lyudy):
    d = _dok(s, created_by="Оксана", workflow_status="na_pravtsi")
    a = _podaty(klient, s, "document", d.id, lyudy["oksana"])
    _yak(lyudy["bohdan"])
    klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "zatverdzheno", "note": "добре"})
    s.expire_all()
    assert s.get(Document, d.id).workflow_status == "uzhodzheno"
    assert s.get(Approval, a.id).approver == "Богдан"
    assert s.exec(select(Comment)).one().text == "добре"
    assert s.exec(select(Notification).where(Notification.kind == "rishennya")).one().user == "Оксана"


def test_na_doopratsyuvannya_povertaye_dokument_na_pravku(klient, s, lyudy):
    d = _dok(s, created_by="Оксана", workflow_status="na_pravtsi")
    a = _podaty(klient, s, "document", d.id, lyudy["oksana"])
    _yak(lyudy["bohdan"])
    r = klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "na_doopratsyuvannya"})
    assert "err=bez-komentarya" in r.headers["location"]
    assert s.get(Approval, a.id).is_open
    klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "na_doopratsyuvannya", "note": "додати суму"})
    s.expire_all()
    assert s.get(Document, d.id).workflow_status == "na_pravtsi"
    assert s.get(Approval, a.id).status == "na_doopratsyuvannya"


def test_finalnyi_dokument_ne_zminyuyetsya_rishennyam(klient, s, lyudy):
    d = _dok(s, created_by="Оксана", workflow_status="uzhodzheno", is_final=True)
    a = _podaty(klient, s, "document", d.id, lyudy["oksana"])
    _yak(lyudy["bohdan"])
    klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "na_doopratsyuvannya", "note": "ні"})
    s.expire_all()
    assert s.get(Document, d.id).workflow_status == "uzhodzheno"


def test_avtor_inshoyi_versiyi_ne_zatverdzhuye_cherez_koleghu(klient, s, lyudy):
    _dok(s, created_by="Богдан", doc_group_uid="grp", version=1)
    v2 = _dok(s, created_by="", uploaded_by="Оксана", doc_group_uid="grp", version=2)
    a = _podaty(klient, s, "document", v2.id, lyudy["oksana"])
    _yak(lyudy["bohdan"])
    r = klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "zatverdzheno"})
    assert r.status_code == 403
    assert s.get(Approval, a.id).is_open


def test_vlasnyk_uhody_ne_zatverdzhuye_yiyi(klient, s, lyudy):
    g = Deal(title="Угода Богдана", owner="Богдан", program_id=1, pipeline_id=1, stage_id=1)
    s.add(g)
    s.commit()
    a = _podaty(klient, s, "deal", g.id, lyudy["oksana"])
    _yak(lyudy["bohdan"])
    assert klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "zatverdzheno"}).status_code == 403


def test_rishennya_lyshe_dlya_zatverdzhuvacha(klient, s, lyudy):
    d = _dok(s, created_by="Оксана")
    a = _podaty(klient, s, "document", d.id, lyudy["oksana"])
    _yak(lyudy["marko"])
    assert klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "zatverdzheno"}).status_code == 403
    assert s.get(Approval, a.id).is_open


def test_nevidomyi_status_i_zakryte_podannya(klient, s, lyudy):
    a = _podaty(klient, s, "site", 5, lyudy["oksana"])
    _yak(lyudy["bohdan"])
    klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "vidklykano"})
    assert s.get(Approval, a.id).is_open                     # only two decisions exist
    klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "zatverdzheno"})
    s.expire_all()
    assert s.get(Approval, a.id).status == "zatverdzheno"
    r = klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "na_doopratsyuvannya", "note": "x"})
    assert r.headers["location"] == "/zatverdzhennya"
    s.expire_all()
    assert s.get(Approval, a.id).status == "zatverdzheno"     # a closed decision is not reopened


def test_vidklykaty_mozhe_lyshe_toi_khto_podav(klient, s, lyudy):
    a = _podaty(klient, s, "site", 7, lyudy["oksana"])
    _yak(lyudy["bohdan"])
    klient.post(f"/zatverdzhennya/{a.id}/vidklykaty")
    assert s.get(Approval, a.id).is_open
    _yak(lyudy["oksana"])
    klient.post(f"/zatverdzhennya/{a.id}/vidklykaty")
    s.expire_all()
    assert s.get(Approval, a.id).status == "vidklykano"


def test_storinka_zatverdzhen(klient, s, lyudy):
    a = _podaty(klient, s, "site", 9, lyudy["oksana"])
    _yak(lyudy["bohdan"])
    klient.post(f"/zatverdzhennya/{a.id}/rishennya", data={"status": "zatverdzheno"})
    r = klient.get("/zatverdzhennya", params={"err": "bez-komentarya"})
    assert r.status_code == 200


# ── comments and recommendations ────────────────────────────────────────────

def test_porozhnii_komentar_ne_stvoryuyetsya(klient, s, lyudy):
    klient.post("/komentar", data={"entity": "site", "entity_id": 3, "text": "   "})
    assert s.exec(select(Comment)).first() is None


def test_rishennya_lyshe_dlya_rekomendatsii(klient, s, lyudy):
    c = Comment(entity="site", entity_id=3, author="Оксана", text="просто коментар", kind="komentar")
    s.add(c)
    s.commit()
    assert klient.post(f"/komentar/{c.id}/vyrishyty", data={"resolution": "vrahovano"}).headers["location"] == "/"
    assert s.get(Comment, c.id).resolution in ("", None)


def test_nevidome_rishennya_rekomendatsii_ce_vrahovano(klient, s, lyudy):
    c = Comment(entity="site", entity_id=3, author="Оксана", text="зробіть так", kind="rekomendatsiya")
    s.add(c)
    s.commit()
    klient.post(f"/komentar/{c.id}/vyrishyty", data={"resolution": "zabuty"})
    s.expire_all()
    assert s.get(Comment, c.id).resolution == "vrahovano"
    assert s.exec(select(Notification)).one().user == "Оксана"


def test_komentar_u_zavdannya_nevidomyi(klient, lyudy):
    assert klient.post("/komentar/9999/u-zavdannya").headers["location"] == "/"


# ── notifications, feed, quick tasks ────────────────────────────────────────

def test_spovishchennya_prochytaty_odne_i_vsi(klient, s, lyudy):
    for i in range(3):
        s.add(Notification(user="Марко", kind="komentar", text=f"n{i}"))
    s.add(Notification(user="Оксана", kind="komentar", text="чуже"))
    s.commit()
    r = klient.get("/spovishchennya")
    assert "n0" in r.text and "чуже" not in r.text
    first = s.exec(select(Notification).where(Notification.user == "Марко")).first()
    klient.post("/spovishchennya/prochytano", data={"notif_id": str(first.id)})
    s.expire_all()
    unread = [n for n in s.exec(select(Notification).where(Notification.user == "Марко")) if not n.read_at]
    assert len(unread) == 2
    klient.post("/spovishchennya/prochytano")
    s.expire_all()
    assert all(n.read_at for n in s.exec(select(Notification).where(Notification.user == "Марко")))
    assert not s.exec(select(Notification).where(Notification.user == "Оксана")).one().read_at
    assert klient.get("/spovishchennya/lichylnyk").status_code == 200


def test_strichka_povna_i_htmx_chastyna(klient, lyudy):
    assert klient.get("/strichka", params={"hto": "moyi"}).status_code == 200
    r = klient.get("/strichka", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "<html" not in r.text


def test_shvydke_doruchennya_bez_nazvy_abo_vykonavtsya(klient, s, lyudy):
    r = klient.post("/doruchennya", data={"entity": "site", "entity_id": "3", "title": "", "assignee": "Оксана"})
    assert r.headers["location"].startswith("/obyekty/3")
    r = klient.post("/doruchennya", data={"title": "x", "assignee": ""})
    assert r.headers["location"] == "/"
    assert s.exec(select(Task)).first() is None
