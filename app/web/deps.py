# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Спільні залежності веб-шару: поточний користувач, рендер зі спільним контекстом."""
from datetime import date

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from .. import authkit, config
from ..db import get_session
from ..i18n import _
from ..models import User
from ..security import KIT
from ..templating import templates


def current_user(request: Request, s: Session = Depends(get_session)) -> User:
    """Deny by default: no live server-side session → redirect to /login."""
    sess = authkit.resolve_session(s, KIT, request.cookies.get(KIT.session_cookie, ""))
    user = s.get(User, sess.user_id) if sess else None
    if not user or not user.active:
        # 307 зберігає метод; для GET веде на форму входу
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user


def require_cap(cap: str):
    """Гейт за дозволом ролі (ТЗ §2)."""
    def _dep(user: User = Depends(current_user)) -> User:
        if not user.can(cap):
            raise HTTPException(403, _("Ваша роль не має права на цю дію"))
        return user
    return _dep


def data_z_formy(raw: str | None) -> date | None:
    """Дата з поля форми: порожньо → None, сміття → 400 (а не 500).

    `date.fromisoformat` на «31.12.2026» чи «завтра» кидав ValueError, і людина
    бачила сторінку «помилка сервера» замість пояснення, що не так із полем.
    """
    v = (raw or "").strip()
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, _("Некоректна дата: очікую формат РРРР-ММ-ДД")) from None


def dni_vidpovidi(settings: dict, typovo: int = 14) -> int:
    """`outreach.reply_days` з налаштувань; зіпсоване значення → типові 14 днів."""
    try:
        n = int(str(settings.get("outreach.reply_days") or typovo).strip())
    except (TypeError, ValueError):
        return typovo
    return n if 0 < n <= 3650 else typovo


def spivpratsya(s, entity: str, entity_id: int) -> dict:
    """Контекст універсального блоку співпраці (`templates/_spivpratsya.html`).

    Викликається в КОЖНОМУ роуті картки — щоб коментарі, затвердження й доручення
    були там, де людина вже дивиться, а не на окремому екрані.
    """
    from sqlmodel import select

    from ..models import COMMENT_KINDS, RESOLUTIONS, STATUSES, User as U
    from ..services import collab
    return {
        "sp_approval": collab.approval_for(s, entity, entity_id),
        "sp_comments": collab.comments_for(s, entity, entity_id),
        "sp_tasks": collab.tasks_for(s, entity, entity_id),
        "sp_feed": collab.feed(s, limit=8, entity=entity, entity_id=entity_id),
        "sp_users": list(s.exec(select(U).where(U.active == True))),  # noqa: E712
        "sp_mention_hint": collab.mention_hint(s),
        "comment_kinds": COMMENT_KINDS,
        "resolutions": RESOLUTIONS,
        "task_statuses": STATUSES,
    }


def _versiya_statyky() -> str:
    """Штамп для `?v=` на статиці — час останньої зміни CSS.

    Без нього браузер тримає стару таблицю навіть після оновлення сторінки: правку
    стилів бачить лише той, хто здогадається зробити hard-reload.

    Якщо жодного файла не знайдено, повертаємо час старту процесу, а НЕ константу:
    константа заморозила б кеш назавжди на одному значенні, тобто зламала б рівно
    те, заради чого цей штамп існує.

    Рахуємо по ВСІХ файлах системи, не по одному. Система розкладена на ядро,
    два компонентні шари, скрипт взаємодій і три набори токенів напрямів — і поки
    штамп залежав лише від одного файла, правка токенів не скидала кеш: сторінка
    показувала стару палітру з новою версткою. Виявлено рендер-QA — кнопки
    лишались у світлих кольорах при темній темі.
    """
    import time
    from pathlib import Path
    koren = Path(__file__).resolve().parents[2]
    # Обидва дерева: `static/` (бібліотека, токени) і `static_v2/` (скрипти
    # екранів). Поки рахувалось лише перше, правка `static_v2/*.js` не скидала
    # кеш — браузер жив зі старим кодом під новою розміткою.
    katalogy = (koren / "static", koren / "static_v2")

    # Обходимо ВСЕ дерево, а не список файлів. Список вівся руками й устиг
    # застаріти: 06.08.2026 у ньому не було жодного `karta/*.js`, хоча саме вони
    # змінились — правка приїхала б у прод стилями без коду, який їх оживляє.
    # Ручний перелік у такому місці завжди відстає від наступної правки, тому
    # прибираємо сам перелік, а не оновлюємо його.
    #
    # `vendor/` пропускаємо: вкладені бібліотеки версіонуються іменем файла й
    # важать більше за решту разом узяту — обходити їх щозапиту марно.
    mtimes = []
    for statyka in katalogy:
        try:
            for f in statyka.rglob("*"):
                if not f.is_file() or f.suffix not in (".css", ".js"):
                    continue
                if "vendor" in f.parts:
                    continue
                mtimes.append(int(f.stat().st_mtime))
        except OSError:
            pass
    return str(max(mtimes)) if mtimes else str(int(time.time()))


# Напрям вигляду. Токени — окремий файл у static/napryamy/.
# 06.08.2026 звужено до ОДНОГО. Лабораторія й Аврора лишились у git-історії:
# три напрями потроювали QA (33 екрани × 3 × 2 теми × 3 ширини) і розмивали
# глибину — та сама праця йшла вшир замість углиб. Механізм підміни токенів
# одним файлом лишається: повернути другий напрям = додати файл і рядок сюди.
NAPRYAMY = {"studiya": "Студія"}
NAPRYAM_TYPOVYI = "studiya"


STATYKA_V = _versiya_statyky()


def _statyka_teper() -> str:
    """Версія статики для поточного запиту.

    У розробці рахуємо ЩОРАЗУ: uvicorn --reload стежить лише за `.py`, тож правка
    CSS не перезапускає процес — а отже штамп, обчислений на старті, лишався б
    старим, і браузер віддавав би кешовану стару систему. Двічі витратив на це
    час, поки шукав неіснуючу помилку в темі.

    У проді рахуємо один раз: файли там не міняються між релізами, а зайвий
    stat() на кожен запит — марна робота.
    """
    return STATYKA_V if config.ENV == "production" else _versiya_statyky()


def render(request: Request, name: str, user: User | None = None, **ctx):
    """Render a template with the shared context (user, CSRF token, CSP nonce, language)."""
    from .. import i18n
    ctx.setdefault("user", user)
    ctx.setdefault("env", config.ENV)
    ctx.setdefault("demo", config.IS_DEMO)
    ctx.setdefault("statyka_v", _statyka_teper())
    ctx.setdefault("napryam", NAPRYAM_TYPOVYI)
    ctx.setdefault("csrf_token", authkit.csrf_token(request))
    ctx.setdefault("csp_nonce", getattr(request.state, "csp_nonce", ""))
    ctx.setdefault("lang", i18n.current())
    status = ctx.pop("status_code", 200)
    return templates.TemplateResponse(request=request, name=name, context=ctx,
                                      status_code=status)


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url, status_code=status_code)
