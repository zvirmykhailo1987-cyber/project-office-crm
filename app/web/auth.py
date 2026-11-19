# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Sign-in / sign-out. Every person has their own account; there is no shared password.

The login page never lists accounts. Failed attempts are rate-limited per IP and
per login; a successful login rotates the session (no session fixation).
"""
from fastapi import APIRouter, Depends, Form, Request
from sqlmodel import Session, select

from .. import audit, authkit
from ..db import get_session
from ..i18n import _
from ..models import User, utcnow
from ..security import KIT, LIMITER, check_pw
from .deps import current_user, redirect, render

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, login: str = Form(""), password: str = Form(""),
          s: Session = Depends(get_session)):
    login = login.strip().lower()[:64]
    keys = LIMITER.keys(authkit.client_ip(request), login)
    wait = LIMITER.retry_after(keys)
    if wait:
        resp = render(request, "login.html", login_value=login,
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
        resp = render(request, "login.html", login_value=login,
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
    """Revoke every session of the current user (lost laptop, shared computer)."""
    n = authkit.revoke_user_sessions(s, user.id)
    audit.log(s, user.name, "auth.logout_all", entity="user", entity_id=user.id,
              tier=audit.TIER_CHANGE, detail=f"sessions={n}")
    s.commit()
    resp = redirect("/login")
    authkit.clear_session_cookie(resp, KIT)
    return resp
