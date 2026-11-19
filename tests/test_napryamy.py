"""Напрями угод: воронка як дані, керована з інтерфейсу (Ф12).

Головне, що тут перевіряється, — не «форма зберігає поле», а те, що зміна
воронки НЕ РУЙНУЄ наявні угоди. Три правила, кожне з яких колись ламалось
мовчки й тому має свій тест:

  1. Правка переживає перезапуск — посів більше не перезаписує заповнену базу.
  2. Жодна угода не лишається на неіснуючій стадії.
  3. Зміна гейта — Tier3: без підтвердження 428, а не тихе збереження.

Тести йдуть через справжній HTTP-клієнт, а не викликають функції роутів: так
разом із логікою перевіряються розбір форми й рендер шаблону.
"""
import pytest
from sqlmodel import select

from app.models import Deal, Pipeline, Program, Stage
from app.seed_pipelines import seed
from app.web.napryamy import _stadiyi


@pytest.fixture()
def baza(s):
    seed(s)
    s.commit()
    return s


def _pipe(s, kod):
    s.expire_all()
    prog = s.exec(select(Program).where(Program.code == kod)).one()
    return prog, s.exec(select(Pipeline).where(Pipeline.program_id == prog.id)).one()


def _st(s, kod):
    _, pipe = _pipe(s, kod)
    return _stadiyi(s, pipe.id)


def _novyi(klient, **kw):
    dani = {"nazva": "", "kod": "", "opys": "", "color": "#0f6fc4",
            "model_balu": "", "dzherelo": "z_nulya", "zagotovka": "",
            "kopiya_id": ""}
    dani.update(kw)
    return klient.post("/napryamy", data=dani)


def _stadiya(klient, kod, st, **kw):
    """POST правки стадії з усіма полями форми — як їх шле браузер."""
    dani = {"title": st.title, "goal": st.goal, "gate_criteria": st.gate_criteria,
            "required_doc_kinds": st.required_doc_kinds, "key_docs": st.key_docs,
            "default_probability": str(st.default_probability),
            "rezultat": st.rezultat, "avto_umova": st.avto_umova,
            "avto_do_id": str(st.avto_do_id or ""), "color": st.color, "confirm": ""}
    dani.update(kw)
    return klient.post(f"/napryamy/{kod}/stadiya/{st.id}", data=dani)


# ── екрани відкриваються ─────────────────────────────────────────────────────

def test_ekrany_vidkryvayutsya(baza, klient):
    assert klient.get("/napryamy").status_code == 200
    r = klient.get("/napryamy/biogaz")
    assert r.status_code == 200 and "Дзвонити" in r.text


def test_bez_prava_admina_403(baza, klient):
    klient.korystuvach.role = "perehovory_kontakt"      # Богдан
    assert klient.get("/napryamy").status_code == 403
    klient.korystuvach.role = "rozrobnyk"


# ── створення напряму: три шляхи ─────────────────────────────────────────────

def test_stvorennya_z_zagotovky(baza, klient):
    _novyi(klient, nazva="Завод HPL", kod="hpl", dzherelo="zagotovka",
           zagotovka="pidryad")
    from app.zagotovky_voronok import ZAGOTOVKY
    st = _st(baza, "hpl")
    assert len(st) == len(ZAGOTOVKY["pidryad"]["stadiyi"])
    assert st[0].order == 0 and st[-1].rezultat   # заготовка закінчується підсумком
    assert all(not x.required_doc_kinds for x in st)  # заготовка не вигадує гейтів


def test_stvorennya_kopiyeyu_bere_gejty(baza, klient):
    src, _ = _pipe(baza, "elevator")
    _novyi(klient, nazva="Елеватори-2", kod="elev2", dzherelo="kopiya",
           kopiya_id=str(src.id))
    stari = {(x.code, x.required_doc_kinds) for x in _st(baza, "elevator")}
    novi = {(x.code, x.required_doc_kinds) for x in _st(baza, "elev2")}
    assert stari == novi and any(k for _, k in novi)


