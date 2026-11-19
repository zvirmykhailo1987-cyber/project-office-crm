# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Шар співпраці (Ф8): затвердження · коментарі й рекомендації · сповіщення · доручення.

Роути викликаємо як звичайні функції (передаючи `user` і `s` напряму) — це тестує
саме правила, а не транспорт. Перевіряємо те, чого раніше в системі не було й що
Марко назвав процесом: «Богдан затверджує або каже, що поміняти», «кожен може
дати рекомендацію», «кожен оперативно бачить і реагує», «кожен кожному може дати
оперативне завдання».
"""
import pytest
from fastapi import HTTPException

from sqlmodel import select

from app.models import (Approval, Deal, Notification, Pipeline, Program, Stage,
                        Task, User)
from app.seed_pipelines import seed
from app.services import collab as svc
from app.web import collab as web


# ── фікстури людей ───────────────────────────────────────────────────────────

@pytest.fixture()
def lyudy(s):
    """Чотири реальні ролі офісу: автор роботи, затверджувач і дві переговорниці."""
    u = {
        "marko": User(name="Марко", login="marko", role="rozrobnyk"),
        "kostyantyn": User(name="Богдан", login="kostyantyn", role="perehovorschyk_dok"),
        "oksana": User(name="Оксана", login="oksana", role="perehovory_kontakt"),
        "tetyana": User(name="Галина", login="tetyana", role="zapyty_lysty"),
    }
    for x in u.values():
        s.add(x)
    s.commit()
    return u


@pytest.fixture()
def uhoda(s):
    seed(s)
    prog = s.exec(select(Program).where(Program.code == "biogaz")).one()
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one()
    st = s.exec(select(Stage).where(Stage.pipeline_id == pipe.id)
                .order_by(Stage.order)).first()
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=st.id,
             title="Хаб Тернопіль", owner="Марко", created_by="Марко")
    s.add(d)
    s.commit()
    return d


def _notif(s, name):
    return list(s.exec(select(Notification).where(Notification.user == name)))


# ── затвердження: два тверді правила ─────────────────────────────────────────

def test_avtor_ne_zatverdzhuye_sebe(s, lyudy, uhoda):
    """Головне правило процесу: рішення про роботу приймає колега, не автор."""
    web.submit(entity="deal", entity_id=uhoda.id, note="стратегія хабу",
               user=lyudy["marko"], s=s)
    a = svc.approval_for(s, "deal", uhoda.id)
    assert a.status == "na_rozglyadi"

    with pytest.raises(HTTPException) as e:
        web.decide(a.id, status="zatverdzheno", note="", user=lyudy["marko"], s=s)
    assert e.value.status_code == 403
    s.refresh(a)
    assert a.status == "na_rozglyadi"          # рішення не пройшло


def test_lyshe_kostyantyn_maye_pravo_zatverdzhuvaty(lyudy):
    """CAP_APPROVE свідомо НЕ входить у набір адміністратора."""
    from app.models import CAP_APPROVE
    assert lyudy["kostyantyn"].can(CAP_APPROVE)
    assert not lyudy["marko"].can(CAP_APPROVE)     # хоч і адмін
    assert not lyudy["oksana"].can(CAP_APPROVE)
    assert not lyudy["tetyana"].can(CAP_APPROVE)


def test_na_doopratsyuvannya_bez_komentarya_ne_prohodyt(s, lyudy, uhoda):
    """«Каже, що треба поміняти» без пояснення — не рішення."""
    web.submit(entity="deal", entity_id=uhoda.id, note="ТЗ", user=lyudy["marko"], s=s)
    a = svc.approval_for(s, "deal", uhoda.id)

    web.decide(a.id, status="na_doopratsyuvannya", note="   ",
               user=lyudy["kostyantyn"], s=s)
    s.refresh(a)
    assert a.status == "na_rozglyadi"

    web.decide(a.id, status="na_doopratsyuvannya", note="поміняти CAPEX на €70.6M",
               user=lyudy["kostyantyn"], s=s)
    s.refresh(a)
    assert a.status == "na_doopratsyuvannya"
    assert a.approver == "Богдан"


def test_rishennya_spovishchaye_avtora_i_lyshaye_komentar(s, lyudy, uhoda):
    web.submit(entity="deal", entity_id=uhoda.id, note="ТЗ", user=lyudy["marko"], s=s)
    a = svc.approval_for(s, "deal", uhoda.id)
    web.decide(a.id, status="na_doopratsyuvannya", note="поміняти строки",
               user=lyudy["kostyantyn"], s=s)

    n = [x for x in _notif(s, "Марко") if x.kind == "rishennya"]
    assert len(n) == 1 and "поміняти строки" in n[0].text
    # рішення видно і в обговоренні сутності, а не лише в черзі
    assert any("поміняти строки" == c.text for c in svc.comments_for(s, "deal", uhoda.id))


def test_podannya_spovishchaye_zatverdzhuvacha_i_ne_dublyuyetsya(s, lyudy, uhoda):
    web.submit(entity="deal", entity_id=uhoda.id, note="перше", user=lyudy["marko"], s=s)
    web.submit(entity="deal", entity_id=uhoda.id, note="друге", user=lyudy["marko"], s=s)
    assert len(list(s.exec(select(Approval)))) == 1        # уже на розгляді — не дублюємо
    assert [x.kind for x in _notif(s, "Богдан")] == ["na_zatverdzhennya"]
    assert _notif(s, "Марко") == []                      # собі не шлемо


def test_povtorne_podannya_zberihaye_lantyug(s, lyudy, uhoda):
    """Історія рішень не губиться: друга подача посилається на першу."""
    web.submit(entity="deal", entity_id=uhoda.id, note="v1", user=lyudy["marko"], s=s)
    a1 = svc.approval_for(s, "deal", uhoda.id)
    web.decide(a1.id, status="na_doopratsyuvannya", note="поміняти",
               user=lyudy["kostyantyn"], s=s)
    web.submit(entity="deal", entity_id=uhoda.id, note="v2", user=lyudy["marko"], s=s)
    a2 = svc.approval_for(s, "deal", uhoda.id)
    assert a2.id != a1.id and a2.parent_approval_id == a1.id


def test_odnakovyi_chas_podannya_ne_plutaye_lantyug(s, lyudy, uhoda):
    """Регрес (25.09.2026): на Windows годинник тікає ~15 мс, тож дві подачі
    нерідко отримують ОДНАКОВИЙ `submitted_at`. Сортування лише за часом тоді
    віддавало стару подачу замість нової (нестабільний тест вище, ~13 % прогонів).
    Порядок мусить бути детермінованим: час, потім id."""
    web.submit(entity="deal", entity_id=uhoda.id, note="v1", user=lyudy["marko"], s=s)
    a1 = svc.approval_for(s, "deal", uhoda.id)
    web.decide(a1.id, status="na_doopratsyuvannya", note="поміняти",
               user=lyudy["kostyantyn"], s=s)
    web.submit(entity="deal", entity_id=uhoda.id, note="v2", user=lyudy["marko"], s=s)
    a2 = s.exec(select(Approval).where(Approval.note == "v2")).one()
    a2.submitted_at = a1.submitted_at            # примусова «нічия» за часом
    s.add(a2)
    s.commit()

    for _ in range(5):                           # не одна вдала спроба, а стабільно
        assert svc.approval_for(s, "deal", uhoda.id).id == a2.id

    # Черга теж детермінована: при нічиї — у порядку подання (за id).
    b = [Approval(entity="deal", entity_id=uhoda.id, submitted_by="Оксана",
                  submitted_at=a1.submitted_at, note=f"q{i}") for i in range(3)]
    for x in b:
        s.add(x)
    s.commit()
    q = [a.id for a in svc.pending_approvals(s)]
    assert q == sorted(q)


# ── коментарі, рекомендації, згадки ──────────────────────────────────────────

def test_kozhen_mozhe_daty_rekomendatsiyu(s, lyudy, uhoda):
    for who in ("oksana", "tetyana", "kostyantyn"):
        web.add_comment(entity="deal", entity_id=uhoda.id, kind="rekomendatsiya",
                        text=f"рекомендація від {who}", user=lyudy[who], s=s)
    rows = svc.comments_for(s, "deal", uhoda.id)
    assert len(rows) == 3 and all(c.is_open_recommendation for c in rows)


def test_rekomendatsiya_vidhylyayetsya_lyshe_z_prychynoyu(s, lyudy, uhoda):
    web.add_comment(entity="deal", entity_id=uhoda.id, kind="rekomendatsiya",
                    text="додати другий хаб", user=lyudy["oksana"], s=s)
    c = svc.comments_for(s, "deal", uhoda.id)[0]

    web.resolve_comment(c.id, resolution="ne_beru", note="", user=lyudy["marko"], s=s)
    s.refresh(c)
    assert c.resolution == ""                    # без причини — не приймаємо

    web.resolve_comment(c.id, resolution="ne_beru", note="немає сировини",
                        user=lyudy["marko"], s=s)
    s.refresh(c)
    assert c.resolution == "ne_beru" and c.resolved_by == "Марко"
    assert any("немає сировини" in x.text for x in _notif(s, "Оксана"))


def test_zgadka_rezolvyt_lyshe_isnuyuchi_logini(s, lyudy, uhoda):
    web.add_comment(entity="deal", entity_id=uhoda.id,
                    text="@oksana гляньте, @nikoho такого немає",
                    user=lyudy["tetyana"], s=s)
    c = svc.comments_for(s, "deal", uhoda.id)[0]
    assert c.mention_list == ["oksana"]           # неіснуючий логін не падає й не вигадується
    assert [x.kind for x in _notif(s, "Оксана")] == ["zgadka"]


def test_komentar_ne_ruhaye_metryky_voronky(s, lyudy, uhoda):
    """Comment — НЕ Activity: обговорення не має вдавати контакт із контрагентом."""
    was = uhoda.last_contacted_at
    web.add_comment(entity="deal", entity_id=uhoda.id, text="обговорення всередині",
                    user=lyudy["oksana"], s=s)
    s.refresh(uhoda)
    assert uhoda.last_contacted_at == was


# ── оперативні доручення ─────────────────────────────────────────────────────

def test_doruchennya_z_kartky_spovishchaye_vykonavtsya(s, lyudy, uhoda):
    """Галина прямо з картки ставить завдання Оксані — Оксана бачить це лічильником."""
    web.quick_task(entity="deal", entity_id=str(uhoda.id),
                   title="уточнити телефон голови", assignee="Оксана", due_date="", priority="normal",
                   user=lyudy["tetyana"], s=s)
    t = s.exec(select(Task)).first()
    assert t.assignee == "Оксана" and t.created_by == "Галина"
    assert t.source == "vid_koleghy" and t.from_colleague
    assert t.deal_id == uhoda.id                 # звʼязок із сутністю збережено
    assert svc.unread_count(s, "Оксана") == 1


def test_vidmova_vid_zavdannya_lyshe_z_prychynoyu(s, lyudy, uhoda):
    web.quick_task(entity="deal", entity_id=str(uhoda.id), title="подзвонити",
                   assignee="Оксана", due_date="", priority="normal", user=lyudy["tetyana"], s=s)
    t = s.exec(select(Task)).first()

    web.decline_task(t.id, reason="   ", user=lyudy["oksana"], s=s)
    s.refresh(t)
    assert t.status != "cancelled" and not t.declined_reason

    web.decline_task(t.id, reason="немає контакту в базі", user=lyudy["oksana"], s=s)
    s.refresh(t)
    assert t.status == "cancelled" and t.declined_reason == "немає контакту в базі"
    assert any(x.kind == "zavdannya_vidmova" for x in _notif(s, "Галина"))


def test_vzyaty_v_robotu_vidpovidaye_postanovnyku(s, lyudy, uhoda):
    web.quick_task(entity="deal", entity_id=str(uhoda.id), title="подзвонити",
                   assignee="Оксана", due_date="", priority="normal", user=lyudy["tetyana"], s=s)
    t = s.exec(select(Task)).first()
    web.accept_task(t.id, user=lyudy["oksana"], s=s)
    s.refresh(t)
    assert t.status == "in_progress" and t.accepted_at
    assert any("взято в роботу" in x.text for x in _notif(s, "Галина"))


def test_chuzhe_zavdannya_ne_zakryty_i_ne_vidhylyty(s, lyudy, uhoda):
    web.quick_task(entity="deal", entity_id=str(uhoda.id), title="подзвонити",
                   assignee="Оксана", due_date="", priority="normal", user=lyudy["tetyana"], s=s)
    t = s.exec(select(Task)).first()
    web.accept_task(t.id, user=lyudy["kostyantyn"], s=s)
    web.decline_task(t.id, reason="не хочу", user=lyudy["kostyantyn"], s=s)
    s.refresh(t)
    assert t.status == "open" and not t.declined_reason


def test_rekomendatsiya_staye_zavdannyam_odnym_klikom(s, lyudy, uhoda):
    web.add_comment(entity="deal", entity_id=uhoda.id, kind="rekomendatsiya",
                    text="перевірити ЄДРПОУ якірного", user=lyudy["kostyantyn"], s=s)
    c = svc.comments_for(s, "deal", uhoda.id)[0]
    web.comment_to_task(c.id, assignee="Галина", due_date="", user=lyudy["kostyantyn"], s=s)

    t = s.exec(select(Task)).first()
    assert t.comment_id == c.id and t.source == "vid_komentarya"
    assert t.deal_id == uhoda.id and "Богдан" in t.note
    assert svc.unread_count(s, "Галина") == 1


# ── сповіщення й стрічка ─────────────────────────────────────────────────────

def test_sobi_spovishchennya_ne_shlemo(s, lyudy, uhoda):
    svc.notify(s, user="Марко", kind="komentar", text="сам собі",
               from_whom="Марко")
    s.commit()
    assert svc.unread_count(s, "Марко") == 0


def test_prochytano_znymaye_lichylnyk(s, lyudy, uhoda):
    web.quick_task(entity="deal", entity_id=str(uhoda.id), title="а", assignee="Оксана", due_date="", priority="normal",
                   user=lyudy["tetyana"], s=s)
    web.quick_task(entity="deal", entity_id=str(uhoda.id), title="б", assignee="Оксана", due_date="", priority="normal",
                   user=lyudy["tetyana"], s=s)
    assert svc.unread_count(s, "Оксана") == 2
    svc.mark_read(s, "Оксана")
    s.commit()
    assert svc.unread_count(s, "Оксана") == 0


def test_strichka_daye_lyudsku_frazu_i_posylannya(s, lyudy, uhoda):
    web.submit(entity="deal", entity_id=uhoda.id, note="ТЗ", user=lyudy["marko"], s=s)
    items = svc.feed(s, limit=20)
    assert items, "стрічка порожня — аудит не рендериться"
    top = items[0]
    assert top.actor == "Марко"
    assert top.phrase and "approval.submit" not in top.phrase   # не сирий код
    assert top.url == f"/voronky/{uhoda.id}"


def test_strichka_filtruyetsya_po_avtoru(s, lyudy, uhoda):
    web.add_comment(entity="deal", entity_id=uhoda.id, text="раз",
                    user=lyudy["oksana"], s=s)
    web.add_comment(entity="deal", entity_id=uhoda.id, text="два",
                    user=lyudy["tetyana"], s=s)
    assert {i.actor for i in svc.feed(s, actor="Оксана")} == {"Оксана"}


# ── «мій день» бачить новий шар ──────────────────────────────────────────────

def test_den_pokazuye_chergu_zatverdzhen_lyshe_zatverdzhuvachu(s, lyudy, uhoda):
    from app.services import den
    web.submit(entity="deal", entity_id=uhoda.id, note="стратегія",
               user=lyudy["marko"], s=s)

    d_kost = den.build_day(s, lyudy["kostyantyn"])
    assert any(i.kind == "approval" for i in d_kost.urgent + d_kost.today)

    d_ira = den.build_day(s, lyudy["oksana"])
    assert not any(i.kind == "approval" for i in d_ira.urgent + d_ira.today)


def test_den_pokazuye_neprochytani_spovishchennya(s, lyudy, uhoda):
    from app.services import den
    web.quick_task(entity="deal", entity_id=str(uhoda.id), title="подзвонити голові",
                   assignee="Оксана", due_date="", priority="normal", user=lyudy["tetyana"], s=s)
    d = den.build_day(s, lyudy["oksana"])
    assert any(i.kind == "notif" and "подзвонити голові" in i.title for i in d.urgent)


# ── Ф9: новий проєкт із дорученням однією дією ───────────────────────────────

def test_novyi_proekt_odrazu_doruchaye(s, lyudy, uhoda):
    """Богдан заводить проєкт і ставить задачу Маркові — однією дією."""
    from app.web import voronky
    from app.models import Program

    prog = s.exec(select(Program).where(Program.code == "biogaz")).one()
    voronky.create(title="Хаб Бучач", program=prog.code, counterparty_id="",
                   owner="Богдан", next_action_date="", next_action_note="",
                   doruchyty_komu="Марко",
                   doruchyty_scho="Підготувати попередню презентацію",
                   doruchyty_koly="", user=lyudy["kostyantyn"], s=s)

    d = s.exec(select(Deal).where(Deal.title == "Хаб Бучач")).one()
    assert d.owner == "Богдан"                    # хто завів — той і власник
    t = s.exec(select(Task).where(Task.deal_id == d.id)).one()
    assert t.assignee == "Марко" and t.from_colleague
    assert t.title == "Підготувати попередню презентацію"
    assert svc.unread_count(s, "Марко") == 1       # дізнався одразу, не випадково


def test_proekt_bez_doruchennya_ne_stvoryuye_zavdannya(s, lyudy, uhoda):
    from app.web import voronky
    from app.models import Program
    prog = s.exec(select(Program).where(Program.code == "biogaz")).one()
    voronky.create(title="Без доручення", program=prog.code, counterparty_id="",
                   owner="", next_action_date="", next_action_note="",
                   doruchyty_komu="", doruchyty_scho="", doruchyty_koly="",
                   user=lyudy["kostyantyn"], s=s)
    d = s.exec(select(Deal).where(Deal.title == "Без доручення")).one()
    assert not list(s.exec(select(Task).where(Task.deal_id == d.id)))


def test_sobi_doruchennya_ne_stvoryuyetsya(s, lyudy, uhoda):
    """Доручити самому собі — не доручення, а просто робота; завдання не заводимо."""
    from app.web import voronky
    from app.models import Program
    prog = s.exec(select(Program).where(Program.code == "biogaz")).one()
    voronky.create(title="Сам собі", program=prog.code, counterparty_id="", owner="",
                   next_action_date="", next_action_note="",
                   doruchyty_komu="Богдан", doruchyty_scho="щось зробити",
                   doruchyty_koly="", user=lyudy["kostyantyn"], s=s)
    d = s.exec(select(Deal).where(Deal.title == "Сам собі")).one()
    assert not list(s.exec(select(Task).where(Task.deal_id == d.id)))
    assert svc.unread_count(s, "Богдан") == 0


def test_den_pokazuye_proekty_bez_prezentatsii(s, lyudy, uhoda):
    """Черга Марка — проєкти, де попередньої презентації ще немає."""
    from app.services import den
    from app.models import Document, KIND_PREZENTATSIYA

    d = den.build_day(s, lyudy["marko"])
    assert any(i.kind == "preza" and uhoda.title in i.title
               for i in d.urgent + d.today), "проєкт без презентації не потрапив у чергу"

    s.add(Document(title="Презентація хабу", kind=KIND_PREZENTATSIYA, deal_id=uhoda.id,
                   created_by="Марко"))
    s.commit()
    d2 = den.build_day(s, lyudy["marko"])
    assert not any(i.kind == "preza" for i in d2.urgent + d2.today), \
        "після появи презентації проєкт має зникнути з черги"


def test_cherga_prezentatsiy_lyshe_u_rozrobnyka(s, lyudy, uhoda):
    from app.services import den
    for hto in ("kostyantyn", "oksana", "tetyana"):
        d = den.build_day(s, lyudy[hto])
        assert not any(i.kind == "preza" for i in d.urgent + d.today + d.soon)
