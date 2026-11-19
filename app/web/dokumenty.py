"""Документи, шаблони, потік узгодження, запити й листи (ТЗ §4a, модулі M8/M14)."""
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlmodel import Session, select

from .. import audit, config
from ..db import get_session
from ..models import (
    CAP_ADMIN,
    CAP_APPROVE,
    CAP_CRM,
    CAP_EDIT_ANY_DOC,
    CAP_SEND,
    CHANNELS,
    DOC_KINDS,
    REPLY_STATUSES,
    WORKFLOW,
    Counterparty,
    DocTemplate,
    Document,
    Outreach,
    Person,
    Setting,
    Site,
    User,
    utcnow,
)
from ..services import docgen
from .deps import current_user, redirect, render, require_cap, spivpratsya

router = APIRouter()

# Що дозволяємо прикріплювати. Свідомо БЕЗ виконуваних і архівів: файл віддається
# назад користувачам, тому кожне зайве розширення — це шлях доставки чужого коду.
DOZVOLENI_ROZSHYRENNYA = {".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
                          ".odt", ".ods", ".odp", ".md", ".txt", ".csv",
                          ".png", ".jpg", ".jpeg", ".webp"}
MAX_ROZMIR_FAYLU = 60 * 1024 * 1024          # 60 МБ — презентація з фото поміщається


def _settings(s: Session) -> dict:
    return {x.key: x.value for x in s.exec(select(Setting))}


def _slug(text: str) -> str:
    """Латинізований базовий шлях файлу — кирилиця в іменах ламає частину інструментів."""
    table = str.maketrans(
        "абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ",
        "abvhgdeezzyiijklmnoprstufhccssyuaABVHGDEEZZYIIJKLMNOPRSTUFHCCSSYUA")
    out = (text or "").translate(table)
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in out)[:60] or "doc"


# ── шаблони ──────────────────────────────────────────────────────────────────

@router.get("/shablony")
def templates(request: Request, group: str = "", user: User = Depends(current_user),
              s: Session = Depends(get_session)):
    stmt = select(DocTemplate)
    if group:
        stmt = stmt.where(DocTemplate.group == group)
    rows = list(s.exec(stmt.order_by(DocTemplate.code)))
    groups = list(s.exec(select(DocTemplate.group).distinct().order_by(DocTemplate.group)))
    return render(request, "dokumenty/shablony.html", user=user, rows=rows,
                  groups=groups, group=group)


@router.get("/shablony/{tpl_id}")
def template_card(tpl_id: int, request: Request, site_id: int = 0,
                  counterparty_id: int = 0, person_id: int = 0,
                  user: User = Depends(current_user), s: Session = Depends(get_session)):
    t = s.get(DocTemplate, tpl_id)
    if not t:
        return redirect("/shablony")
    site = s.get(Site, site_id) if site_id else None
    cp = s.get(Counterparty, counterparty_id) if counterparty_id else None
    person = s.get(Person, person_id) if person_id else None

    ctx = docgen.build_context(site=site, counterparty=cp, person=person,
                              settings=_settings(s))
    filled = docgen.fill(t.body_md, ctx)
    hromady = list(s.exec(select(Site).where(Site.site_type == "hromada_lokatsiya")
                          .order_by(Site.name)))
    return render(request, "dokumenty/shablon.html", user=user, t=t, filled=filled,
                  site=site, cp=cp, person=person, hromady=hromady)


