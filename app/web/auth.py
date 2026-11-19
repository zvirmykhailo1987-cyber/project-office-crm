# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Sign-in / sign-out. Every person has their own account; there is no shared password.

The login page never lists accounts — except in demo mode, where the public demo
accounts (synthetic data) are listed with one-click fill. Failed attempts are rate-limited
per IP and per login; a successful login rotates the session (no session fixation).

Demo mode shares those accounts between every visitor, so nothing may let one visitor
take them away from the next: there is no per-login lock (only per IP and per IP+login,
both expire after 15 minutes) and "sign out everywhere" is refused.
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, select

from .. import audit, authkit, config
from ..db import get_session
from ..i18n import _
from ..models import User, utcnow
from ..security import KIT, LIMITER, check_pw
from .deps import current_user, redirect, render

router = APIRouter()

# Shown when a demo visitor tries to change a shared demo account (demo mode only).
DEMO_LOCKED = ("Демо-акаунти спільні, тож у демо їх змінити не можна. "
               "Дані скидаються щоночі о 04:00 (CET).")


def demo_accounts() -> list[tuple[str, str, str]]:
    """(login, role label, password) of the public demo accounts; empty outside demo mode.

    The single source is the demo seeder, so the sign-in page can never drift from the
    accounts that actually exist."""
    if not config.IS_DEMO:
        return []
    from ..models.people import ROLES
    from ..seed_demo import DEMO_USERS, demo_password
    return [(login, ROLES[role], demo_password(login)) for login, (_name, role) in DEMO_USERS.items()]


def refuse_in_demo() -> None:
    """Raise 403 with a clear message when the shared demo accounts would be changed."""
    if config.IS_DEMO:
        raise HTTPException(status_code=403, detail=_(DEMO_LOCKED))


def limiter_keys(ip: str, login: str) -> list[str]:
    """Limiter keys for a sign-in attempt. In demo mode the per-login key (any IP) is
    dropped: the demo accounts are shared, so it would let one visitor lock everyone out."""
    keys = LIMITER.keys(ip, login)
    if config.IS_DEMO:
        keys = [k for k in keys if not k.startswith("user:")]
    return keys


def _login_page(request: Request, **ctx):
    return render(request, "login.html", demo_accounts=demo_accounts(), **ctx)


@router.get("/login")
def login_form(request: Request):
    return _login_page(request)


@router.post("/login")
def login(request: Request, login: str = Form(""), password: str = Form(""),
          s: Session = Depends(get_session)):
    login = login.strip().lower()[:64]
    keys = limiter_keys(authkit.client_ip(request), login)
    wait = LIMITER.retry_after(keys)
    if wait:
        resp = _login_page(request, login_value=login,
                      error=_("Забагато спроб входу. Спробуйте за {n} хв.").replace(
                          "{n}", str(max(1, round(wait / 60)))))
        resp.status_code = 429
        resp.headers["Retry-After"] = str(wait)
        return resp

    user = s.exec(select(User).where(User.login == login)).first() if login else None
    ok, new_hash = check_pw(user.password_hash if user else "", password)
    if not user or not user.active or not ok:
        LIMITER.failure(keys)
        # Логін задає той, хто стукає ззовні, — тому він НЕ автор запису (інакше
        # довільний рядок ставав би «людиною» в журналі) і лише обрізаними даними.
        audit.log(s, "anonymous", "auth.login_failed", entity="user",
                  entity_id=user.id if user else None, tier=audit.TIER_CHANGE,
                  detail=f"login={login[:32]!r}")
        s.commit()
        resp = _login_page(request, login_value=login,
                      error=_("Невірний логін або пароль"))
        resp.status_code = 401
        return resp

    LIMITER.success(keys)
    if new_hash:                       # legacy sha256 or outdated argon2 params
        user.password_hash = new_hash
    user.last_login_at = utcnow()
    audit.log(s, user.name, "auth.login", entity="user", entity_id=user.id,
              tier=audit.TIER_CHANGE)
    s.add(user)
    s.commit()
    # rotate: an old session cookie sent with the login request is revoked
    authkit.revoke_session(s, request.cookies.get(KIT.session_cookie, ""))
    raw = authkit.create_session(s, KIT, user.id, request)
    resp = redirect("/")
    authkit.set_session_cookie(resp, KIT, raw)
    return resp


@router.post("/logout")
def logout(request: Request, user: User = Depends(current_user),
           s: Session = Depends(get_session)):
    authkit.revoke_session(s, request.cookies.get(KIT.session_cookie, ""))
    resp = redirect("/login")
    authkit.clear_session_cookie(resp, KIT)
    return resp


@router.post("/logout/everywhere")
def logout_everywhere(user: User = Depends(current_user), s: Session = Depends(get_session)):
    """Revoke every session of the current user (lost laptop, shared computer).

    Refused in demo mode: the account is shared, so it would sign out every other visitor."""
    refuse_in_demo()
    n = authkit.revoke_user_sessions(s, user.id)
    audit.log(s, user.name, "auth.logout_all", entity="user", entity_id=user.id,
              tier=audit.TIER_CHANGE, detail=f"sessions={n}")
    s.commit()
    resp = redirect("/login")
    authkit.clear_session_cookie(resp, KIT)
    return resp