def test_stvorennya_z_nulya_daye_odnu_stadiyu(baza, klient):
    _novyi(klient, nazva="Біодизель", kod="biodyzel")
    assert [x.code for x in _st(baza, "biodyzel")] == ["novyi"]


def test_kod_unikalnyi(baza, klient):
    r = _novyi(klient, nazva="Ще біогаз", kod="biogaz")
    assert "err=zaynyato" in r.headers["location"]
    baza.expire_all()
    assert len(list(baza.exec(select(Program).where(Program.code == "biogaz")))) == 1


# ── правка переживає перезапуск ──────────────────────────────────────────────

def test_pravka_perezhyvaye_posiv(baza, klient):
    """Найважливіший тест модуля.

    Раніше `seed()` бігав на КОЖНОМУ старті й перезаписував поля стадій
    літералами з коду. Будь-яка правка зникала після перезапуску — тихо,
    без сліду в журналі. Тепер посів мовчить, якщо база не порожня.
    """
    st = _st(baza, "biogaz")[1]
    _stadiya(klient, "biogaz", st, title="Передзвонити власнику")
    seed(baza)                      # імітація перезапуску застосунку
    baza.commit()
    baza.expire_all()
    assert baza.get(Stage, st.id).title == "Передзвонити власнику"


# ── гейт — Tier3 ─────────────────────────────────────────────────────────────

def test_zmina_gejta_bez_pidtverdzhennya_428(baza, klient):
    st = _st(baza, "elevator")[3]
    r = _stadiya(klient, "elevator", st, required_doc_kinds="COM-99")
    assert r.status_code == 428
    baza.expire_all()
    assert baza.get(Stage, st.id).required_doc_kinds != "COM-99"


def test_zmina_gejta_z_pidtverdzhennyam_prohodyt(baza, klient):
    st = _st(baza, "elevator")[3]
    _stadiya(klient, "elevator", st, required_doc_kinds="COM-99", confirm="1")
    baza.expire_all()
    assert baza.get(Stage, st.id).required_doc_kinds == "COM-99"


def test_pravka_bez_gejta_ne_vymagaye_pidtverdzhennya(baza, klient):
    st = _st(baza, "biogaz")[0]
    _stadiya(klient, "biogaz", st, title="Свіжий")
    baza.expire_all()
    assert baza.get(Stage, st.id).title == "Свіжий"


# ── жодної осиротілої угоди ──────────────────────────────────────────────────

def _ugoda(s, kod, i):
    prog, pipe = _pipe(s, kod)
    st = _stadiyi(s, pipe.id)
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=st[i].id, title="ПАП")
    s.add(d)
    s.commit()
    return d, st


def test_stadiyu_z_ugodamy_ne_prybraty_bez_perenesennya(baza, klient):
    d, st = _ugoda(baza, "biogaz", 2)
    r = klient.post(f"/napryamy/biogaz/stadiya/{st[2].id}/prybraty",
                    data={"kudy_ugody": ""})
    assert "err=kudy" in r.headers["location"]
    baza.expire_all()
    assert baza.get(Stage, st[2].id) is not None
    assert baza.get(Deal, d.id).stage_id == st[2].id


def test_pislya_perenesennya_vydalennya_prohodyt(baza, klient):
    d, st = _ugoda(baza, "biogaz", 2)
    # id запамʼятовуємо ДО видалення: після нього звернення до .id підняло б
    # видалений рядок і впало на refresh, а не на змісті перевірки
    zvidky, kudy, did = st[2].id, st[1].id, d.id
    klient.post(f"/napryamy/biogaz/stadiya/{zvidky}/prybraty",
                data={"kudy_ugody": str(kudy), "confirm": "1"})
    baza.expire_all()
    assert baza.get(Stage, zvidky) is None
    assert baza.get(Deal, did).stage_id == kudy


