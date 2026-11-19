"""Завдання й KPI (ТЗ §4, модулі M9/M10)."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from sqlmodel import Session, select

from .. import audit
from ..db import get_session
from ..models import (
    CAP_CRM,
    PRIORITIES,
    STATUSES,
    TASK_SOURCES,
    Cluster,
    Counterparty,
    Deal,
    Outreach,
    Task,
    User,
    utcnow,
)
from ..services import collab, den
from .deps import current_user, data_z_formy, redirect, render, require_cap

router = APIRouter()


@router.get("/zavdannya")
def index(request: Request, assignee: str = "", status: str = "open",
          user: User = Depends(current_user), s: Session = Depends(get_session)):
    stmt = select(Task)
    if status == "open":
        stmt = stmt.where(Task.status.in_(["open", "in_progress"]))
    elif status:
        stmt = stmt.where(Task.status == status)
    if assignee:
        stmt = stmt.where(Task.assignee == assignee)

    rows = list(s.exec(stmt.order_by(Task.due_date, Task.id.desc())))
    # без дати — вниз списку, прострочені — вгору
    rows.sort(key=lambda t: (t.due_date is None, t.due_date or date.max))

    users = list(s.exec(select(User).where(User.active == True)))  # noqa: E712
    deals = {d.id: d for d in s.exec(select(Deal))}
    cps = {c.id: c for c in s.exec(select(Counterparty).where(
        Counterparty.id.in_([t.counterparty_id for t in rows if t.counterparty_id]
                            or [-1])))}
    # Кластер-звʼязок — по-новому (Task.cluster_id,): завдання хабу
    # показує, до якого хабу воно, як і завдання угоди показує угоду.
    haby = {c.id: c for c in s.exec(select(Cluster).where(
        Cluster.id.in_([t.cluster_id for t in rows if t.cluster_id] or [-1])))}
    return render(request, "zavdannya/index.html", user=user, rows=rows, users=users,
                  assignee=assignee, status=status, statuses=STATUSES,
                  priorities=PRIORITIES, sources=TASK_SOURCES, deals=deals, cps=cps,
                  haby=haby)


@router.post("/zavdannya")
def create(title: str = Form(""), note: str = Form(""), assignee: str = Form(""),
           due_date: str = Form(""), priority: str = Form("normal"),
           deal_id: str = Form(""), counterparty_id: str = Form(""),
           user: User = Depends(require_cap(CAP_CRM)),
           s: Session = Depends(get_session)):
    if not title.strip():
        return redirect("/zavdannya")
    t = Task(title=title.strip(), note=note.strip(),
             assignee=assignee or user.name,
             due_date=data_z_formy(due_date),
             priority=priority if priority in PRIORITIES else "normal",
             source="vid_koleghy" if assignee and assignee != user.name else "manual",
             deal_id=int(deal_id) if deal_id.isdigit() else None,
             counterparty_id=int(counterparty_id) if counterparty_id.isdigit() else None,
             created_by=user.name)
    s.add(t)
    s.flush()
    audit.log(s, user.name, "task.create", entity="task", entity_id=t.id,
              tier=audit.TIER_CHANGE, detail=t.title[:80])
    # Оперативність: людина має дізнатися про доручення з лічильника, а не відкривши список
    if t.assignee != user.name:
        collab.notify(s, user=t.assignee, kind="zavdannya",
                      text=f"завдання від {user.name}: {t.title[:70]}",
                      url=f"/zavdannya#t{t.id}", entity="task", entity_id=t.id,
                      from_whom=user.name)
    s.commit()
    return redirect("/zavdannya")


def mozhe_zminyty(t: Task, user: User, status: str) -> bool:
    """Скасувати — лише постановник (виконавець відмовляється через «Відмовитись» із
    причиною); інші зміни — виконавець або постановник. Завдання без виконавця — будь-хто."""
    if status == "cancelled":
        return t.created_by == user.name
    return not t.assignee or user.name in (t.assignee, t.created_by)


@router.post("/zavdannya/{task_id}/status")
def set_status(task_id: int, status: str = Form("done"),
               user: User = Depends(require_cap(CAP_CRM)),
               s: Session = Depends(get_session)):
    t = s.get(Task, task_id)
    if not t or status not in STATUSES:
        return redirect("/zavdannya")
    if not mozhe_zminyty(t, user, status):
        return redirect("/zavdannya?err=prava")
    t.status = status
    if status == "done":
        t.done_at, t.done_by = utcnow(), user.name
    t.updated_at = utcnow()
    s.add(t)
    audit.log(s, user.name, "task.status", entity="task", entity_id=task_id,
              tier=audit.TIER_CHANGE, detail=STATUSES[status])
    if status == "done" and t.created_by not in ("правило", user.name):
        collab.notify(s, user=t.created_by, kind="zavdannya",
                      text=f"{user.name} — виконано: {t.title[:70]}",
                      url=f"/zavdannya#t{task_id}", entity="task", entity_id=task_id,
                      from_whom=user.name)
    s.commit()
    return redirect("/zavdannya")


@router.post("/zavdannya/synk")
def sync_rules(user: User = Depends(require_cap(CAP_CRM)),
               s: Session = Depends(get_session)):
    """Створює завдання за правилами: контроль відповідей на надіслані листи.

    Ідемпотентно: на один лист — одне завдання (`outreach_id` унікальний ключ).
    """
    have = {t.outreach_id for t in s.exec(select(Task).where(Task.outreach_id.is_not(None)))}
    made = 0
    for o in s.exec(select(Outreach).where(Outreach.reply_status == "chekaiemo")):
        if o.id in have or not o.reply_due_date:
            continue
        s.add(Task(title=f"Перевірити відповідь: {o.subject or 'лист'}",
                   note=f"кому: {o.recipient or '—'}",
                   assignee=o.sent_by or user.name, due_date=o.reply_due_date,
                   source="outreach_due", outreach_id=o.id,
                   counterparty_id=o.counterparty_id, document_id=o.document_id,
                   created_by="правило"))
        made += 1
    if made:
        audit.log(s, user.name, "task.sync", entity="task", tier=audit.TIER_BULK,
                  detail=f"створено {made} завдань контролю відповідей")
        s.commit()
    return redirect("/zavdannya")


@router.get("/kpi")
def kpi_page(request: Request, days: int = 30, user: User = Depends(current_user),
             s: Session = Depends(get_session)):
    return render(request, "zavdannya/kpi.html", user=user, k=den.kpi(s, days),
                  days=days)
