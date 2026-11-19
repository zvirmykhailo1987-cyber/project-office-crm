"""Обʼєкти, хаби, скоринг і карта (ТЗ §4, модулі M5–M7)."""
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func
from sqlmodel import Session, select

from .. import audit, config
from ..db import get_session
from ..models import (
    CAP_CRM,
    CLUSTER_TYPE_LABELS,
    CLUSTER_STAGES,
    MEMBER_STATUS,
    SITE_TYPES,
    Cluster,
    ClusterMember,
    Counterparty,
    Deal,
    Stage,
    Score,
    ScoreComponent,
    Site,
    Task,
    User,
    utcnow,
)
from ..services import scoring
from .deps import current_user, redirect, render, require_cap, spivpratsya

router = APIRouter()

SORTS = {"score": "за балом", "name": "за назвою", "kw": "за потужністю"}


def _priority(value: float) -> str:
    """Та сама шкала, що в Score.priority — але без завантаження обʼєкта."""
    if value >= 65:
        return "Високий"
    return "Середній" if value >= 45 else "Низький"


def _scores(s: Session, site_ids: list[int], model: str = "") -> dict[int, Score]:
    """Бали за id сайтів. МОДЕЛЬ обовʼязкова там, де їх може бути кілька.

    На громаді-кандидаті живуть дві моделі одночасно (C1-C9 і рейтинг кандидатів) —
    без фільтра по моделі сюди потрапляє випадкова з двох, залежно від порядку рядків.
    """
    if not site_ids:
        return {}
    stmt = select(Score).where(Score.site_id.in_(site_ids))
    if model:
        stmt = stmt.where(Score.model == model)
    return {sc.site_id: sc for sc in s.exec(stmt)}


@router.get("/obyekty")
def index(request: Request, q: str = "", site_type: str = "biogaz_object",
          oblast: str = "", cluster_id: int = 0, member_status: str = "",
          sort: str = "score", page: int = 1,
          user: User = Depends(current_user), s: Session = Depends(get_session)):
    stmt = select(Site).where(Site.site_type == site_type)
    cnt = select(func.count()).select_from(Site).where(Site.site_type == site_type)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Site.name.like(like))
        cnt = cnt.where(Site.name.like(like))
    if oblast:
        stmt, cnt = stmt.where(Site.oblast == oblast), cnt.where(Site.oblast == oblast)
    if cluster_id:
        stmt = stmt.where(Site.cluster_id == cluster_id)
        cnt = cnt.where(Site.cluster_id == cluster_id)
    if member_status:
        stmt = stmt.where(Site.member_status == member_status)
        cnt = cnt.where(Site.member_status == member_status)

    total = s.exec(cnt).one()
    page = max(1, page)
    model = scoring.MODEL_ZA_TYPOM.get(site_type) or ""

    if sort == "name":
        stmt = stmt.order_by(Site.name)
    elif sort == "kw":
        stmt = stmt.order_by(Site.est_capacity_kw_el.desc())
    elif sort == "score":
        # Сортування за балом мусить іти В SQL, ДО пагінації.
        # Раніше сторінку спершу відрізали, а потім упорядковували в Python —
        # тобто «за балом» перемішувало 50 випадкових рядків. Перевірено на живій
        # базі 06.08.2026: максимум на 1-й сторінці 78.9 при справжньому 83.6,
        # три найкращі обʼєкти не потрапляли на неї взагалі.
        # LEFT JOIN, а не INNER: обʼєкт без балу має лишатись у списку — інакше
        # «невідомо» тихо зникає з вибірки (Г15).
        stmt = (stmt.outerjoin(Score, (Score.site_id == Site.id)
                               & (Score.model == model))
                    .order_by(Score.value.is_(None), Score.value.desc()))

    rows = list(s.exec(stmt.offset((page - 1) * config.PAGE_SIZE)
                       .limit(config.PAGE_SIZE)))
    scores = _scores(s, [r.id for r in rows], model)

    oblasts = list(s.exec(select(Site.oblast).where(Site.oblast != "",
                                                    Site.site_type == site_type)
                          .distinct().order_by(Site.oblast)))
    base_qs = urlencode({k: v for k, v in {
        "q": q, "site_type": site_type, "oblast": oblast,
        "cluster_id": cluster_id or "", "member_status": member_status, "sort": sort,
    }.items() if v})

    return render(request, "obyekty/index.html", user=user, rows=rows, scores=scores,
                  total=total, q=q, page=page, page_size=config.PAGE_SIZE,
                  site_type=site_type, site_types=SITE_TYPES, oblast=oblast,
                  oblasts=oblasts, member_status=member_status,
                  member_statuses=MEMBER_STATUS, sort=sort, sorts=SORTS,
                  base_qs=base_qs, cluster_id=cluster_id)