@router.post("/shablony/{tpl_id}/generate")
def generate(tpl_id: int, site_id: str = Form(""), counterparty_id: str = Form(""),
             deal_id: str = Form(""), title: str = Form(""),
             user: User = Depends(require_cap(CAP_CRM)),
             s: Session = Depends(get_session)):
    """Генерує чернетку документа. Це Tier1 — файл нікуди не йде, лише зберігається."""
    t = s.get(DocTemplate, tpl_id)
    if not t:
        return redirect("/shablony")

    site = s.get(Site, int(site_id)) if site_id.isdigit() else None
    cp = s.get(Counterparty, int(counterparty_id)) if counterparty_id.isdigit() else None
    if site and not cp and site.counterparty_id:
        cp = s.get(Counterparty, site.counterparty_id)

    ctx = docgen.build_context(site=site, counterparty=cp, settings=_settings(s))
    filled = docgen.fill(t.body_md, ctx)

    group_uid = f"tpl:{t.code}:{site.id if site else (cp.id if cp else 'general')}"
    prev = list(s.exec(select(Document).where(Document.doc_group_uid == group_uid)))
    version = len(prev) + 1
    base = f"{_slug(t.code)}_{_slug(site.name if site else (cp.name if cp else 'zahalnyi'))}_v{version}"

    out = docgen.render(filled.text, basename=base, title=t.title,
                        formats=tuple(t.output_formats.split(",")))
    main = out.get("docx") or out["md"]

    doc = Document(
        title=(title.strip() or f"{t.code} — {site.name if site else (cp.name if cp else t.title)}"),
        kind=t.code, doc_group_uid=group_uid, version=version,
        filename=main.name, stored_path=str(main), size=main.stat().st_size,
        deal_id=int(deal_id) if deal_id.isdigit() else None,
        counterparty_id=cp.id if cp else None,
        workflow_status="chernetka", uploaded_by=user.name, created_by=user.name,
    )
    s.add(doc)
    s.flush()
    audit.log(s, user.name, "document.generate", entity="document", entity_id=doc.id,
              tier=audit.TIER_CHANGE,
              detail=f"{t.code} v{version}; незаповнених плейсхолдерів: {len(filled.unresolved)}")
    s.commit()
    return redirect(f"/dokumenty/{doc.id}")


# ── документи ────────────────────────────────────────────────────────────────

@router.get("/dokumenty")
def documents(request: Request, status: str = "", kind: str = "",
              user: User = Depends(current_user), s: Session = Depends(get_session)):
    stmt = select(Document)
    if status:
        stmt = stmt.where(Document.workflow_status == status)
    if kind:
        stmt = stmt.where(Document.kind == kind)
    rows = list(s.exec(stmt.order_by(Document.created_at.desc()).limit(300)))
    counts = {k: s.exec(select(func.count()).select_from(Document)
                        .where(Document.workflow_status == k)).one() for k in WORKFLOW}
    return render(request, "dokumenty/index.html", user=user, rows=rows, status=status,
                  kind=kind, workflow=WORKFLOW, counts=counts, doc_kinds=DOC_KINDS,
                  err=request.query_params.get("err"))


@router.get("/dokumenty/{doc_id}")
def document_card(doc_id: int, request: Request, user: User = Depends(current_user),
                  s: Session = Depends(get_session)):
    d = s.get(Document, doc_id)
    if not d:
        return redirect("/dokumenty")
    versions = list(s.exec(select(Document)
                           .where(Document.doc_group_uid == d.doc_group_uid)
                           .order_by(Document.version.desc())))
    cp = s.get(Counterparty, d.counterparty_id) if d.counterparty_id else None
    persons = list(s.exec(select(Person).where(Person.counterparty_id == d.counterparty_id))
                   ) if d.counterparty_id else []
    sent = list(s.exec(select(Outreach).where(Outreach.document_id == doc_id)))
    text = ""
    md = (config.DOCS_DIR / d.filename).with_suffix(".md")
    if md.exists():
        text = md.read_text(encoding="utf-8")
    unresolved = docgen.find_placeholders(text) if text else []
    return render(request, "dokumenty/card.html", user=user, d=d, versions=versions,
                  cp=cp, persons=persons, sent=sent, text=text, unresolved=unresolved,
                  workflow=WORKFLOW, channels=CHANNELS,
                  reply_days=int(_settings(s).get("outreach.reply_days") or 14),
                  **spivpratsya(s, "document", doc_id))


