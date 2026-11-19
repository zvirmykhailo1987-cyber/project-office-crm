"""Напрями угод: створення й редагування воронок з інтерфейсу (Ф12).

ЧОМУ ЦЕ ОКРЕМИЙ ЕКРАН, А НЕ ЧАСТИНА НАЛАШТУВАНЬ. Воронка — не налаштування, а
ОПИС ПРОЦЕСУ, на якому тримається робота всіх чотирьох. Зміна стадії ретроактивно
міняє правила для вже заведених угод: гейт, який учора пропускав, сьогодні може
блокувати. Тому кожна небезпечна дія тут показує ЦІНУ (скільки угод зачепить) і
вимагає підтвердження, а не просто «зберегти».

Три тверді правила, які цей модуль забезпечує:
  1. Жодна угода не лишається на неіснуючій стадії — видалення вимагає перенесення.
  2. Напрям з угодами не видаляється, лише архівується.
  3. Зміна обовʼязкових документів (гейта) — Tier3: з підтвердженням і в журналі.

Право — CAP_ADMIN. У Богдана його немає свідомо: він веде переговори всередині
процесу, а не перебудовує сам процес.
"""
import re

from fastapi import APIRouter, Depends, Form, Request
from sqlmodel import Session, func, select

from .. import audit
from ..db import get_session
from ..models import (
    AVTO_UMOVY,
    CAP_ADMIN,
    MODELI_BALU,
    REZULTATY,
    Deal,
    Pipeline,
    Program,
    Stage,
    User,
    utcnow,
)
from ..zagotovky_voronok import ZAGOTOVKY, stadiyi_zagotovky
from .deps import redirect, render, require_cap

router = APIRouter()

KOD_RE = re.compile(r"^[a-z][a-z0-9_]{1,29}$")


def _kod(syre: str, zapasnyi: str = "") -> str:
    """Нормалізує код: латиниця, цифри, підкреслення. Порожній → запасний."""
    k = re.sub(r"[^a-z0-9_]", "_", (syre or "").strip().lower()).strip("_")
    k = re.sub(r"_{2,}", "_", k)
    return k if KOD_RE.match(k) else zapasnyi


def _voronka(s: Session, prog: Program) -> Pipeline:
    return s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).first()


def _stadiyi(s: Session, pipe_id: int) -> list[Stage]:
    return list(s.exec(select(Stage).where(Stage.pipeline_id == pipe_id)
                       .order_by(Stage.order, Stage.id)))


def _ugod_na_stadiyi(s: Session, pipe_id: int) -> dict[int, int]:
    """Скільки угод стоїть на кожній стадії — це і є ціна будь-якої зміни."""
    rows = s.exec(select(Deal.stage_id, func.count()).where(
        Deal.pipeline_id == pipe_id).group_by(Deal.stage_id))
    return {sid: n for sid, n in rows if sid}


@router.get("/napryamy")
def spysok(request: Request, user: User = Depends(require_cap(CAP_ADMIN)),
           s: Session = Depends(get_session)):
    progs = list(s.exec(select(Program).order_by(Program.order, Program.id)))
    dani = []
    for p in progs:
        pipe = _voronka(s, p)
        st = _stadiyi(s, pipe.id) if pipe else []
        n_ugod = s.exec(select(func.count()).select_from(Deal)
                        .where(Deal.program_id == p.id)).one()
        dani.append({"p": p, "pipe": pipe, "stadiy": len(st), "ugod": n_ugod,
                     "model": MODELI_BALU.get(p.model_balu, p.model_balu)})
    return render(request, "napryamy/index.html", user=user, dani=dani,
                  zagotovky=ZAGOTOVKY, programy=progs)


