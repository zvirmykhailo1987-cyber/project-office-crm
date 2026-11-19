"""Моделі скорингу з ОБОВʼЯЗКОВИМ розкладом на компоненти (ТЗ, Додаток А §4.1).

Дві моделі:
  `fat_score`         — біогаз-точка, формула з `biogaz_robocha/scripts/extract_biogaz.py:102`;
  `hromada_screening` — громада під елеватор, ваги C1–C9 з
                        `elevator_robocha/references/community_screening_criteria.csv`.

Тверде правило: функція повертає і підсумок, І компоненти. Зберігати лише число
заборонено — тоді неможливо пояснити бал ні собі, ні інвестору.
"""
from dataclasses import dataclass, field

from sqlmodel import Session, delete, select

from ..models import Score, ScoreComponent, Site

FORMULA_VERSION = "1"


@dataclass
class Component:
    key: str
    label: str
    raw: float | None
    normalized: float
    weight: float
    source: str = ""
    missing: bool = False

    @property
    def contribution(self) -> float:
        return round(self.normalized * self.weight * 100, 2)


@dataclass
class Result:
    model: str
    value: float
    components: list[Component] = field(default_factory=list)

    @property
    def priority(self) -> str:
        if self.value >= 65:
            return "Високий"
        return "Середній" if self.value >= 45 else "Низький"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ── FAT_SCORE ────────────────────────────────────────────────────────────────
# Ваги дослівно з extract_biogaz.py:102. Змінювати лише через Tier3 (ТЗ §9).
FAT_WEIGHTS = {
    "pow": (0.42, "Потужність", "min(кВт / 3000, 1)"),
    "grid": (0.18, "Близькість до е-мережі", "max(0, 1 − км / 30)"),
    "gas": (0.08, "Близькість до газу", "max(0, 1 − км / 30)"),
    "suit": (0.15, "Базова придатність", "infra_score / 100"),
    "contact": (0.10, "Наявність контакту", "телефон=1 · email=0.5 · немає=0"),
    "feed": (0.07, "Обсяг сировини", "min(т/рік / 50 000, 1)"),
}


def fat_score(*, kw=None, d_power=None, d_gas=None, suit=None,
              has_phone=False, has_email=False, feed_t=None) -> Result:
    """Композитний бал 0–100. Відсутні відстані трактуються як 30 км (як у джерелі)."""
    comps = [
        Component("pow", FAT_WEIGHTS["pow"][1], kw,
                  _clamp01((kw or 0) / 3000.0), 0.42, FAT_WEIGHTS["pow"][2], kw is None),
        Component("grid", FAT_WEIGHTS["grid"][1], d_power,
                  _clamp01(1.0 - (d_power if d_power is not None else 30) / 30.0),
                  0.18, FAT_WEIGHTS["grid"][2], d_power is None),
        Component("gas", FAT_WEIGHTS["gas"][1], d_gas,
                  _clamp01(1.0 - (d_gas if d_gas is not None else 30) / 30.0),
                  0.08, FAT_WEIGHTS["gas"][2], d_gas is None),
        Component("suit", FAT_WEIGHTS["suit"][1], suit,
                  _clamp01((suit or 0) / 100.0), 0.15, FAT_WEIGHTS["suit"][2], suit is None),
        Component("contact", FAT_WEIGHTS["contact"][1],
                  1 if has_phone else (0.5 if has_email else 0),
                  1.0 if has_phone else (0.5 if has_email else 0.0),
                  0.10, FAT_WEIGHTS["contact"][2], not (has_phone or has_email)),
        Component("feed", FAT_WEIGHTS["feed"][1], feed_t,
                  _clamp01((feed_t or 0) / 50000.0), 0.07, FAT_WEIGHTS["feed"][2],
                  feed_t is None),
    ]
    total = round(sum(c.normalized * c.weight for c in comps) * 100, 1)
    return Result("fat_score", total, comps)


