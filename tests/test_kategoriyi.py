"""Категорія підприємства (ТЗ ГІС-карти Богдана, 25.08.2026).

Перевіряються БІЗНЕС-ПРАВИЛА, а не виклики: множинність (компанія буває і
рослинництвом, і ВРХ), «невідомо ≠ нуль» (порожній вхід не дає категорії),
похідність (перебудова після імпорту), і що майданчик ДПСС стає окремою
точкою своєї компанії — модель «АГРО ХХХ» з прикладу Богдана.
"""

from app.models import Counterparty, Site
from app.services.kategoriyi import vyvedy_kategoriyi, vyvedy_operatsiyi
from app.services import kat_rebuild


# ── класифікатор: КАТЕГОРІЯ (осн. КВЕД) vs ОПЕРАЦІЇ (ДПСС/поголівʼя) ──────────

def test_katehoriya_sugobo_za_osn_kved():
    """КЛЮЧ (рішення 27.08): категорія — ЛИШЕ осн. КВЕД. Осн. 01.11 + реальне
    поголівʼя ВРХ → категорія РОСЛИННИЦТВО (НЕ ВРХ), а ВРХ іде в ОПЕРАЦІЇ.
    Саме поголівʼя заганяло 236 рослинників у вкладку ВРХ."""
    kats, _, osnovna, operatsiyi, _ = vyvedy_kategoriyi(kved="01.11", vrh=500)
    assert kats == ["roslynnytstvo"]
    assert operatsiyi == ["vrh"]
    assert osnovna == "roslynnytstvo"


def test_vtorynnyy_kved_ne_klasyfikuye():
    """Осн. 01.11 + 01.46 «про запас» у all_kved → лише рослинництво, без свиней
    (ні в категорії, ні в операціях — все.kved не є реальною операцією)."""
    kats, _, _, operatsiyi, _ = vyvedy_kategoriyi(kved="01.11",
                                                  all_kved="01.11;01.46;10.51")
    assert kats == ["roslynnytstvo"] and operatsiyi == []


def test_dpss_ide_v_operatsiyi_ne_v_katehoriyu():
    """Свиноферма з ДПСС при осн. 01.11 → КАТЕГОРІЯ рослинництво, ОПЕРАЦІЯ свині.
    Слід операцій називає джерело."""
    kats, _, osnovna, operatsiyi, op_slid = vyvedy_kategoriyi(
        kved="01.11", dpss='ТОВ "Х" свиноферма')
    assert kats == ["roslynnytstvo"]
    assert operatsiyi == ["svyni"]
    assert "svyni←ДПСС" in op_slid
    assert osnovna == "roslynnytstvo"


def test_chysta_katehoriya_bez_operatsiy():
    """Осн. 01.46 без ДПСС/поголівʼя → свинарство, операцій немає."""
    kats, _, osnovna, operatsiyi, _ = vyvedy_kategoriyi(kved="01.46")
    assert kats == ["svyni"] and operatsiyi == [] and osnovna == "svyni"


def test_pervynna_ne_pidminyaetsya_operatsiyeyu():
    """Осн. КВЕД не с/г (68.20) + ДПСС ВРХ → категорія/первинна ПОРОЖНІ (немає
    с/г КВЕД), а ВРХ — в операціях. Реальна операція не «фарбує» під категорію."""
    kats, _, osnovna, operatsiyi, _ = vyvedy_kategoriyi(kved="68.20",
                                                        dpss="молочна ферма ВРХ")
    assert kats == [] and osnovna == ""
    assert operatsiyi == ["vrh"]


def test_zmishane_potrebuye_utochnennya():
    """01.50 — окрема категорія «змішане (потребує уточнення)» за таблицею ТЗ."""
    kats, _, osnovna, _, _ = vyvedy_kategoriyi(kved="01.50")
    assert kats == ["zmishane"] and osnovna == "zmishane"


def test_porozhniy_vhid_ne_daye_nichoho():
    """Г15: порожній вхід → ні категорії, ні операції."""
    kats, slid, osnovna, operatsiyi, _ = vyvedy_kategoriyi()
    assert kats == [] and operatsiyi == [] and slid == "" and osnovna == ""


