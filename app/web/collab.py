"""Роути співпраці: коментарі · затвердження · стрічка · сповіщення · доручення (Ф8)."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from .. import audit
from ..db import get_session
from ..models import (
    APPROVAL_STATUSES,
    CAP_APPROVE,
    CAP_CRM,
    COMMENT_KINDS,
    ENTITIES,
    RESOLUTIONS,
    Approval,
    Comment,
    Notification,
    Task,
    User,
    entity_label,
    entity_url,
    utcnow,
)
from ..services import collab
from .deps import current_user, data_z_formy, redirect, render, require_cap

router = APIRouter()


def _back(entity: str, entity_id: int, anchor: str = "spivpratsya"):
    url = entity_url(entity, entity_id) or "/"
    return redirect(f"{url}#{anchor}" if "#" not in url else url)


def _check_entity(entity: str, entity_id: int):
    if entity not in ENTITIES or entity_id <= 0:
        raise HTTPException(400, f"Невідома сутність: {entity}")


# Сутність → FK завдання. Хаб (`cluster`) був відсутній: доручення з картки хабу
# лягало без звʼязку й не зʼявлялось на /haby/{id}.
TASK_FK = {"deal": "deal_id", "site": "site_id", "counterparty": "counterparty_id",
           "document": "document_id", "cluster": "cluster_id"}


def _avtory_sutnosti(s: Session, entity: str, entity_id: int) -> set[str]:
    """Хто автор сутності, яку затверджують (не лише той, хто подав)."""
    from ..models import Deal, Document
    if entity == "document":
        d = s.get(Document, entity_id)
        if not d:
            return set()
        # автор будь-якої версії — автор документа (нова версія не «відмиває» авторство)
        versii = s.exec(select(Document).where(Document.doc_group_uid == d.doc_group_uid)).all()             if d.doc_group_uid else [d]
        return {x for v in versii for x in (v.created_by, v.uploaded_by) if x}
    if entity == "deal":
        g = s.get(Deal, entity_id)
        return {x for x in (g.owner, g.created_by) if x} if g else set()
    return set()


# ── КОМЕНТАРІ Й РЕКОМЕНДАЦІЇ ─────────────────────────────────────────────────

@router.post("/komentar")
def add_comment(entity: str = Form(...), entity_id: int = Form(...),
                text: str = Form(""), kind: str = Form("komentar"),
                user: User = Depends(require_cap(CAP_CRM)),
                s: Session = Depends(get_session)):
    """Коментар/рекомендація/питання на будь-якій сутності. Доступно ВСІМ."""
    _check_entity(entity, entity_id)
    text = (text or "").strip()
    if not text:
        return _back(entity, entity_id)

    mentioned = collab.find_mentions(s, text)
    c = Comment(entity=entity, entity_id=entity_id, author=user.name, text=text,
                kind=kind if kind in COMMENT_KINDS else "komentar",
                mentions=",".join(u.login for u in mentioned),
                created_by=user.name)
    s.add(c)
    s.flush()

    url = entity_url(entity, entity_id)
    label = entity_label(entity)
    # 1) згадані — окремий, найсильніший сигнал
    collab.notify_many(s, [u.name for u in mentioned], kind="zgadka",
                       text=f"згадка від {user.name}: {text[:140]}",
                       url=url, entity=entity, entity_id=entity_id, from_whom=user.name)
    # 2) причетні до сутності — але не двічі тим, кого вже згадали
    already = {u.name for u in mentioned}
    collab.notify_many(
        s, [o for o in collab.owners_of(s, entity, entity_id) if o not in already],
        kind="rekomendatsiya" if c.is_recommendation else "komentar",
        text=f"{user.name} — {c.kind_label.lower()} на «{label}»: {text[:140]}",
        url=url, entity=entity, entity_id=entity_id, from_whom=user.name)

    audit.log(s, user.name, "comment.create", entity=entity, entity_id=entity_id,
              tier=audit.TIER_CHANGE, detail=f"{c.kind_label}: {text[:80]}")
    s.commit()
    return _back(entity, entity_id)


@router.post("/komentar/{comment_id}/vyrishyty")
def resolve_comment(comment_id: int, resolution: str = Form("vrahovano"),
                    note: str = Form(""),
                    user: User = Depends(require_cap(CAP_CRM)),
                    s: Session = Depends(get_session)):
    """Рекомендацію можна прийняти або відхилити — з причиною. Це робить її дією."""
    c = s.get(Comment, comment_id)
    if not c or not c.is_recommendation:
        return redirect("/")
    if resolution not in RESOLUTIONS:
        resolution = "vrahovano"
    if resolution == "ne_beru" and not note.strip():
        return _back(c.entity, c.entity_id)      # відмова без причини — не приймаємо

    c.resolution, c.resolved_by = resolution, user.name
    c.resolved_at, c.resolution_note = utcnow(), note.strip()
    s.add(c)
    collab.notify(s, user=c.author, kind="rekomendatsiya",
                  text=f"{user.name}: {RESOLUTIONS[resolution].lower()} — «{c.text[:90]}»"
                       + (f" · {note.strip()[:90]}" if note.strip() else ""),
                  url=entity_url(c.entity, c.entity_id), entity=c.entity,
                  entity_id=c.entity_id, from_whom=user.name)
    audit.log(s, user.name, "comment.resolve", entity=c.entity, entity_id=c.entity_id,
              tier=audit.TIER_CHANGE,
              detail=f"{RESOLUTIONS[resolution]}: {c.text[:60]}")
    s.commit()
    return _back(c.entity, c.entity_id)


@router.post("/komentar/{comment_id}/u-zavdannya")
def comment_to_task(comment_id: int, assignee: str = Form(""), due_date: str = Form(""),
                    user: User = Depends(require_cap(CAP_CRM)),
                    s: Session = Depends(get_session)):
    """Рекомендація → завдання одним кліком, звʼязок зберігається."""
    c = s.get(Comment, comment_id)
    if not c:
        return redirect("/")
    fk = TASK_FK.get(c.entity)
    t = Task(title=c.text[:120], note=f"З рекомендації {c.author}",
             assignee=assignee or c.author, source="vid_komentarya",
             comment_id=c.id, created_by=user.name,
             due_date=data_z_formy(due_date),
             **({fk: c.entity_id} if fk else {}))
    s.add(t)
    s.flush()
    collab.notify(s, user=t.assignee, kind="zavdannya",
                  text=f"завдання від {user.name}: {t.title[:140]}",
                  url=f"/zavdannya#t{t.id}", entity="task", entity_id=t.id,
                  from_whom=user.name)
    audit.log(s, user.name, "task.from_comment", entity="task", entity_id=t.id,
              tier=audit.TIER_CHANGE, detail=f"{t.title[:70]} → {t.assignee}")
    s.commit()
    return _back(c.entity, c.entity_id)


# ── ЗАТВЕРДЖЕННЯ ─────────────────────────────────────────────────────────────

@router.post("/zatverdzhennya/podaty")
def submit(entity: str = Form(...), entity_id: int = Form(...), note: str = Form(""),
           user: User = Depends(require_cap(CAP_CRM)),
           s: Session = Depends(get_session)):
    """Подати будь-що на затвердження. Повторне подання після доопрацювання —
    новий запис із посиланням на попередній (щоб історія рішень не губилась)."""
    _check_entity(entity, entity_id)
    prev = collab.approval_for(s, entity, entity_id)
    if prev and prev.is_open:
        return _back(entity, entity_id)          # уже на розгляді — не дублюємо

    a = Approval(entity=entity, entity_id=entity_id, submitted_by=user.name,
                 note=note.strip(), created_by=user.name,
                 parent_approval_id=prev.id if prev else None)
    s.add(a)
    s.flush()

    approvers = [u.name for u in s.exec(select(User).where(User.active == True))  # noqa: E712
                 if u.can(CAP_APPROVE)]
    collab.notify_many(s, approvers, kind="na_zatverdzhennya",
                       text=f"на затвердження від {user.name}: «{entity_label(entity)}»"
                            + (f" — {note.strip()[:120]}" if note.strip() else ""),
                       url=entity_url(entity, entity_id), entity=entity,
                       entity_id=entity_id, from_whom=user.name)
    audit.log(s, user.name, "approval.submit", entity=entity, entity_id=entity_id,
              tier=audit.TIER_CHANGE, detail=note.strip()[:80] or entity_label(entity))
    s.commit()
    return _back(entity, entity_id)


@router.post("/zatverdzhennya/{approval_id}/rishennya")
def decide(approval_id: int, status: str = Form(...), note: str = Form(""),
           user: User = Depends(require_cap(CAP_APPROVE)),
           s: Session = Depends(get_session)):
    """Рішення приймає лише той, хто має право затверджувати.

    ДВА ТВЕРДІ ПРАВИЛА:
      1. Автор НЕ затверджує себе — інакше процес фіктивний.
      2. «На доопрацювання» без коментаря не приймається — це і є «скажи, що поміняти».
    """
    a = s.get(Approval, approval_id)
    if not a or not a.is_open:
        return redirect("/zatverdzhennya")
    if a.submitted_by == user.name or user.name in _avtory_sutnosti(s, a.entity, a.entity_id):
        # Друге правило ловить обхід через колегу: документ Богдана подає на
        # затвердження хтось інший — і Богдан затверджує власну роботу.
        raise HTTPException(403, "Автор не затверджує власну роботу — це має зробити колега")
    if status not in ("zatverdzheno", "na_doopratsyuvannya"):
        return redirect("/zatverdzhennya")
    if status == "na_doopratsyuvannya" and not note.strip():
        return redirect(f"/zatverdzhennya?err=bez-komentarya&a={approval_id}")

    a.status, a.approver = status, user.name
    a.decided_at, a.decision_note = utcnow(), note.strip()
    s.add(a)

    # рішення лишає слід і як коментар — щоб воно було в обговоренні сутності
    if note.strip():
        s.add(Comment(entity=a.entity, entity_id=a.entity_id, author=user.name,
                      text=note.strip(), kind="komentar", created_by=user.name))

    # документ синхронізуємо зі станом рішення (фінальний — незмінний)
    if a.entity == "document":
        from ..models import Document
        d = s.get(Document, a.entity_id)
        if d and not d.is_final:
            d.workflow_status = "uzhodzheno" if status == "zatverdzheno" else "na_pravtsi"
            d.updated_at = utcnow()
            s.add(d)

    collab.notify(s, user=a.submitted_by, kind="rishennya",
                  text=f"{user.name}: {APPROVAL_STATUSES[status].lower()}"
                       + (f" — {note.strip()[:140]}" if note.strip() else ""),
                  url=entity_url(a.entity, a.entity_id), entity=a.entity,
                  entity_id=a.entity_id, from_whom=user.name)
    audit.log(s, user.name, "approval.decide", entity=a.entity, entity_id=a.entity_id,
              tier=audit.TIER_CHANGE,
              detail=f"{APPROVAL_STATUSES[status]}: {note.strip()[:70]}")
    s.commit()
    return redirect("/zatverdzhennya")


@router.post("/zatverdzhennya/{approval_id}/vidklykaty")
def withdraw(approval_id: int, user: User = Depends(require_cap(CAP_CRM)),
             s: Session = Depends(get_session)):
    a = s.get(Approval, approval_id)
    if not a or not a.is_open or a.submitted_by != user.name:
        return redirect("/zatverdzhennya")
    a.status, a.decided_at = "vidklykano", utcnow()
    s.add(a)
    audit.log(s, user.name, "approval.withdraw", entity=a.entity, entity_id=a.entity_id,
              tier=audit.TIER_CHANGE, detail=entity_label(a.entity))
    s.commit()
    return _back(a.entity, a.entity_id)


@router.get("/zatverdzhennya")
def approvals_page(request: Request, user: User = Depends(current_user),
                   s: Session = Depends(get_session)):
    pending = collab.pending_approvals(s)
    recent = list(s.exec(select(Approval).where(Approval.status != "na_rozglyadi")
                         .order_by(Approval.decided_at.desc(), Approval.id.desc()).limit(30)))
    return render(request, "zatverdzhennya.html", user=user, pending=pending,
                  recent=recent, statuses=APPROVAL_STATUSES,
                  can_decide=user.can(CAP_APPROVE),
                  err=request.query_params.get("err"))


# ── СТРІЧКА ──────────────────────────────────────────────────────────────────

@router.get("/strichka")
def feed_page(request: Request, hto: str = "", user: User = Depends(current_user),
              s: Session = Depends(get_session)):
    items = collab.feed(s, limit=100, actor=(user.name if hto == "moyi" else ""))
    people = [u.name for u in s.exec(select(User).where(User.active == True))]  # noqa: E712
    tpl = "_strichka_rows.html" if request.headers.get("HX-Request") else "strichka.html"
    # Мітка «нове» — те саме, чим живе блок змін на «моєму дні»: при заходах по
    # кілька разів на день головне питання до стрічки — «що тут відколи я був».
    # Саму мітку НЕ зсуваємо: її рухає лише перегляд «мого дня».
    return render(request, tpl, user=user, items=items, hto=hto, people=people,
                  bachyv_do=user.bachyv_do)


# ── СПОВІЩЕННЯ ───────────────────────────────────────────────────────────────

@router.get("/spovishchennya/lichylnyk")
def badge(request: Request, user: User = Depends(current_user),
          s: Session = Depends(get_session)):
    """Фрагмент лічильника — оновлюється полінгом кожні 30 с."""
    return render(request, "_lichylnyk.html", user=user,
                  n=collab.unread_count(s, user.name))


@router.get("/spovishchennya")
def inbox(request: Request, user: User = Depends(current_user),
          s: Session = Depends(get_session)):
    rows = list(s.exec(select(Notification).where(Notification.user == user.name)
                       .order_by(Notification.created_at.desc()).limit(100)))
    return render(request, "spovishchennya.html", user=user, rows=rows,
                  unread=collab.unread_count(s, user.name))


@router.post("/spovishchennya/prochytano")
def read_all(notif_id: str = Form(""), user: User = Depends(current_user),
             s: Session = Depends(get_session)):
    collab.mark_read(s, user.name, int(notif_id) if notif_id.isdigit() else None)
    s.commit()
    return redirect("/spovishchennya")


# ── ОПЕРАТИВНІ ДОРУЧЕННЯ ─────────────────────────────────────────────────────

@router.post("/doruchennya")
def quick_task(entity: str = Form(""), entity_id: str = Form(""),
               title: str = Form(""), assignee: str = Form(""),
               due_date: str = Form(""), priority: str = Form("normal"),
               user: User = Depends(require_cap(CAP_CRM)),
               s: Session = Depends(get_session)):
    """Поставити завдання колезі прямо з картки — три поля, без переходу сторінки."""
    title = (title or "").strip()
    if not title or not assignee:
        return _back(entity, int(entity_id)) if entity_id.isdigit() else redirect("/")

    eid = int(entity_id) if entity_id.isdigit() else None
    fk = TASK_FK.get(entity)
    t = Task(title=title, assignee=assignee, created_by=user.name,
             source="vid_koleghy" if assignee != user.name else "manual",
             priority=priority, due_date=data_z_formy(due_date),
             **({fk: eid} if fk and eid else {}))
    s.add(t)
    s.flush()
    collab.notify(s, user=assignee, kind="zavdannya",
                  text=f"завдання від {user.name}: {title[:140]}"
                       + (f" · до {t.due_date:%d.%m}" if t.due_date else ""),
                  url=f"/zavdannya#t{t.id}", entity="task", entity_id=t.id,
                  from_whom=user.name)
    audit.log(s, user.name, "task.create", entity="task", entity_id=t.id,
              tier=audit.TIER_CHANGE, detail=f"{title[:70]} → {assignee}")
    s.commit()
    return _back(entity, eid) if eid else redirect("/zavdannya")


@router.post("/zavdannya/{task_id}/vzyaty")
def accept_task(task_id: int, user: User = Depends(require_cap(CAP_CRM)),
                s: Session = Depends(get_session)):
    t = s.get(Task, task_id)
    if not t or t.assignee != user.name:
        return redirect("/zavdannya")
    t.status, t.accepted_at, t.updated_at = "in_progress", utcnow(), utcnow()
    s.add(t)
    collab.notify(s, user=t.created_by, kind="zavdannya",
                  text=f"{user.name} — взято в роботу: {t.title[:140]}",
                  url=f"/zavdannya#t{t.id}", entity="task", entity_id=t.id,
                  from_whom=user.name)
    audit.log(s, user.name, "task.accept", entity="task", entity_id=task_id,
              tier=audit.TIER_CHANGE, detail=t.title[:70])
    s.commit()
    return redirect("/zavdannya")


@router.post("/zavdannya/{task_id}/vidmovytys")
def decline_task(task_id: int, reason: str = Form(""),
                 user: User = Depends(require_cap(CAP_CRM)),
                 s: Session = Depends(get_session)):
    """Відмова ЛИШЕ з причиною — інакше постановник не знає, що робити далі."""
    t = s.get(Task, task_id)
    if not t or t.assignee != user.name:
        return redirect("/zavdannya")
    if not reason.strip():
        return redirect("/zavdannya?err=bez-prychyny")
    t.status, t.declined_reason, t.updated_at = "cancelled", reason.strip(), utcnow()
    s.add(t)
    collab.notify(s, user=t.created_by, kind="zavdannya_vidmova",
                  text=f"{user.name} — відмова: «{t.title[:90]}» — {reason.strip()[:120]}",
                  url=f"/zavdannya#t{t.id}", entity="task", entity_id=t.id,
                  from_whom=user.name)
    audit.log(s, user.name, "task.decline", entity="task", entity_id=task_id,
              tier=audit.TIER_CHANGE, detail=reason.strip()[:70])
    s.commit()
    return redirect("/zavdannya")
