"""Access boundaries, generated from the route table — a new route is covered automatically.

For EVERY route of the app:
  1. without a session      → redirect to /login (never a page, never a 500);
  2. unsafe method, no CSRF → 403 (even with a valid session);
  3. a route guarded by `require_cap(cap)` → 403 for every role lacking `cap`
     (deny by default: the check lives on the route, not in the template).
"""
import re
import secrets
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import authkit
from app.models import ROLE_CAPS, User
from app.security import KIT

PUBLIC = {"/login", "/health", "/lang/{code}", "/csp-report"}
TOKEN = "boundary-csrf-token-0123456789abcdef"


def _flat(routes):
    """FastAPI ≥0.140 wraps included routers; unwrap to the real APIRoute objects."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _flat(inner.routes)
        else:
            yield r


def _routes():
    from app.main import app
    for r in _flat(app.routes):
        if isinstance(r, APIRoute) and r.path not in PUBLIC:
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield r, m


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def _caps_of(route) -> set[str]:
    """Capabilities demanded by `require_cap(...)` dependencies of a route."""
    found = set()

    def walk(dep):
        fn = dep.call
        if fn is not None and getattr(fn, "__closure__", None) and fn.__qualname__.startswith("require_cap"):
            for cell in fn.__closure__:
                if isinstance(cell.cell_contents, str):
                    found.add(cell.cell_contents)
        for d in dep.dependencies:
            walk(d)
    walk(route.dependant)
    return found


ROUTES = list(_routes())
# A capability every role holds (e.g. `crm`) is just "signed in" — covered by test 1.
GUARDED = [(r, m, cap) for r, m in ROUTES for cap in _caps_of(r)
           if any(cap not in caps for caps in ROLE_CAPS.values())]


@pytest.fixture()
def app_engine(engine):
    from app.db import get_session
    from app.main import app

    def _sess():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = _sess
    yield engine
    app.dependency_overrides.clear()


def _session_for(engine, role: str) -> str:
    raw = secrets.token_urlsafe(32)
    with Session(engine) as s:
        u = User(name=f"Test {role}", login=f"t_{role}", role=role, active=True)
        s.add(u)
        s.commit()
        now = authkit.utcnow()
        s.add(authkit.AuthSession(token_hash=authkit._token_hash(raw), user_id=u.id, created_at=now,
                                  last_seen_at=now, expires_at=now + timedelta(hours=1)))
        s.commit()
    return raw


def test_route_table_is_not_empty():
    assert len(ROUTES) > 60 and len(GUARDED) > 10


@pytest.mark.parametrize("route,method", ROUTES, ids=lambda x: getattr(x, "path", x))
def test_anonymous_is_sent_to_login(app_engine, route, method):
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(KIT.csrf_cookie, TOKEN)
    r = c.request(method, _url(route.path), headers={"X-CSRF-Token": TOKEN})
    assert r.status_code == 307 and r.headers["location"] == "/login", (method, route.path, r.status_code)


@pytest.mark.parametrize("route,method", [(r, m) for r, m in ROUTES if m != "GET"],
                         ids=lambda x: getattr(x, "path", x))
def test_unsafe_request_without_csrf_is_403(app_engine, route, method):
    from app.main import app
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(KIT.session_cookie, _session_for(app_engine, "rozrobnyk"))
    r = c.request(method, _url(route.path))
    assert r.status_code == 403, (method, route.path, r.status_code)


@pytest.mark.parametrize("route,method,cap", GUARDED, ids=lambda x: getattr(x, "path", x))
def test_role_without_capability_gets_403(app_engine, route, method, cap):
    from app.main import app
    lacking = [role for role, caps in ROLE_CAPS.items() if cap not in caps]
    for role in lacking:
        c = TestClient(app, follow_redirects=False)
        c.cookies.set(KIT.session_cookie, _session_for(app_engine, role))
        c.cookies.set(KIT.csrf_cookie, TOKEN)
        r = c.request(method, _url(route.path), headers={"X-CSRF-Token": TOKEN})
        assert r.status_code == 403, (role, method, route.path, r.status_code)
