"""SQLite-двигун + ідемпотентна ініціалізація (create_all + migrate + FTS + bootstrap).

Міграції — ідемпотентні ALTER через PRAGMA table_info, без Alembic (ТЗ §10).
"""
from sqlmodel import Session, SQLModel, create_engine, select

from . import config
from . import fts as fts_mod
from . import models  # noqa: F401 — реєструє таблиці в metadata
from .models import DataSource, User
from .security import hash_pw

engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False},
)

# Нові колонки додавати сюди: {"counterparty": {"нова": "TEXT DEFAULT ''"}}
WANTED_COLUMNS: dict[str, dict[str, str]] = {
    # Ф9 — оновлений зріз баз 05.08.2026: реєстрові дані юрособи
    "counterparty": {
        "all_kved": "TEXT DEFAULT ''",
        "statut_kapital": "REAL",
        "vat_code": "TEXT DEFAULT ''",
        "date_reg": "TEXT DEFAULT ''",
        "org_status": "TEXT DEFAULT ''",
        "financially_active": "INTEGER",       # NULL = не перевіряли, не «ні»
        "beneficiaries": "TEXT DEFAULT ''",
        "transport_size": "TEXT DEFAULT ''",
        "real_estate_count": "INTEGER",
        "activity_text": "TEXT DEFAULT ''",
    },
    # Ф9 — властивості МАЙДАНЧИКА + сліди походження даних
    "site": {
        "capacity_t": "REAL",
        "dist_elevator_km": "REAL",
        "sector": "TEXT DEFAULT ''",
        "dpss_facilities": "TEXT DEFAULT ''",
        "mismatch": "TEXT DEFAULT ''",
        "updated_from": "TEXT DEFAULT ''",
        "data_updated_at": "TIMESTAMP",        # окремо від updated_at — див. models/site.py
        "land_area_ha": "REAL",
        "fin_active": "INTEGER",
        # ГІС-карта за ТЗ Богдана (25.08.2026): категорія підприємства.
        # Кома-список слагів, МНОЖИННА (пряме «??» з ТЗ: буває і рослинництво,
        # і тваринництво одночасно). kat_dzherelo — слід «звідки виведено»,
        # без нього кожна суперечка з реальністю читалась би як «карта бреше».
        "kat_pidpr": "TEXT DEFAULT ''",
        "kat_dzherelo": "TEXT DEFAULT ''",
        # первинна категорія за осн. КВЕД (уточнення Богдана 26.08.2026):
        # колір/сортування — за нею, членство у фільтрах — за kat_pidpr.
        "kat_osnovna": "TEXT DEFAULT ''",
        # реальні тваринницькі операції (ДПСС/поголівʼя) окремо від категорії
        # (рішення 27.08.2026): позначка «є реальна ферма», не змішується у вкладки.
        "operatsiyi": "TEXT DEFAULT ''",
        "operatsiyi_dzherelo": "TEXT DEFAULT ''",
    },
    # Ф9 — яка кластеризація дала хаб (стара зі складом / нова зі свіжим ТЕО)
    "cluster": {"source": "TEXT DEFAULT ''"},
    "person": {},
    # Ф11 — «що змінилось відколи ви були»: мітка останнього перегляду «мого дня»
    "user": {"bachyv_do": "TIMESTAMP"},
    # Ф8 — шар співпраці: завдання може вирости з коментаря, його можна взяти
    # в роботу або відмовитись із причиною
    "task": {
        "comment_id": "INTEGER",
        "cluster_id": "INTEGER",
        "accepted_at": "TIMESTAMP",
        "declined_reason": "TEXT DEFAULT ''",
    },
    # Ф10 — перенесення переписки: архівні кімнати з обмеженням видимості
    "chatroom": {
        "uchasnyky": "TEXT DEFAULT ''",
        "dzherelo": "TEXT DEFAULT ''",
        "wa_chat": "TEXT DEFAULT ''",
        "poryadok": "INTEGER DEFAULT 0",
    },
    "chatmessage": {
        "media_vtracheno": "INTEGER DEFAULT 0",
        "vidsutni_fayly": "TEXT DEFAULT ''",
    },
    # Ф12 — напрями угод керуються з інтерфейсу: поведінка воронки стає ДАНИМИ
    # замість впізнавання стадій за рядковим кодом
    "program": {
        "opys": "TEXT DEFAULT ''",
        "model_balu": "TEXT DEFAULT ''",
    },
    "stage": {
        "rezultat": "TEXT DEFAULT ''",
        "avto_umova": "TEXT DEFAULT ''",
        "avto_do_id": "INTEGER",
    },
}


def migrate(eng=None) -> None:
    eng = eng or engine
    with eng.connect() as c:
        c.exec_driver_sql("PRAGMA journal_mode=WAL")
        c.exec_driver_sql("PRAGMA busy_timeout=5000")
        for table, cols in WANTED_COLUMNS.items():
            have = {r[1] for r in c.exec_driver_sql(f"PRAGMA table_info({table})")}
            if not have:
                continue  # таблиці ще немає — create_all зробить її повною
            for col, ddl in cols.items():
                if col not in have:
                    c.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        c.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_cp_edrpou "
            "ON counterparty(edrpou) WHERE edrpou != ''"
        )
        # Карта тягне десятки тисяч точок одного типу з координатами — без цього
        # індексу це повне сканування site на кожне завантаження карти.
        c.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_site_karta ON site(site_type, lat)")
        # Бал вибирається завжди разом із моделлю: на одній громаді їх дві
        # (C1–C9 і рейтинг кандидатів), і без моделі в ключі рядок дублюється.
        c.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_score_model ON score(site_id, model)")
        # Коди напрямів і стадій мають бути унікальні. Без цих індексів дублікат
        # проходив мовчки — а на нього спираються гейти (порівняння рядків) і
        # посилання шаблонів листів, тож два «elevator» тихо ламали б обидва.
        c.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_program_code ON program(code)")
        c.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_stage_code "
            "ON stage(pipeline_id, code) WHERE code != ''")
        c.commit()
    _dozapovnyty_voronky(eng)


