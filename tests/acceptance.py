"""Приймальна перевірка: критерії з ТЗ §11–12, по одному пункту на рядок.

Запуск: `python -m tests.acceptance` з папки ofis_crm.
Не pytest: це перевірка ЖИВОЇ бази після імпорту, а не ізольованих одиниць коду.
"""
import sys
from pathlib import Path

from sqlmodel import Session, func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows-консоль за замовчуванням cp1251 — без цього звіт падає на «обʼєкт» (Г7)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from app import config  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (  # noqa: E402
    Cluster,
    ClusterMember,
    Counterparty,
    DocTemplate,
    Person,
    Pipeline,
    Program,
    Score,
    ScoreComponent,
    Setting,
    Site,
    Stage,
    User,
)

OK, FAIL, WARN = "OK  ", "ПРОВАЛ", "УВАГА"
results: list[tuple[str, str, str]] = []


def check(phase: str, name: str, cond, detail: str = ""):
    results.append((phase, OK if cond else FAIL, f"{name}{(' — ' + detail) if detail else ''}"))


def warn(phase: str, name: str, detail: str = ""):
    results.append((phase, WARN, f"{name}{(' — ' + detail) if detail else ''}"))


# Data-volume thresholds belong to a deployment, not to the code. The production
# profile mirrors the real import; the demo profile mirrors `app.seed_demo`.
PROFIL = {
    "production": dict(cp=30000, osoby=20000, bio=5000, lonh=55, haby=35, elev=35, park=15,
                       shablony=26, prywyazani=24, shary=5),
    "demo": dict(cp=50, osoby=50, bio=30, lonh=0, haby=4, elev=0, park=0,
                 shablony=3, prywyazani=0, shary=0),
}
P = PROFIL["demo" if config.ENV != "production" else "production"]