def test_posluhy_dlya_silhospu_ne_roslynnytstvo():
    """01.61 (допоміжна діяльність) сам по собі — НЕ рослинництво."""
    assert vyvedy_kategoriyi(kved="01.61")[0] == []


def test_ptashnyk_operatsiya_ne_mistyt_pidryadka_ptah():
    """Бойовий кейс ДПСС: «пташник» не містить підрядка «птах» → операція ptytsia."""
    assert vyvedy_operatsiyi(dpss="ФГ пташник на 40 тис.")[0] == ["ptytsia"]


def test_poholivya_tekstom_tak_operatsiya():
    """Колонка «Свині»='так' → операція svyni (не категорія); «ні» → нічого."""
    assert vyvedy_operatsiyi(pigs="так")[0] == ["svyni"]
    assert vyvedy_operatsiyi(vrh="ні")[0] == []


# ── перебудова як похідне поле (закриває Г11) ────────────────────────────────

def test_perebudova_vyvodyt_katehoriyu_z_osn_kved(s):
    """kat_pidpr/kat_osnovna — похідні: після появи осн. КВЕД у контрагента
    перебудова ставить категорію, якої при створенні site ще не було."""
    cp = Counterparty(name="Агро", edrpou="11110000", kved="01.46")
    s.add(cp); s.commit()
    site = Site(site_type="biogaz_object", name="Агро", counterparty_id=cp.id,
                lat=49.0, lon=25.0)                       # site.kved порожній
    s.add(site); s.commit()
    assert site.kat_pidpr == ""          # при створенні ще порожньо

    kat_rebuild.perebuduvaty(s); s.commit()
    s.refresh(site)
    assert site.kat_pidpr == "svyni"     # осн. КВЕД контрагента 01.46
    assert site.kat_osnovna == "svyni"


def test_vtorynnyy_kved_ne_potraplyaye_v_perebudovu(s):
    """Похідне поле не тягне вторинний 01.46 у «свині»: осн. 01.11 лишається
    рослинництвом навіть маючи 01.46 у all_kved (скарга Богдана)."""
    cp = Counterparty(name="Рослинник", edrpou="44440000", kved="01.11",
                      all_kved="01.11;01.46")
    s.add(cp); s.commit()
    site = Site(site_type="biogaz_object", name="Рослинник",
                counterparty_id=cp.id, kved="01.11", lat=49.0, lon=25.0)
    s.add(site); s.commit()
    kat_rebuild.perebuduvaty(s); s.commit()
    s.refresh(site)
    assert site.kat_pidpr == "roslynnytstvo"
    assert "svyni" not in site.kat_pidpr


def test_perebudova_maidanchyka_dpss_v_operatsiyi(s):
    """Майданчик ДПСС не має КВЕД → КАТЕГОРІЯ порожня, а реальна потужність —
    в ОПЕРАЦІЯХ (рішення 27.08): майданчик живе в шарі «реальна операція»."""
    cp = Counterparty(name="Ферма", edrpou="22220000")
    s.add(cp); s.commit()
    mj = Site(site_type="majdanchyk", name="свиноферма — Ферма",
              counterparty_id=cp.id, dpss_facilities="свиноферма",
              kat_pidpr="", lat=49.0, lon=25.0)
    s.add(mj); s.commit()
    kat_rebuild.perebuduvaty(s); s.commit()
    s.refresh(mj)
    assert mj.kat_pidpr == ""          # немає КВЕД → немає категорії
    assert mj.operatsiyi == "svyni"    # реальна операція з ДПСС


def test_perebudova_idempotentna(s):
    """Другий прогін нічого не змінює — інакше «змінено N» брехало б у звіті."""
    cp = Counterparty(name="Агро2", edrpou="33330000", kved="01.41")
    s.add(cp); s.commit()
    s.add(Site(site_type="biogaz_object", name="Агро2", counterparty_id=cp.id,
               kved="01.41", lat=49.0, lon=25.0))
    s.commit()
    kat_rebuild.perebuduvaty(s); s.commit()
    r2 = kat_rebuild.perebuduvaty(s)
    assert r2["zmineno"] == 0
