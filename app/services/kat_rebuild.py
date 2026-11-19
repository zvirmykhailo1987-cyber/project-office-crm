"""Перебудова денормалізованих полів `Site.kat_pidpr` / `kat_osnovna` по базі.

ЧОМУ ОКРЕМИЙ ПРОХІД, А НЕ ПРИ UPSERT SITE. Категорія рахується з ДПСС/поголівʼя
й ОСНОВНОГО КВЕД, а КВЕД живе в Counterparty й наповнюється ОКРЕМИМ джерелом
(ЄДР-збагачення `src_kontakty`). На момент імпорту `src_biogaz` КВЕД ще може не
бути. Тому категорія — похідне поле, яке перебудовується ПІСЛЯ всіх джерел, у
тому самому місці, що й FTS (`run_import` кличе обидва).

ЦЕ ЗАКРИВАЄ Г11. Доки виведення жило лише в ручному `scripts/vyvedy_kategoriyi`,
кожен `run_import` лишав категорії застарілими, а карта показувала стан
попереднього тижня — мовчки. Тепер прохід — частина пайплайну.

МАЙДАНЧИКИ ТЕЖ. Майданчик ДПСС не має КВЕД, тож КАТЕГОРІЯ в нього порожня — але
він і Є реальна операція: `operatsiyi` виводиться з ДПСС-тексту (свиноферма/ВРХ/
птахо…). Так майданчики живуть у шарі «реальна операція», а не у вкладках-категоріях.
"""
from __future__ import annotations

import collections

from sqlmodel import Session, select

from ..models import Counterparty, utcnow
from ..models.site import Site
from .kategoriyi import KATEGORIYI, _kved_kategorija, vyvedy_kategoriyi

_TVARYNNI = {"vrh", "svyni", "ptytsia"}   # операційні види з site-КВЕД


def perebuduvaty(s: Session) -> dict:
    """Перерахувати kat_pidpr/kat_dzherelo/kat_osnovna для біогаз-обʼєктів і
    майданчиків ДПСС.

    Повертає {'vsoho', 'bez', 'zmineno', 'rozpodil': {slug: n}} для звіту.
    """
    cp = {c.id: c for c in s.exec(select(Counterparty))}
    rozpodil = collections.Counter()
    bez = zmineno = 0
    sites = list(s.exec(select(Site).where(
        Site.site_type.in_(["biogaz_object", "majdanchyk"]))))
    op_rozpodil = collections.Counter()
    for site in sites:
        k = cp.get(site.counterparty_id)
        # Майданчик — це ПОТУЖНІСТЬ, а не юрособа: власного КВЕД не має й НЕ
        # успадковує материнський (інакше свиноферма-майданчик компанії 01.46
        # отримала б категорію «свині», хоча має бути чистою операцією). Категорія
        # майданчика лишається порожньою, операція — з його ДПСС/поголівʼя.
        #
        # КАТЕГОРІЯ = ОСНОВНИЙ КВЕД КОМПАНІЇ (cp.kved з ЄДР) — саме його Богдан
        # бачить як «Основний КВЕД» у файлі. site.kved (з біогаз-бази) для 324
        # компаній (1%) розходиться з реєстрацією й давав ВРХ там, де ЄДР каже
        # рослинництво — тому пріоритет за реєстрацією ЄДР, а реальна тваринницька
        # активність такого сайту лишається в ОПЕРАЦІЯХ (через поголівʼя/ДПСС).
        kved_kat = "" if site.site_type == "majdanchyk" else (
            (k.kved if k else "") or site.kved)
        kats, slid, osnovna, operatsiyi, op_slid = vyvedy_kategoriyi(
            kved=kved_kat,
            all_kved=(k.all_kved if k else ""),
            activity_text=(k.activity_text if k else ""),
            dpss=site.dpss_facilities,
            vrh=site.vrh_head, pigs=site.pigs_head, poultry=site.poultry_head,
        )
        # site.kved (з біогаз-бази) вказує на тваринництво, а категорія за
        # реєстрацією ЄДР — інша → це реальна операція сайту, не категорія.
        if site.site_type != "majdanchyk":
            sk = _kved_kategorija(site.kved or "")
            if sk in _TVARYNNI and sk not in kats and sk not in operatsiyi:
                operatsiyi = operatsiyi + [sk]
                op_slid = (op_slid + "; " if op_slid else "") + f"{sk}←КВЕД сайту {site.kved}"
        # порядок операцій — за словником категорій (стабільно)
        operatsiyi = [s for s in KATEGORIYI if s in operatsiyi]
        nove = ",".join(kats)
        nove_op = ",".join(operatsiyi)
        if not kats:
            bez += 1
        for slag in kats:
            rozpodil[slag] += 1
        for slag in operatsiyi:
            op_rozpodil[slag] += 1
        if (site.kat_pidpr != nove or site.kat_dzherelo != slid
                or site.kat_osnovna != osnovna
                or site.operatsiyi != nove_op or site.operatsiyi_dzherelo != op_slid):
            site.kat_pidpr = nove
            site.kat_dzherelo = slid
            site.kat_osnovna = osnovna
            site.operatsiyi = nove_op
            site.operatsiyi_dzherelo = op_slid
            # бампаємо updated_at, щоб ETag карти збився після рекласифікації
            # (інакше карта віддавала б застарілий payload із кешу — Г17)
            site.updated_at = utcnow()
            s.add(site)
            zmineno += 1
    return {"vsoho": len(sites), "bez": bez, "zmineno": zmineno,
            "rozpodil": {KATEGORIYI[k]: n for k, n in rozpodil.items()},
            "operatsiyi": {KATEGORIYI[k]: n for k, n in op_rozpodil.items()}}
