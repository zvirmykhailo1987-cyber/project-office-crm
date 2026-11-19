"""Карти обох напрямів: подача даних і сторінки (ТЗ §4d, Ф9).

Чому колонковий формат, а не GeoJSON: обгортка `{"type":"Feature","geometry":…}`
коштує ~120 байтів структури на точку — на 21 тисячі це 2.5 МБ чистого боілерплейту.
Колонки прибирають повтор ключів; під gzip уся біогаз-база важить ~0.7 МБ, тому
вантажимо її ОДИН раз, а фільтри й ваги придатності рахуються на клієнті миттєво.
Серверна агрегація за зумом тут не підходить: користувач крутить повзунки ваг, і
кожен рух означав би запит.
"""
import hashlib

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlmodel import Session, select

from ..db import get_session
from ..models import (HRUBA_TOCHNIST, Cluster, Counterparty, Person, Score,
                      ScoreComponent, Site, User)
from ..services import scoring
from ..templating import plural
from .deps import current_user, render

router = APIRouter()

# Колонки, які їдуть на клієнт. Свідомо БЕЗ важких текстів (ACTIVITY_TEXT, ТЗ,
# перелік ДПСС) — вони потрібні лише у відкритій картці й тягнуться окремо.
# Розширення 25.08.2026 (ТЗ Богдана): категорія підприємства + поля для
# порогових фільтрів (голови/біометан/земля) і зведення громади. Додається в
# КІНЕЦЬ списку: biogaz.js адресує колонки за назвою через індекс POLYA, але
# збережені URL-стани й ETag живуть у людей у вкладках — порядок не ламаємо.
POLYA_BIOGAZ = ("id", "nazva", "lat", "lon", "tochnist", "oblast", "hromada",
                "kat", "kw", "feed", "d_gaz", "d_merezha", "d_doroha",
                "bal", "sektor", "fin", "status", "edrpou", "rozbizhnist",
                "kat_pidpr", "vrh", "svyni", "ptytsia", "zemlya_ha",
                "biometan_m3", "waste_type", "m", "kat_osnovna", "operatsiyi")

POLYA_ELEVATORY = ("id", "nazva", "lat", "lon", "tochnist", "oblast", "typ",
                   "bal", "zerno_ha", "propusk_t", "do_elevatora", "mistkist_t",
                   "d_merezha", "d_doroha", "metodyka")

KARTY = {
    "biogaz": {"tytul": "Карта біоенергетики", "polya": POLYA_BIOGAZ},
    "elevatory": {"tytul": "Карта елеваторів", "polya": POLYA_ELEVATORY},
}

# Стеля вибірки. Не «тихе обрізання»: якщо спрацює — meta скаже про це словами.
STELYA = 30000


def _pl(n: int) -> str:
    return plural(n, "точка", "точки", "точок")


def _bal_mapa(s: Session, site_ids: list[int], model: str) -> dict[int, float]:
    if not site_ids or not model:
        return {}
    return {sid: val for val, sid in s.exec(
        select(Score.value, Score.site_id).where(Score.site_id.in_(site_ids),
                                                 Score.model == model))}


# ── сторінки ─────────────────────────────────────────────────────────────────

@router.get("/karta")
def storinka_biogaz(request: Request, user: User = Depends(current_user)):
    return render(request, "karta/biogaz.html", user=user, karta="biogaz")


@router.get("/karta-elevatory")
def storinka_elevatory(request: Request, user: User = Depends(current_user)):
    return render(request, "karta/elevatory.html", user=user, karta="elevatory")


# ── дані ─────────────────────────────────────────────────────────────────────