# ── Скринінг громад C1–C9 ────────────────────────────────────────────────────
# Ваги з community_screening_criteria.csv (сума = 100).
HROMADA_CRITERIA = [
    ("c1_grain_t_yr", "C1 Обсяги зерна від фермерів", 25, 100000.0,
     "100 000–200 000 т/рік"),
    ("c2_underserved_ha", "C2 Недообслуговувані землі", 20, 13000.0, "~13–16 тис. га"),
    ("c3_ind_park", "C3 Індустріальний парк", 15, 1.0, "4 га в наявному або 12 га новий"),
    ("c4_rail", "C4 Залізничне сполучення", 10, 1.0, "доступ до колії"),
    ("c5_road", "C5 Автомобільні дороги", 5, 1.0, "якісний підʼїзд"),
    ("c6_power_mw", "C6 Електропостачання", 10, 1.0, "вільна потужність 1–2 МВт"),
    ("c7_guarantee_ready", "C7 Готовність гарантувати борг EIFO", 10, 1.0, "$3–5 млн"),
    ("c8_ahc_ready", "C8 Комунальне підприємство (AHC)", 3, 1.0, "готовність створити"),
    ("c9_transparency", "C9 Прозорість і перевірки", 2, 1.0, "згода на аудит"),
]


def hromada_screening(values: dict) -> Result:
    """Бал громади 0–100. `None` = «немає даних» → компонент з missing=True і внеском 0."""
    comps = []
    for key, label, weight, target, note in HROMADA_CRITERIA:
        raw = values.get(key)
        norm = 0.0 if raw is None else _clamp01(float(raw) / target)
        comps.append(Component(key, label, raw, norm, weight / 100.0, note, raw is None))
    total = round(sum(c.normalized * c.weight for c in comps) * 100, 1)
    return Result("hromada_screening", total, comps)


def elevator_ranking(score: float, dzherelo: str,
                     syri: dict | None = None) -> Result:
    """Рейтинг громади-кандидата під елеватор — ПЕРЕНОСИМО готовий бал джерела.

    ЧОМУ не перераховуємо: ваги 225-громадного скорингу нам невідомі. Він рахує
    зернові землі в радіусі 35 км за КВЕД (Дубляни — 151 830 га), а критерії
    C1–C9 з ваг EIFO цілять у 13–16 тис. га. Це різні методики з різними
    шкалами. Вигадати ваги «щоб виглядало як розклад» означало б підмінити
    чуже вимірювання власним припущенням — тому компонент рівно один і він
    чесно каже, звідки взято число.

    Порівнювати цей бал із `hromada_screening` НЕ МОЖНА — на картці й на карті
    вони показуються окремо, з підписом моделі.
    """
    comps = [Component("dzherelo", "Рейтинг джерела", score, _clamp01(score / 100),
                       1.0, dzherelo, False)]
    for klyuch, pidpys in (("grain_ha", "Зернові землі, га"),
                           ("throughput", "Пропускна здатність, т/рік"),
                           ("dist_elevator", "До найближчого елеватора, км")):
        znach = (syri or {}).get(klyuch)
        # Довідкові складові: показуємо СИРЕ значення з нульовою вагою — вони
        # пояснюють бал, але не додають до нього нічого власного
        comps.append(Component(klyuch, pidpys, znach, 0.0, 0.0, dzherelo,
                               znach is None))
    return Result("elevator_ranking", round(float(score), 1), comps)


# Яка модель балу застосовна до якого типу обʼєкта. `None` означає «для цього типу
# балу НЕМАЄ» — і це чесніше, ніж порахувати FAT_SCORE індустріальному парку: число
# вийшло б правдоподібним, тому помилку ніхто б не помітив.
MODEL_ZA_TYPOM: dict[str, str | None] = {
    "biogaz_object": "fat_score",
    "hromada_lokatsiya": "hromada_screening",
    "elevator_isnuyuchyi": None,      # діючий обʼєкт, а не кандидат — оцінювати нічого
    "industrialnyi_park": None,       # майданчик-довідка, не інвестиційна ціль
}


def score_site(site: Site, *, has_phone=False, has_email=False) -> Result | None:
    """Бал за типом обʼєкта. `None` — коли модель для типу не передбачена.

    ЧОМУ НЕМАЄ ФОЛБЕКА. Раніше невідомий тип мовчки отримував `fat_score`:
    формулу біогаз-точки з вагами під потужність і сировину. Обʼєкт нового
    напряму — скажімо, майданчик під HPL — отримав би правдоподібне число,
    порахуване не за тим. Найгірший вид помилки: така, що виглядає як результат.
    Тепер невідомий тип чесно не має балу, і це видно на картці.
    """
    model = MODEL_ZA_TYPOM.get(site.site_type)
    if model is None:
        return None
    if model == "hromada_screening":
        return hromada_screening({k: getattr(site, k) for k, *_ in HROMADA_CRITERIA})
    return fat_score(kw=site.est_capacity_kw_el, d_power=site.dist_power_km,
                     d_gas=site.dist_gas_km, suit=site.infra_score,
                     has_phone=has_phone, has_email=has_email,
                     feed_t=site.est_feedstock_t_year)


