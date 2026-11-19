# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""API карти (Ф9): чесність лічильників, колонковий формат, ETag, межі моделі балу.

Головне, що тут перевіряється, — не «чи віддались дані», а чи МОЖНА ЇМ ВІРИТИ:
сума показаного й прихованого має сходитись із загальним числом, а точка з
координатою рівня області має бути позначена як така.
"""
import pytest

from app.models import Cluster, Site, User
from app.services import scoring
from app.web import karta as web


@pytest.fixture()
def user(s):
    u = User(name="Марко", login="marko", role="rozrobnyk")
    s.add(u)
    s.commit()
    return u


class _Zapyt:
    """Мінімальний обʼєкт запиту: роут читає лише заголовки."""
    def __init__(self, headers=None):
        self.headers = headers or {}


def _tochka(s, **kw):
    p = dict(site_type="biogaz_object", name="ТОВ «Тест»", oblast="Тернопільська",
             lat=49.5, lon=25.6, geo_precision="settlement", est_capacity_kw_el=500)
    p.update(kw)
    site = Site(**p)
    s.add(site)
    s.commit()
    return site


def _dani(vidpovid):
    """JSONResponse → dict (тіло серіалізоване в байти)."""
    import json
    return json.loads(vidpovid.body)


# ── чесність лічильників ────────────────────────────────────────────────────

def test_pokazano_plyus_bez_koordynat_dorivnyuye_vsim(s, user):
    _tochka(s)
    _tochka(s, name="Без координат", lat=None, lon=None)
    _tochka(s, name="Теж без", lat=None, lon=None)

    d = _dani(web.tochky(None, _Zapyt(), karta="biogaz", user=user, s=s))
    m = d["meta"]
    assert m["vsoho"] == 3
    assert m["pokazano"] + m["bez_koordynat"] == m["vsoho"], "лічильники не сходяться"
    assert "2 без координат" in m["prychyna"]


def test_hruba_koordynata_pomichena_i_porahovana(s, user):
    _tochka(s, name="Точна")
    _tochka(s, name="Лише область", geo_precision="oblast")
    _tochka(s, name="Лише район", geo_precision="raion")

    d = _dani(web.tochky(None, _Zapyt(), karta="biogaz", user=user, s=s))
    assert d["meta"]["hrubi_koordynaty"] == 2
    assert "точністю до району чи області" in d["meta"]["prychyna"]

    i = d["polya"].index("tochnist")
    assert {r[i] for r in d["ryadky"]} == {"settlement", "oblast", "raion"}


def test_prychyna_lyudskoyu_movoyu_a_ne_kod(s, user):
    _tochka(s, lat=None, lon=None)
    d = _dani(web.tochky(None, _Zapyt(), karta="biogaz", user=user, s=s))
    p = d["meta"]["prychyna"]
    assert p and not any(c in p for c in "{}[]_"), f"схоже на код, а не на текст: {p}"


# ── формат ──────────────────────────────────────────────────────────────────

def test_kolonkovyi_format_rozpakovuyetsya(s, user):
    site = _tochka(s, hromada="Бучацька", est_capacity_kw_el=1200)
    d = _dani(web.tochky(None, _Zapyt(), karta="biogaz", user=user, s=s))
    assert len(d["ryadky"]) == 1
    obj = dict(zip(d["polya"], d["ryadky"][0]))
    assert obj["id"] == site.id
    assert obj["nazva"] == "ТОВ «Тест»"
    assert obj["kw"] == 1200
    assert obj["hromada"] == "Бучацька"
    assert len(d["ryadky"][0]) == len(d["polya"]), "рядок і заголовок різної довжини"


def test_koordynaty_okruhleni_do_metra(s, user):
    _tochka(s, lat=49.123456789, lon=25.987654321)
    d = _dani(web.tochky(None, _Zapyt(), karta="biogaz", user=user, s=s))
    lat = d["ryadky"][0][d["polya"].index("lat")]
    assert lat == 49.12346, "зайві знаки роздувають відповідь без користі"


def test_try_stany_finansovoyi_aktyvnosti_ne_zlypayutsya(s, user):
    _tochka(s, name="Активний", fin_active=True)
    _tochka(s, name="Неактивний", fin_active=False)
    _tochka(s, name="Не перевіряли", fin_active=None)
    d = _dani(web.tochky(None, _Zapyt(), karta="biogaz", user=user, s=s))
    i = d["polya"].index("fin")
    assert sorted(str(r[i]) for r in d["ryadky"]) == ["0", "1", "None"]


# ── ETag ────────────────────────────────────────────────────────────────────

def test_etag_daye_304_i_zminyuyetsya_z_danymy(s, user):
    _tochka(s)
    persha = web.tochky(None, _Zapyt(), karta="biogaz", user=user, s=s)
    etag = persha.headers["etag"]

    povtor = web.tochky(None, _Zapyt({"if-none-match": etag}), karta="biogaz",
                        user=user, s=s)
    assert povtor.status_code == 304

    _tochka(s, name="Нова точка")
    nova = web.tochky(None, _Zapyt({"if-none-match": etag}), karta="biogaz",
                      user=user, s=s)
    assert nova.status_code != 304, "дані змінились, а ETag лишився старим"


def test_etag_ne_padaye_na_kyrylytsi(s, user):
    """HTTP-заголовки приймають лише latin-1 — назва області кирилицею їх ламала."""
    _tochka(s, oblast="Тернопільська")
    v = web.tochky(None, _Zapyt(), karta="biogaz", oblast="Тернопільська",
                   user=user, s=s)
    v.headers["etag"].encode("latin-1")          # має не кинути виняток


# ── межі моделі балу ────────────────────────────────────────────────────────

def test_bal_beretsya_z_pravylnoyi_modeli(s, user):
    """На громаді дві моделі балу — карта не має брати випадкову."""
    hro = _tochka(s, site_type="hromada_lokatsiya", name="Громада", lat=49.1, lon=25.1)
    scoring.save(s, hro.id, scoring.hromada_screening({"c1_grain_t_yr": 50000}))
    scoring.save_many(s, [(hro.id, scoring.Result("elevator_ranking", 70.0, [
        scoring.Component("dzherelo", "Рейтинг", 70.0, 0.7, 1.0, "xlsx", False)]))])
    s.commit()

    d = _dani(web.tochky(None, _Zapyt(), karta="elevatory", user=user, s=s))
    assert len(d["ryadky"]) == 1, "дві моделі балу задублювали точку на карті"
    assert d["ryadky"][0][d["polya"].index("bal")] == 70.0


def test_park_bez_balu_vse_odno_na_karti(s, user):
    _tochka(s, site_type="industrialnyi_park", name="ІП Тернопіль", lat=49.5, lon=25.6)
    d = _dani(web.tochky(None, _Zapyt(), karta="elevatory", user=user, s=s))
    assert len(d["ryadky"]) == 1
    assert d["ryadky"][0][d["polya"].index("bal")] is None


# ── картка й довідники ──────────────────────────────────────────────────────

def test_kartka_kazhe_pro_hrubu_koordynatu(s, user):
    site = _tochka(s, geo_precision="oblast")
    d = web.tochka(site.id, user=user, s=s)
    assert d["hruba"] is True
    assert d["tochnist"] == "лише область"


def test_kartka_viddaye_rozbizhnist_dzherel(s, user):
    site = _tochka(s, mismatch="у файлі «суха біомаса», за базою — «пташиний послід»",
                   dpss_facilities="Свиноферма")
    d = web.tochka(site.id, user=user, s=s)
    assert "пташиний послід" in d["rozbizhnist"]
    assert d["dpss"] == "Свиноферма"


def test_dovidnyky_daye_lichylnyky_bez_tyahannya_tochok(s, user):
    _tochka(s, oblast="Тернопільська", waste_category="A_biogas_wet")
    _tochka(s, oblast="Тернопільська", waste_category="A_biogas_wet")
    _tochka(s, oblast="Львівська", waste_category="C_poultry")

    d = web.dovidnyky(karta="biogaz", user=user, s=s)
    obl = {x["znachennya"]: x["n"] for x in d["oblasti"]}
    assert obl == {"Тернопільська": 2, "Львівська": 1}
    assert {x["znachennya"] for x in d["kategoriyi"]} == {"A_biogas_wet", "C_poultry"}


def test_haby_viddayut_dzherelo_klasteryzatsiyi(s, user):
    s.add(Cluster(site_code="#001", type="CLUSTER_CHP", type_label="Когенерація",
                  lat=49.5, lon=25.6, total_mw=5.0, source="karta_clusters"))
    s.commit()
    d = web.haby(user=user, s=s)
    assert d["ryadky"][0][d["polya"].index("dzherelo")] == "karta_clusters"
