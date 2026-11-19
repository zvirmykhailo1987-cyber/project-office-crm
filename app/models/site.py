"""Обʼєкти обох напрямів + кластери + пояснюваний бал (ТЗ, Додаток А §2–4.1).

Один `site` двох типів:
  `biogaz_object`    — точка з сировиною й ТЕО (з `biogaz_all.csv`, 45 колонок);
  `hromada_lokatsiya` — громада-локація під елеватор (критерії C1–C9).

Бал ЗАВЖДИ зберігається розкладеним на компоненти (`Score` + `ScoreComponent`).
Причина — `biogaz_robocha/АНАЛІЗ-ТЕХНОЛОГІЙ.md` розд. A.3: без компонентів неможливо
відповісти «чому ця точка вища за ту» й неможливо пояснити бал інвестору.
"""
from datetime import datetime

from sqlmodel import Field, SQLModel

from .base import BaseRow, utcnow

SITE_TYPES = {
    "biogaz_object": "Біогаз-обʼєкт",
    # Виробничий майданчик компанії (ферма в
    # селі А і свинокомплекс у селі Б — окремі точки, привʼязані до однієї
    # юрособи через counterparty_id). Джерело — реєстр ДПСС.
    "majdanchyk": "Виробничий майданчик",
    "hromada_lokatsiya": "Громада-локація",
    "elevator_isnuyuchyi": "Наявний елеватор",
    "industrialnyi_park": "Індустріальний парк",
}

# Точність координати. Визначає, чи можна взагалі ставити точку на місцевість:
# `oblast` означає «десь в області», і 4 857 таких обʼєктів сидять у 5 точках —
# показувати їх як звичайні маркери означає малювати скупчення, якого не існує.
GEO_PRECISION_LABELS = {
    "settlement": "населений пункт",
    "hromada": "громада",
    "raion": "район",
    "oblast": "лише область",
    "": "невідомо",
}
# Точності, за яких координата НЕ придатна для відстаней, теплокарти й зон збору
HRUBA_TOCHNIST = {"oblast", "raion", ""}

# статус опрацювання точки в межах хабу — з biogaz_robocha/crm/models.py:26
MEMBER_STATUS = ["Новий", "Дзвонили", "Не додзвонились", "Зацікавлений", "Відмова", "Діюча"]

# Стадії РОЗВИТКУ хабу (Cluster.stage). За ТЗ: «після підтвердження
# даних підприємство переводиться до попередньої оцінки, Pre-FS або
# девелопменту». Це девелоперська воронка хабу, окрема від обдзвону обʼєкта
# (MEMBER_STATUS) — раніше поле stage було вільним рядком без канону, і його
# не можна було ні фільтрувати, ні рахувати конверсію.
CLUSTER_STAGES = ["Новий", "Попередня оцінка", "Pre-FS", "Девелопмент", "Пауза"]

CLUSTER_TYPE_LABELS = {
    "CLUSTER_CHP": "Когенерація",
    "CLUSTER_POULTRY": "Пташиний послід",
    "CLUSTER_BIOMETHANE": "Біометан",
    "CLUSTER_MILK": "Молочні ферми",
    "CLUSTER_STATION": "Станція",
}


