"""Скоринг: формула, розклад на компоненти, «немає даних» ≠ нуль (ТЗ, Додаток А §4.1)."""
import pytest
from sqlmodel import select

from app.models import Score, ScoreComponent, Site
from app.services import scoring


# ── FAT_SCORE: звірка з першоджерелом ────────────────────────────────────────

def _reference(kw, d_power, d_gas, suit, has_phone, has_email, feed_t):
    """Дослівна формула з biogaz_robocha/scripts/extract_biogaz.py:102."""
    p_pow = min((kw or 0) / 3000.0, 1.0)
    p_grid = max(0.0, 1.0 - (d_power if d_power is not None else 30) / 30.0)
    p_gas = max(0.0, 1.0 - (d_gas if d_gas is not None else 30) / 30.0)
    p_suit = (suit or 0) / 100.0
    p_contact = 1.0 if has_phone else (0.5 if has_email else 0.0)
    p_feed = min((feed_t or 0) / 50000.0, 1.0)
    return round((0.42 * p_pow + 0.18 * p_grid + 0.08 * p_gas + 0.15 * p_suit
                  + 0.10 * p_contact + 0.07 * p_feed) * 100, 1)


CASES = [
    dict(kw=2332, d_power=22.1, d_gas=23.3, suit=23, has_phone=True, has_email=False,
         feed_t=23141),
    dict(kw=None, d_power=None, d_gas=None, suit=None, has_phone=False, has_email=False,
         feed_t=None),
    dict(kw=9999, d_power=0, d_gas=0, suit=100, has_phone=True, has_email=True,
         feed_t=99999),
    dict(kw=500, d_power=45, d_gas=60, suit=10, has_phone=False, has_email=True,
         feed_t=1200),
]


@pytest.mark.parametrize("c", CASES)
def test_fat_score_matches_reference_formula(c):
    assert scoring.fat_score(**c).value == _reference(**c)


def test_fat_score_bounds():
    assert scoring.fat_score(**CASES[1]).value == 0.0        # усе порожнє
    assert scoring.fat_score(**CASES[2]).value == 100.0      # усе максимальне


def test_components_sum_to_total():
    res = scoring.fat_score(**CASES[0])
    assert round(sum(c.contribution for c in res.components), 1) == pytest.approx(
        res.value, abs=0.1)


def test_six_components_with_weights():
    res = scoring.fat_score(**CASES[0])
    assert [c.key for c in res.components] == ["pow", "grid", "gas", "suit",
                                               "contact", "feed"]
    assert round(sum(c.weight for c in res.components), 2) == 1.0


def test_missing_data_is_flagged_not_zeroed_silently():
    res = scoring.fat_score(**CASES[1])
    missing = {c.key for c in res.components if c.missing}
    assert {"pow", "grid", "gas", "suit", "contact", "feed"} == missing


def test_priority_is_derived_not_stored():
    assert scoring.fat_score(kw=3000, d_power=0, d_gas=0, suit=100,
                             has_phone=True, feed_t=50000).priority == "Високий"
    assert scoring.Result("fat_score", 50).priority == "Середній"
    assert scoring.Result("fat_score", 10).priority == "Низький"


# ── Скринінг громад C1–C9 ────────────────────────────────────────────────────

def test_hromada_weights_sum_to_100():
    assert sum(w for _, _, w, _, _ in scoring.HROMADA_CRITERIA) == 100


def test_hromada_nine_criteria():
    res = scoring.hromada_screening({})
    assert len(res.components) == 9
    assert [c.key for c in res.components][0] == "c1_grain_t_yr"


def test_empty_questionnaire_scores_zero_and_marks_all_missing():
    res = scoring.hromada_screening({})
    assert res.value == 0.0
    assert all(c.missing for c in res.components)


def test_full_questionnaire_scores_100():
    vals = {k: t for k, _, _, t, _ in scoring.HROMADA_CRITERIA}
    assert scoring.hromada_screening(vals).value == 100.0


def test_zero_differs_from_no_data():
    """0 = «перевірили, немає»; None = «ще не питали». Бал однаковий, прапорець різний."""
    zero = scoring.hromada_screening({"c4_rail": 0})
    none = scoring.hromada_screening({})
    c_zero = [c for c in zero.components if c.key == "c4_rail"][0]
    c_none = [c for c in none.components if c.key == "c4_rail"][0]
    assert c_zero.missing is False and c_none.missing is True
    assert zero.value == none.value == 0.0


def test_partial_questionnaire_is_capped_by_answered_weights():
    """Відповіли лише на C1 → максимум 25 балів, і видно, що решта не заповнена."""
    res = scoring.hromada_screening({"c1_grain_t_yr": 200000})
    assert res.value == 25.0
    assert len([c for c in res.components if c.missing]) == 8


def test_value_above_target_does_not_exceed_weight():
    res = scoring.hromada_screening({"c1_grain_t_yr": 10_000_000})
    assert res.value == 25.0


# ── збереження ───────────────────────────────────────────────────────────────

def test_save_stores_components(s):
    site = Site(site_type="biogaz_object", name="тест", est_capacity_kw_el=1500,
                dist_power_km=5, infra_score=50, est_feedstock_t_year=20000)
    s.add(site)
    s.commit()
    res = scoring.score_site(site, has_phone=True)
    scoring.save(s, site.id, res)
    s.commit()

    sc = s.exec(select(Score).where(Score.site_id == site.id)).one()
    comps = list(s.exec(select(ScoreComponent).where(ScoreComponent.score_id == sc.id)))
    assert sc.value == res.value and len(comps) == 6
    assert sc.priority in ("Високий", "Середній", "Низький")