def _dozapovnyty_voronky(eng) -> None:
    """Одноразове дозаповнення нових полів стадій у ВЖЕ наявній базі.

    Посів більше не переписує стадії (і правильно робить), тому наявні воронки
    самі нових полів не отримають. Виводимо `rezultat` із того, що вже є —
    прапорів і кодів, — і відновлюємо єдиний автоперехід, який досі був зашитий
    у `voronky.py`.

    Ідемпотентно: чіпаємо лише рядки, де поле ще порожнє. Якщо Марко вже
    поставив результат руками, ми його не перебиваємо.
    """
    with eng.connect() as c:
        if not {r[1] for r in c.exec_driver_sql("PRAGMA table_info(stage)")}:
            return
        # виграш / втрата / відкладено — з прапорів і кодів, як воно читалось досі
        c.exec_driver_sql(
            "UPDATE stage SET rezultat='vygrash' "
            "WHERE (rezultat IS NULL OR rezultat='') AND is_won=1")
        c.exec_driver_sql(
            "UPDATE stage SET rezultat='vidkladeno' "
            "WHERE (rezultat IS NULL OR rezultat='') AND is_terminal=1 "
            "AND code='vidkladeno'")
        c.exec_driver_sql(
            "UPDATE stage SET rezultat='vtrata' "
            "WHERE (rezultat IS NULL OR rezultat='') AND is_terminal=1")
        # автоперехід «Дзвонити → Контакт встановлено», який був у коді
        c.exec_driver_sql(
            "UPDATE stage SET avto_umova='aktyvnist', avto_do_id=("
            "  SELECT k.id FROM stage k"
            "  WHERE k.pipeline_id = stage.pipeline_id AND k.code='kontakt') "
            "WHERE code='dzvonyty' AND (avto_umova IS NULL OR avto_umova='') "
            "AND EXISTS (SELECT 1 FROM stage k WHERE k.pipeline_id = stage.pipeline_id "
            "            AND k.code='kontakt')")
        c.commit()


# Synthetic demo team (fictional people). Roles mirror the real division of work.
TEAM = [
    ("Марко", "marko", "rozrobnyk"),
    ("Богдан", "bohdan", "perehovorschyk_dok"),
    ("Оксана", "oksana", "perehovory_kontakt"),
    ("Галина", "halyna", "zapyty_lysty"),
]

# Data sources registered on first start (synthetic demo descriptions).
SOURCES = [
    ("demo_kontakty", "Contacts directory (CSV import)", "csv",
     "imports/contacts.csv", "organisations and contact persons"),
    ("demo_obyekty", "Biogas sites (GeoJSON)", "geojson",
     "imports/sites.geojson", "sites with feedstock, capacity and coordinates"),
    ("demo_hromady", "Community screening (CSV)", "csv",
     "imports/communities.csv", "candidate communities with C1-C9 scores"),
    ("letters", "Letter templates", "md",
     "templates/letters/", "outreach letter templates"),
]


def bootstrap(eng=None) -> None:
    """Створює команду й реєстр джерел при першому запуску. Ідемпотентно."""
    eng = eng or engine
    with Session(eng) as s:
        if not s.exec(select(User)).first():
            pw = config.BOOTSTRAP_PASSWORD
            if config.IS_PROD and len(pw) < 12:
                raise RuntimeError("Empty database in production: set OFIS_BOOTSTRAP_PASSWORD "
                                   "(12+ characters) for the first admin account.")
            for name, login, role in TEAM:
                s.add(User(
                    name=name, login=login, role=role,
                    password_hash=hash_pw(pw) if pw and login == TEAM[0][1] else "",
                    created_by="bootstrap",
                ))
        have = {d.code for d in s.exec(select(DataSource))}
        for code, title, kind, path, gives in SOURCES:
            if code not in have:
                s.add(DataSource(code=code, title=title, kind=kind,
                                 path_or_endpoint=path, what_it_gives=gives,
                                 owner="marko", created_by="bootstrap"))
        s.commit()

        # налаштування офісу — підставляються в шаблони листів
        from .models import DEFAULT_SETTINGS, Setting
        known = {x.key for x in s.exec(select(Setting))}
        for key, val, label in DEFAULT_SETTINGS:
            if key not in known:
                s.add(Setting(key=key, value=val, label=label))
        s.commit()

        # єдина кімната чату — звірка по `code`, як для джерел і налаштувань вище
        from .models import KIMNATA_OFIS, ChatRoom
        if not s.exec(select(ChatRoom).where(ChatRoom.code == KIMNATA_OFIS)).first():
            s.add(ChatRoom(code=KIMNATA_OFIS, title="Загальна група офісу",
                           is_default=True, created_by="bootstrap"))
            s.commit()

    from .seed_pipelines import seed as seed_pipelines
    with Session(eng) as s:
        seed_pipelines(s)


def init_db(eng=None) -> None:
    eng = eng or engine
    SQLModel.metadata.create_all(eng)
    migrate(eng)
    with Session(eng) as s:
        fts_mod.ensure_fts(s)
        s.commit()
    bootstrap(eng)


def get_session():
    with Session(engine) as s:
        yield s