class Site(BaseRow, table=True):
    site_type: str = Field(default="biogaz_object", index=True)
    program_id: int | None = Field(default=None, foreign_key="program.id", index=True)
    counterparty_id: int | None = Field(default=None, foreign_key="counterparty.id",
                                           index=True)
    name: str = Field(default="", index=True)
    oblast: str = Field(default="", index=True)
    raion: str = ""
    hromada: str = Field(default="", index=True)
    city: str = ""
    lat: float | None = None
    lon: float | None = None
    geo_precision: str = ""

    # ── біогаз ────────────────────────────────────────────────────────────────
    waste_category: str = ""
    waste_type: str = ""
    kved: str = ""
    segment: str = ""
    tier: str = ""
    recommended_station: str = ""
    est_feedstock_t_year: float | None = None
    est_biogas_m3_year: float | None = None
    est_biomethane_m3_year: float | None = None
    est_energy_mwh_year: float | None = None
    est_capacity_kw_el: float | None = None
    vrh_head: int | None = None
    poultry_head: int | None = None
    pigs_head: int | None = None
    dist_road_km: float | None = None
    dist_power_km: float | None = None
    dist_gas_km: float | None = None
    infra_score: float | None = None
    tz_plant: str = ""
    tz_capex: float | None = None
    tz_revenue: float | None = None
    tz_opex: float | None = None
    tz_payback: float | None = None
    tz_irr: float | None = None
    cluster_id: int | None = Field(default=None, foreign_key="cluster.id", index=True)
    member_status: str = Field(default="Новий", index=True)
    is_anchor: bool = False

    # ── громада-локація (елеватор) ───────────────────────────────────────────
    center_guess: str = ""
    match_conf: str = ""
    dist_continental_km: float | None = None
    c1_grain_t_yr: float | None = None
    c2_underserved_ha: float | None = None
    c3_ind_park: float | None = None
    c4_rail: float | None = None
    c5_road: float | None = None
    c6_power_mw: float | None = None
    c7_guarantee_ready: float | None = None
    c8_ahc_ready: float | None = None
    c9_transparency: float | None = None
    rada_decision_status: str = ""
    farmer_volume_committed_t: float | None = None
    gap_funding_secured_usd: float | None = None
    guarantee_amount_usd: float | None = None      # Tier3 на зміну
    passport_md: str = ""

    # ── елеватор / індустріальний парк ───────────────────────────────────────
    capacity_t: float | None = None            # місткість зберігання, тонн
    dist_elevator_km: float | None = None      # до найближчого наявного елеватора

    # ── оновлення 05.08.2026: сліди походження й розбіжностей ────────────────
    sector: str = Field(default="", index=True)   # «≥2МВт», «Потенційні (потужність н/д)»
    dpss_facilities: str = ""                     # потужності з держреєстру ДПСС
    mismatch: str = ""                            # у чому джерела не збігаються
    updated_from: str = ""                        # чим саме збагачено запис
    # ЧОМУ окремо від BaseRow.updated_at: те — «коли МИ змінили рядок» (аудит),
    # це — «коли оновилось ДЖЕРЕЛО». Злиття знищує можливість їх розрізнити.
    data_updated_at: datetime | None = None
    land_area_ha: float | None = None
    fin_active: bool | None = None             # копія з контрагента заради фільтра

    # ── категорія підприємства (ТЗ ГІС-карти, 25.08.2026) ────────────────────
    # Кома-список слагів із `services/kategoriyi.py` (vrh, svyni, ptytsia,
    # roslynnytstvo, tsukor…). Множинна — господарство буває і рослинництвом,
    # і ВРХ одночасно. `kat_dzherelo` — слід виведення для картки.
    # `kat_osnovna` — ПЕРВИННА категорія (один слаг за осн. КВЕД): колір маркера й сортування — за нею, а членство
    # у фільтрах — за повним `kat_pidpr` (реальні операції з ДПСС теж світяться).
    kat_pidpr: str = Field(default="", index=True)
    kat_dzherelo: str = ""
    kat_osnovna: str = Field(default="", index=True)
    # реальні тваринницькі операції (ДПСС/поголівʼя) — ОКРЕМА позначка «є реальна
    # ферма», НЕ категорія: компанія-рослинник із
    # поголівʼям лишається рослинництвом у вкладці, а тут має операцію vrh/svyni…
    operatsiyi: str = Field(default="", index=True)
    operatsiyi_dzherelo: str = ""

    source: str = Field(default="", index=True)
    notes: str = ""

    @property
    def type_label(self) -> str:
        return SITE_TYPES.get(self.site_type, self.site_type)

    @property
    def tochnist_label(self) -> str:
        return GEO_PRECISION_LABELS.get(self.geo_precision, self.geo_precision)

    @property
    def hruba_tochnist(self) -> bool:
        """Координата не годиться для відстаней — лише для «десь тут»."""
        return self.geo_precision in HRUBA_TOCHNIST


class Cluster(BaseRow, table=True):
    """Хаб: ≥2 МВт у радіусі 20 км. Усе, крім CRM-полів, ОБЧИСЛЮЄТЬСЯ кластеризацією."""
    site_code: str = Field(default="", index=True)     # SITE_ID з clusters.csv
    type: str = Field(default="", index=True)
    type_label: str = ""
    oblast: str = Field(default="", index=True)
    total_mw: float | None = None
    total_kw: float | None = None
    n_donors: int = 0
    radius_km: float = 20
    waste_mix: str = ""
    gas_km: float | None = None
    energy_mwh: float | None = None
    feedstock_t: float | None = None
    anchor_name: str = ""
    anchor_edrpou: str = ""
    capex_eur: float | None = None
    revenue_eur_y: float | None = None
    payback_y: float | None = None
    irr_pct: float | None = None
    lat: float | None = None
    lon: float | None = None
    stage: str = "Новий"
    owner: str = ""
    notes: str = ""
    # Яка кластеризація дала цей хаб. Два набори живуть поруч свідомо: стара
    # версія має розкладений СКЛАД (ClusterMember), нова — свіже ТЕО, але складу
    # джерело не дає. Зливати їх означало б або втратити склад, або видати старі
    # числа за нові — тому показуємо обидва з видимою позначкою.
    source: str = Field(default="", index=True)


class ClusterMember(SQLModel, table=True):
    """Поле MEMBERS з CSV розкладене в таблицю — інакше по ньому не побудувати запит."""
    id: int | None = Field(default=None, primary_key=True)
    cluster_id: int = Field(foreign_key="cluster.id", index=True)
    site_id: int | None = Field(default=None, foreign_key="site.id", index=True)
    edrpou: str = Field(default="", index=True)


class Score(BaseRow, table=True):
    site_id: int = Field(foreign_key="site.id", index=True)
    model: str = Field(default="fat_score", index=True)   # fat_score | hromada_screening
    value: float = 0
    formula_version: str = "1"
    computed_at: datetime = Field(default_factory=utcnow)

    @property
    def priority(self) -> str:
        """Обчислюється, НЕ редагується (biogaz_robocha/crm/seed.py:41)."""
        if self.value >= 65:
            return "Високий"
        return "Середній" if self.value >= 45 else "Низький"


class ScoreComponent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    score_id: int = Field(foreign_key="score.id", index=True)
    key: str = ""
    label: str = ""
    raw: float | None = None          # сире значення
    normalized: float = 0                # 0–1
    weight: float = 0
    contribution: float = 0              # внесок у підсумок, у балах
    source: str = ""                     # звідки взято сире значення
    missing: bool = False                # даних не було — використано дефолт
