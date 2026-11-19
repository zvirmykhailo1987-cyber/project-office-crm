"""Shared authentication and web-security kit.

The same file ships in both demo apps (OFIS and the Grants portal); keep the two
copies identical. It is framework-aware (FastAPI + SQLModel) but app-agnostic:
every app passes its own settings through :class:`KitConfig`.

What it provides
----------------
* **Passwords** — argon2id (``argon2-cffi``). Legacy ``sha256(SECRET + pw)``
  hashes are still accepted once and transparently re-hashed to argon2id on the
  first successful login (:func:`verify_password` returns the new hash).
* **Server-side sessions** — the cookie carries a random 256-bit token; the
  database stores only its SHA-256. Every session has an absolute expiry, an
  idle timeout and can be revoked (logout, "sign out everywhere", password reset).
* **CSRF** — double-submit token: an HttpOnly cookie plus a hidden form field or
  ``X-CSRF-Token`` header on every unsafe request; Origin/Referer must match the
  host when the browser sends them. Bearer-token API prefixes are exempt: they
  never authenticate with cookies.
* **Login rate limiting** — failures are counted per (client IP, login) pair, per IP
  and per login, each with its own threshold: a single client cannot lock everyone
  out, and one burst cannot lock a victim's account for everybody.
* **Request size limits** — an ASGI guard rejects oversized bodies (413) before any
  form or upload is parsed; sign-in and CSP-report bodies have a much smaller cap.
* **Security headers** — strict CSP with a per-request nonce, ``nosniff``,
  ``frame-ancestors 'none'``, ``Referrer-Policy``, ``Permissions-Policy``,
  COOP/CORP and HSTS in production.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import FastAPI, HTTPException, Request
from starlette.responses import Response
from sqlmodel import Field, Session, SQLModel, select

_LOG = logging.getLogger("uvicorn.error")
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"


def utcnow() -> datetime:
    """Naive UTC (the apps store naive UTC timestamps in SQLite)."""
    return datetime.now(UTC).replace(tzinfo=None)


# ── Configuration ────────────────────────────────────────────────────────────
@dataclass
class KitConfig:
    app_slug: str                       # cookie prefix, e.g. "ofis" / "hranty"
    secret: str                         # app secret (also the legacy sha256 salt)
    secure_cookies: bool = False        # True in production (HTTPS only)
    session_ttl: int = 60 * 60 * 24 * 7  # absolute lifetime, seconds
    idle_timeout: int = 60 * 60 * 8      # inactivity timeout, seconds
    max_failures: int = 5            # per (IP, login) pair
    max_failures_ip: int = 30        # per client IP, any logins
    max_failures_login: int = 50     # per login, from any IPs (slows distributed guessing)
    failure_window: int = 15 * 60
    lockout: int = 15 * 60
    max_body: int = 26 * 1024 * 1024     # bytes; uploads included
    small_body: int = 16 * 1024          # sign-in, logout, CSP reports
    small_body_paths: tuple[str, ...] = ("/login", "/logout", "/csp-report")
    csrf_exempt_prefixes: tuple[str, ...] = ("/api/", "/csp-report")
    # extra CSP sources, e.g. {"img-src": ["https://tile.openstreetmap.org"]}
    csp_extra: dict[str, list[str]] = field(default_factory=dict)

    @property
    def session_cookie(self) -> str:
        # `__Host-` = Secure, Path=/, no Domain: a sibling subdomain cannot plant it.
        return ("__Host-" if self.secure_cookies else "") + f"{self.app_slug}_session"

    @property
    def csrf_cookie(self) -> str:
        return ("__Host-" if self.secure_cookies else "") + f"{self.app_slug}_csrf"


# ── Passwords ────────────────────────────────────────────────────────────────
_HASHER = PasswordHasher()  # argon2id, library defaults (t=3, m=64 MiB, p=4)
# Verifying against a dummy hash when the user does not exist keeps response
# time flat, so the login form does not leak which accounts exist.
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def legacy_sha256(secret: str, password: str) -> str:
    """The pre-argon2 scheme: sha256(SECRET + password), hex."""
    return hashlib.sha256((secret + password).encode("utf-8")).hexdigest()


def verify_password(stored: str, password: str, *, legacy_secret: str) -> tuple[bool, str | None]:
    """Check a password. Returns ``(ok, new_hash)``.

    ``new_hash`` is set when the stored hash must be upgraded: a legacy sha256
    hash that matched, or an argon2 hash with outdated parameters.
    """
    if not stored:
        _dummy_verify(password)
        return False, None
    if stored.startswith("$argon2"):
        try:
            _HASHER.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False, None
        return True, (_HASHER.hash(password) if _HASHER.check_needs_rehash(stored) else None)
    # legacy sha256 — constant-time compare, then upgrade
    _dummy_verify(password)
    if hmac.compare_digest(stored, legacy_sha256(legacy_secret, password)):
        return True, _HASHER.hash(password)
    return False, None


def _dummy_verify(password: str) -> None:
    try:
        _HASHER.verify(_DUMMY_HASH, password)
    except VerificationError:
        pass


def password_problem(password: str) -> str | None:
    """Minimal policy for new passwords; returns an error message or None."""
    if len(password) < 12:
        return "Password must be at least 12 characters long."
    if password.lower() == password and password.isalpha():
        return "Use a mix of letters with digits or symbols."
    return None


# ── Sessions ─────────────────────────────────────────────────────────────────
class AuthSession(SQLModel, table=True):
    """A server-side login session. The cookie holds the raw token; we keep its hash."""
    __tablename__ = "auth_session"
    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    user_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=utcnow)
    revoked_at: datetime | None = None
    ip: str = ""
    user_agent: str = ""


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def create_session(s: Session, cfg: KitConfig, user_id: int, request: Request) -> str:
    raw = secrets.token_urlsafe(32)
    now = utcnow()
    s.add(AuthSession(token_hash=_token_hash(raw), user_id=user_id, created_at=now,
                      last_seen_at=now, expires_at=now + timedelta(seconds=cfg.session_ttl),
                      ip=client_ip(request)[:64],
                      user_agent=request.headers.get("user-agent", "")[:200]))
    s.commit()
    return raw


def resolve_session(s: Session, cfg: KitConfig, raw: str) -> AuthSession | None:
    """Return the live session for a raw cookie token, or None."""
    if not raw or len(raw) > 128:
        return None
    sess = s.exec(select(AuthSession).where(AuthSession.token_hash == _token_hash(raw))).first()
    if not sess or sess.revoked_at is not None:
        return None
    now = utcnow()
    if now >= sess.expires_at or now - sess.last_seen_at > timedelta(seconds=cfg.idle_timeout):
        return None
    # Touch at most once a minute: no write on every request.
    if now - sess.last_seen_at > timedelta(seconds=60):
        sess.last_seen_at = now
        s.add(sess)
        s.commit()
    return sess


def revoke_session(s: Session, raw: str) -> None:
    if not raw:
        return
    sess = s.exec(select(AuthSession).where(AuthSession.token_hash == _token_hash(raw))).first()
    if sess and sess.revoked_at is None:
        sess.revoked_at = utcnow()
        s.add(sess)
        s.commit()


def revoke_user_sessions(s: Session, user_id: int) -> int:
    n = 0
    for sess in s.exec(select(AuthSession).where(AuthSession.user_id == user_id,
                                                 AuthSession.revoked_at == None)):  # noqa: E711
        sess.revoked_at = utcnow()
        s.add(sess)
        n += 1
    s.commit()
    return n


def set_session_cookie(response: Any, cfg: KitConfig, raw: str) -> None:
    response.set_cookie(cfg.session_cookie, raw, max_age=cfg.session_ttl, path="/",
                        httponly=True, samesite="lax", secure=cfg.secure_cookies)


def clear_session_cookie(response: Any, cfg: KitConfig) -> None:
    response.delete_cookie(cfg.session_cookie, path="/", httponly=True,
                           samesite="lax", secure=cfg.secure_cookies)


# ── Login rate limiting ──────────────────────────────────────────────────────
class LoginLimiter:
    """In-process failure counter (single worker). Keys: ``ip:…`` and ``user:…``."""

    def __init__(self, cfg: KitConfig):
        self.cfg = cfg
        self._fails: dict[str, deque[float]] = {}
        self._locked: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def keys(ip: str, login: str) -> list[str]:
        login = login.strip().lower()[:64]
        return [f"pair:{ip}|{login}", f"ip:{ip}", f"user:{login}"]

    def _limit(self, key: str) -> int:
        if key.startswith("ip:"):
            return self.cfg.max_failures_ip
        if key.startswith("user:"):
            return self.cfg.max_failures_login
        return self.cfg.max_failures

    def retry_after(self, keys: list[str]) -> int:
        """Seconds until the caller may try again; 0 = allowed."""
        now = time.monotonic()
        with self._lock:
            waits = [int(until - now) + 1 for k in keys
                     if (until := self._locked.get(k)) and until > now]
        return max(waits, default=0)

    def failure(self, keys: list[str]) -> None:
        now = time.monotonic()
        with self._lock:
            for k in keys:
                q = self._fails.setdefault(k, deque())
                q.append(now)
                while q and now - q[0] > self.cfg.failure_window:
                    q.popleft()
                if len(q) >= self._limit(k):
                    self._locked[k] = now + self.cfg.lockout
                    q.clear()

    def success(self, keys: list[str]) -> None:
        with self._lock:
            for k in keys:
                if k.startswith("pair:"):
                    self._fails.pop(k, None)
                    self._locked.pop(k, None)

    def reset_all(self) -> None:
        with self._lock:
            self._fails.clear()
            self._locked.clear()


# ── CSRF ─────────────────────────────────────────────────────────────────────
def csrf_token(request: Request) -> str:
    """The token to embed in forms of the current response (set by the middleware)."""
    return getattr(request.state, "csrf_token", "")


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True          # non-browser client; the token check still applies
    if origin == "null":
        return False         # sandboxed/opaque origin
    host = request.headers.get("host", "")
    return urlsplit(origin).netloc == host


async def csrf_protect(request: Request) -> None:
    """Global FastAPI dependency: rejects unsafe requests without a valid token."""
    if request.method not in UNSAFE_METHODS:
        return
    cfg: KitConfig = request.app.state.authkit
    # Machine API routes authenticate with a Bearer token only and never read
    # cookies, so a forged cross-site request carries no credential there.
    if any(request.url.path.startswith(p) for p in cfg.csrf_exempt_prefixes):
        return
    cookie = request.cookies.get(cfg.csrf_cookie, "")
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            form = await request.form()          # cached by Starlette, reused by the route
            sent = str(form.get(CSRF_FIELD, ""))
    if not cookie or not sent or not hmac.compare_digest(cookie, sent) or not _same_origin(request):
        raise HTTPException(403, "CSRF check failed. Reload the page and try again.")


_CSP_LOG = {"window": 0.0, "n": 0}


def _csp_log_allowed(per_minute: int = 30) -> bool:
    """At most `per_minute` CSP violation lines per minute: reports cannot flood the log."""
    now = time.monotonic()
    if now - _CSP_LOG["window"] > 60:
        _CSP_LOG["window"], _CSP_LOG["n"] = now, 0
    _CSP_LOG["n"] += 1
    return _CSP_LOG["n"] <= per_minute


class BodySizeLimit:
    """Pure ASGI guard: 413 for bodies over the limit, checked on Content-Length AND while
    streaming (chunked uploads), before FastAPI parses any form or file part."""

    def __init__(self, app: Any, cfg: KitConfig) -> None:
        self.app, self.cfg = app, cfg

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        limit = self.cfg.small_body if path in self.cfg.small_body_paths else self.cfg.max_body
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        await _too_large(send)
                        return
                except ValueError:
                    await _too_large(send)
                    return
        seen = 0

        async def limited_receive() -> Any:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await _too_large(send)


class _BodyTooLarge(Exception):
    pass


async def _too_large(send: Any) -> None:
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": b"Request body too large"})


# ── Security headers + per-request state ─────────────────────────────────────
def _csp(nonce: str, cfg: KitConfig) -> str:
    d: dict[str, list[str]] = {
        "default-src": ["'self'"],
        "script-src": ["'self'", f"'nonce-{nonce}'"],
        "style-src": ["'self'", f"'nonce-{nonce}'"],
        "img-src": ["'self'", "data:", "blob:"],
        "font-src": ["'self'"],
        "connect-src": ["'self'"],
        "frame-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
        "base-uri": ["'none'"],
        "object-src": ["'none'"],
        "report-uri": ["/csp-report"],
    }
    for k, v in cfg.csp_extra.items():
        d.setdefault(k, []).extend(v)
    return "; ".join(f"{k} {' '.join(v)}" for k, v in d.items())


def install(app: FastAPI, cfg: KitConfig) -> None:
    """Register the security middleware, the body-size guard and the CSP report sink."""
    app.state.authkit = cfg

    @app.post("/csp-report", include_in_schema=False, status_code=204)
    async def _csp_report(request: Request) -> Response:
        """Browsers POST CSP violations here; we log a short, size-capped summary."""
        body = (await request.body())[:16_384]       # the size guard already capped the stream
        try:
            rep = json.loads(body or b"{}").get("csp-report", {})
        except (ValueError, AttributeError):
            rep = {}
        if isinstance(rep, dict) and rep and _csp_log_allowed():
            _LOG.warning("csp-violation %s blocked=%s source=%s:%s sample=%r",
                         str(rep.get("violated-directive", ""))[:60], str(rep.get("blocked-uri", ""))[:120],
                         str(rep.get("source-file", ""))[:120], rep.get("line-number"),
                         str(rep.get("script-sample", ""))[:60])
        return Response(status_code=204)

    @app.middleware("http")
    async def _security(request: Request,
                        call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.csp_nonce = secrets.token_urlsafe(16)
        existing = request.cookies.get(cfg.csrf_cookie, "")
        fresh = not (existing and 20 <= len(existing) <= 128)
        request.state.csrf_token = secrets.token_urlsafe(32) if fresh else existing
        response = await call_next(request)
        h = response.headers
        h.setdefault("Content-Security-Policy", _csp(request.state.csp_nonce, cfg))
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if cfg.secure_cookies:
            h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        if fresh:
            response.set_cookie(cfg.csrf_cookie, request.state.csrf_token, path="/",
                                httponly=True, samesite="strict", secure=cfg.secure_cookies)
        return response

    app.add_middleware(BodySizeLimit, cfg=cfg)