def bez_danykh(res: Result | None) -> bool:
    """Жодної складової з даними — це «немає даних», а не бал 0.0.

    Нуль читався б як «перевірили — громада ні на що не годиться», хоча
    насправді її ще ні про що не питали.
    """
    return res is None or all(c.missing for c in res.components)


def prybraty(s: Session, site_id: int, model: str) -> int:
    """Прибрати бал моделі з сайту (коли даних для нього не лишилось)."""
    old = list(s.exec(select(Score).where(Score.site_id == site_id, Score.model == model)))
    for o in old:
        s.exec(delete(ScoreComponent).where(ScoreComponent.score_id == o.id))
        s.delete(o)
    return len(old)


def save(s: Session, site_id: int, res: Result) -> Score:
    """Перезаписує бал сайту разом з компонентами (стара версія не накопичується)."""
    old = list(s.exec(select(Score).where(Score.site_id == site_id,
                                          Score.model == res.model)))
    for o in old:
        s.exec(delete(ScoreComponent).where(ScoreComponent.score_id == o.id))
        s.delete(o)
    sc = Score(site_id=site_id, model=res.model, value=res.value,
               formula_version=FORMULA_VERSION, created_by="scoring")
    s.add(sc)
    s.flush()
    for c in res.components:
        s.add(ScoreComponent(score_id=sc.id, key=c.key, label=c.label, raw=c.raw,
                             normalized=round(c.normalized, 4), weight=c.weight,
                             contribution=c.contribution, source=c.source,
                             missing=c.missing))
    return sc


def save_many(s: Session, rezultaty: list[tuple[int, Result]]) -> int:
    """Те саме, що `save()`, але пачкою.

    ЧОМУ окремо: `save()` на кожен сайт робить SELECT + DELETE + INSERT + ~7 вставок
    компонентів. На 21 тисячі обʼєктів це ~190 тис. окремих операцій — десятки хвилин
    і транзакція, яку страшно обірвати. Тут — три запити на всю пачку.

    Пачка має бути однорідною за моделлю (усі результати одного `res.model`) —
    інакше DELETE зачепив би чужу модель балу на тому самому сайті.
    """
    if not rezultaty:
        return 0
    modeli = {r.model for _, r in rezultaty}
    if len(modeli) > 1:
        raise ValueError(f"пачка має бути однією моделлю, а не {sorted(modeli)}")
    model = modeli.pop()

    # Один сайт — один бал моделі. У джерелах трапляються дублі рядків
    # (у скорингу громад «Dublyany» двічі), і без цього на сайт лягало ДВА
    # Score, а компоненти чіплялись лише до одного — бал ставав «голим числом»,
    # яке нема чим пояснити. Лишаємо останній результат.
    za_saytom: dict[int, Result] = {}
    for sid, res in rezultaty:
        za_saytom[sid] = res
    rezultaty = list(za_saytom.items())
    ids = list(za_saytom)

    stari = list(s.exec(select(Score.id).where(Score.site_id.in_(ids),
                                               Score.model == model)))
    if stari:
        s.exec(delete(ScoreComponent).where(ScoreComponent.score_id.in_(stari)))
        s.exec(delete(Score).where(Score.id.in_(stari)))

    for sid, res in rezultaty:
        s.add(Score(site_id=sid, model=res.model, value=res.value,
                    formula_version=FORMULA_VERSION, created_by="scoring"))
    s.flush()

    # id новостворених балів — одним запитом, без звернення по кожному окремо
    novi = {sid: scid for scid, sid in s.exec(
        select(Score.id, Score.site_id).where(Score.site_id.in_(ids),
                                              Score.model == model))}
    komponenty = [
        {"score_id": novi[sid], "key": c.key, "label": c.label, "raw": c.raw,
         "normalized": round(c.normalized, 4), "weight": c.weight,
         "contribution": c.contribution, "source": c.source, "missing": c.missing}
        for sid, res in rezultaty for c in res.components if sid in novi
    ]
    if komponenty:
        s.bulk_insert_mappings(ScoreComponent, komponenty)
    return len(rezultaty)
