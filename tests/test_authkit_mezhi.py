# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Edges of the shared security kit (`app/authkit.py`) that the HTTP tests do not reach.

The kit is shared byte-for-byte with the Grants portal, so it is only tested here, never
changed. Each case is a guarantee: a wrong legacy password is refused, an idle session is
touched at most once a minute, old failures age out of the lockout window, an opaque
origin fails CSRF, a malformed Content-Length is a 413, and HSTS is sent over HTTPS.
"""
import asyncio
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import authkit
from app.models import User
from app.security import KIT, LIMITER


def test_starhyi_sha256_z_nevirnym_parolem_vidkhylyayetsya():
    stored = authkit.legacy_sha256("salt", "right-password")
    assert authkit.verify_password(stored, "right-password", legacy_secret="salt")[0] is True
    assert authkit.verify_password(stored, "wrong-password", legacy_secret="salt") == (False, None)


def test_polityka_novoho_parolya():
    assert authkit.password_problem("short1!") is not None
    assert authkit.password_problem("onlylowercaseletters") is not None
    assert authkit.password_problem("OnlyLettersMixedCase") is not None
    assert authkit.password_problem("letters-and-digits-42") is None


def test_sesiya_onovlyuyetsya_ne_chastishe_za_khvylynu(engine):
    with Session(engine) as s:
        s.add(User(id=1, name="Марко", login="marko", role="rozrobnyk"))
        s.commit()

        class _Req:
            headers = {"user-agent": "t"}
            client = type("C", (), {"host": "1.1.1.1"})()

        raw = authkit.create_session(s, KIT, 1, _Req())
        sess = s.exec(select(authkit.AuthSession)).one()
        recent = sess.last_seen_at
        assert authkit.resolve_session(s, KIT, raw).last_seen_at == recent     # no write

        sess.last_seen_at = authkit.utcnow() - timedelta(minutes=5)
        s.add(sess)
        s.commit()
        touched = authkit.resolve_session(s, KIT, raw)
        assert touched.last_seen_at > authkit.utcnow() - timedelta(seconds=5)
        authkit.revoke_session(s, "")                                  # empty token: no-op
        authkit.revoke_session(s, raw)
        authkit.revoke_session(s, raw)                                 # twice: still one revocation
        assert authkit.resolve_session(s, KIT, raw) is None


def test_stari_nevdachi_vypadayut_z_vikna(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(authkit.time, "monotonic", lambda: now[0])
    keys = LIMITER.keys("10.0.0.1", "marko")
    for _ in range(KIT.max_failures - 1):
        LIMITER.failure(keys)
    now[0] += KIT.failure_window + 1                       # the earlier failures age out
    LIMITER.failure(keys)
    assert LIMITER.retry_after(keys) == 0
    for _ in range(KIT.max_failures - 1):
        LIMITER.failure(keys)
    assert LIMITER.retry_after(keys) > 0                    # five inside one window: locked


@pytest.fixture()
def gist(engine):
    from app.db import get_session
    from app.main import app

    def _sess():
        with Session(engine) as sess:
            yield sess
    app.dependency_overrides[get_session] = _sess
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()


def test_csrf_vidkhylyaye_neprozoryi_origin(gist):
    gist.get("/login")
    tok = gist.cookies.get(KIT.csrf_cookie)
    r = gist.post("/logout", data={"csrf_token": tok}, headers={"Origin": "null"})
    assert r.status_code == 403
    r = gist.post("/logout", data={"csrf_token": tok}, headers={"Referer": "http://testserver/x"})
    assert r.status_code == 303                              # same origin through Referer: allowed


def test_zipsovanyi_content_length_tse_413(gist):
    r = gist.post("/csp-report", content=b"{}", headers={"Content-Length": "abc",
                                                         "Content-Type": "application/csp-report"})
    assert r.status_code == 413


def test_csp_zvit_iz_smittyam_ne_padaye(gist):
    r = gist.post("/csp-report", content=b"not json", headers={"Content-Type": "application/csp-report"})
    assert r.status_code == 204
    r = gist.post("/csp-report", content=b"[1, 2]", headers={"Content-Type": "application/csp-report"})
    assert r.status_code == 204


def test_inshi_pomylky_zastosunku_ne_maskuyutsya_pid_413():
    async def zlamanyi(scope, receive, send):
        raise RuntimeError("real bug")

    guard = authkit.BodySizeLimit(zlamanyi, KIT)
    scope = {"type": "http", "method": "POST", "path": "/x", "headers": []}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass
    with pytest.raises(RuntimeError, match="real bug"):
        asyncio.run(guard(scope, receive, send))


def test_hsts_lyshe_z_https_kukamy():
    cfg = authkit.KitConfig(app_slug="t", secret="x" * 40, secure_cookies=True)
    app = FastAPI()

    @app.get("/p")
    def _p():
        return {"ok": True}
    authkit.install(app, cfg)
    r = TestClient(app, base_url="https://testserver").get("/p")
    assert r.headers["strict-transport-security"].startswith("max-age=63072000")
    assert r.cookies.get("__Host-t_csrf") or "__Host-t_csrf" in r.headers.get("set-cookie", "")