@router.get("/dokumenty/{doc_id}/file")
def download(doc_id: int, fmt: str = "", user: User = Depends(current_user),
             s: Session = Depends(get_session)):
    d = s.get(Document, doc_id)
    if not d:
        return redirect("/dokumenty")
    # `stored_path` — де файл лежить насправді. Він може бути поза DOCS_DIR: файл,
    # перенесений із чату, лишається у сховищі чату, щоб не дублювати 100 МБ байтів.
    # Дозволені лише два корені — інакше поле в БД стало б довільним читанням з диска.
    path = config.DOCS_DIR / d.filename
    if d.stored_path:
        kand = Path(d.stored_path)
        koreni = (config.DOCS_DIR.resolve(), config.CHAT_DIR.resolve())
        if kand.exists() and kand.resolve().parent in koreni:
            path = kand
    if fmt:
        cand = path.with_suffix("." + fmt)
        if cand.exists():
            path = cand
    if not path.exists():
        return redirect(f"/dokumenty/{doc_id}")
    return FileResponse(path, filename=path.name)


@router.post("/dokumenty/zavantazhyty")
async def upload(request: Request, kind: str = Form(""), title: str = Form(""),
                 deal_id: str = Form(""), counterparty_id: str = Form(""),
                 fayl: UploadFile = File(...),
                 user: User = Depends(require_cap(CAP_CRM)),
                 s: Session = Depends(get_session)):
    """Прикріпити готовий файл (презентація з Canva, PPTX, PDF, скан).

    Генерація з шаблону покриває листи, але презентацію Марко робить назовні —
    без цього роуту вона лишається поза системою, і затвердити її нема чого.
    """
    imya = Path(fayl.filename or "").name          # відрізаємо шлях: захист від ../
    if not imya:
        return redirect("/dokumenty?err=bez-faylu")
    if Path(imya).suffix.lower() not in DOZVOLENI_ROZSHYRENNYA:
        return redirect("/dokumenty?err=rozshyrennya")

    did = int(deal_id) if deal_id.strip().isdigit() else None
    cpid = int(counterparty_id) if counterparty_id.strip().isdigit() else None
    group = f"upload:{kind or 'fayl'}:{did or cpid or 'zahalnyi'}"
    prev = list(s.exec(select(Document).where(Document.doc_group_uid == group)))
    version = len(prev) + 1

    # Ім'я на диску робимо своє — щоб два «Презентація.pptx» не затерли один одного
    dest = config.DOCS_DIR / f"{_slug(kind or 'fayl')}_{group.replace(':', '-')}" \
                             f"_v{version}{Path(imya).suffix.lower()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    danci = await fayl.read()
    if len(danci) > MAX_ROZMIR_FAYLU:
        return redirect("/dokumenty?err=zavelykyi")
    dest.write_bytes(danci)

    doc = Document(title=title.strip() or Path(imya).stem, kind=kind.strip() or "fayl",
                   doc_group_uid=group, version=version, filename=dest.name,
                   stored_path=str(dest), size=len(danci), deal_id=did,
                   counterparty_id=cpid, workflow_status="chernetka",
                   uploaded_by=user.name, created_by=user.name)
    s.add(doc)
    s.flush()
    audit.log(s, user.name, "document.upload", entity="document", entity_id=doc.id,
              tier=audit.TIER_CHANGE,
              detail=f"{imya} · {len(danci) // 1024} КБ · {doc.kind} v{version}")
    s.commit()
    return redirect(f"/dokumenty/{doc.id}")


@router.post("/dokumenty/{doc_id}/status")
def set_workflow(doc_id: int, workflow_status: str = Form(""),
                 confirm: str = Form(""), user: User = Depends(current_user),
                 s: Session = Depends(get_session)):
    """Перехід станом. «Узгоджено» ставить лише той, хто затверджує, — і не собі."""
    d = s.get(Document, doc_id)
    if not d or workflow_status not in WORKFLOW:
        return redirect(f"/dokumenty/{doc_id}")
    # Ф8: «узгоджено» — це рішення, а не самообслуговування. Раніше сюди проходив
    # автор і адміністратор, тобто автор затверджував сам себе.
    if workflow_status == "uzhodzheno":
        if not user.can(CAP_APPROVE):
            return redirect(f"/dokumenty/{doc_id}?err=lyshe-zatverdzhuvach")
        if d.created_by == user.name:
            return redirect(f"/dokumenty/{doc_id}?err=samozatverdzhennya")
    if workflow_status == "do_vidpravlennya" \
            and not (user.can(CAP_APPROVE) or user.can(CAP_SEND)
                     or d.created_by == user.name):
        return redirect(f"/dokumenty/{doc_id}?err=prava")

    was = d.workflow_status
    d.workflow_status = workflow_status
    d.updated_at = utcnow()
    s.add(d)
    audit.log(s, user.name, "document.workflow", entity="document", entity_id=doc_id,
              tier=audit.TIER_CHANGE,
              detail=f"{WORKFLOW.get(was, was)} → {WORKFLOW[workflow_status]}")
    s.commit()
    return redirect(f"/dokumenty/{doc_id}")


