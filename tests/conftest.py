# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Спільні фікстури: БД у пам'яті + клієнт застосунку."""
import os

os.environ.setdefault("UI_DEFAULT_LANG", "uk")
os.environ.setdefault("OFIS_ENV", "dev")
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


CSRF_TEST_TOKEN = "test-csrf-token-0123456789abcdef"


def z_csrf(c):
    """Give a test client a valid double-submit CSRF pair (cookie + header).

    Business-logic tests then post forms as a browser with a loaded page would;
    the CSRF rules themselves are covered by tests/test_security.py with a bare client.
    """
    from app.security import KIT
    c.cookies.set(KIT.csrf_cookie, CSRF_TEST_TOKEN)
    c.headers["X-CSRF-Token"] = CSRF_TEST_TOKEN
    return c


@pytest.fixture(autouse=True)
def _limiter_chystyi():
    from app.security import LIMITER
    LIMITER.reset_all()
    yield
    LIMITER.reset_all()


@pytest.fixture()
def engine():
    import app.models  # noqa: F401 — register every table before create_all
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    from app import fts as fts_mod
    with Session(eng) as s:
        fts_mod.ensure_fts(s)
        s.commit()
    yield eng
    # Рушій треба закривати явно. Без цього кожен тест лишав відкрите
    # зʼєднання SQLite: 186 `ResourceWarning` на один прогін (знайдено
    # 06.08.2026, коли з-під 10 тис. однакових попереджень нарешті стало видно
    # решту). На базі в памʼяті це не падає, але вчить неправильного — і той
    # самий патерн на файловій базі тримає її залоченою під Windows.
    eng.dispose()


@pytest.fixture()
def s(engine):
    with Session(engine) as sess:
        yield sess


@pytest.fixture()
def klient_bez_vhodu(engine):
    """Клієнт БЕЗ підміни користувача — щоб перевірити сам ланцюг входу.

    Фікстура `klient` нижче підмінює `current_user`, і це правильно для тестів
    бізнес-логіки: інакше кожен тест починався б із логіну. Але наслідок був у
    тому, що ланцюг «форма → пароль → cookie → сесія» не виконувався ЖОДНОГО разу
    за 261 тест — знайдено передрелізним прогоном `reliz` 06.08.2026.
    Тут підміняється лише сесія БД.
    """
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app

    def _sess():
        with Session(engine) as sess:
            yield sess

    app.dependency_overrides[get_session] = _sess
    c = z_csrf(TestClient(app, follow_redirects=False))
    yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def klient(engine):
    """Справжній HTTP-клієнт застосунку на БД у памʼяті.

    Через нього тести проходять той самий шлях, що й людина: розбір форми,
    залежності, рендер шаблону. Це навмисно — половина помилок у цьому
    проєкті жила саме в шаблоні, а не в логіці, і юніт-виклик роута її не бачив.
    Вхід підмінюється на рівні залежності: пароль тут нічого не перевіряє.
    """
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app
    from app.models import User
    from app.web.deps import current_user

    admin = User(id=1, name="Марко", role="rozrobnyk", active=True)

    def _sess():
        with Session(engine) as sess:
            yield sess

    app.dependency_overrides[get_session] = _sess
    app.dependency_overrides[current_user] = lambda: admin
    # Свідомо БЕЗ `with`: контекстний менеджер запустив би lifespan, а той через
    # init_db() пішов би у ФАЙЛОВУ базу проєкту замість цієї, у памʼяті.
    c = z_csrf(TestClient(app, follow_redirects=False))
    c.korystuvach = admin
    yield c
    app.dependency_overrides.clear()
