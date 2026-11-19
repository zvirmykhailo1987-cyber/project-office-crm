"""Головна під роль + панель стану баз даних (ТЗ §4c, модуль M15)."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..services import collab, den
from ..models import (
    CAP_DATA_SERVICE,
    Activity,
    AuditEntry,
    Counterparty,
    DataSource,
    Deal,
    ImportConflict,
    ImportRun,
    Person,
    Program,
    Stage,
    User,
    utcnow,
)
from .deps import current_user, render, require_cap

router = APIRouter()


# поле → людська назва. Порядок визначає порядок у таблиці покриття.
COVERAGE_FIELDS = [
    ("edrpou", "ЄДРПОУ"), ("phone", "Телефон"), ("email", "Email"),
    ("address", "Адреса"), ("city", "Місто"), ("hromada", "Громада"),
    ("kved", "КВЕД"), ("lat", "Координати"),
]


def _coverage(s: Session) -> dict:
    """Покриття полів по довіднику — це і є «стан баз» (ТЗ §4c).

    Один SQL-прохід замість восьми COUNT-сканів: на 31 тис. рядків вісім окремих
    запитів давали ~0.4 с на кожне відкриття головної.
    """
    cols = ", ".join(
        f"SUM(CASE WHEN {c} IS NOT NULL AND {c} != '' THEN 1 ELSE 0 END)"
        if c != "lat" else "SUM(CASE WHEN lat IS NOT NULL THEN 1 ELSE 0 END)"
        for c, _ in COVERAGE_FIELDS)
    row = s.connection().exec_driver_sql(
        f"SELECT COUNT(*), {cols} FROM counterparty").fetchone()  # noqa: S608 — columns are code constants  # nosec B608
    total = row[0]
    if not total:
        return {"total": 0, "fields": {}, "persons": 0}
    out = {label: {"n": row[i + 1] or 0, "pct": round(100 * (row[i + 1] or 0) / total)}
           for i, (_, label) in enumerate(COVERAGE_FIELDS)}
    return {
        "total": total,
        "fields": out,
        "persons": s.exec(select(func.count()).select_from(Person)).one(),
    }


@router.get("/dyzayn")
def dyzayn(request: Request, user: User = Depends(current_user)):
    """Живий стенд дизайн-системи: усі елементи в усіх станах, обидві теми.

    Не декорація: сюди дивляться, коли перевіряють, чи новий екран говорить тією
    самою мовою, що й решта. Дешевше, ніж шукати розбіжності по 29 екранах.
    """
    return render(request, "dyzayn.html", user=user)


@router.get("/")
def home(request: Request, user: User = Depends(current_user),
         s: Session = Depends(get_session)):
    cov = _coverage(s)
    needs_check = s.exec(
        select(func.count()).select_from(Counterparty)
        .where(Counterparty.data_status == "needs_check")
    ).one()

    # «Мій день» збирається під роль: у кожного свої джерела рядків (ТЗ §3, О8)
    day = den.build_day(s, user)

    # «Що змінилось відколи ви були». Мітку зсуваємо ПІСЛЯ обчислення дельти,
    # інакше блок завжди був би порожній: власне відкриття екрана затирало б
    # межу, від якої рахуємо. Пауза між заходами в цьому режимі — години, тому
    # свіжіші за 3 хвилини повернення (F5, повернення з іншої вкладки) межу не
    # зсувають — інакше людина втратила б підсумок, ще не встигнувши прочитати.
    zminy = collab.zminy_vid(s, user.name, user.bachyv_do)

    # Пульс: скільки подій було в кожен із останніх 14 днів. Дає спарклайну й
    # тепловій смузі реальні дані замість намальованих — інакше мікрочарт це
    # декорація, а не показник.
    pochatok = date.today() - timedelta(days=13)
    podii = list(s.exec(select(AuditEntry.at).where(
        AuditEntry.at >= datetime.combine(pochatok, datetime.min.time()),
        AuditEntry.action != "auth.login")))
    za_dnyamy = {pochatok + timedelta(days=i): 0 for i in range(14)}
    for (koly,) in ((p,) if not isinstance(p, tuple) else p for p in podii):
        d = koly.date()
        if d in za_dnyamy:
            za_dnyamy[d] += 1
    pulse = [za_dnyamy[k] for k in sorted(za_dnyamy)]
    pulse_max = max(pulse) or 1
    teper = utcnow()
    if user.bachyv_do is None or (teper - user.bachyv_do).total_seconds() > 180:
        user.bachyv_do = teper
        s.add(user)
        s.commit()

    active = list(s.exec(select(Deal).where(Deal.status == "active")))
    mine = [d for d in active if d.owner == user.name]
    progs = {p.id: p for p in s.exec(select(Program))}
    by_program: dict[int, list] = {}
    for d in active:
        by_program.setdefault(d.program_id, []).append(d)

    # ── АНАЛІТИКА ВОРОНКИ (конверсія по стадіях) ────────────────────────────
    # Скільки активних угод стоїть на кожній стадії кожної програми. Доти
    # дашборд показував лише СУМУ по програмі — не було видно, де воронка
    # звужується. Стадії з бази, впорядковані за position.
    stages = {st.id: st for st in s.exec(select(Stage))}
    voronka: dict[int, list] = {}   # program_id → [(stage, к-сть), ...]
    for pid, deals in by_program.items():
        lichyl: dict[int, int] = {}
        for d in deals:
            lichyl[d.stage_id] = lichyl.get(d.stage_id, 0) + 1
        etapy = sorted((stages[sid] for sid in lichyl if sid in stages),
                       key=lambda st: st.order)
        voronka[pid] = [(st, lichyl[st.id]) for st in etapy]

    # ── ПРОГРЕС КОМАНДИ ─────────────────────────────────────────────────────
    # Угоди у власності + активності за 14 днів по кожному. Дашборд був суто
    # особистий («мій день»); команді бракувало спільної картини.
    aktyv14 = list(s.exec(select(Activity.author).where(
        Activity.when >= datetime.combine(pochatok, datetime.min.time()))))
    komanda: dict[str, dict] = {}
    for d in active:
        if d.owner:
            komanda.setdefault(d.owner, {"ugod": 0, "aktyv": 0, "prostrocheno": 0})
            komanda[d.owner]["ugod"] += 1
    dnes = date.today()
    for d in active:
        if d.owner and d.next_action_date and d.next_action_date < dnes:
            komanda.setdefault(d.owner, {"ugod": 0, "aktyv": 0, "prostrocheno": 0})
            komanda[d.owner]["prostrocheno"] += 1
    for (avtor,) in ((a,) if not isinstance(a, tuple) else a for a in aktyv14):
        if avtor in komanda:
            komanda[avtor]["aktyv"] += 1

    # Прострочені разом — одним числом із переходом (див. §4 у списку завдань)
    prostrocheno = sum(1 for d in active
                       if d.next_action_date and d.next_action_date < dnes)

    return render(request, "dashboard.html", user=user, cov=cov,
                  needs_check=needs_check, day=day, zminy=zminy, mine=mine,
                  progs=progs, by_program=by_program, total_active=len(active),
                  pulse=pulse, pulse_max=pulse_max,
                  voronka=voronka, komanda=komanda, prostrocheno=prostrocheno)


@router.get("/bazy")
def bazy(request: Request, user: User = Depends(require_cap(CAP_DATA_SERVICE)),
         s: Session = Depends(get_session)):
    """Панель сервісу баз даних — робоче місце Марка."""
    sources = list(s.exec(select(DataSource).order_by(DataSource.code)))
    runs = list(s.exec(select(ImportRun).order_by(ImportRun.started_at.desc()).limit(20)))
    conflicts = s.exec(
        select(func.count()).select_from(ImportConflict)
        .where(ImportConflict.resolution == "pending")
    ).one()
    return render(request, "bazy.html", user=user, sources=sources, runs=runs,
                  conflicts=conflicts, cov=_coverage(s))