@router.get("/api/karta/tochky")
def tochky(response: Response, request: Request, karta: str = "biogaz",
           oblast: str = "", user: User = Depends(current_user),
           s: Session = Depends(get_session)):
    if karta not in KARTY:
        karta = "biogaz"

    if karta == "biogaz":
        # Майданчики (ДПСС) — у ТІЙ САМІЙ вибірці: модель Богдана, ферма в
        # селі А і свинокомплекс у селі Б — окремі точки поруч із компаніями.
        typy = ["biogaz_object", "majdanchyk"]
        model = "fat_score"
    else:
        typy = ["hromada_lokatsiya", "elevator_isnuyuchyi", "industrialnyi_park"]
        model = "elevator_ranking"

    umovy = [Site.site_type.in_(typy)]
    if oblast:
        umovy.append(Site.oblast == oblast)

    vsoho = s.exec(select(func.count()).select_from(Site).where(*umovy)).one()
    z_koord = s.exec(select(func.count()).select_from(Site)
                     .where(*umovy, Site.lat.is_not(None))).one()

    # ETag: поки нічого не змінилось — браузер не тягне мегабайти вдруге.
    # Хеш, а не сирі значення: назва області кирилицею, а HTTP-заголовки
    # приймають лише latin-1 — інакше 500 на «?oblast=Тернопільська».
    ostannya = s.exec(select(func.max(Site.updated_at)).where(*umovy)).one()
    # Довжина POLYA — у підписі: інакше після додавання колонок браузер із
    # старим ETag отримує 304 і живе зі старою ФОРМОЮ рядків, а JS адресує
    # колонки, яких у кешованій відповіді немає.
    pidpys = f"{karta}|{oblast}|{vsoho}|{z_koord}|{ostannya}|{len(KARTY[karta]['polya'])}"
    etag = 'W/"' + hashlib.sha1(pidpys.encode("utf-8"), usedforsecurity=False).hexdigest()[:16] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    rows = list(s.exec(
        select(Site.id, Site.name, Site.lat, Site.lon, Site.geo_precision,
               Site.oblast, Site.hromada, Site.waste_category,
               Site.est_capacity_kw_el, Site.est_feedstock_t_year,
               Site.dist_gas_km, Site.dist_power_km, Site.dist_road_km,
               Site.sector, Site.fin_active, Site.member_status, Site.mismatch,
               Site.site_type, Site.c1_grain_t_yr, Site.c2_underserved_ha,
               Site.dist_elevator_km, Site.capacity_t, Site.source,
               Site.kat_pidpr, Site.vrh_head, Site.pigs_head, Site.poultry_head,
               Site.land_area_ha, Site.est_biomethane_m3_year, Site.waste_type,
               Site.kat_osnovna, Site.operatsiyi)
        .where(*umovy, Site.lat.is_not(None)).limit(STELYA)))

    bal = _bal_mapa(s, [r[0] for r in rows], model)
    edrpou = {}
    if karta == "biogaz" and rows:
        edrpou = {sid: e for sid, e in s.exec(
            select(Site.id, Counterparty.edrpou)
            .join(Counterparty, Counterparty.id == Site.counterparty_id)
            .where(Site.id.in_([r[0] for r in rows]), Counterparty.edrpou != ""))}

    ryadky = []
    hrubi = 0
    for r in rows:
        (sid, nazva, lat, lon, tochnist, obl, hrom, kat, kw, feed, d_gaz, d_mer,
         d_dor, sektor, fin, status, rozb, styp, c1, c2, d_elev, mistk, dzherelo,
         kat_p, vrh, svyni, ptytsia, zemlya, biometan, wt, kat_osn, operatsiyi) = r
        if tochnist in HRUBA_TOCHNIST:
            hrubi += 1
        if karta == "biogaz":
            ryadky.append([sid, nazva, round(lat, 5), round(lon, 5), tochnist or "",
                           obl or "", hrom or "", kat or "", kw, feed, d_gaz, d_mer,
                           d_dor, bal.get(sid), sektor or "",
                           1 if fin else (0 if fin is False else None),
                           status or "", edrpou.get(sid, ""), rozb or "",
                           kat_p or "", vrh, svyni, ptytsia, zemlya, biometan,
                           wt or "", 1 if styp == "majdanchyk" else 0,
                           kat_osn or "", operatsiyi or ""])
        else:
            ryadky.append([sid, nazva, round(lat, 5), round(lon, 5), tochnist or "",
                           obl or "", styp, bal.get(sid), c1, c2, d_elev, mistk,
                           d_mer, d_dor, dzherelo or ""])

    bez_koord = vsoho - z_koord
    obrizano = len(rows) >= STELYA
    chastyny = []
    if bez_koord:
        chastyny.append(f"{bez_koord} без координат")
    if hrubi:
        chastyny.append(f"{hrubi} лише з точністю до району чи області")
    if obrizano:
        chastyny.append(f"показано перші {STELYA} — звузьте фільтр")

    return JSONResponse(
        {
            "polya": KARTY[karta]["polya"],
            "ryadky": ryadky,
            "meta": {
                "pokazano": len(ryadky), "vsoho": vsoho, "bez_koordynat": bez_koord,
                "hrubi_koordynaty": hrubi, "obrizano": obrizano,
                "prychyna": " · ".join(chastyny),
                "pidsumok": f"{len(ryadky)} {_pl(len(ryadky))} з {vsoho}",
            },
        },
        headers={"ETag": etag,
                 "Cache-Control": "private, max-age=0, must-revalidate"})


