# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Project Office CRM (CRM проєктного офісу) — точка збірки застосунку."""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import authkit, config, i18n
from sqlmodel import Session

from .db import get_session, init_db
from .security import KIT
from .web import (ai, auth, chat, collab, dashboard, dokumenty, dovidnyk, karta,
                  napryamy, obyekty, palitra, voronky, zavdannya)


def _baza_zhyva() -> None:
    """У проді не стартуємо на ПОРОЖНІЙ базі — це майже завжди не той том.

    Найтихіша аварія контейнерного деплою: том `/data` не примонтувався (описка
    в шляху, том створився після старту, перейменований застосунок). SQLite
    тоді спокійно створює нову базу у шарі контейнера, застосунок піднімається,
    віддає сторінки — і кожен запис іде в те, що зникне при наступному
    оновленні. Зовні це виглядає як «система працює».

    Тому в проді порожній довідник = падіння з поясненням. Тут база їде на
    сервер уже наповненою, тож нуль тут не буває
    законним станом. Локально (`dev`) перевірки немає: там порожня база — це
    нормальний перший запуск.
    """
    if not config.IS_PROD:
        return
    from sqlalchemy import text
    from sqlmodel import Session

    from .db import engine
    with Session(engine) as s:
        n = s.exec(text("select count(*) from counterparty")).one()[0]
    if not n:
        raise RuntimeError(
            f"Довідник порожній, а OFIS_ENV=production. Майже напевно не "
            f"змонтовано том із базою: DB_PATH={config.DB_PATH}. "
            f"Перевірте, що том змонтовано в {config.DATA_DIR} ДО старту, "
            f"і що базу перенесено. Стартувати на порожній базі не буду — "
            f"інакше робота писалась би в контейнер і зникла при оновленні."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.check_prod_ready()
    init_db()
    _baza_zhyva()
    yield


# OpenAPI/Swagger only in local development — never in demo or production.
_DOCS = config.ENV == "dev"
app = FastAPI(title="Project Office CRM", lifespan=lifespan,
              dependencies=[Depends(authkit.csrf_protect)],
              docs_url="/docs" if _DOCS else None, redoc_url=None,
              openapi_url="/openapi.json" if _DOCS else None)


@app.middleware("http")
async def _mova(request: Request, call_next):
    """Interface language from the `lang` cookie (default English)."""
    i18n.set_lang(i18n.pick(request.cookies.get(i18n.COOKIE)))
    return await call_next(request)


authkit.install(app, KIT)
# Карта віддає ~6 МБ JSON на 21 тис. точок — під gzip це ~0.7 МБ.
# Без стиснення перше відкриття карти було б неприйнятно довгим.
app.add_middleware(GZipMiddleware, minimum_size=1024)
class Statyka(StaticFiles):
    """Статика, яку браузер ЗАВЖДИ перевіряє перед тим, як узяти з кешу.

    Версійний штамп `?v=` у посиланнях не рятує ES-модулі: `import './yadro.js'`
    усередині `biogaz.js` резолвиться відносно базового URL і запит модуля НЕ
    успадковує. Тобто оновлений `biogaz.js` приїжджає, а `yadro.js` при ньому
    лишається старий — рівно та комбінація, за якої сторінка «майже працює», і
    причину шукають годинами.

    `no-cache` — це не «не кешуй», а «кешуй, але спитай перед використанням»:
    у відповідь іде 304 без тіла. Для внутрішнього інструмента на десяток
    людей ця ціна нульова поруч із ціною одного такого розслідування.

    `vendor/` живе за іншим правилом: бібліотеки міняються лише коли ми їх
    свідомо оновлюємо, а важать більше за решту разом узяту.
    """

    def file_response(self, *args, **kwargs):
        vidpovid = super().file_response(*args, **kwargs)
        shlyah = str(args[0] if args else kwargs.get("full_path", ""))
        if "vendor" in shlyah.replace("\\", "/").split("/"):
            vidpovid.headers["Cache-Control"] = "public, max-age=604800"
        else:
            vidpovid.headers["Cache-Control"] = "no-cache"
        return vidpovid


app.mount("/static", Statyka(directory=str(config.BASE_DIR / "static")), name="static")
# Статика другої шкіри — окремим коренем, тим самим класом (він уже правильно
# тримає `no-cache` для своїх файлів і тижневий кеш для `vendor/`). Монтується
# лише якщо тека є: доки шкіри немає, продукт мусить працювати як раніше.
app.mount("/static_v2", Statyka(directory=str(config.BASE_DIR / "static_v2")), name="static_v2")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(dovidnyk.router)
app.include_router(voronky.router)
app.include_router(obyekty.router)
app.include_router(dokumenty.router)
app.include_router(zavdannya.router)
app.include_router(ai.router)
app.include_router(collab.router)
app.include_router(chat.router)
app.include_router(karta.router)
app.include_router(palitra.router)
app.include_router(napryamy.router)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    """307 from current_user → login redirect; everything else → branded error page."""
    if exc.status_code == 307:
        # 303, never 307: a 307 would re-send an expired form (or a whole upload) to /login
        return RedirectResponse((exc.headers or {}).get("Location", "/login"), status_code=303)
    return _error_page(request, exc.status_code, str(exc.detail or ""))


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    return _error_page(request, 422, i18n.translate("Некоректні дані форми або адреси."))


@app.exception_handler(Exception)
async def _server_error(request: Request, exc: Exception):
    import logging
    logging.getLogger("uvicorn.error").exception("unhandled error on %s", request.url.path)
    return _error_page(request, 500, "")


def _error_page(request: Request, code: int, detail: str):
    if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"detail": detail or str(code)}, status_code=code)
    from .web.deps import render
    try:
        return render(request, "pomylka.html", kod=code, detail=detail, status_code=code)
    except Exception:                                       # noqa: BLE001 — last resort
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(f"Error {code}", status_code=code)