@router.post("/dokumenty/{doc_id}/final")
def finalize(doc_id: int, confirm: str = Form(""), signed_at: str = Form(""),
             user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Tier3: позначка «фінальний» незворотна — після неї документ не змінюється."""
    d = s.get(Document, doc_id)
    if not d:
        return redirect("/dokumenty")
    audit.require_confirm("document.finalize", confirm == "yes")
    d.is_final = True
    d.workflow_status = "uzhodzheno"
    if signed_at:
        d.signed_at = date.fromisoformat(signed_at)
    d.updated_at = utcnow()
    s.add(d)
    audit.log(s, user.name, "document.finalize", entity="document", entity_id=doc_id,
              tier=audit.TIER_IRREVERSIBLE, confirmed=True,
              detail=f"{d.kind} v{d.version}")
    s.commit()
    return redirect(f"/dokumenty/{doc_id}")


@router.post("/dokumenty/{doc_id}/nova-versiya")
def new_version(doc_id: int, note: str = Form(""),
                user: User = Depends(require_cap(CAP_EDIT_ANY_DOC)),
                s: Session = Depends(get_session)):
    """Правка чужого документа створює НОВУ версію, оригінал лишається (ТЗ §2)."""
    d = s.get(Document, doc_id)
    if not d:
        return redirect("/dokumenty")
    prev = list(s.exec(select(Document).where(Document.doc_group_uid == d.doc_group_uid)))
    src_md = (config.DOCS_DIR / d.filename).with_suffix(".md")
    text = src_md.read_text(encoding="utf-8") if src_md.exists() else ""
    base = f"{_slug(d.kind)}_{_slug(d.title)}_v{len(prev) + 1}"
    out = docgen.render(text, basename=base, title=d.title)
    main = out.get("docx") or out["md"]

    nd = Document(title=d.title, kind=d.kind, doc_group_uid=d.doc_group_uid,
                  version=len(prev) + 1, filename=main.name, stored_path=str(main),
                  size=main.stat().st_size, deal_id=d.deal_id,
                  counterparty_id=d.counterparty_id, stage_id=d.stage_id,
                  workflow_status="na_pravtsi", uploaded_by=user.name,
                  created_by=user.name)
    s.add(nd)
    s.flush()
    audit.log(s, user.name, "document.new_version", entity="document", entity_id=nd.id,
              tier=audit.TIER_CHANGE,
              detail=f"v{d.version} → v{nd.version}; {note[:80]}")
    s.commit()
    return redirect(f"/dokumenty/{nd.id}")


# ── запити й листи (робоче місце Галини) ─────────────────────────────────────

@router.get("/lysty")
def outreach_list(request: Request, tab: str = "cherga",
                  user: User = Depends(current_user), s: Session = Depends(get_session)):
    queue = list(s.exec(select(Document)
                        .where(Document.workflow_status == "do_vidpravlennya")
                        .order_by(Document.updated_at)))
    sent = list(s.exec(select(Outreach).order_by(Outreach.sent_at.desc()).limit(200)))
    overdue = [o for o in sent if o.is_overdue]
    waiting = [o for o in sent if o.reply_status == "chekaiemo" and not o.is_overdue]

    docs = {d.id: d for d in s.exec(select(Document).where(
        Document.id.in_([o.document_id for o in sent if o.document_id] or [-1])))}
    cps = {c.id: c for c in s.exec(select(Counterparty).where(
        Counterparty.id.in_([o.counterparty_id for o in sent if o.counterparty_id] or [-1])))}

    return render(request, "dokumenty/lysty.html", user=user, tab=tab, queue=queue,
                  sent=sent, overdue=overdue, waiting=waiting, docs=docs, cps=cps,
                  channels=CHANNELS, reply_statuses=REPLY_STATUSES)


@router.post("/lysty/nadislano")
def mark_sent(document_id: str = Form(""), counterparty_id: str = Form(""),
              recipient: str = Form(""), subject: str = Form(""),
              channel: str = Form("email"), reply_due_date: str = Form(""),
              user: User = Depends(require_cap(CAP_SEND)),
              s: Session = Depends(get_session)):
    """Фіксація факту відправлення. Система НЕ надсилає — надсилає людина (ТЗ §9)."""
    d = s.get(Document, int(document_id)) if document_id.isdigit() else None
    days = int(_settings(s).get("outreach.reply_days") or 14)
    due = date.fromisoformat(reply_due_date) if reply_due_date \
        else date.today() + timedelta(days=days)

    o = Outreach(document_id=d.id if d else None,
                 document_version=d.version if d else 1,
                 counterparty_id=int(counterparty_id) if counterparty_id.isdigit()
                 else (d.counterparty_id if d else None),
                 deal_id=d.deal_id if d else None,
                 recipient=recipient.strip(),
                 subject=subject.strip() or (d.title if d else ""),
                 channel=channel if channel in CHANNELS else "email",
                 sent_at=utcnow(), sent_by=user.name, reply_due_date=due,
                 created_by=user.name)
    s.add(o)
    if d:
        d.workflow_status = "nadislano"
        d.updated_at = utcnow()
        s.add(d)
    # Стрічці потрібне посилання: лист сам сторінки не має, тому ведемо на документ,
    # а якщо листа склали без документа — на картку контрагента.
    ent, ent_id = ("document", d.id) if d else ("counterparty", o.counterparty_id)
    audit.log(s, user.name, "outreach.sent", entity=ent, entity_id=ent_id,
              tier=audit.TIER_CHANGE,
              detail=f"{o.subject[:60]} → {o.recipient[:40]} ({CHANNELS[o.channel]})")
    s.commit()
    return redirect("/lysty?tab=reyestr")


@router.post("/lysty/{outreach_id}/vidpovid")
def mark_reply(outreach_id: int, reply_status: str = Form("otrymano"),
               reply_note: str = Form(""),
               user: User = Depends(require_cap(CAP_SEND)),
               s: Session = Depends(get_session)):
    o = s.get(Outreach, outreach_id)
    if not o or reply_status not in REPLY_STATUSES:
        return redirect("/lysty")
    o.reply_status = reply_status
    o.reply_at = date.today()
    o.reply_note = reply_note.strip()
    o.updated_at = utcnow()
    s.add(o)
    audit.log(s, user.name, "outreach.reply", entity="outreach", entity_id=outreach_id,
              tier=audit.TIER_CHANGE, detail=REPLY_STATUSES[reply_status])
    s.commit()
    return redirect("/lysty?tab=reyestr")


# ── налаштування офісу ───────────────────────────────────────────────────────

@router.get("/nalashtuvannya")
def settings_page(request: Request, user: User = Depends(require_cap(CAP_ADMIN)),
                  s: Session = Depends(get_session)):
    rows = list(s.exec(select(Setting).order_by(Setting.key)))
    return render(request, "dokumenty/nalashtuvannya.html", user=user, rows=rows)


@router.post("/nalashtuvannya")
async def settings_save(request: Request, user: User = Depends(require_cap(CAP_ADMIN)),
                        s: Session = Depends(get_session)):
    form = await request.form()
    changed = []
    for st in s.exec(select(Setting)):
        new = (form.get(st.key) or "").strip()
        if new != st.value:
            changed.append(st.key)
            st.value = new
            st.updated_at = utcnow()
            s.add(st)
    if changed:
        audit.log(s, user.name, "settings.update", entity="setting",
                  tier=audit.TIER_CHANGE, detail=", ".join(changed))
        s.commit()
    return redirect("/nalashtuvannya")