@router.get("/api/karta/haby")
def haby(karta: str = "biogaz", user: User = Depends(current_user),
         s: Session = Depends(get_session)):
    """Хаби окремим шаром: у них своя геометрія й своя шкала (МВт, не бал)."""
    rows = list(s.exec(select(Cluster).where(Cluster.lat.is_not(None))))
    return {
        "polya": ("id", "kod", "typ", "mw", "donoriv", "oblast", "gaz_km",
                  "capex", "irr", "lat", "lon", "dzherelo", "yakir"),
        "ryadky": [[c.id, c.site_code or "", c.type_label or c.type, c.total_mw,
                    c.n_donors, c.oblast or "", c.gas_km, c.capex_eur, c.irr_pct,
                    round(c.lat, 5), round(c.lon, 5), c.source or "",
                    c.anchor_name or ""] for c in rows],
        "meta": {"pokazano": len(rows), "vsoho": len(rows)},
    }


@router.get("/api/karta/tochka/{site_id}")
def tochka(site_id: int, user: User = Depends(current_user),
           s: Session = Depends(get_session)):
    """Повна картка для бічної шухляди — тягнеться лише при відкритті."""
    site = s.get(Site, site_id)
    if not site:
        return {"error": "немає такого обʼєкта"}
    cp = s.get(Counterparty, site.counterparty_id) if site.counterparty_id else None
    kerivnyk = None
    if cp:
        kerivnyk = s.exec(
            select(Person).where(Person.counterparty_id == cp.id,
                                 Person.role.contains("ерівник"))).first()
    model = scoring.MODEL_ZA_TYPOM.get(site.site_type, "fat_score")
    sc = s.exec(select(Score).where(Score.site_id == site_id,
                                    Score.model == model)).first() if model else None
    comps = list(s.exec(select(ScoreComponent)
                        .where(ScoreComponent.score_id == sc.id)
                        .order_by(ScoreComponent.contribution.desc()))) if sc else []
    hab = s.get(Cluster, site.cluster_id) if site.cluster_id else None

    # Майданчики тієї ж компанії (модель «АГРО ХХХ» 1:N). Для майданчика цей
    # список показує «сусідів» по компанії — теж корисно.
    majdanchyky = []
    if site.counterparty_id:
        majdanchyky = [
            {"id": x.id, "nazva": x.name.split(" — ")[0][:60],
             "misce": x.city or x.raion or x.oblast, "kat": x.kat_pidpr}
            for x in s.exec(select(Site).where(
                Site.counterparty_id == site.counterparty_id,
                Site.site_type == "majdanchyk", Site.id != site.id))]

    return {
        "id": site.id, "nazva": site.name, "typ": site.type_label,
        "majdanchyky": majdanchyky,
        "url": f"/obyekty/{site.id}",
        "misce": " · ".join(x for x in (site.oblast, site.raion, site.hromada,
                                        site.city) if x),
        "tochnist": site.tochnist_label, "hruba": site.hruba_tochnist,
        # Категорія підприємства (ТЗ 25.08.2026) — зі СЛІДОМ виведення:
        # суперечка «чому це у свинарстві» вирішується читанням kat_dzherelo,
        # а не вірою в карту.
        "kat_pidpr": site.kat_pidpr, "kat_dzherelo": site.kat_dzherelo,
        # реальні тваринницькі операції (ДПСС/поголівʼя) — окрема позначка
        "operatsiyi": site.operatsiyi, "operatsiyi_dzherelo": site.operatsiyi_dzherelo,
        "syrovyna": site.waste_type, "kategoriya": site.waste_category,
        "sektor": site.sector, "stantsiya": site.recommended_station,
        "kw": site.est_capacity_kw_el, "feed_t": site.est_feedstock_t_year,
        "energiya_mwh": site.est_energy_mwh_year,
        "vrh": site.vrh_head, "ptytsya": site.poultry_head, "svyni": site.pigs_head,
        "zemlya_ha": site.land_area_ha,
        "d_gaz": site.dist_gas_km, "d_merezha": site.dist_power_km,
        "d_doroha": site.dist_road_km,
        "tz": {"plant": site.tz_plant, "capex": site.tz_capex,
               "revenue": site.tz_revenue, "payback": site.tz_payback,
               "irr": site.tz_irr},
        "mistkist_t": site.capacity_t, "do_elevatora": site.dist_elevator_km,
        "status": site.member_status,
        "dpss": site.dpss_facilities, "rozbizhnist": site.mismatch,
        "onovleno_z": site.updated_from,
        "dani_onovleno": site.data_updated_at.strftime("%d.%m.%Y")
        if site.data_updated_at else "",
        "hab": {"id": hab.id, "kod": hab.site_code, "mw": hab.total_mw} if hab else None,
        "kontakt": {"edrpou": cp.edrpou, "adresa": cp.address,
                    "telefon": cp.phone or cp.phone2,
                    "email": cp.email, "sayt": cp.website,
                    "kved": cp.kved, "usi_kved": cp.all_kved,
                    "status_org": cp.org_status,
                    "kerivnyk": kerivnyk.name if kerivnyk else "",
                    "url": f"/dovidnyk/{cp.id}"} if cp else None,
        "bal": {"znachennya": sc.value, "priorytet": sc.priority, "model": sc.model,
                "skladovi": [{"label": c.label, "vnesok": c.contribution,
                              "syre": c.raw, "nemaye": c.missing}
                             for c in comps]} if sc else None,
        "notatky": site.notes,
    }


@router.get("/api/karta/dovidnyky")
def dovidnyky(karta: str = "biogaz", user: User = Depends(current_user),
              s: Session = Depends(get_session)):
    """Значення фільтрів із лічильниками — щоб не тягнути всі точки лише заради
    списку областей (саме так робила попередня версія карти)."""
    typy = (["biogaz_object"] if karta == "biogaz"
            else ["hromada_lokatsiya", "elevator_isnuyuchyi", "industrialnyi_park"])
    umova = Site.site_type.in_(typy)

    def _grupa(kolonka):
        return [{"znachennya": v or "", "n": n} for v, n in s.exec(
            select(kolonka, func.count()).where(umova, kolonka.is_not(None))
            .group_by(kolonka).order_by(func.count().desc())) if v]

    return {
        "oblasti": _grupa(Site.oblast),
        "kategoriyi": _grupa(Site.waste_category),
        "sektory": _grupa(Site.sector),
        "typy": _grupa(Site.site_type),
        "tochnist": _grupa(Site.geo_precision),
    }