@router.post("/napryamy")
def stvoryty(nazva: str = Form(""), kod: str = Form(""), opys: str = Form(""),
             color: str = Form("#0f6fc4"), model_balu: str = Form(""),
             dzherelo: str = Form("z_nulya"), zagotovka: str = Form(""),
             kopiya_id: str = Form(""),
             user: User = Depends(require_cap(CAP_ADMIN)),
             s: Session = Depends(get_session)):
    """Створює напрям одним із трьох шляхів: копія · заготовка · з нуля."""
    nazva = nazva.strip()
    if not nazva:
        return redirect("/napryamy?err=nazva")
    k = _kod(kod or nazva)
    if not k:
        return redirect("/napryamy?err=kod")
    if s.exec(select(Program).where(Program.code == k)).first():
        return redirect("/napryamy?err=zaynyato")

    poryadok = (s.exec(select(func.max(Program.order))).one() or 0) + 1
    prog = Program(code=k, title=nazva, opys=opys.strip(), color=color,
                   order=poryadok,
                   model_balu=model_balu if model_balu in MODELI_BALU else "",
                   created_by=user.name)
    s.add(prog)
    s.flush()
    pipe = Pipeline(program_id=prog.id, code=f"{k}_default",
                    title=f"Воронка «{nazva}»", created_by=user.name)
    s.add(pipe)
    s.flush()

    # Джерело стадій. Копія бере ВСЕ, включно з гейтами: сенс копії саме в тому,
    # щоб не набирати заново критерії, які вже вивірені на іншому напрямі.
    rows: list[dict] = []
    zvidky = "з нуля"
    if dzherelo == "kopiya" and kopiya_id.isdigit():
        src = s.get(Program, int(kopiya_id))
        src_pipe = _voronka(s, src) if src else None
        if src_pipe:
            zvidky = f"копія «{src.title}»"
            for st in _stadiyi(s, src_pipe.id):
                rows.append(dict(code=st.code, title=st.title, goal=st.goal,
                                 gate_criteria=st.gate_criteria,
                                 required_doc_kinds=st.required_doc_kinds,
                                 key_docs=st.key_docs, color=st.color,
                                 default_probability=st.default_probability,
                                 rezultat=st.rezultat))
    elif dzherelo == "zagotovka" and zagotovka in ZAGOTOVKY:
        zvidky = f"заготовка «{ZAGOTOVKY[zagotovka]['nazva']}»"
        rows = stadiyi_zagotovky(zagotovka)
    if not rows:
        rows = [dict(code="novyi", title="Новий", goal="", gate_criteria="",
                     required_doc_kinds="", key_docs="", color="#64748b",
                     default_probability=5, rezultat="")]

    for i, r in enumerate(rows):
        st = Stage(pipeline_id=pipe.id, order=i, created_by=user.name, **r)
        st.synhronizuvaty_prapory()
        s.add(st)

    audit.log(s, user.name, "program.create", entity="program", entity_id=prog.id,
              tier=audit.TIER_CHANGE,
              detail=f"«{nazva}» ({k}), {len(rows)} стадій, {zvidky}")
    s.commit()
    return redirect(f"/napryamy/{k}")


@router.get("/napryamy/{kod}")
def redaktor(kod: str, request: Request,
             user: User = Depends(require_cap(CAP_ADMIN)),
             s: Session = Depends(get_session)):
    prog = s.exec(select(Program).where(Program.code == kod)).first()
    if not prog:
        return redirect("/napryamy")
    pipe = _voronka(s, prog)
    stadiyi = _stadiyi(s, pipe.id) if pipe else []
    lich = _ugod_na_stadiyi(s, pipe.id) if pipe else {}
    n_ugod = s.exec(select(func.count()).select_from(Deal)
                    .where(Deal.program_id == prog.id)).one()
    return render(request, "napryamy/redaktor.html", user=user, prog=prog,
                  pipe=pipe, stadiyi=stadiyi, lich=lich, n_ugod=n_ugod,
                  rezultaty=REZULTATY, avto_umovy=AVTO_UMOVY,
                  modeli=MODELI_BALU)


@router.post("/napryamy/{kod}/napryam")
def zberegty_napryam(kod: str, nazva: str = Form(""), opys: str = Form(""),
                     color: str = Form("#0f6fc4"), model_balu: str = Form(""),
                     user: User = Depends(require_cap(CAP_ADMIN)),
                     s: Session = Depends(get_session)):
    prog = s.exec(select(Program).where(Program.code == kod)).first()
    if not prog:
        return redirect("/napryamy")
    # Код НЕ міняємо навіть якщо прийшов: на нього посилаються шаблони листів
    # (`DocTemplate.program_code`) та імпортери, і мовчазна зміна їх відвʼязала б.
    prog.title = nazva.strip() or prog.title
    prog.opys = opys.strip()
    prog.color = color
    prog.model_balu = model_balu if model_balu in MODELI_BALU else ""
    prog.updated_at = utcnow()
    s.add(prog)
    audit.log(s, user.name, "program.edit", entity="program", entity_id=prog.id,
              tier=audit.TIER_CHANGE, detail=prog.title[:80])
    s.commit()
    return redirect(f"/napryamy/{kod}")


@router.post("/napryamy/{kod}/arhiv")
def arhiv(kod: str, user: User = Depends(require_cap(CAP_ADMIN)),
          s: Session = Depends(get_session)):
    """Архівувати/повернути. Видалення напряму немає взагалі — навіть без угод.

    Причина: на код напряму посилаються шаблони листів і сліди в журналі аудиту.
    Архів прибирає напрям із вибору для НОВИХ угод, а історію лишає читабельною.
    """
    prog = s.exec(select(Program).where(Program.code == kod)).first()
    if not prog:
        return redirect("/napryamy")
    prog.active = not prog.active
    prog.updated_at = utcnow()
    s.add(prog)
    audit.log(s, user.name, "program.archive", entity="program", entity_id=prog.id,
              tier=audit.TIER_CHANGE,
              detail=("повернуто в роботу" if prog.active else "в архів"))
    s.commit()
    return redirect("/napryamy")