@router.get("/obyekty/{site_id}")
def card(site_id: int, request: Request, user: User = Depends(current_user),
         s: Session = Depends(get_session)):
    site = s.get(Site, site_id)
    if not site:
        return redirect("/obyekty")
    # На картці показуємо бал ТІЄЇ моделі, яка передбачена для типу обʼєкта
    model = scoring.MODEL_ZA_TYPOM.get(site.site_type, "fat_score")
    sc = s.exec(select(Score).where(Score.site_id == site_id,
                                    Score.model == model)).first() if model else None
    comps = list(s.exec(select(ScoreComponent).where(ScoreComponent.score_id == sc.id)
                        .order_by(ScoreComponent.contribution.desc()))) if sc else []
    cp = s.get(Counterparty, site.counterparty_id) if site.counterparty_id else None
    cluster = s.get(Cluster, site.cluster_id) if site.cluster_id else None
    # Угоди контрагента цього обʼєкта: саме тут менеджер вирішує «є вже угода
    # чи заводити» — доти з картки обʼєкта це не було видно.
    ugody = list(s.exec(select(Deal).where(Deal.counterparty_id == site.counterparty_id)
                        .order_by(Deal.status))) if site.counterparty_id else []
    stadiyi = {st.id: st for st in s.exec(
        select(Stage).where(Stage.id.in_([d.stage_id for d in ugody] or [-1])))}
    return render(request, "obyekty/card.html", user=user, site=site, score=sc,
                  comps=comps, cp=cp, cluster=cluster, member_statuses=MEMBER_STATUS,
                  ugody=ugody, stadiyi=stadiyi,
                  **spivpratsya(s, "site", site_id))


@router.post("/obyekty/{site_id}/status")
def set_status(site_id: int, member_status: str = Form(""),
               user: User = Depends(require_cap(CAP_CRM)),
               s: Session = Depends(get_session)):
    site = s.get(Site, site_id)
    if site and member_status in MEMBER_STATUS:
        site.member_status = member_status
        site.updated_at = utcnow()
        s.add(site)
        audit.log(s, user.name, "site.status", entity="site", entity_id=site_id,
                  tier=audit.TIER_CHANGE, detail=member_status)
        s.commit()
    return redirect(f"/obyekty/{site_id}")


@router.post("/obyekty/{site_id}/kryterii")
def set_criteria(site_id: int,
                 c1_grain_t_yr: str = Form(""), c2_underserved_ha: str = Form(""),
                 c3_ind_park: str = Form(""), c4_rail: str = Form(""),
                 c5_road: str = Form(""), c6_power_mw: str = Form(""),
                 c7_guarantee_ready: str = Form(""), c8_ahc_ready: str = Form(""),
                 c9_transparency: str = Form(""),
                 user: User = Depends(require_cap(CAP_CRM)),
                 s: Session = Depends(get_session)):
    """Анкета COM-05. Порожнє поле лишається «немає даних», а НЕ нулем:
    нуль означав би «перевірили — немає», порожнє означає «ще не питали»."""
    site = s.get(Site, site_id)
    if not site:
        return redirect("/obyekty")

    incoming = {
        "c1_grain_t_yr": c1_grain_t_yr, "c2_underserved_ha": c2_underserved_ha,
        "c3_ind_park": c3_ind_park, "c4_rail": c4_rail, "c5_road": c5_road,
        "c6_power_mw": c6_power_mw, "c7_guarantee_ready": c7_guarantee_ready,
        "c8_ahc_ready": c8_ahc_ready, "c9_transparency": c9_transparency,
    }
    changed = []
    for key, raw in incoming.items():
        raw = (raw or "").strip().replace(",", ".")
        new = None
        if raw:
            try:
                new = float(raw)
            except ValueError:
                continue
        if getattr(site, key) != new:
            setattr(site, key, new)
            changed.append(f"{key}={new if new is not None else 'немає даних'}")

    if changed:
        site.updated_at = utcnow()
        s.add(site)
        s.flush()
        res = scoring.score_site(site)
        if res:
            scoring.save(s, site.id, res)
        audit.log(s, user.name, "site.criteria", entity="site", entity_id=site_id,
                  tier=audit.TIER_CHANGE, detail="; ".join(changed))
        s.commit()
    return redirect(f"/obyekty/{site_id}")


