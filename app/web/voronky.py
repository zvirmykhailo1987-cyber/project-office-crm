"""Воронки: канбан, картка угоди, переходи з гейтами, активності (ТЗ §4, Додаток Б)."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from sqlmodel import Session, select

from .. import audit
from ..db import get_session
from ..models import (
    ACTIVITY_TYPES,
    CAP_CRM,
    DOC_KINDS,
    Activity,
    Counterparty,
    Deal,
    Document,
    Person,
    Pipeline,
    Program,
    Site,
    Stage,
    Task,
    User,
    utcnow,
)
from ..services import collab, gates
from .deps import current_user, redirect, render, require_cap, spivpratsya

router = APIRouter()


def _program(s: Session, code: str | None) -> Program | None:
    """Чинний напрям або None, якщо жодного немає.

    `progs[0]` без захисту давав 500 на всіх трьох екранах воронки, щойно
    активних напрямів не лишалось — а це не екзотика: досить заархівувати
    обидва на `/napryamy`. Порожній стан, показаний як помилка сервера, —
    найгірший із можливих: людина бачить поламану систему замість «тут поки
    порожньо, ось що зробити».
    """
    progs = list(s.exec(select(Program).where(Program.active == True)  # noqa: E712
                        .order_by(Program.order)))
    if code:
        for p in progs:
            if p.code == code:
                return p
    return progs[0] if progs else None


def _stages(s: Session, pipeline_id: int) -> list[Stage]:
    return list(s.exec(select(Stage).where(Stage.pipeline_id == pipeline_id)
                       .order_by(Stage.order)))


@router.get("/voronky")
def board(request: Request, program: str = "", owner: str = "",
          user: User = Depends(current_user), s: Session = Depends(get_session)):
    progs = list(s.exec(select(Program).order_by(Program.order)))
    prog = _program(s, program or None)
    if prog is None:
        return render(request, "voronky/bez_napryamu.html", user=user, progs=progs)
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).first()
    stages = _stages(s, pipe.id)

    stmt = select(Deal).where(Deal.program_id == prog.id)
    if owner:
        stmt = stmt.where(Deal.owner == owner)
    deals = list(s.exec(stmt.order_by(Deal.next_action_date)))

    names = {c.id: c.name for c in s.exec(select(Counterparty).where(
        Counterparty.id.in_([d.counterparty_id for d in deals if d.counterparty_id] or [-1])
    ))}

    by_stage: dict[int, list] = {st.id: [] for st in stages}
    for d in deals:
        by_stage.setdefault(d.stage_id, []).append(d)

    owners = sorted({d.owner for d in deals if d.owner})
    return render(request, "voronky/board.html", user=user, programs=progs, prog=prog,
                  stages=stages, by_stage=by_stage, names=names, owners=owners,
                  cur_owner=owner, total=len(deals))


@router.get("/voronky/nova")
def new_form(request: Request, program: str = "", counterparty_id: int | None = None,
             user: User = Depends(current_user), s: Session = Depends(get_session)):
    prog = _program(s, program or None)
    if prog is None:
        return redirect("/voronky")
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).first()
    cp = s.get(Counterparty, counterparty_id) if counterparty_id else None
    return render(request, "voronky/new.html", user=user,
                  programs=list(s.exec(select(Program).order_by(Program.order))),
                  prog=prog, stages=_stages(s, pipe.id), cp=cp, doc_kinds=DOC_KINDS,
                  users=list(s.exec(select(User).where(User.active == True))))  # noqa: E712


@router.post("/voronky/nova")
def create(title: str = Form(""), program: str = Form(""),
           counterparty_id: str = Form(""), owner: str = Form(""),
           next_action_date: str = Form(""), next_action_note: str = Form(""),
           doruchyty_komu: str = Form(""), doruchyty_scho: str = Form(""),
           doruchyty_koly: str = Form(""),
           user: User = Depends(require_cap(CAP_CRM)),
           s: Session = Depends(get_session)):
    """Новий проєкт. Хто його заводить — той і власник.

    Уточнення Марка 05.08.2026: новий проєкт приносить Богдан і ОДРАЗУ ставить на
    нього задачу («підготувати попередню презентацію»). Тому доручення — частина цієї ж
    форми, а не окрема дія: інакше проєкт заведено, а розробник про нього не знає.
    """
    prog = _program(s, program or None)
    if prog is None:
        return redirect("/voronky")
    pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).first()
    first = _stages(s, pipe.id)[0]

    cp_id = int(counterparty_id) if counterparty_id.strip().isdigit() else None
    cp = s.get(Counterparty, cp_id) if cp_id else None
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=first.id,
             title=(title.strip() or (cp.name if cp else "Без назви")),
             counterparty_id=cp_id, owner=owner or user.name,
             probability=first.default_probability,
             next_action_note=next_action_note.strip(),
             next_action_date=date.fromisoformat(next_action_date)
             if next_action_date else None,
             created_by=user.name)
    s.add(d)
    s.flush()
    audit.log(s, user.name, "deal.create", entity="deal", entity_id=d.id,
              tier=audit.TIER_CHANGE, detail=d.title)

    komu, scho = doruchyty_komu.strip(), doruchyty_scho.strip()
    if komu and scho and komu != user.name:
        t = Task(title=scho, note=f"по проєкту «{d.title[:60]}»", assignee=komu,
                 source="vid_koleghy", deal_id=d.id,
                 counterparty_id=cp_id, created_by=user.name,
                 due_date=date.fromisoformat(doruchyty_koly) if doruchyty_koly else None)
        s.add(t)
        s.flush()
        collab.notify(s, user=komu, kind="zavdannya",
                      text=f"новий проєкт від {user.name}: {d.title[:70]} — {scho[:80]}",
                      url=f"/voronky/{d.id}", entity="deal", entity_id=d.id,
                      from_whom=user.name)
        audit.log(s, user.name, "task.create", entity="task", entity_id=t.id,
                  tier=audit.TIER_CHANGE, detail=f"{scho[:60]} → {komu}")
    s.commit()
    return redirect(f"/voronky/{d.id}")


@router.get("/voronky/{deal_id}")
def card(deal_id: int, request: Request, user: User = Depends(current_user),
         s: Session = Depends(get_session)):
    d = s.get(Deal, deal_id)
    if not d:
        return redirect("/voronky")
    stages = _stages(s, d.pipeline_id)
    cur = s.get(Stage, d.stage_id)
    cp = s.get(Counterparty, d.counterparty_id) if d.counterparty_id else None
    persons = list(s.exec(select(Person).where(Person.counterparty_id == d.counterparty_id))
                   ) if d.counterparty_id else []
    acts = list(s.exec(select(Activity).where(Activity.deal_id == deal_id)
                       .order_by(Activity.when.desc())))
    docs = list(s.exec(select(Document).where(Document.deal_id == deal_id)
                       .order_by(Document.created_at.desc())))
    have = gates.satisfied_doc_kinds(s, deal_id)

    # для кожної стадії — чи відкритий перехід і чого бракує
    checks = {st.id: gates.check(s, d, st) for st in stages}

    # 360° з боку угоди: інші угоди того самого контрагента + його обʼєкти.
    # Доти з картки угоди не було видно, що з компанією ведеться ще щось і
    # які в неї майданчики — менеджер мусив вертатись у довідник.
    inshi_ugody, obyekty = [], []
    if d.counterparty_id:
        inshi_ugody = list(s.exec(
            select(Deal).where(Deal.counterparty_id == d.counterparty_id,
                               Deal.id != deal_id).order_by(Deal.status)))
        obyekty = list(s.exec(
            select(Site).where(Site.counterparty_id == d.counterparty_id)
            .order_by(Site.site_type).limit(30)))
    stadiyi = {st.id: st for st in s.exec(
        select(Stage).where(Stage.id.in_([x.stage_id for x in inshi_ugody] or [-1])))}

    return render(request, "voronky/card.html", user=user, d=d, cur=cur, stages=stages,
                  cp=cp, persons=persons, acts=acts, docs=docs, have=have,
                  checks=checks, activity_types=ACTIVITY_TYPES, doc_kinds=DOC_KINDS,
                  prog=s.get(Program, d.program_id),
                  inshi_ugody=inshi_ugody, obyekty=obyekty, stadiyi=stadiyi,
                  **spivpratsya(s, "deal", deal_id))


@router.post("/voronky/{deal_id}/stage")
def move(deal_id: int, stage_id: int = Form(...), confirm: str = Form(""),
         user: User = Depends(require_cap(CAP_CRM)),
         s: Session = Depends(get_session)):
    d = s.get(Deal, deal_id)
    target = s.get(Stage, stage_id)
    if not d or not target:
        return redirect("/voronky")

    res = gates.check(s, d, target)
    if not res.allowed:
        return redirect(f"/voronky/{deal_id}?gate=1")

    was = s.get(Stage, d.stage_id)
    d.stage_id = target.id
    d.stage_entered_at = utcnow()
    d.probability = target.default_probability
    # Статус угоди — з поля стадії, не з її коду. Раніше «відкладено» вгадувалось
    # рядком `code == "vidkladeno"`, тож у будь-якій новій воронці термінальна
    # стадія «пауза» мовчки ставала б «втратою».
    d.status = target.status_ugody
    d.updated_at = utcnow()
    s.add(d)
    audit.log(s, user.name, "deal.stage", entity="deal", entity_id=deal_id,
              tier=audit.TIER_CHANGE,
              detail=f"{was.title if was else '—'} → {target.title}")
    s.commit()
    return redirect(f"/voronky/{deal_id}")


@router.post("/voronky/{deal_id}/activity")
def add_activity(deal_id: int, type: str = Form("Дзвінок"), outcome: str = Form(""),
                 note: str = Form(""), user: User = Depends(require_cap(CAP_CRM)),
                 s: Session = Depends(get_session)):
    d = s.get(Deal, deal_id)
    if not d:
        return redirect("/voronky")
    a = Activity(type=type if type in ACTIVITY_TYPES else "Нотатка",
                 deal_id=deal_id, counterparty_id=d.counterparty_id,
                 author=user.name, outcome=outcome.strip(), note=note.strip(),
                 created_by=user.name)
    s.add(a)
    d.last_contacted_at = a.when

    # Автоперехід — за ПРАВИЛОМ СТАДІЇ, а не за зашитими кодами. Раніше тут
    # стояло «якщо code == dzvonyty, шукай стадію kontakt»: у новій воронці цієї
    # зручності не було б узагалі, доки хтось не назве стадії так само.
    cur = s.get(Stage, d.stage_id)
    if (cur and cur.avto_umova == "aktyvnist" and cur.avto_do_id
            and a.type in ("Дзвінок", "WhatsApp", "Email") and a.outcome):
        nxt = s.get(Stage, cur.avto_do_id)
        # Ціль мусить лишатись у тій самій воронці: стадію могли перенести або
        # видалити, і тоді угода поїхала б у чужий напрям.
        if nxt and nxt.pipeline_id == d.pipeline_id:
            d.stage_id = nxt.id
            d.stage_entered_at = utcnow()
            d.probability = nxt.default_probability
            d.status = nxt.status_ugody
            audit.log(s, user.name, "deal.stage.auto", entity="deal", entity_id=deal_id,
                      tier=audit.TIER_CHANGE,
                      detail=f"авто: {cur.title} → {nxt.title}")
    s.add(d)
    # Активність своєї сторінки не має — у стрічці веде на угоду.
    audit.log(s, user.name, "activity.create", entity="deal", entity_id=deal_id,
              tier=audit.TIER_CHANGE, detail=f"{a.type}: {a.outcome[:60]}")
    s.commit()
    return redirect(f"/voronky/{deal_id}")


@router.post("/voronky/{deal_id}/next-action")
def set_next_action(deal_id: int, next_action_date: str = Form(""),
                    next_action_note: str = Form(""),
                    user: User = Depends(require_cap(CAP_CRM)),
                    s: Session = Depends(get_session)):
    d = s.get(Deal, deal_id)
    if not d:
        return redirect("/voronky")
    d.next_action_date = date.fromisoformat(next_action_date) if next_action_date else None
    d.next_action_note = next_action_note.strip()
    d.updated_at = utcnow()
    s.add(d)
    audit.log(s, user.name, "deal.next_action", entity="deal", entity_id=deal_id,
              tier=audit.TIER_CHANGE, detail=f"{next_action_date} {next_action_note[:40]}")
    s.commit()
    return redirect(f"/voronky/{deal_id}")


@router.post("/voronky/{deal_id}/document")
def add_document(deal_id: int, kind: str = Form(""), title: str = Form(""),
                 is_final: str = Form(""), signed_at: str = Form(""),
                 user: User = Depends(require_cap(CAP_CRM)),
                 s: Session = Depends(get_session)):
    """Фіксація документа угоди. Позначка «фінальний» — Tier3 (ТЗ §9)."""
    d = s.get(Deal, deal_id)
    if not d or not kind.strip():
        return redirect(f"/voronky/{deal_id}")

    final = is_final == "on"
    group = f"deal:{deal_id}:{kind.strip()}"
    prev = list(s.exec(select(Document).where(Document.doc_group_uid == group)))
    doc = Document(title=title.strip() or kind.strip(), kind=kind.strip(),
                   doc_group_uid=group, version=len(prev) + 1, deal_id=deal_id,
                   counterparty_id=d.counterparty_id, stage_id=d.stage_id,
                   is_final=final,
                   signed_at=date.fromisoformat(signed_at) if signed_at else None,
                   workflow_status="uzhodzheno" if final else "chernetka",
                   uploaded_by=user.name, created_by=user.name)
    s.add(doc)
    s.flush()                     # потрібен doc.id — інакше стрічка не дасть посилання
    audit.log(s, user.name, "document.create", entity="document", entity_id=doc.id,
              tier=audit.TIER_CHANGE, detail=f"{kind} v{doc.version}")
    if final:
        # Tier3: позначка фінальності незворотна — фіксуємо підтвердження в аудиті
        audit.log(s, user.name, "document.finalize", entity="document", entity_id=doc.id,
                  tier=audit.TIER_IRREVERSIBLE, confirmed=True,
                  detail=f"угода {deal_id}, вид {kind}")
    s.commit()
    return redirect(f"/voronky/{deal_id}")