@router.post("/napryamy/{kod}/stadiya")
def dodaty_stadiyu(kod: str, title: str = Form(""), stage_kod: str = Form(""),
                   user: User = Depends(require_cap(CAP_ADMIN)),
                   s: Session = Depends(get_session)):
    prog = s.exec(select(Program).where(Program.code == kod)).first()
    pipe = _voronka(s, prog) if prog else None
    if not pipe:
        return redirect("/napryamy")
    nazva = title.strip() or "Нова стадія"
    k = _kod(stage_kod or nazva, f"st{len(_stadiyi(s, pipe.id)) + 1}")
    if s.exec(select(Stage).where(Stage.pipeline_id == pipe.id,
                                  Stage.code == k)).first():
        return redirect(f"/napryamy/{kod}?err=kod_zaynyato")
    # Нова стадія стає перед термінальними: інакше вона опиниться після
    # «Угода/Відмова», куди робочий процес уже не доходить.
    st_all = _stadiyi(s, pipe.id)
    pered = [x for x in st_all if not x.rezultat]
    poryadok = (pered[-1].order + 1) if pered else 0
    for x in st_all:
        if x.order >= poryadok:
            x.order += 1
            s.add(x)
    st = Stage(pipeline_id=pipe.id, order=poryadok, code=k, title=nazva,
               created_by=user.name)
    st.synhronizuvaty_prapory()
    s.add(st)
    audit.log(s, user.name, "stage.create", entity="program", entity_id=prog.id,
              tier=audit.TIER_CHANGE, detail=f"{prog.title}: {nazva}")
    s.commit()
    return redirect(f"/napryamy/{kod}")


@router.post("/napryamy/{kod}/stadiya/{sid}")
def zberegty_stadiyu(kod: str, sid: int, request: Request,
                     title: str = Form(""), goal: str = Form(""),
                     gate_criteria: str = Form(""),
                     required_doc_kinds: str = Form(""), key_docs: str = Form(""),
                     default_probability: str = Form("0"),
                     rezultat: str = Form(""), avto_umova: str = Form(""),
                     avto_do_id: str = Form(""), color: str = Form("#64748b"),
                     confirm: str = Form(""),
                     user: User = Depends(require_cap(CAP_ADMIN)),
                     s: Session = Depends(get_session)):
    prog = s.exec(select(Program).where(Program.code == kod)).first()
    st = s.get(Stage, sid)
    pipe = _voronka(s, prog) if prog else None
    if not (prog and pipe and st and st.pipeline_id == pipe.id):
        return redirect("/napryamy")

    novi_gejty = ",".join(x.strip() for x in required_doc_kinds.split(",") if x.strip())
    gejty_zminylys = novi_gejty != (st.required_doc_kinds or "")

    # Гейт — Tier3. Зміна обовʼязкових документів ретроактивно перекриває шлях
    # угодам, які вже стоять далі по воронці: вчора вони пройшли, сьогодні їхній
    # шлях назад-вперед заблокований. Тому — окреме підтвердження, не «зберегти».
    if gejty_zminylys:
        audit.require_confirm("stage.gates", confirmed=bool(confirm))

    st.title = title.strip() or st.title
    st.goal = goal.strip()
    st.gate_criteria = gate_criteria.strip()
    st.required_doc_kinds = novi_gejty
    st.key_docs = ",".join(x.strip() for x in key_docs.split(",") if x.strip())
    st.color = color
    try:
        st.default_probability = max(0, min(100, int(default_probability or 0)))
    except ValueError:
        pass
    buv_rezultat = st.rezultat
    st.rezultat = rezultat if rezultat in REZULTATY else ""
    if st.rezultat != buv_rezultat:
        # Статус угоди — похідний від результату стадії, на якій вона стоїть.
        # Без цього стадія ставала «виграшем», а угоди на ній лишались «в роботі»
        # (і навпаки) — KPI й канбан показували різне.
        for ugoda in s.exec(select(Deal).where(Deal.stage_id == st.id)):
            ugoda.status = st.status_ugody
            ugoda.updated_at = utcnow()
            s.add(ugoda)
    st.avto_umova = avto_umova if avto_umova in AVTO_UMOVY else ""
    ciljova = s.get(Stage, int(avto_do_id)) if avto_do_id.isdigit() else None
    # Ціль автопереходу мусить бути в ТІЙ САМІЙ воронці й не собою — інакше
    # угода поїхала б у чужий напрям або зациклилась.
    st.avto_do_id = (ciljova.id if ciljova and ciljova.pipeline_id == pipe.id
                     and ciljova.id != st.id and st.avto_umova else None)
    st.synhronizuvaty_prapory()
    st.updated_at = utcnow()
    s.add(st)

    if gejty_zminylys:
        audit.log(s, user.name, "stage.gates", entity="program", entity_id=prog.id,
                  tier=audit.TIER_IRREVERSIBLE, confirmed=True,
                  detail=f"{prog.title} · {st.title}: гейт «{novi_gejty or '—'}»")
    else:
        audit.log(s, user.name, "stage.edit", entity="program", entity_id=prog.id,
                  tier=audit.TIER_CHANGE, detail=f"{prog.title} · {st.title}")
    s.commit()
    return redirect(f"/napryamy/{kod}")


