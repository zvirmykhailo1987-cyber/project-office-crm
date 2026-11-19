# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Demo mode: the public demo accounts are listed on the sign-in page and cannot be taken
away from the next visitor. Production behaviour is pinned by the same tests run with
demo mode off.
"""
import html

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import authkit, config
from app.security import KIT, LIMITER
from app.web.auth import DEMO_LOCKED, demo_accounts, limiter_keys

NOTICE_UK = "Демо-версія: усі дані вигадані й скидаються щоночі о 04:00 (CET)."
NOTICE_EN = "Demo version: all data is fictitious and resets every night at 04:00 CET."
LOCKED_EN = "Demo accounts are shared and cannot be changed in the demo. Data resets every night at 04:00 CET."


@pytest.fixture()
def demo_db(engine, tmp_path, monkeypatch):
    from app import db, seed_demo
    monkeypatch.setattr(config, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(config, "CHAT_DIR", tmp_path / "chat")
    db.bootstrap(engine)
    with Session(engine) as s:
        seed_demo.seed(s)
    return engine


@pytest.fixture()
def demo_on(monkeypatch):
    monkeypatch.setattr(config, "IS_DEMO", True)


@pytest.fixture()
def browser(demo_db):
    from app.db import get_session
    from app.main import app

    def _sess():
        with Session(demo_db) as sess:
            yield sess

    app.dependency_overrides[get_session] = _sess
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()


def _sign_in(c: TestClient, login="marko", password="marko-demo-2026"):
    c.get("/login")
    return c.post("/login", data={"login": login, "password": password,
                                  "csrf_token": c.cookies.get(KIT.csrf_cookie)})


# ── sign-in page ─────────────────────────────────────────────────────────────

def test_demo_accounts_come_from_the_seeder(demo_on):
    from app.models.people import ROLES
    from app.seed_demo import DEMO_USERS, demo_password
    got = demo_accounts()
    assert [a[0] for a in got] == list(DEMO_USERS)
    for login, rol, parol in got:
        assert parol == demo_password(login) and rol == ROLES[DEMO_USERS[login][1]]


def test_no_demo_accounts_outside_demo_mode():
    assert config.IS_DEMO is False
    assert demo_accounts() == []


def test_login_page_lists_every_demo_account_with_one_click_fill(demo_on, browser):
    page = browser.get("/login").text
    for login, _rol, parol in demo_accounts():
        assert f'data-login="{login}" data-parol="{parol}"' in page
        assert f"<code>{parol}</code>" in page
    assert NOTICE_UK in page
    assert "DEMO.md" not in page
    assert "onclick" not in page and "<style" not in page      # CSP: behaviour lives in app.js


def test_login_page_in_english(demo_on, browser):
    browser.cookies.set("lang", "en")
    page = html.unescape(browser.get("/login").text)
    assert "Demo accounts" in page and NOTICE_EN in page
    assert "Software development and analytics" in page and "Pick a role to fill in the form." in page


def test_login_page_lists_nothing_without_demo_mode(browser):
    page = browser.get("/login").text
    assert "demo-akaunt" not in page and "marko-demo-2026" not in page and NOTICE_UK not in page


def test_one_click_fill_script_targets_the_form_fields():
    js = (config.BASE_DIR / "static_v2" / "app.js").read_text(encoding="utf-8")
    assert '".demo-akaunt"' in js and '"l-login"' in js and '"l-parol"' in js


# ── in-app banner ────────────────────────────────────────────────────────────

def test_in_app_banner_says_data_resets_nightly(demo_on, browser):
    assert _sign_in(browser).status_code == 303
    page = browser.get("/").text
    assert 'class="k-banner demo-smuha"' in page and NOTICE_UK in page
    assert 'action="/logout/everywhere"' not in page           # the refused action is not offered


def test_no_banner_and_full_profile_menu_without_demo_mode(browser):
    assert _sign_in(browser).status_code == 303
    page = browser.get("/").text
    assert "demo-smuha" not in page and 'action="/logout/everywhere"' in page


# ── shared accounts cannot be taken away ─────────────────────────────────────

def test_logout_everywhere_is_refused_in_demo(demo_on, browser):
    other = TestClient(browser.app, follow_redirects=False)
    assert _sign_in(other).status_code == 303
    assert _sign_in(browser).status_code == 303
    browser.cookies.set("lang", "en")
    r = browser.post("/logout/everywhere", data={"csrf_token": browser.cookies.get(KIT.csrf_cookie)})
    assert r.status_code == 403
    assert LOCKED_EN in html.unescape(r.text)
    assert other.get("/").status_code == 200                    # the other visitor stays signed in


def test_logout_everywhere_still_works_outside_demo(browser):
    assert _sign_in(browser).status_code == 303
    r = browser.post("/logout/everywhere", data={"csrf_token": browser.cookies.get(KIT.csrf_cookie)})
    assert r.status_code == 303


def test_demo_message_is_translated():
    from app import i18n
    assert i18n.translate(DEMO_LOCKED, "en") == LOCKED_EN


def test_no_per_login_lock_in_demo(demo_on):
    keys = limiter_keys("10.0.0.1", "marko")
    assert not [k for k in keys if k.startswith("user:")]
    LIMITER.reset_all()
    for i in range(KIT.max_failures_login + 5):                # a distributed lock-out attempt
        LIMITER.failure(limiter_keys(f"10.3.{i}.1", "marko"))
    assert LIMITER.retry_after(limiter_keys("10.9.9.9", "marko")) == 0   # a new visitor gets in
    LIMITER.reset_all()


def test_per_login_lock_kept_outside_demo():
    assert any(k.startswith("user:") for k in limiter_keys("10.0.0.1", "marko"))


def test_demo_lockout_expires(demo_on, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(authkit.time, "monotonic", lambda: now[0])
    LIMITER.reset_all()
    keys = limiter_keys("10.4.4.4", "marko")
    for _ in range(KIT.max_failures):
        LIMITER.failure(keys)
    assert 0 < LIMITER.retry_after(keys) <= KIT.lockout + 1
    now[0] += KIT.lockout + 1
    assert LIMITER.retry_after(keys) == 0
    LIMITER.reset_all()


def test_demo_login_after_lock_from_another_ip_works(demo_on, browser):
    for _ in range(KIT.max_failures):
        LIMITER.failure(limiter_keys("testclient-other", "marko"))
    assert _sign_in(browser).status_code == 303