def test_save_replaces_previous_score(s):
    site = Site(site_type="biogaz_object", name="тест", est_capacity_kw_el=100)
    s.add(site)
    s.commit()
    scoring.save(s, site.id, scoring.score_site(site))
    s.commit()
    site.est_capacity_kw_el = 3000
    scoring.save(s, site.id, scoring.score_site(site))
    s.commit()

    scores = list(s.exec(select(Score).where(Score.site_id == site.id)))
    comps = list(s.exec(select(ScoreComponent)))
    assert len(scores) == 1 and len(comps) == 6      # старі компоненти прибрані
    assert scores[0].value > 40


def test_site_type_picks_the_right_model(s):
    bio = Site(site_type="biogaz_object", name="б")
    hro = Site(site_type="hromada_lokatsiya", name="г")
    assert scoring.score_site(bio).model == "fat_score"
    assert scoring.score_site(hro).model == "hromada_screening"


# ── Ф9: модель балу залежить від типу обʼєкта ────────────────────────────────

def test_park_i_isnuyuchyi_elevator_ne_otrymuyut_balu():
    """Фальшивий бал гірший за відсутність: число виглядало б правдоподібно."""
    from app.models import Site
    from app.services import scoring

    for typ in ("industrialnyi_park", "elevator_isnuyuchyi"):
        site = Site(site_type=typ, name="х", est_capacity_kw_el=500,
                    dist_power_km=1, dist_gas_km=1, infra_score=80)
        assert scoring.score_site(site) is None, f"{typ} не має отримувати бал"


def test_typ_vyznachaye_model_balu():
    from app.models import Site
    from app.services import scoring

    assert scoring.score_site(Site(site_type="biogaz_object")).model == "fat_score"
    assert scoring.score_site(
        Site(site_type="hromada_lokatsiya")).model == "hromada_screening"


def test_save_many_perezapysuye_lyshe_svoyu_model(s):
    """На одній громаді дві моделі балу — пачка не має чіпати чужу."""
    from sqlmodel import select
    from app.models import Score, ScoreComponent, Site
    from app.services import scoring

    site = Site(site_type="hromada_lokatsiya", name="Громада")
    s.add(site)
    s.commit()

    scoring.save(s, site.id, scoring.hromada_screening({"c1_grain_t_yr": 50000}))
    s.commit()
    insha = scoring.Result("elevator_ranking", 70.0, [
        scoring.Component("dzherelo", "Рейтинг джерела", 70.0, 0.7, 1.0, "xlsx", False)])
    scoring.save_many(s, [(site.id, insha)])
    s.commit()

    modeli = {sc.model: sc.value for sc in
              s.exec(select(Score).where(Score.site_id == site.id))}
    assert set(modeli) == {"hromada_screening", "elevator_ranking"}
    assert modeli["elevator_ranking"] == 70.0

    # повторний прогін пачки не плодить дублів і не лишає осиротілих компонентів
    scoring.save_many(s, [(site.id, insha)])
    s.commit()
    bal = list(s.exec(select(Score).where(Score.site_id == site.id,
                                          Score.model == "elevator_ranking")))
    assert len(bal) == 1
    zhyvi = {x.id for x in s.exec(select(Score))}
    syroty = [c for c in s.exec(select(ScoreComponent)) if c.score_id not in zhyvi]
    assert not syroty, "компоненти видаленого балу лишились у базі"


def test_save_many_ne_zmishuye_modeli(s):
    from app.models import Site
    from app.services import scoring
    a = Site(site_type="hromada_lokatsiya", name="A")
    b = Site(site_type="hromada_lokatsiya", name="B")
    s.add(a), s.add(b)
    s.commit()
    with pytest.raises(ValueError):
        scoring.save_many(s, [(a.id, scoring.hromada_screening({})),
                              (b.id, scoring.Result("elevator_ranking", 1.0, []))])


def test_dubl_u_pachtsi_ne_daye_holoho_balu(s):
    """Дубль рядка в джерелі не має лишати бал без розкладу.

    Було: два Score на один сайт, компоненти чіплялись лише до одного —
    і залишався бал-«голе число», якого нема чим пояснити.
    """
    from app.models import Score, ScoreComponent, Site
    from app.services import scoring

    site = Site(site_type="hromada_lokatsiya", name="Дубляни")
    s.add(site)
    s.commit()

    res = scoring.elevator_ranking(70.0, "test.csv", {"grain_ha": 151830})
    scoring.save_many(s, [(site.id, res), (site.id, res)])   # той самий сайт двічі
    s.commit()

    bal = list(s.exec(select(Score).where(Score.site_id == site.id)))
    assert len(bal) == 1, "дубль у пачці створив два бали"
    komp = list(s.exec(select(ScoreComponent).where(
        ScoreComponent.score_id == bal[0].id)))
    assert komp, "бал лишився без розкладу"


def test_elevator_ranking_ne_vygaduye_vah(s):
    """Ваги 225-громадного скорингу невідомі — переносимо бал, а не вигадуємо розклад."""
    from app.services import scoring
    r = scoring.elevator_ranking(70.0, "elevator_candidates.csv",
                                 {"grain_ha": 151830, "throughput": 607318})
    assert r.model == "elevator_ranking" and r.value == 70.0
    vahy = [c.weight for c in r.components]
    assert sum(vahy) == 1.0, "сума ваг має бути 1.0 — рівно один оцінковий компонент"
    assert sum(1 for c in r.components if c.weight > 0) == 1
    dovidkovi = [c for c in r.components if c.weight == 0]
    assert dovidkovi and all(c.source for c in dovidkovi), \
        "довідкові складові мають казати, звідки взяті"