@router.get("/haby")
def clusters(request: Request, oblast: str = "", user: User = Depends(current_user),
             s: Session = Depends(get_session)):
    stmt = select(Cluster)
    if oblast:
        stmt = stmt.where(Cluster.oblast == oblast)
    rows = list(s.exec(stmt.order_by(Cluster.total_mw.desc())))
    oblasts = list(s.exec(select(Cluster.oblast).where(Cluster.oblast != "")
                          .distinct().order_by(Cluster.oblast)))
    return render(request, "obyekty/haby.html", user=user, rows=rows, oblast=oblast,
                  oblasts=oblasts, labels=CLUSTER_TYPE_LABELS)


@router.get("/haby/{cluster_id}")
def cluster_card(cluster_id: int, request: Request,
                 user: User = Depends(current_user), s: Session = Depends(get_session)):
    c = s.get(Cluster, cluster_id)
    if not c:
        return redirect("/haby")
    members = list(s.exec(select(ClusterMember)
                          .where(ClusterMember.cluster_id == cluster_id)))
    sites = {x.id: x for x in s.exec(select(Site).where(
        Site.id.in_([m.site_id for m in members if m.site_id] or [-1])))}
    scores = _scores(s, list(sites), "fat_score")
    rows = sorted(
        [(m, sites.get(m.site_id)) for m in members],
        key=lambda t: (scores[t[1].id].value if t[1] and t[1].id in scores else -1),
        reverse=True)
    # Завдання хабу — з тим самим FK cluster_id, що Task уже має; наступна дія
    # хабу живе тут, а не окремим полем. Відкриті зверху.
    zavdannya = list(s.exec(
        select(Task).where(Task.cluster_id == cluster_id, Task.status == "open")
        .order_by(Task.due_date.is_(None), Task.due_date).limit(20)))
    komanda = [u.name for u in s.exec(select(User).where(User.active == True))]  # noqa: E712
    return render(request, "obyekty/hab.html", user=user, c=c, rows=rows,
                  scores=scores, labels=CLUSTER_TYPE_LABELS,
                  cluster_stages=CLUSTER_STAGES, komanda=komanda,
                  zavdannya=zavdannya, dnes=date.today(),
                  **spivpratsya(s, "cluster", cluster_id))


@router.post("/haby/{cluster_id}/stan")
def cluster_stan(cluster_id: int, stage: str = Form(""), owner: str = Form(""),
                 user: User = Depends(require_cap(CAP_CRM)),
                 s: Session = Depends(get_session)):
    """Стадія розвитку хабу й власник. Поведінка та сама, що в обʼєкта
    (`set_status`): канон-перевірка + аудит; воронку це не рухає — хаб має
    власну девелоперську воронку (CLUSTER_STAGES)."""
    c = s.get(Cluster, cluster_id)
    if not c:
        return redirect("/haby")
    if stage and stage in CLUSTER_STAGES:
        c.stage = stage
    if owner is not None:
        c.owner = owner.strip()
    c.updated_at = utcnow()
    s.add(c)
    audit.log(s, user.name, "cluster.stan", entity="cluster", entity_id=cluster_id,
              tier=audit.TIER_CHANGE, detail=f"{c.stage} · {c.owner}")
    s.commit()
    return redirect(f"/haby/{cluster_id}")


# Карта переїхала в app/web/karta.py (Ф9): нова подача даних, два напрями,
# фільтри й ваги придатності. Старі /karta і /api/points видалено разом зі
# `templates/karta.html` — залишати два джерела правди про карту не можна.