@app.get("/lang/{code}")
def change_language(code: str, next: str = "/"):
    """Switch the interface language. `next` must be a local path (no open redirect)."""
    if not next.startswith("/") or next.startswith("//") or "\\" in next:
        next = "/"
    resp = RedirectResponse(next, status_code=303)
    if code in i18n.LANGS:
        resp.set_cookie(i18n.COOKIE, code, max_age=60 * 60 * 24 * 365, path="/",
                        samesite="lax", secure=config.SECURE_COOKIES)
    return resp


@app.get("/health")
def health(s: Session = Depends(get_session)):
    """Здоровʼя = процес живий І БАЗА ВІДПОВІДАЄ НА ЗАПИТ.

    «200 OK» без звернення до бази доводить лише те, що uvicorn піднявся. При
    зламаній базі — заблокований файл, права, побитий SQLite — такий health
    лишався б зеленим, поки система не віддає жодної сторінки.

    Чого цей health НЕ доводить: що змонтовано правильний том. Перевірено
    07.08.2026: на порожній базі застосунок сам створює 4 користувачів, тож
    «люди є» буває і на чистому контейнері. Підміну тому ловить окремий
    стартовий запобіжник (`_baza_zhyva`) — тут ми чесно віддаємо ЧИСЛА, щоб
    підміну було видно оком: 4 контрагенти замість 46 тисяч ні з чим не
    сплутати.
    """
    from sqlalchemy import text

    try:
        lyudej = s.exec(text("select count(*) from user")).one()[0]
        kontrahentiv = s.exec(text("select count(*) from counterparty")).one()[0]
    except Exception as e:                                  # noqa: BLE001
        detail = {"baza": type(e).__name__} if config.ENV == "dev" else {}
        return JSONResponse({"ok": False, **detail}, status_code=503)
    if config.ENV != "dev":
        return {"ok": True}
    return {"ok": True, "env": config.ENV,
            "lyudej": lyudej, "kontrahentiv": kontrahentiv}
