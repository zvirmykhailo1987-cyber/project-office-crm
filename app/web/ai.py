# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""AI-автозаповнення: файл → чернетка → підтвердження людиною (ТЗ §6, модуль M11).

Ключове: ніщо не потрапляє в базу без натискання людини. Чернетка живе в `AiRun`,
у робочі таблиці переноситься лише після «Підтвердити».
"""
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlmodel import Session, select

from .. import audit, config
from ..db import get_session
from ..fts import fts_upsert
from ..models import (
    AI_KINDS,
    CAP_CRM,
    AiRun,
    Site,
    User,
    utcnow,
)
from ..normalize import norm_edrpou, norm_phone
from ..services import extract, llm, scoring
from .deps import current_user, redirect, render, require_cap, spivpratsya

router = APIRouter()

UPLOAD_DIR = config.DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_MB = 20

# Що приймаємо на витяг. Розширення береться з імені клієнта, але САМЕ імʼя на
# диск не потрапляє ніколи: раніше `UPLOAD_DIR / f"..._{file.filename}"` з
# імʼям `x\..\..\pwned.txt` писало файл поза теку завантажень.
DOZVOLENI_AI = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt", ".md", ".csv",
                ".xlsx", ".xlsm", ".docx"}


def _pokaz_imya(syre: str | None) -> str:
    """Оригінальне імʼя — лише як ДАНІ для показу (без шляху, обрізане)."""
    imya = (syre or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return imya[:200] or "файл"


def _shlyakh_zavantazhennya(syre: str | None) -> Path | None:
    """Згенероване імʼя (uuid + дозволене розширення) усередині UPLOAD_DIR.

    None — розширення поза білим списком. Вихід за межі теки неможливий за
    побудовою, але перевіряємо й розвʼязаний шлях: це дешевше за інцидент.
    """
    ext = Path(_pokaz_imya(syre)).suffix.lower()
    if ext not in DOZVOLENI_AI:
        return None
    koren = UPLOAD_DIR.resolve()
    path = (koren / f"{utcnow():%Y%m%d_%H%M%S}_{uuid.uuid4().hex}{ext}").resolve()
    if path.parent != koren:
        return None
    return path


@router.get("/ai")
def index(request: Request, user: User = Depends(current_user),
          s: Session = Depends(get_session)):
    runs = list(s.exec(select(AiRun).order_by(AiRun.created_at.desc()).limit(50)))
    hromady = list(s.exec(select(Site).where(Site.site_type == "hromada_lokatsiya")
                          .order_by(Site.name)))
    return render(request, "ai/index.html", user=user, runs=runs, kinds=AI_KINDS,
                  hromady=hromady, llm_on=llm.available(),
                  provider=config.LLM_PROVIDER)


@router.post("/ai/vytyah")
async def do_extract(request: Request, kind: str = Form("rahunok"),
                     site_id: str = Form(""), file: UploadFile = File(...),
                     user: User = Depends(require_cap(CAP_CRM)),
                     s: Session = Depends(get_session)):
    if kind not in AI_KINDS:
        return redirect("/ai")

    raw = await file.read()
    if len(raw) > MAX_MB * 1024 * 1024:
        run = AiRun(kind=kind, engine="none", source_file=_pokaz_imya(file.filename),
                    error=f"Файл більший за {MAX_MB} МБ", created_by=user.name)
        s.add(run)
        s.commit()
        return redirect(f"/ai/{run.id}")

    path = _shlyakh_zavantazhennya(file.filename)
    if path is None:
        run = AiRun(kind=kind, engine="none", source_file=_pokaz_imya(file.filename),
                    error="Тип файлу не підтримується", created_by=user.name)
        s.add(run)
        s.commit()
        return redirect(f"/ai/{run.id}")
    path.write_bytes(raw)

    src = extract.read_file(path)
    if src.empty:
        run = AiRun(kind=kind, engine="none", source_file=_pokaz_imya(file.filename),
                    source_chars=len(src.text),
                    error=src.note or "З файлу не вдалося дістати текст",
                    created_by=user.name)
        s.add(run)
        s.flush()
        audit.log(s, user.name, "ai.extract.fail", entity="airun", entity_id=run.id,
                  tier=audit.TIER_CHANGE, detail=run.error[:120])
        s.commit()
        return redirect(f"/ai/{run.id}")

    r = extract.run(kind, src.text)
    if site_id.isdigit() and kind == "anketa":
        r.data.setdefault("_site_id", int(site_id))

    run = AiRun(kind=kind, engine=r.engine, model=config.LLM_MODEL if r.engine == "llm" else "",
                source_file=_pokaz_imya(file.filename), source_chars=r.source_chars,
                output_json=json.dumps(r.data, ensure_ascii=False, indent=1),
                schema_valid=r.schema_valid, fallback_used=r.fallback_used,
                arithmetic_ok=(r.arithmetic.ok if r.arithmetic else None),
                arithmetic_note=(r.arithmetic.note if r.arithmetic else ""),
                fields_filled=r.fields_filled, fields_total=r.fields_total,
                duration_ms=r.duration_ms, error=r.error, created_by=user.name)
    s.add(run)
    s.flush()
    audit.log(s, user.name, "ai.extract", entity="airun", entity_id=run.id,
              tier=audit.TIER_CHANGE,
              detail=f"{kind}: {r.engine}, заповнено {run.fill_rate}%")
    s.commit()
    return redirect(f"/ai/{run.id}")


@router.get("/ai/{run_id}")
def run_card(run_id: int, request: Request, user: User = Depends(current_user),
             s: Session = Depends(get_session)):
    run = s.get(AiRun, run_id)
    if not run:
        return redirect("/ai")
    data = json.loads(run.output_json) if run.output_json else {}
    site = s.get(Site, data["_site_id"]) if data.get("_site_id") else None
    return render(request, "ai/run.html", user=user, run=run, data=data,
                  kinds=AI_KINDS, site=site, **spivpratsya(s, "airun", run_id))


@router.post("/ai/{run_id}/pidtverdyty")
def confirm(run_id: int, request: Request,
            user: User = Depends(require_cap(CAP_CRM)),
            s: Session = Depends(get_session)):
    """Переносить чернетку в робочі таблиці. Тільки після явного натискання людини."""
    run = s.get(AiRun, run_id)
    if not run or run.confirmed_at:
        return redirect(f"/ai/{run_id}")
    data = json.loads(run.output_json) if run.output_json else {}
    applied = ""

    if run.kind == "anketa" and data.get("_site_id"):
        site = s.get(Site, data["_site_id"])
        if site:
            changed = []
            for key in ("c1_grain_t_yr", "c2_underserved_ha", "c3_ind_park", "c4_rail",
                        "c5_road", "c6_power_mw", "c7_guarantee_ready",
                        "c8_ahc_ready", "c9_transparency"):
                v = data.get(key)
                if v is not None and getattr(site, key) != v:
                    setattr(site, key, v)
                    changed.append(key)
            if changed:
                site.updated_at = utcnow()
                s.add(site)
                s.flush()
                res = scoring.score_site(site)
                if res:
                    scoring.save(s, site.id, res)
            applied = f"громада «{site.name}»: {len(changed)} критеріїв"

    elif run.kind == "kontakty":
        from ..importers.common import Index, Stats, upsert, upsert_person
        idx, st = Index(s), Stats()
        for o in data.get("organizatsii") or []:
            if not (o.get("nazva") or o.get("edrpou")):
                continue
            cp = upsert(s, idx, {
                "name": (o.get("nazva") or "").strip(),
                "edrpou": norm_edrpou(o.get("edrpou")),
                "city": (o.get("misto") or "").strip(),
                "phone": norm_phone(o.get("telefon") or ""),
                "email": (o.get("email") or "").strip(),
                "source": f"ai:{run.id}", "data_status": "needs_check",
            }, f"ai:{run.id}", st)
            if o.get("kerivnyk"):
                upsert_person(s, cp, {"name": o["kerivnyk"], "role": "Керівник",
                                      "phone": norm_phone(o.get("telefon") or ""),
                                      "email": o.get("email") or "",
                                      "is_primary": True, "source": f"ai:{run.id}"})
            s.flush()
            fts_upsert(s, cp)
        applied = (f"довідник: нових {st.new}, дозаповнено {st.merged}, "
                   f"без змін {st.unchanged}")

    elif run.kind == "rahunok":
        # Рахунок нікуди не «застосовується» автоматично: це вхідний документ.
        # Підтвердження означає лише «дані звірено» — оплату робить людина (Tier3).
        applied = "рахунок звірено; оплату виконує людина поза системою"

    run.confirmed_by, run.confirmed_at = user.name, utcnow()
    run.applied_to = applied
    s.add(run)
    audit.log(s, user.name, "ai.confirm", entity="airun", entity_id=run_id,
              tier=audit.TIER_CHANGE, detail=applied[:120])
    s.commit()
    return redirect(f"/ai/{run_id}")