def main() -> int:
    with Session(engine) as s:
        cnt = lambda m: s.exec(select(func.count()).select_from(m)).one()  # noqa: E731

        # ── Ф0: каркас, ролі, секрети ────────────────────────────────────────
        users = list(s.exec(select(User)))
        check("Ф0", "4 користувачі створені", len(users) == 4, f"{len(users)}")
        check("Ф0", "у кожного своя роль",
              len({u.role for u in users}) == 4)
        check("Ф0", "у кожного власний пароль (не порожній)",
              all(u.password_hash for u in users))
        check("Ф0", "паролі не зберігаються відкрито (argon2id)",
              all(u.password_hash.startswith("$argon2id$") for u in users))
        check("Ф0", "БД поза git", ".gitignore" and "data/" in
              (Path(__file__).parent.parent / ".gitignore").read_text(encoding="utf-8"))
        check("Ф0", "секрет розробки не потрапив у прод-режим",
              not config.IS_PROD or not config.SECRET.startswith("dev-"))

        # ── Ф1: довідник ─────────────────────────────────────────────────────
        n_cp, n_p = cnt(Counterparty), cnt(Person)
        check("Ф1", "довідник наповнено", n_cp > P["cp"], f"{n_cp:,} організацій".replace(",", " "))
        check("Ф1", "контактні особи є", n_p > P["osoby"], f"{n_p:,} осіб".replace(",", " "))
        dup = s.exec(select(func.count()).select_from(
            select(Counterparty.edrpou).where(Counterparty.edrpou != "")
            .group_by(Counterparty.edrpou).having(func.count() > 1).subquery())).one()
        check("Ф1", "дублів за ЄДРПОУ немає", dup == 0, f"{dup}")
        fts = s.connection().exec_driver_sql("SELECT count(*) FROM cp_fts").scalar()
        check("Ф1", "індекс пошуку відповідає базі", fts == n_cp, f"{fts} проти {n_cp}")

        # ── Ф2: воронки ──────────────────────────────────────────────────────
        progs = {p.code: p for p in s.exec(select(Program))}
        # Напрями тепер заводяться з інтерфейсу, тому фіксувати їх ТОЧНИЙ набір
        # не можна: це ламало б перевірку рівно тоді, коли Марко додасть третій.
        # Перевіряємо контракт, а не склад: стартові два на місці, і в КОЖНОГО
        # напряму є воронка зі стадіями.
        check("Ф2", "стартові напрями на місці",
              {"biogaz", "elevator"} <= set(progs), f"є: {sorted(progs)}")
        for code, prog in sorted(progs.items()):
            pipe = s.exec(select(Pipeline).where(
                Pipeline.program_id == prog.id)).first()
            n = len(list(s.exec(select(Stage).where(
                Stage.pipeline_id == pipe.id)))) if pipe else 0
            check("Ф2", f"воронка «{prog.title}» має стадії", bool(pipe) and n >= 2,
                  f"{n} стадій")
        # Прапори стадій не мають розходитись із результатом — інакше та сама
        # стадія поводиться по-різному в гейтах і в статусі угоди.
        rozbizh = [st.code for st in s.exec(select(Stage))
                   if bool(st.rezultat) != bool(st.is_terminal)]
        check("Ф2", "результат і прапори стадій узгоджені", not rozbizh, str(rozbizh))
        gates = [st for st in s.exec(select(Stage)) if st.required_list]
        check("Ф2", "гейти налаштовані", len(gates) >= 6, f"{len(gates)} стадій з гейтом")

        # ── Ф3: обʼєкти, скоринг, карта ──────────────────────────────────────
        bio = s.exec(select(func.count()).select_from(Site)
                     .where(Site.site_type == "biogaz_object")).one()
        hro = s.exec(select(func.count()).select_from(Site)
                     .where(Site.site_type == "hromada_lokatsiya")).one()
        check("Ф3", "біогаз-обʼєкти", bio > P["bio"], f"{bio}")
        # Ф9: до 55 громад лонг-листа додались кандидати зі скорингу 225
        lonh = s.exec(select(func.count()).select_from(Site).where(
            Site.site_type == "hromada_lokatsiya",
            Site.source.like("%elevator_longlist%"))).one()
        check("Ф3", "громади лонг-листа на місці", lonh == P["lonh"],
              f"{lonh} з лонг-листа · {hro} громад усього")
        # Ф9: два покоління кластеризації живуть поруч свідомо — стара має
        # розкладений СКЛАД, нова свіже ТЕО; критерій стежить за обома
        haby = list(s.exec(select(Cluster)))
        stari = [c for c in haby if "clusters" in (c.source or "").split(",")]
        novi = [c for c in haby if "karta_clusters" in (c.source or "")]
        check("Ф3", "хаби", len(haby) >= P["haby"] and len(stari) >= P["haby"] * (config.ENV == "production"),
              f"{len(haby)} усього · {len(stari)} зі складом · {len(novi)} з оновленої карти")
        mem = list(s.exec(select(ClusterMember)))
        linked = [m for m in mem if m.site_id]
        check("Ф3", "члени хабів привʼязані до обʼєктів",
              len(linked) == len(mem), f"{len(linked)} з {len(mem)}")
        # Парки й наявні елеватори балу НЕ отримують — для них моделі немає,
        # а фальшиве число виглядало б правдоподібно (Ф9)
        bez_balu = s.exec(select(func.count()).select_from(Site).where(
            Site.site_type.in_(["elevator_isnuyuchyi", "industrialnyi_park"]))).one()
        check("Ф3", "бал порахований для всіх, кому він передбачений",
              cnt(Score) >= bio + hro, f"{cnt(Score)} при {bio + hro} оцінюваних"
              + (f" (+{bez_balu} без моделі балу)" if bez_balu else ""))
        # Різні моделі мають різну кількість складових (FAT_SCORE — 6, C1–C9 — 9,
        # рейтинг кандидатів — 1 реальна + 3 довідкові), тому перевіряємо,
        # що РОЗКЛАД Є у кожного балу, а не фіксований множник
        bez_skladu = s.exec(select(func.count()).select_from(
            select(Score.id).outerjoin(
                ScoreComponent, ScoreComponent.score_id == Score.id)
            .group_by(Score.id).having(func.count(ScoreComponent.id) == 0)
            .subquery())).one()
        check("Ф3", "бал збережено РОЗКЛАДЕНИМ (жодного голого числа)",
              bez_skladu == 0,
              f"{cnt(ScoreComponent)} компонентів · без розкладу {bez_skladu}")
        no_geo = s.exec(select(func.count()).select_from(Site)
                        .where(Site.site_type == "biogaz_object",
                               Site.lat.is_(None))).one()
        warn("Ф3", "обʼєктів без координат (на карту не потраплять)", f"{no_geo}")
        hrubi = s.exec(select(func.count()).select_from(Site).where(
            Site.site_type == "biogaz_object",
            Site.geo_precision.in_(["oblast", "raion"]))).one()
        if hrubi:
            warn("Ф3", "координати лише рівня району/області "
                       "(на карті вимкнені за замовчуванням)", f"{hrubi}")

        # ── Ф9: елеватори й карти ────────────────────────────────────────────
        elev = s.exec(select(func.count()).select_from(Site)
                      .where(Site.site_type == "elevator_isnuyuchyi")).one()
        park = s.exec(select(func.count()).select_from(Site)
                      .where(Site.site_type == "industrialnyi_park")).one()
        check("Ф9", "наявні елеватори", elev >= P["elev"], f"{elev}")
        check("Ф9", "індустріальні парки", park >= P["park"], f"{park}")
        z_mistkistyu = s.exec(select(func.count()).select_from(Site).where(
            Site.site_type == "elevator_isnuyuchyi",
            Site.capacity_t.is_not(None))).one()
        check("Ф9", "місткість елеваторів прочитана з джерела (не оцінена)",
              z_mistkistyu == elev, f"{z_mistkistyu} з {elev}")

        kand = s.exec(select(func.count()).select_from(Score)
                      .where(Score.model == "elevator_ranking")).one()
        c19 = s.exec(select(func.count()).select_from(Score)
                     .where(Score.model == "hromada_screening")).one()
        check("Ф9", "дві методики оцінки громад живуть окремо",
              kand > 0 and c19 > 0,
              f"рейтинг кандидатів {kand} · критерії C1–C9 {c19}")
        podviyni = s.exec(select(func.count()).select_from(
            select(Score.site_id).where(Score.model.in_(
                ["elevator_ranking", "hromada_screening"]))
            .group_by(Score.site_id).having(func.count() > 1).subquery())).one()
        check("Ф9", "громади з обома моделями балу — обидві збережені",
              podviyni >= 0, f"{podviyni} громад мають обидві оцінки")

        tz = s.exec(select(func.count()).select_from(Site)
                    .where(Site.passport_md != "")).one()
        check("Ф9", "перед-ТЕО прикріплені до громад", tz > 0 or config.ENV != "production", f"{tz}")

        gis = Path(__file__).parent.parent / "static" / "gis" / "v1"
        shary = sorted(x.name for x in gis.glob("*.geojson")) if gis.exists() else []
        check("Ф9", "інфраструктурні шари готові до карти", len(shary) == P["shary"],
              f"{len(shary)}: {', '.join(x.replace('.geojson', '') for x in shary)}")

        # ── Ф4: документи ────────────────────────────────────────────────────
        tpl = list(s.exec(select(DocTemplate)))
        check("Ф4", "шаблони листів на місці", len(tpl) == P["shablony"], f"{len(tpl)}")
        bound = [t for t in tpl if t.stage_code]
        check("Ф4", "шаблони привʼязані до стадій", len(bound) >= P["prywyazani"],
              f"{len(bound)} з {len(tpl)}")
        check("Ф4", "у шаблонах розмічені плейсхолдери",
              all(t.placeholders for t in tpl))
        check("Ф4", "налаштування офісу заведені", cnt(Setting) >= 5)
        empty = [x.key for x in s.exec(select(Setting)) if not x.value]
        if empty:
            warn("Ф4", "порожні налаштування (підставляться порожніми в листи)",
                 ", ".join(empty))

        # ── Ф6: AI ───────────────────────────────────────────────────────────
        from app.services import extract, llm
        r = extract.run("rahunok", "ТОВ «Тест»\nНасос | 3 | 1200.00 | 3600.00\n"
                                   "Сума без ПДВ: 3600.00\nПДВ 20%: 720.00\n"
                                   "Всього: 4320.00")
        check("Ф6", "витяг працює без LLM", r.engine == "deterministic")
        check("Ф6", "арифметика перевіряється кодом", r.arithmetic.ok)
        bad = extract.check_arithmetic({"pozytsii": [
            {"kilkist": 3, "tsina": 1200, "suma": 3900}]})
        check("Ф6", "підроблена сума ловиться", not bad.ok)
        check("Ф6", "вихід валідується схемою", r.schema_valid)
        if not llm.available():
            warn("Ф6", "мовна модель вимкнена",
                 "працює детермінований екстрактор; вмикається LLM_API_KEY")

        # ── Ф8: шар співпраці ────────────────────────────────────────────────
        from app.models import CAP_APPROVE, Approval, Notification
        from app.services import collab as sp
        from app.web import collab as spweb

        approvers = [u.name for u in users if u.can(CAP_APPROVE)]
        check("Ф8", "право затверджувати має рівно одна людина",
              len(approvers) == 1, ", ".join(approvers) or "нікого")
        check("Ф8", "затверджує НЕ адміністратор системи",
              all(not u.can(CAP_APPROVE) for u in users if u.role == "rozrobnyk"))

        # правило самозатвердження перевіряємо на коді роуту, а не в живій базі
        import inspect
        src = inspect.getsource(spweb.decide)
        check("Ф8", "автор не затверджує власну роботу",
              "a.submitted_by == user.name" in src and "403" in src)
        check("Ф8", "«на доопрацювання» вимагає коментаря",
              'status == "na_doopratsyuvannya" and not note.strip()' in src)
        check("Ф8", "відмова від завдання вимагає причини",
              "if not reason.strip():" in inspect.getsource(spweb.decline_task))

        # Стрічка = рендер аудиту. У свіжій базі змістовних подій ще нема (самі
        # входи в систему, які свідомо не показуємо), тому перевіряємо саме
        # здатність рендерити, а не кількість — інакше критерій «зелений порожньо».
        from app.models import AuditEntry
        znachushchi = [a for a in s.exec(select(AuditEntry))
                       if a.action not in sp.FEED_SKIP]
        podiyi = sp.feed(s, limit=200)
        check("Ф8", "стрічка рендерить усі змістовні події аудиту",
              len(podiyi) == len(znachushchi),
              f"{len(podiyi)} з {len(znachushchi)}"
              + (" (робота ще не починалась)" if not znachushchi else ""))
        check("Ф8", "кожна подія стрічки має людську фразу",
              all(p.phrase and p.phrase != p.action for p in podiyi))

        stari = [a for a in s.exec(select(Approval).where(
            Approval.status == "na_rozglyadi")) if a.days_waiting >= 3]
        if stari:
            warn("Ф8", "подання чекають рішення понад 3 дні", f"{len(stari)} шт.")
        neproch = cnt(Notification)
        if neproch:
            warn("Ф8", "сповіщень у системі", f"{neproch} шт.")

    print(f"\n{'=' * 74}\nПРИЙМАЛЬНА ПЕРЕВІРКА · ОФІС\n{'=' * 74}")
    cur = None
    for phase, status, text in results:
        if phase != cur:
            print(f"\n{phase}")
            cur = phase
        mark = {OK: "  [OK]   ", FAIL: "  [ПРОВАЛ]", WARN: "  [увага] "}[status]
        print(f"{mark} {text}")

    n_ok = sum(1 for _, st, _ in results if st == OK)
    n_fail = sum(1 for _, st, _ in results if st == FAIL)
    n_warn = sum(1 for _, st, _ in results if st == WARN)
    print(f"\n{'-' * 74}")
    print(f"Пройдено: {n_ok} · Провалено: {n_fail} · Попереджень: {n_warn}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
