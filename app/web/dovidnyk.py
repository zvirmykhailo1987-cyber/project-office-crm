# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Довідник контактів (ТЗ §4b, модуль M13).

Усі організації та підприємства бази в одному місці. Фільтри — у SQL, не в Python:
база на десятки тисяч рядків, вибірка в пам'ять неприпустима.
"""
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .. import audit, config
from ..db import get_session
from ..fts import fts_upsert, search_ids
from ..i18n import _
from ..templating import bezpechnyi_url
from ..models import (
    ACTIVITY_TYPES,
    CATEGORIES,
    DATA_STATUSES,
    PARTY_ROLES,
    CAP_CRM,
    Activity,
    Counterparty,
    Deal,
    Document,
    Person,
    Site,
    Stage,
    Task,
    User,
    utcnow,
)
from ..normalize import norm_edrpou, norm_phone
from .deps import current_user, redirect, render, require_cap, spivpratsya

router = APIRouter()

# поля, дозволені до інлайн-редагування (білий список — не довіряємо формі)
EDITABLE = {
    "name", "legal_name", "edrpou", "oblast", "raion", "city", "address", "hromada",
    "phone", "phone2", "email", "website", "kved", "category", "roles", "tags",
    "notes", "data_status",
}
# Поля, що рендеряться посиланням (`href`) — приймаємо лише http(s)
URL_POLYA = {"website"}


def _apply_filters(stmt, p: dict):
    """Фільтри довідника — усі в SQL."""
    if p.get("oblast"):
        stmt = stmt.where(Counterparty.oblast == p["oblast"])
    if p.get("raion"):
        stmt = stmt.where(Counterparty.raion == p["raion"])
    if p.get("hromada"):
        stmt = stmt.where(Counterparty.hromada == p["hromada"])
    if p.get("category"):
        stmt = stmt.where(Counterparty.category == p["category"])
    if p.get("role"):
        stmt = stmt.where(Counterparty.roles.contains(p["role"]))
    if p.get("source"):
        stmt = stmt.where(Counterparty.source.contains(p["source"]))
    if p.get("data_status"):
        stmt = stmt.where(Counterparty.data_status == p["data_status"])
    if p.get("has") == "phone":
        stmt = stmt.where(Counterparty.phone != "")
    elif p.get("has") == "email":
        stmt = stmt.where(Counterparty.email != "")
    elif p.get("has") == "no_contact":
        stmt = stmt.where(Counterparty.phone == "", Counterparty.email == "")
    return stmt


# Сортування — БІЛИЙ СПИСОК полів, а не назва колонки з адреси. Інакше
# `?sort=<будь-що>` летить у `getattr`, і адресний рядок стає керуванням моделлю.
# `completeness` — обчислювана властивість Python, у SQL її не існує. Сортувати
# «повноту» після пагінації не можна (це впорядкувало б 50 випадкових рядків),
# тому той самий бал складається виразом у самому запиті. Список ознак ОДИН і
# той самий, що в `Counterparty.completeness` — розійдуться, і колонка почне
# суперечити числу, яке сама ж показує; звірку тримає `test_povnota_zbihayetsya`.
_POVNOTA = sum(
    case((umova, 1), else_=0) for umova in (
        Counterparty.edrpou != "",
        (Counterparty.phone != "") | (Counterparty.phone2 != ""),
        Counterparty.email != "",
        Counterparty.address != "",
        Counterparty.city != "",
        Counterparty.oblast != "",
        Counterparty.kved != "",
        (Counterparty.lat.is_not(None)) & (Counterparty.lon.is_not(None)),
    )
)

SORTS = {
    "name": Counterparty.name,
    "edrpou": Counterparty.edrpou,
    "oblast": Counterparty.oblast,
    "category": Counterparty.category,
    "povnota": _POVNOTA,
}


@router.get("/dovidnyk")
def index(request: Request, q: str = "", page: int = 1,
          oblast: str = "", raion: str = "", hromada: str = "", category: str = "",
          role: str = "", source: str = "", data_status: str = "", has: str = "",
          sort: str = "name", dir: str = "asc",
          user: User = Depends(current_user), s: Session = Depends(get_session)):
    p = dict(oblast=oblast, raion=raion, hromada=hromada, category=category,
             role=role, source=source, data_status=data_status, has=has)

    stmt = select(Counterparty)
    count_stmt = select(func.count()).select_from(Counterparty)
    if q.strip():
        ids = search_ids(s, q, limit=5000)
        if not ids:
            ids = [-1]
        stmt = stmt.where(Counterparty.id.in_(ids))
        count_stmt = count_stmt.where(Counterparty.id.in_(ids))
    stmt = _apply_filters(stmt, p)
    count_stmt = _apply_filters(count_stmt, p)

    total = s.exec(count_stmt).one()
    page = max(1, page)
    # Сортування йде В SQL, ДО пагінації. Впорядкувати вже відрізану сторінку —
    # це перемішати 50 випадкових рядків і назвати це «за повнотою»; така помилка
    # вже була на `/obyekty` (див. `tests/test_obyekty_sort.py`).
    if sort not in SORTS:
        sort, dir = "name", "asc"
    kolonka = SORTS[sort]
    rows = list(s.exec(
        stmt.order_by(kolonka.desc() if dir == "desc" else kolonka.asc(),
                      Counterparty.id)   # стабільний тайбрейк: інакше рядки з
                                         # однаковим значенням стрибають між сторінками
        .offset((page - 1) * config.PAGE_SIZE)
        .limit(config.PAGE_SIZE)
    ))

    oblasts = list(s.exec(
        select(Counterparty.oblast).where(Counterparty.oblast != "")
        .distinct().order_by(Counterparty.oblast)
    ))

    # рядок запиту без page — щоб пагінація не губила фільтри
    # Два рядки запиту: пагінація тягне сортування з собою, а посилання в шапці
    # додає СВОЄ — тому там сортування бути не повинно, інакше в адресі
    # опиниться два `sort`, і виграє випадковий.
    base_qs_bez_sort = urlencode({k: v for k, v in ({"q": q} | p).items() if v})
    base_qs = urlencode({k: v for k, v in
                         ({"q": q} | p | {"sort": sort, "dir": dir}).items() if v})

    tpl = "dovidnyk/_rows.html" if request.headers.get("HX-Request") else "dovidnyk/index.html"
    return render(request, tpl, user=user, rows=rows, total=total, q=q, page=page,
                  page_size=config.PAGE_SIZE, params=p, oblasts=oblasts,
                  base_qs=base_qs, base_qs_bez_sort=base_qs_bez_sort,
                  categories=CATEGORIES, party_roles=PARTY_ROLES,
                  data_statuses=DATA_STATUSES, sort=sort, dir=dir)


@router.get("/dovidnyk/{cp_id}")
def card(cp_id: int, request: Request, user: User = Depends(current_user),
         s: Session = Depends(get_session)):
    cp = s.get(Counterparty, cp_id)
    if not cp:
        return redirect("/dovidnyk")
    persons = list(s.exec(
        select(Person).where(Person.counterparty_id == cp_id)
        .order_by(Person.is_primary.desc(), Person.name)
    ))

    # Історія контактів. До 06.08.2026 тут стояло «Зʼявиться у фазі Ф2» — але
    # `Activity` існує й пишеться з картки угоди від самого початку; бракувало
    # не даних, а виводу.
    #
    # Беремо активності, привʼязані до контрагента ПРЯМО або через його угоди:
    # менеджер записує дзвінок у картці угоди, і без другої гілки історія
    # контрагента виглядала б порожньою саме там, де роботи найбільше.
    ugody = list(s.exec(select(Deal).where(Deal.counterparty_id == cp_id)
                        .order_by(Deal.status, Deal.updated_at.desc())))
    ugody_ids = [d.id for d in ugody]
    umova = Activity.counterparty_id == cp_id
    if ugody_ids:
        umova = umova | Activity.deal_id.in_(ugody_ids)
    aktyvnosti = list(s.exec(
        select(Activity).where(umova).order_by(Activity.when.desc()).limit(50)))

    # ── 360°: усе, що привʼязане до цієї компанії наявними FK ────────────────
    # Досі картка це НЕ показувала — була глухим кутом: з неї не видно, у яких
    # угодах компанія, які в неї майданчики, документи, завдання. Дані лежали в
    # базі; бракувало виводу. Це і є перехід «довідник» → «CRM».
    stadiyi = {st.id: st for st in s.exec(
        select(Stage).where(Stage.id.in_([d.stage_id for d in ugody] or [-1])))}
    obyekty = list(s.exec(select(Site).where(Site.counterparty_id == cp_id)
                          .order_by(Site.site_type, Site.name).limit(100)))
    dokumenty = list(s.exec(
        select(Document).where(Document.counterparty_id == cp_id)
        .order_by(Document.created_at.desc()).limit(30)))
    # Завдання компанії — і привʼязані прямо, і через її угоди: інакше завдання
    # «передзвонити по угоді Х» не видно з картки компанії, де його й шукають.
    t_umova = Task.counterparty_id == cp_id
    if ugody_ids:
        t_umova = t_umova | Task.deal_id.in_(ugody_ids)
    zavdannya = list(s.exec(
        select(Task).where(t_umova, Task.status.in_(["open", "in_progress"]))
        .order_by(Task.due_date.is_(None), Task.due_date).limit(20)))

    return render(request, "dovidnyk/card.html", user=user, cp=cp, persons=persons,
                  categories=CATEGORIES, party_roles=PARTY_ROLES,
                  data_statuses=DATA_STATUSES, editable=sorted(EDITABLE),
                  aktyvnosti=aktyvnosti, activity_types=ACTIVITY_TYPES,
                  ugody=ugody, stadiyi=stadiyi, obyekty=obyekty,
                  dokumenty=dokumenty, zavdannya=zavdannya, dnes=date.today(),
                  **spivpratsya(s, "counterparty", cp_id))


@router.post("/dovidnyk/{cp_id}/activity")
def add_activity(cp_id: int, type: str = Form("Дзвінок"), outcome: str = Form(""),
                 note: str = Form(""), user: User = Depends(require_cap(CAP_CRM)),
                 s: Session = Depends(get_session)):
    """Запис активності просто з картки контрагента.

    Тут НЕМАЄ автопереходу стадії, на відміну від такого ж запису в картці угоди
    (`voronky.add_activity`): контакт без угоди нікуди не рухає воронку, і
    вигадувати, до якої саме угоди його приткнути, було б здогадкою за людину.
    """
    cp = s.get(Counterparty, cp_id)
    if not cp:
        return redirect("/dovidnyk")
    if not outcome.strip():
        return redirect(f"/dovidnyk/{cp_id}?err=outcome")

    a = Activity(type=type if type in ACTIVITY_TYPES else "Нотатка",
                 counterparty_id=cp_id, author=user.name,
                 outcome=outcome.strip(), note=note.strip(), created_by=user.name)
    s.add(a)
    audit.log(s, user.name, "counterparty.activity", entity="counterparty",
              entity_id=cp_id, tier=audit.TIER_CHANGE, detail=f"{a.type}: {a.outcome}")
    s.commit()
    return redirect(f"/dovidnyk/{cp_id}")


@router.post("/dovidnyk/{cp_id}/field")
def edit_field(cp_id: int, field: str = Form(...), value: str = Form(""),
               user: User = Depends(require_cap(CAP_CRM)),
               s: Session = Depends(get_session)):
    """Інлайн-правка одного поля. Tier1 — зворотна, пишеться в аудит."""
    cp = s.get(Counterparty, cp_id)
    if not cp or field not in EDITABLE:
        return {"ok": False, "error": "Поле недоступне для редагування"}
    if field == "edrpou":
        value = norm_edrpou(value)
    elif field in ("phone", "phone2"):
        value = norm_phone(value)
    elif field in URL_POLYA:
        value = value.strip()
        # Поле рендериться як href: `javascript:`/`data:` тут — це XSS в один клік
        if value and not bezpechnyi_url(value):
            return JSONResponse({"ok": False,
                                 "error": _("Адреса має починатися з http:// або https://")},
                                status_code=400)
    before = getattr(cp, field)
    if before == value:
        return {"ok": True, "value": value, "changed": False}
    if field == "edrpou" and value:
        inshyi = s.exec(select(Counterparty.id).where(Counterparty.edrpou == value,
                                                      Counterparty.id != cp_id)).first()
        if inshyi:
            return {"ok": False,
                    "error": _("Цей ЄДРПОУ вже має інший контрагент") + f" (#{inshyi})"}
    setattr(cp, field, value)
    cp.updated_at = utcnow()
    s.add(cp)
    audit.log(s, user.name, "counterparty.edit", entity="counterparty", entity_id=cp_id,
              tier=audit.TIER_CHANGE, detail=f"{field}: «{before}» → «{value}»")
    try:
        s.commit()
    except IntegrityError:
        # Унікальний індекс бази (uq_cp_edrpou) — на випадок гонки двох правок
        s.rollback()
        return {"ok": False, "error": _("Цей ЄДРПОУ вже має інший контрагент")}
    s.refresh(cp)
    fts_upsert(s, cp)
    s.commit()
    return {"ok": True, "value": value, "changed": True}