@router.post("/napryamy/{kod}/stadiya/{sid}/porydok")
def perestavyty(kod: str, sid: int, kudy: str = Form("vgoru"),
                user: User = Depends(require_cap(CAP_ADMIN)),
                s: Session = Depends(get_session)):
    """Порядок міняється обміном із сусідом. Угоди привʼязані до `stage_id`,
    не до порядку, тому нікуди не зсуваються."""
    prog = s.exec(select(Program).where(Program.code == kod)).first()
    pipe = _voronka(s, prog) if prog else None
    if not pipe:
        return redirect("/napryamy")
    st_all = _stadiyi(s, pipe.id)
    i = next((n for n, x in enumerate(st_all) if x.id == sid), None)
    if i is None:
        return redirect(f"/napryamy/{kod}")
    j = i - 1 if kudy == "vgoru" else i + 1
    if not (0 <= j < len(st_all)):
        return redirect(f"/napryamy/{kod}")
    st_all[i].order, st_all[j].order = st_all[j].order, st_all[i].order
    s.add(st_all[i])
    s.add(st_all[j])
    audit.log(s, user.name, "stage.reorder", entity="program", entity_id=prog.id,
              tier=audit.TIER_CHANGE,
              detail=f"{prog.title}: {st_all[i].title} ↔ {st_all[j].title}")
    s.commit()
    return redirect(f"/napryamy/{kod}")


@router.post("/napryamy/{kod}/stadiya/{sid}/prybraty")
def prybraty_stadiyu(kod: str, sid: int, kudy_ugody: str = Form(""),
                     confirm: str = Form(""),
                     user: User = Depends(require_cap(CAP_ADMIN)),
                     s: Session = Depends(get_session)):
    """Прибрати стадію. Угоди на ній ОБОВʼЯЗКОВО переносяться.

    Це головне правило екрана. Без нього видалення лишало б угоди з
    `stage_id`, якого немає: картка відкривається, стадія порожня, гейти
    не рахуються, і зрозуміти чому — неможливо.
    """
    prog = s.exec(select(Program).where(Program.code == kod)).first()
    pipe = _voronka(s, prog) if prog else None
    st = s.get(Stage, sid)
    if not (prog and pipe and st and st.pipeline_id == pipe.id):
        return redirect("/napryamy")
    if len(_stadiyi(s, pipe.id)) <= 1:
        return redirect(f"/napryamy/{kod}?err=ostannya")

    ugody = list(s.exec(select(Deal).where(Deal.stage_id == sid)))
    target: Stage | None = None
    if ugody:
        target = s.get(Stage, int(kudy_ugody)) if kudy_ugody.isdigit() else None
        if not target or target.pipeline_id != pipe.id or target.id == sid:
            return redirect(f"/napryamy/{kod}?err=kudy&sid={sid}")
    # Tier3 (TIER3_ACTIONS["stage.delete"]): журнал уже писав confirmed=True,
    # хоча жодного підтвердження не питали. Перевірка — після валідації форми,
    # щоб помилка «куди перенести угоди» показувалась на своєму місці.
    audit.require_confirm("stage.delete", bool(confirm))
    if target is not None:
        for d in ugody:
            d.stage_id = target.id
            d.stage_entered_at = utcnow()
            d.status = target.status_ugody
            d.probability = target.default_probability
            s.add(d)

    # Автопереходи, які вели сюди, стають порожніми — інакше вони вказували б
    # на неіснуючу стадію й тихо не спрацьовували.
    for x in s.exec(select(Stage).where(Stage.avto_do_id == sid)):
        x.avto_do_id = None
        x.avto_umova = ""
        s.add(x)

    nazva = st.title
    s.delete(st)
    audit.log(s, user.name, "stage.delete", entity="program", entity_id=prog.id,
              tier=audit.TIER_IRREVERSIBLE, confirmed=True,
              detail=f"{prog.title}: прибрано «{nazva}»"
                     + (f", перенесено угод: {len(ugody)}" if ugody else ""))
    s.commit()
    return redirect(f"/napryamy/{kod}")
