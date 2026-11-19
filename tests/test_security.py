"""Security boundaries of sign-in, sessions, CSRF, rate limiting and headers.

Every test drives the real HTTP stack (TestClient) without overriding
`current_user`, so the whole chain form → password → session row → cookie runs.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import authkit, config
from app.models import AuditEntry, User
from app.security import KIT, LIMITER, hash_pw

PAROL = "correct-horse-battery-9"


@pytest.fixture()
def lyudy(engine):
    with Session(engine) as s:
        s.add(User(id=1, name="Марко", login="marko", role="rozrobnyk",
                   active=True, password_hash=hash_pw(PAROL)))
        s.add(User(id=2, name="Вимкнений", login="vymk", role="rozrobnyk",
                   active=False, password_hash=hash_pw(PAROL)))
        s.add(User(id=3, name="Безпарольний", login="bezpar", role="rozrobnyk",
                   active=True, password_hash=""))
        s.add(User(id=4, name="Галина", login="halyna", role="zapyty_lysty",
                   active=True, password_hash=hash_pw(PAROL)))
        s.commit()
    return True


@pytest.fixture()
def gist(engine):
    """A browser with no session: gets its CSRF cookie the honest way (GET /login)."""
    from app.db import get_session
    from app.main import app

    def _sess():
        with Session(engine) as sess:
            yield sess

    app.dependency_overrides[get_session] = _sess
    c = TestClient(app, follow_redirects=False)
    yield c
    app.dependency_overrides.clear()


def token(c: TestClient) -> str:
    c.get("/login")
    return c.cookies.get(KIT.csrf_cookie)


def uviity(c: TestClient, login="marko", password=PAROL):
    return c.post("/login", data={"login": login, "password": password,
                                  "csrf_token": token(c)})


# ── login page ───────────────────────────────────────────────────────────────

def test_login_page_lists_no_accounts(lyudy, gist):
    r = gist.get("/login")
    assert r.status_code == 200
    for imya in ("Марко", "Галина", "marko", "halyna"):
        assert imya not in r.text, f"login page leaks account {imya!r}"
    assert '<select' not in r.text


def test_protected_screen_redirects_to_login(lyudy, gist):
    r = gist.get("/zavdannya")
    assert r.status_code == 303 and r.headers["location"] == "/login"


# ── passwords ────────────────────────────────────────────────────────────────

def test_hash_is_argon2id_and_salted():
    a, b = hash_pw(PAROL), hash_pw(PAROL)
    assert a.startswith("$argon2id$") and a != b
    assert authkit.verify_password(a, PAROL, legacy_secret="x") == (True, None)
    assert authkit.verify_password(a, "wrong", legacy_secret="x")[0] is False


def test_legacy_sha256_hash_is_upgraded_on_login(engine, gist):
    legacy = authkit.legacy_sha256(config.SECRET, PAROL)
    with Session(engine) as s:
        s.add(User(id=9, name="Старий", login="staryi", role="rozrobnyk",
                   active=True, password_hash=legacy))
        s.commit()
    r = uviity(gist, "staryi")
    assert r.status_code == 303
    with Session(engine) as s:
        assert s.get(User, 9).password_hash.startswith("$argon2id$")
    gist.cookies.clear()
    assert uviity(gist, "staryi").status_code == 303   # the new hash works


def test_correct_password_creates_server_side_session(lyudy, gist, engine):
    r = uviity(gist)
    assert r.status_code == 303
    raw = r.cookies.get(KIT.session_cookie)
    assert raw and len(raw) >= 40
    with Session(engine) as s:
        sess = s.exec(select(authkit.AuthSession)).one()
        assert sess.user_id == 1 and sess.token_hash != raw   # only the hash is stored
    assert gist.get("/zavdannya").status_code == 200


def test_login_is_case_and_space_insensitive(lyudy, gist):
    assert uviity(gist, "  MARKO ").status_code == 303


@pytest.mark.parametrize("login,parol", [
    ("marko", "wrong-password"), ("marko", ""), ("nobody", PAROL),
    ("vymk", PAROL), ("bezpar", ""), ("bezpar", "anything"),
])
def test_bad_credentials_give_401_and_no_session(lyudy, gist, login, parol):
    r = uviity(gist, login, parol)
    assert r.status_code == 401
    assert KIT.session_cookie not in r.cookies
    assert "Невірний логін або пароль" in r.text or "Invalid" in r.text


def test_failed_login_is_audited(lyudy, gist, engine):
    uviity(gist, "marko", "nope")
    with Session(engine) as s:
        assert s.exec(select(AuditEntry).where(AuditEntry.action == "auth.login_failed")).first()


def test_session_cookie_flags(lyudy, gist):
    syra = uviity(gist).headers.get("set-cookie", "")
    assert "HttpOnly" in syra and "SameSite=lax" in syra.replace("Lax", "lax")
    assert "Path=/" in syra


# ── sessions: expiry, idle timeout, revocation ───────────────────────────────

def _sesiya(engine):
    with Session(engine) as s:
        return s.exec(select(authkit.AuthSession)).one()


def test_logout_revokes_the_session_server_side(lyudy, gist, engine):
    raw = uviity(gist).cookies.get(KIT.session_cookie)
    r = gist.post("/logout", data={"csrf_token": gist.cookies.get(KIT.csrf_cookie)})
    assert r.status_code == 303
    assert _sesiya(engine).revoked_at is not None
    gist.cookies.set(KIT.session_cookie, raw)            # replaying the old cookie
    assert gist.get("/zavdannya").status_code == 303


def test_logout_everywhere_revokes_all_sessions(lyudy, engine, gist):
    from app.main import app
    druhyi = TestClient(app, follow_redirects=False)
    uviity(druhyi)
    uviity(gist)
    r = gist.post("/logout/everywhere", data={"csrf_token": gist.cookies.get(KIT.csrf_cookie)})
    assert r.status_code == 303
    assert druhyi.get("/zavdannya").status_code == 303


@pytest.mark.parametrize("pole,zsuv", [("expires_at", timedelta(seconds=-1)),
                                       ("last_seen_at", timedelta(seconds=-KIT.idle_timeout - 5))])
def test_expired_or_idle_session_is_rejected(lyudy, gist, engine, pole, zsuv):
    uviity(gist)
    with Session(engine) as s:
        sess = s.exec(select(authkit.AuthSession)).one()
        setattr(sess, pole, authkit.utcnow() + zsuv)
        s.add(sess)
        s.commit()
    assert gist.get("/zavdannya").status_code == 303


def test_forged_cookie_redirects_not_500(lyudy, gist):
    for kuka in ("1:pidrobleno", "x" * 500, "", "%D1%81%D0%BC"):
        gist.cookies.set(KIT.session_cookie, kuka)
        assert gist.get("/zavdannya").status_code == 303


def test_deactivated_user_loses_access_immediately(lyudy, gist, engine):
    uviity(gist, "halyna")
    with Session(engine) as s:
        u = s.get(User, 4)
        u.active = False
        s.add(u)
        s.commit()
    assert gist.get("/zavdannya").status_code == 303


def test_login_rotates_session(lyudy, gist, engine):
    persha = uviity(gist).cookies.get(KIT.session_cookie)
    druha = uviity(gist).cookies.get(KIT.session_cookie)
    assert persha != druha
    with Session(engine) as s:
        rows = s.exec(select(authkit.AuthSession)).all()
        assert sum(1 for r in rows if r.revoked_at is None) == 1


# ── CSRF ─────────────────────────────────────────────────────────────────────

def test_post_without_csrf_token_is_403(lyudy, gist):
    gist.get("/login")
    r = gist.post("/login", data={"login": "marko", "password": PAROL})
    assert r.status_code == 403


def test_post_with_wrong_csrf_token_is_403(lyudy, gist):
    gist.get("/login")
    r = gist.post("/login", data={"login": "marko", "password": PAROL,
                                  "csrf_token": "x" * 32})
    assert r.status_code == 403


def test_csrf_cross_origin_request_is_403(lyudy, gist):
    tok = token(gist)
    r = gist.post("/login", data={"login": "marko", "password": PAROL, "csrf_token": tok},
                  headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_authenticated_post_needs_csrf(lyudy, gist):
    uviity(gist)
    r = gist.post("/logout")            # session cookie alone is not enough
    assert r.status_code == 403


def test_csrf_header_is_accepted_for_scripts(lyudy, gist):
    tok = token(gist)
    r = gist.post("/login", data={"login": "marko", "password": PAROL},
                  headers={"X-CSRF-Token": tok})
    assert r.status_code == 303


def test_every_post_form_carries_csrf_field():
    """Static guard: no POST form in any template is missing the hidden field."""
    import re
    from pathlib import Path
    koren = Path(__file__).resolve().parents[1] / "templates_v2"
    bez = []
    for f in koren.rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r"<form\b[^>]*method=\"post\"[^>]*>", t, re.I | re.S):
            if 'name="csrf_token"' not in t[m.end():m.end() + 200]:
                bez.append(f"{f.name}:{t[:m.start()].count(chr(10)) + 1}")
    assert not bez, f"POST forms without CSRF field: {bez}"


# ── rate limiting ────────────────────────────────────────────────────────────

def test_login_is_locked_after_repeated_failures(lyudy, gist):
    for _ in range(KIT.max_failures):
        assert uviity(gist, "marko", "wrong").status_code == 401
    r = uviity(gist, "marko", PAROL)                # even the right password now waits
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert KIT.session_cookie not in r.cookies


def test_lockout_policy_pair_ip_login():
    """5 failures lock the (IP, login) pair only; a shared proxy IP or a victim's login is
    locked only by much larger volumes (30 per IP, 50 per login)."""
    LIMITER.reset_all()
    for _ in range(KIT.max_failures):
        LIMITER.failure(LIMITER.keys("10.0.0.1", "marko"))
    assert LIMITER.retry_after(LIMITER.keys("10.0.0.1", "marko")) > 0          # the pair
    assert LIMITER.retry_after(LIMITER.keys("10.0.0.1", "halyna")) == 0        # same IP, other login
    assert LIMITER.retry_after(LIMITER.keys("10.0.0.2", "marko")) == 0         # other IP, same login
    LIMITER.reset_all()
    for i in range(KIT.max_failures_ip):
        LIMITER.failure(LIMITER.keys("10.0.0.3", f"user{i}"))
    assert LIMITER.retry_after(LIMITER.keys("10.0.0.3", "fresh")) > 0          # spraying from one IP
    LIMITER.reset_all()
    for i in range(KIT.max_failures_login):
        LIMITER.failure(LIMITER.keys(f"10.1.{i}.1", "marko"))
    assert LIMITER.retry_after(LIMITER.keys("10.9.9.9", "marko")) > 0          # distributed guessing


def test_oversized_bodies_are_rejected_before_parsing(gist):
    big = b"x" * (KIT.small_body + 1)
    r = gist.post("/login", content=big, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    r = gist.post("/csp-report", content=big, headers={"Content-Type": "application/csp-report"})
    assert r.status_code == 413
    r = gist.post("/csp-report", content=b'{"csp-report": {"violated-directive": "style-src-attr"}}',
                  headers={"Content-Type": "application/csp-report"})
    assert r.status_code == 204


def test_health_reveals_nothing_outside_dev(gist, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "ENV", "demo")
    assert gist.get("/health").json() == {"ok": True}


# ── headers ──────────────────────────────────────────────────────────────────

def test_security_headers_present(lyudy, gist):
    r = gist.get("/login")
    h = r.headers
    csp = h["content-security-policy"]
    assert "script-src 'self' 'nonce-" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "same-origin"


def test_templates_have_no_inline_handlers_or_unnonced_scripts():
    import re
    from pathlib import Path
    koren = Path(__file__).resolve().parents[1] / "templates_v2"
    bidy = []
    for f in koren.rglob("*.html"):
        t = f.read_text(encoding="utf-8")
        if re.search(r"\son[a-z]+\s*=\s*[\"']", t):
            bidy.append(f"{f.name}: inline event handler")
        if re.search(r"\sstyle\s*=\s*[\"']", t):
            bidy.append(f"{f.name}: inline style attribute (blocked by the CSP)")
        for m in re.finditer(r"<script\b([^>]*)>", t):
            attrs = m.group(1)
            if "src=" not in attrs and "application/json" not in attrs and "nonce=" not in attrs:
                bidy.append(f"{f.name}: inline <script> without nonce")
    assert not bidy, bidy


def test_openapi_docs_closed_outside_dev(lyudy, gist):
    from app import main
    if main._DOCS:
        pytest.skip("dev environment exposes /docs by design")
    assert gist.get("/docs").status_code == 404
    assert gist.get("/openapi.json").status_code == 404


def test_language_switch_rejects_open_redirect(gist):
    for zla in ("https://evil.example/", "//evil.example", "/\\evil.example"):
        r = gist.get("/lang/uk", params={"next": zla})
        assert r.headers["location"] == "/"
    assert gist.get("/lang/uk", params={"next": "/zavdannya"}).headers["location"] == "/zavdannya"


def test_oversized_chunked_body_is_413_not_500(gist):
    """No Content-Length: the guard counts streamed bytes; the error must not surface as a 500."""
    def body():
        for _ in range(4):
            yield b"x" * (KIT.small_body // 2)
    r = gist.post("/csp-report", content=body(), headers={"Content-Type": "application/csp-report"})
    assert r.status_code == 413


def test_oversized_chunked_form_body_is_413(lyudy, gist):
    """Routes with Form parameters parse the body inside FastAPI: still 413, never 400/500."""
    tok = token(gist)

    def body():
        yield f"csrf_token={tok}&login=".encode()
        for _ in range(4):
            yield b"x" * (KIT.small_body // 2)
    r = gist.post("/login", content=body(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_per_login_lock_does_not_block_a_known_client():
    """A distributed burst locks the login for strangers, not for a client that signed in before."""
    LIMITER.reset_all()
    LIMITER.success(LIMITER.keys("10.5.5.5", "marko"))
    for i in range(KIT.max_failures_login):
        LIMITER.failure(LIMITER.keys(f"10.2.{i}.1", "marko"))
    assert LIMITER.retry_after(LIMITER.keys("10.9.9.9", "marko")) > 0       # stranger: locked
    assert LIMITER.retry_after(LIMITER.keys("10.5.5.5", "marko")) == 0      # owner's client: not
    LIMITER.reset_all()


def test_expired_session_post_is_redirected_with_303(gist):
    """307 would re-send the form (or a whole upload) to /login."""
    tok = token(gist)
    r = gist.post("/logout/everywhere", data={"csrf_token": tok})
    assert r.status_code == 303 and r.headers["location"].endswith("/login")