def test_vydalennya_obnulyaye_avtoperehody_syudy(baza, klient):
    st = _st(baza, "biogaz")
    dzvonyty = next(x for x in st if x.code == "dzvonyty")
    kontakt = next(x for x in st if x.code == "kontakt")
    assert dzvonyty.avto_do_id == kontakt.id      # посів відновив правило даними
    dz_id = dzvonyty.id
    klient.post(f"/napryamy/biogaz/stadiya/{kontakt.id}/prybraty", data={"confirm": "1"})
    baza.expire_all()
    d = baza.get(Stage, dz_id)
    assert d.avto_do_id is None and d.avto_umova == ""


def test_ostannyu_stadiyu_ne_prybraty(baza, klient):
    _novyi(klient, nazva="Тест", kod="tst")
    st = _st(baza, "tst")[0]
    r = klient.post(f"/napryamy/tst/stadiya/{st.id}/prybraty", data={})
    assert "err=ostannya" in r.headers["location"]
    baza.expire_all()
    assert baza.get(Stage, st.id) is not None


# ── автоперехід і результат — даними, не кодом ───────────────────────────────

def test_avtoperehid_pratsyuye_v_novomu_napryami(baza, klient):
    """Правило «активність із результатом → далі» задається в НОВІЙ воронці
    з інтерфейсу й спрацьовує без жодного рядка коду під цей напрям."""
    _novyi(klient, nazva="Посередництво", kod="poser", dzherelo="zagotovka",
           zagotovka="poserednytstvo")
    prog, pipe = _pipe(baza, "poser")
    st = _stadiyi(baza, pipe.id)
    _stadiya(klient, "poser", st[0], avto_umova="aktyvnist",
             avto_do_id=str(st[1].id))
    d = Deal(program_id=prog.id, pipeline_id=pipe.id, stage_id=st[0].id, title="LIMO")
    baza.add(d)
    baza.commit()

    klient.post(f"/voronky/{d.id}/activity",
                data={"type": "Дзвінок", "outcome": "чекає перелік", "note": ""})
    baza.expire_all()
    assert baza.get(Deal, d.id).stage_id == st[1].id


def test_rezultat_zadaye_status_ugody(baza, klient):
    """Термінальна стадія ставить статус із поля `rezultat`, не з коду."""
    _novyi(klient, nazva="Гранти", kod="grant2", dzherelo="zagotovka",
           zagotovka="grant")
    st = _st(baza, "grant2")
    _stadiya(klient, "grant2", st[2], rezultat="vidkladeno")
    baza.expire_all()
    pauza = baza.get(Stage, st[2].id)
    assert pauza.status_ugody == "paused" and pauza.is_terminal and not pauza.is_won


# ── порядок і архів ──────────────────────────────────────────────────────────

def test_nova_stadiya_stae_pered_terminalnymy(baza, klient):
    klient.post("/napryamy/elevator/stadiya",
                data={"title": "Юридична перевірка", "stage_kod": "yur"})
    st = _st(baza, "elevator")
    i = next(n for n, x in enumerate(st) if x.code == "yur")
    assert not st[i].rezultat
    assert all(not x.rezultat for x in st[:i])       # усі попередні — робочі
    assert any(x.rezultat for x in st[i + 1:])       # підсумкові лишились позаду


def test_perestanovka_ne_ruhaye_ugody(baza, klient):
    d, st = _ugoda(baza, "biogaz", 3)
    klient.post(f"/napryamy/biogaz/stadiya/{st[3].id}/porydok",
                data={"kudy": "vgoru"})
    baza.expire_all()
    # Угода привʼязана до стадії, а не до її місця у воронці
    assert baza.get(Deal, d.id).stage_id == st[3].id
    assert [x.id for x in _st(baza, "biogaz")][2] == st[3].id


def test_arhiv_ne_vydalyaye(baza, klient):
    klient.post("/napryamy/biogaz/arhiv")
    baza.expire_all()
    assert _pipe(baza, "biogaz")[0].active is False
    klient.post("/napryamy/biogaz/arhiv")
    baza.expire_all()
    assert _pipe(baza, "biogaz")[0].active is True
