# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Start-up guards, schema migration, bootstrap, error pages and the password CLI.

These are the paths that only run on a server: a production start with development
secrets, an empty database behind a missing volume, a first start with no admin
password. Each is a guarantee the README states, so each is pinned here.
"""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as sa_engine
from sqlmodel import Session, SQLModel, create_engine, select
from starlette.requests import Request

from app import config, db, main
from app.models import ChatRoom, Counterparty, DataSource, Setting, Stage, User
from app.security import check_pw


# ── production refuses to start with development secrets ────────────────────

def test_prod_ne_startuye_bez_sekretu(monkeypatch):
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "IS_DEMO", False)
    monkeypatch.setattr(config, "SECRET", "")
    with pytest.raises(RuntimeError, match="OFIS_SECRET"):
        config.check_prod_ready()


@pytest.mark.parametrize("sekret", ["dev-secret-not-for-production-but-long-enough", "short-random"])
def test_prod_ne_startuye_z_dev_abo_korotkym_sekretom(monkeypatch, sekret):
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "IS_DEMO", False)
    monkeypatch.setattr(config, "SECRET", sekret)
    with pytest.raises(RuntimeError, match="32 characters"):
        config.check_prod_ready()


def test_prod_i_demo_vodnochas_zaboroneno(monkeypatch):
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "IS_DEMO", True)
    monkeypatch.setattr(config, "SECRET", "r" * 40)
    with pytest.raises(RuntimeError, match="demo and production"):
        config.check_prod_ready()


def test_prod_startuye_z_vypadkovym_sekretom(monkeypatch):
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "IS_DEMO", False)
    monkeypatch.setattr(config, "SECRET", "r" * 40)
    config.check_prod_ready()                       # no exception


def test_secrets_env_pidvantazhuyetsya_ale_ne_peretyraye_zadane(tmp_path, monkeypatch):
    (tmp_path / "secrets.env").write_text(
        "# comment\n\nOFIS_T_A = \"quoted\"\nOFIS_T_B='single'\nno-equals-line\nOFIS_T_C=zzz\n",
        encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.delenv("OFIS_T_A", raising=False)
    monkeypatch.delenv("OFIS_T_B", raising=False)
    monkeypatch.setenv("OFIS_T_C", "from-outside")
    try:
        config._load_secrets_file()
        assert os.environ["OFIS_T_A"] == "quoted"
        assert os.environ["OFIS_T_B"] == "single"
        assert os.environ["OFIS_T_C"] == "from-outside"     # the environment wins
    finally:
        os.environ.pop("OFIS_T_A", None)
        os.environ.pop("OFIS_T_B", None)


def test_secrets_env_vidsutnii_ne_pomylka(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    config._load_secrets_file()


# ── an empty database in production is a missing volume, not a fresh start ──

def test_prod_na_porozhnii_bazi_padaye(engine, monkeypatch):
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(db, "engine", engine)
    with pytest.raises(RuntimeError, match="DB_PATH"):
        main._baza_zhyva()


def test_prod_na_napovnenii_bazi_startuye(engine, s, monkeypatch):
    s.add(Counterparty(name="ТОВ Тест"))
    s.commit()
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(db, "engine", engine)
    main._baza_zhyva()


def test_lifespan_vykonuye_vsi_zapobizhnyky(engine, monkeypatch):
    """Lifespan = secrets check → schema → empty-volume check, in that order."""
    vyklyky = []
    monkeypatch.setattr(config, "check_prod_ready", lambda: vyklyky.append("secrets"))
    monkeypatch.setattr(main, "init_db", lambda: vyklyky.append("init"))
    monkeypatch.setattr(main, "_baza_zhyva", lambda: vyklyky.append("volume"))
    with TestClient(main.app):
        pass
    assert vyklyky == ["secrets", "init", "volume"]


# ── schema: migration and bootstrap ─────────────────────────────────────────

def _file_engine(tmp_path):
    import app.models  # noqa: F401
    eng = create_engine(f"sqlite:///{tmp_path / 'm.sqlite'}")
    return eng


def test_init_db_na_chystii_bazi_stvoryuye_komandu_dzherela_kimnatu(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", "")
    eng = _file_engine(tmp_path)
    try:
        db.init_db(eng)
        db.init_db(eng)                               # idempotent
        with Session(eng) as s:
            users = list(s.exec(select(User)))
            assert sorted(u.login for u in users) == sorted(t[1] for t in db.TEAM)
            assert all(not u.password_hash for u in users)   # no password set without a bootstrap secret
            assert len(list(s.exec(select(DataSource)))) == len(db.SOURCES)
            assert len(list(s.exec(select(ChatRoom)))) == 1
            assert s.exec(select(Setting)).first() is not None
            assert s.exec(select(Stage)).first() is not None
    finally:
        eng.dispose()


def test_bootstrap_u_prodi_vymagaye_parol_admina(tmp_path, monkeypatch):
    eng = _file_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", "short")
    try:
        with pytest.raises(RuntimeError, match="OFIS_BOOTSTRAP_PASSWORD"):
            db.bootstrap(eng)
        monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", "OnlyLettersButLongEnough")
        with pytest.raises(RuntimeError, match="not letters only"):
            db.bootstrap(eng)
        with Session(eng) as s:
            assert s.exec(select(User)).first() is None
    finally:
        eng.dispose()


def test_bootstrap_paroll_lyshe_pershomu_adminu(tmp_path, monkeypatch):
    eng = _file_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "BOOTSTRAP_PASSWORD", "a-long-bootstrap-pass")
    try:
        db.bootstrap(eng)
        with Session(eng) as s:
            by_login = {u.login: u for u in s.exec(select(User))}
        admin = by_login[db.TEAM[0][1]]
        assert check_pw(admin.password_hash, "a-long-bootstrap-pass")[0]
        assert all(not u.password_hash for k, u in by_login.items() if k != db.TEAM[0][1])
    finally:
        eng.dispose()


def test_migrate_dodaye_kolonky_yakykh_brakuye(tmp_path):
    eng = _file_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    with eng.connect() as c:
        c.exec_driver_sql("ALTER TABLE stage DROP COLUMN avto_umova")
        c.commit()
    try:
        db.migrate(eng)
        with eng.connect() as c:
            cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(stage)")}
            indexes = {r[1] for r in c.exec_driver_sql("PRAGMA index_list(stage)")}
        assert "avto_umova" in cols
        assert "uq_stage_code" in indexes             # duplicate stage codes would break the gates
    finally:
        eng.dispose()


def test_dozapovnennya_stadii_ne_perebyvaye_ruchne(tmp_path):
    eng = _file_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    from app.models import Pipeline, Program
    with Session(eng) as s:
        s.add(Program(id=1, code="p", title="P"))
        s.add(Pipeline(id=1, program_id=1, code="v", title="V"))
        for sid, code, won, term, rez in [(1, "dzvonyty", 0, 0, ""), (2, "kontakt", 0, 0, ""),
                                          (3, "ugoda", 1, 1, ""), (4, "vidkladeno", 0, 1, ""),
                                          (5, "vidmova", 0, 1, ""), (6, "ruchna", 1, 1, "vtrata")]:
            s.add(Stage(id=sid, pipeline_id=1, code=code, title=code, order=sid,
                        is_won=bool(won), is_terminal=bool(term), rezultat=rez))
        s.commit()
    try:
        db._dozapovnyty_voronky(eng)
        with Session(eng) as s:
            st = {x.code: x for x in s.exec(select(Stage))}
        assert st["ugoda"].rezultat == "vygrash"
        assert st["vidkladeno"].rezultat == "vidkladeno"
        assert st["vidmova"].rezultat == "vtrata"
        assert st["ruchna"].rezultat == "vtrata"        # set by a person: not overwritten
        assert st["dzvonyty"].avto_umova == "aktyvnist" and st["dzvonyty"].avto_do_id == 2
    finally:
        eng.dispose()


def test_dozapovnennya_bez_tablytsi_stadii(tmp_path):
    eng = sa_engine(f"sqlite:///{tmp_path / 'none.sqlite'}")
    try:
        db._dozapovnyty_voronky(eng)
    finally:
        eng.dispose()


# ── health and error pages ──────────────────────────────────────────────────

def test_health_503_koly_baza_ne_vidpovidaye():
    from app.db import get_session
    bez_tablyts = create_engine("sqlite://")
    def _sess():
        with Session(bez_tablyts) as sess:
            yield sess
    main.app.dependency_overrides[get_session] = _sess
    try:
        r = TestClient(main.app).get("/health")
        assert r.status_code == 503
        assert r.json() == {"ok": False, "baza": "OperationalError"}     # dev only names the error
    finally:
        main.app.dependency_overrides.clear()
        bez_tablyts.dispose()


def test_health_503_bez_detalei_poza_dev(monkeypatch):
    from app.db import get_session
    bez_tablyts = create_engine("sqlite://")
    def _sess():
        with Session(bez_tablyts) as sess:
            yield sess
    main.app.dependency_overrides[get_session] = _sess
    monkeypatch.setattr(config, "ENV", "production")
    try:
        r = TestClient(main.app).get("/health")
        assert r.status_code == 503 and r.json() == {"ok": False}
    finally:
        main.app.dependency_overrides.clear()
        bez_tablyts.dispose()


def test_health_u_dev_pokazuye_chysla(klient_bez_vhodu):
    body = klient_bez_vhodu.get("/health").json()
    assert body["ok"] is True and body["env"] == "dev" and "kontrahentiv" in body


def _zapyt(path="/x", accept="text/html"):
    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": [(b"accept", accept.encode())],
             "scheme": "http", "server": ("t", 80), "client": ("1.2.3.4", 1), "root_path": ""}
    return Request(scope)


def test_neobroblena_pomylka_ne_rozkryvaye_detalei():
    r = asyncio.run(main._server_error(_zapyt("/api/x"), ValueError("secret detail")))
    assert r.status_code == 500
    assert b"secret detail" not in r.body


def test_storinka_pomylky_maye_ostanniy_rubizh(monkeypatch):
    import app.web.deps as deps

    def zlamanyi(*a, **k):
        raise RuntimeError("template broken")
    monkeypatch.setattr(deps, "render", zlamanyi)
    r = main._error_page(_zapyt(), 500, "")
    assert r.status_code == 500 and r.body == b"Error 500"


def test_api_pomylka_v_json(klient):
    r = klient.get("/api/nemaye-takoho")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_nekorektnyi_parametr_daye_422_storinku(klient):
    r = klient.get("/dokumenty/ne-chyslo")
    assert r.status_code == 422 and "text/html" in r.headers["content-type"]


def test_nevidoma_mova_ne_stavyt_cookie(klient):
    r = klient.get("/lang/xx", params={"next": "/"})
    assert r.status_code == 303 and "lang=" not in r.headers.get("set-cookie", "")


def test_statyka_vendor_kesh_tyzhden_inshe_no_cache(klient):
    assert klient.get("/static/vendor/htmx.min.js").headers["cache-control"] == "public, max-age=604800"
    css = next(p for p in (config.BASE_DIR / "static_v2").rglob("*.css"))
    rel = css.relative_to(config.BASE_DIR / "static_v2").as_posix()
    assert klient.get(f"/static_v2/{rel}").headers["cache-control"] == "no-cache"


# ── password CLI (`python -m app.parol`) ────────────────────────────────────

@pytest.fixture()
def parol_cli(engine, s, monkeypatch):
    from app import parol
    s.add(User(name="Марко", login="marko", role="rozrobnyk"))
    s.add(User(name="Галина", login="halyna", role="zapyty_lysty"))
    s.commit()
    monkeypatch.setattr(parol, "engine", engine)
    return parol


def _hash(engine, login):
    with Session(engine) as s:
        return s.exec(select(User).where(User.login == login)).one().password_hash


def test_parol_bez_argumentiv(parol_cli):
    assert parol_cli.main([]) == 2


def test_parol_nevidomyi_login(parol_cli):
    assert parol_cli.main(["nikhto"]) == 2
    assert parol_cli.zadaty("nikhto", "whatever-123") is False


def test_parol_zakorotkyi_i_ne_zbihsya(parol_cli, engine, monkeypatch):
    monkeypatch.setattr(parol_cli.getpass, "getpass", lambda prompt="": "short")
    assert parol_cli.main(["marko"]) == 2
    monkeypatch.setattr(parol_cli.getpass, "getpass", lambda prompt="": "OnlyLettersMixedCase")
    assert parol_cli.main(["marko"]) == 2
    vvedennya = iter(["long-enough-1", "long-enough-2"])
    monkeypatch.setattr(parol_cli.getpass, "getpass", lambda prompt="": next(vvedennya))
    assert parol_cli.main(["marko"]) == 2
    assert _hash(engine, "marko") == ""


def test_parol_zminyuyetsya_i_ne_beretsya_z_argumentiv(parol_cli, engine, monkeypatch):
    monkeypatch.setattr(parol_cli.getpass, "getpass", lambda prompt="": "long-enough-pass")
    assert parol_cli.main(["marko"]) == 0
    assert check_pw(_hash(engine, "marko"), "long-enough-pass")[0]
    assert _hash(engine, "marko").startswith("$argon2id$")


def test_parol_usim_vypadkovi_bez_skhozhykh_symvoliv(parol_cli, engine, capsys):
    assert parol_cli.main(["--usim"]) == 0
    out = capsys.readouterr().out
    for login in ("marko", "halyna"):
        line = next(x for x in out.splitlines() if x.strip().startswith(login))
        pw = line.split()[-1]
        assert len(pw) == 14 and not set(pw) & set("lI1O0")
        assert check_pw(_hash(engine, login), pw)[0]
