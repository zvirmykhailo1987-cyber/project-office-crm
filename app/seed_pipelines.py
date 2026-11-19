# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Посів двох воронок. Ідемпотентний: стадії звіряються за (pipeline, code).

Джерела дослівно:
- біогаз — `biogaz_robocha/crm/models.py:16` (STAGES + STAGE_COLORS);
- елеватор — `elevator_robocha/docs/funnel.md` (9 стадій, гейти, ключові листи).
"""
from sqlmodel import Session, select

from .models import Pipeline, Program, Stage

# ── Біоенергетика: 10 стадій, м'які переходи ─────────────────────────────────
# Гейт лише на «КП надіслано»: без прикріпленого КП воронка бреше у звіті.
BIOGAZ_STAGES = [
    # code, title, color, probability, gate_criteria, required_doc_kinds, terminal, won
    ("novyi", "Новий", "#64748b", 5, "Картка заведена, є телефон або email", "", 0, 0),
    ("dzvonyty", "Дзвонити", "#0ea5e9", 10, "Призначено відповідального й дату дзвінка", "", 0, 0),
    ("kontakt", "Контакт встановлено", "#6366f1", 20,
     "Є щонайменше одна активність із результатом", "", 0, 0),
    ("kvalifikovanyi", "Кваліфікований", "#8b5cf6", 35,
     "Підтверджено сировину й потужність; бал перерахований на реальних даних", "", 0, 0),
    ("zustrich", "Зустріч/візит", "#a855f7", 50, "Зафіксована активність «Зустріч»", "", 0, 0),
    ("kp", "КП надіслано", "#f59e0b", 60,
     "Прикріплено комерційну пропозицію", "KP", 0, 0),
    ("peremovyny", "Перемовини", "#f97316", 75,
     "Є активність після надсилання КП", "", 0, 0),
    ("ugoda", "Угода", "#22c55e", 100, "Прикріплено договір", "dogovir", 1, 1),
    ("vidmova", "Відмова", "#ef4444", 0, "Вказано причину відмови", "", 1, 0),
    ("vidkladeno", "Відкладено", "#94a3b8", 0, "Вказано дату повернення", "", 1, 0),
]

# ── Елеватори: 9 стадій (0–8), ЖОРСТКІ гейти, два підписи ────────────────────
ELEVATOR_STAGES = [
    ("s0", "0. Підготовка", "#64748b", 5,
     "Критерії скринінгу затверджено", "", "", 0, 0),
    ("s1", "1. Upstream-мандат", "#0ea5e9", 15,
     "Контракт #1 підписано (мандат Офіс↔інвестор)", "INV-05",
     "INV-01,INV-02,INV-03,INV-06,INV-04,INV-05", 0, 0),
    ("s2", "2. Джерело + скринінг громад", "#6366f1", 25,
     "Громада проходить скринінг: бал C1–C9 розрахований і не нижчий за поріг", "",
     "COM-05,INV-08", 0, 0),
    ("s3", "3. Перший контакт громади", "#8b5cf6", 35,
     "Отримано згоду голови на зустріч", "", "COM-01,COM-02", 0, 0),
    ("s4", "4. Голова + рада: зняття страху", "#a855f7", 50,
     "MOU громади підписано — головне горло воронки (гарантія) пройдено", "COM-06",
     "COM-03,COM-04,AUX-01,AUX-02,COM-06", 0, 0),
    ("s5", "5. Обсяг фермерів", "#f59e0b", 60,
     "Зібрано LOI на 100 000–200 000 т/рік", "FARM-02",
     "FARM-01,FARM-03,FARM-02,FARM-04,AUX-04", 0, 0),
    ("s6", "6. GAP-фінансування", "#f97316", 70,
     "Джерела GAP підтверджено ($3–3,6 млн)", "", "AUX-03", 0, 0),
    ("s7", "7. Структурування", "#ea580c", 85,
     "LOI громади + due diligence фінансиста пройдено", "COM-07", "COM-07,INV-07", 0, 0),
    ("s8", "8. Два підписи", "#22c55e", 100,
     "Контракт #2 підписано + financial close", "COM-09", "COM-08,COM-09", 1, 1),
]

PROGRAMS = [
    ("biogaz", "Біоенергетика", "#12855a", 1),
    ("elevator", "Елеватори", "#b4610a", 2),
]


# Стадії, які закривають угоду, і чим саме. Раніше це вгадувалось із коду:
# `voronky.py` шукав рядок «vidkladeno», `templating.py` — підрядок «vidmov».
# Тепер результат — поле стадії, і посів просто його заповнює.
REZULTAT_ZA_KODOM = {
    "ugoda": "vygrash", "s8": "vygrash",
    "vidmova": "vtrata",
    "vidkladeno": "vidkladeno",
}

# Автопереходи стартових воронок — теж дані, а не правило в коді.
# (стадія, умова, куди) — біогаз рухається сам, щойно є результат розмови.
AVTOPEREHODY = {"dzvonyty": ("aktyvnist", "kontakt")}


def _stage_rows(nabir):
    """Один формат для будь-якого набору стадій.

    Раніше тут стояла розвилка `if code == "biogaz"`, і третій напрям падав би в
    гілку елеватора з іншою кількістю полів. Тепер набори нормалізуються до
    9 полів на вході, а не всередині.
    """
    for order, (c, t, col, p, gate, req, docs, term, won) in enumerate(nabir):
        rez = REZULTAT_ZA_KODOM.get(c, "vygrash" if won else ("vtrata" if term else ""))
        umova, kudy = AVTOPEREHODY.get(c, ("", ""))
        yield dict(order=order, code=c, title=t, color=col, default_probability=p,
                   gate_criteria=gate, required_doc_kinds=req, key_docs=docs,
                   rezultat=rez, is_terminal=bool(term), is_won=bool(won), goal="",
                   avto_umova=umova, _avto_kudy=kudy)


NABORY = {
    # у біогазу немає key_docs — доповнюємо порожнім, щоб форма була спільною
    "biogaz": [(c, t, col, p, g, r, "", term, won)
               for c, t, col, p, g, r, term, won in BIOGAZ_STAGES],
    "elevator": ELEVATOR_STAGES,
}


def seed(s: Session) -> dict:
    """Створює стартові напрями, воронки й стадії — ЛИШЕ на порожній базі.

    ЧОМУ «лише на порожній». Раніше `seed()` виконувався на кожному старті
    (`db.bootstrap`) і перезаписував КОЖНЕ поле стадії значенням із коду. Тобто
    щойно зʼявився екран редагування воронок, будь-яка правка Марка зникала б
    після перезапуску — тихо, без помилки, без сліду в журналі. Знайти таке
    самому майже неможливо: «я ж міняв, а воно повернулось».

    Тепер посів дає стартовий стан і замовкає. Напрями — дані, і власником цих
    даних є людина, а не модуль.
    """
    made = {"programs": 0, "pipelines": 0, "stages": 0, "updated": 0, "propushcheno": 0}

    if s.exec(select(Program)).first() is not None:
        made["propushcheno"] = 1
        return made

    for code, title, color, order in PROGRAMS:
        prog = s.exec(select(Program).where(Program.code == code)).first()
        if not prog:
            prog = Program(code=code, title=title, color=color, order=order,
                           created_by="seed")
            s.add(prog)
            s.flush()
            made["programs"] += 1

        pipe = s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).first()
        if not pipe:
            pipe = Pipeline(program_id=prog.id, code=f"{code}_default",
                            title=f"Воронка «{title}»", created_by="seed")
            s.add(pipe)
            s.flush()
            made["pipelines"] += 1

        have = {st.code: st for st in
                s.exec(select(Stage).where(Stage.pipeline_id == pipe.id))}
        avto: list[tuple[Stage, str]] = []
        for row in _stage_rows(NABORY[code]):
            kudy = row.pop("_avto_kudy", "")
            st = have.get(row["code"])
            if st is None:
                st = Stage(pipeline_id=pipe.id, created_by="seed", **row)
                st.synhronizuvaty_prapory()
                s.add(st)
                have[st.code] = st
                made["stages"] += 1
            if kudy:
                avto.append((st, kudy))

        # Автопереходи проставляємо ДРУГИМ проходом: ціль може ще не існувати,
        # коли створюється стадія-джерело.
        s.flush()
        for st, kudy in avto:
            target = have.get(kudy)
            if target:
                st.avto_do_id = target.id
                s.add(st)

    s.commit()
    return made
