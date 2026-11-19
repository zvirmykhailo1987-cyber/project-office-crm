# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Конфіг з ENV. У проді — fail-fast, якщо секрети не задані (ТЗ §8, O3).

Секрети НІКОЛИ не в git: локально — дефолти для розробки, у проді — `DATA_DIR/secrets.env`
або змінні оточення Coolify.
"""
import os
from pathlib import Path

ENV = os.getenv("OFIS_ENV", "production")   # fail-closed: dev/demo must be chosen explicitly            # dev | demo | production
IS_PROD = ENV == "production"
# Demo = the public showcase: synthetic data (`python -m app.seed_demo`), demo
# accounts allowed. Never combined with production.
IS_DEMO = ENV == "demo"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_secrets_file() -> None:
    """Підвантажує DATA_DIR/secrets.env у os.environ (не перетирає задане ззовні)."""
    p = DATA_DIR / "secrets.env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_secrets_file()

DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "ofis.sqlite"))
DOCS_DIR = Path(os.getenv("DOCS_DIR", DATA_DIR / "docs"))
DOCS_DIR.mkdir(parents=True, exist_ok=True)

# Вкладення чату лежать ОКРЕМО від реєстру документів: у чат летить усе підряд —
# скріншот, zip з базою, голосове, — і реєстр ділових документів від цього втратив би
# сенс. Важливий файл переноситься в документи окремою дією.
CHAT_DIR = Path(os.getenv("CHAT_DIR", DATA_DIR / "chat"))
CHAT_DIR.mkdir(parents=True, exist_ok=True)

PORT = int(os.getenv("PORT", "8030"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "50"))

SECRET = os.getenv("OFIS_SECRET", "" if IS_PROD else "dev-secret-not-for-production")
# Password of the first admin on an EMPTY database in production. There is no
# default: production refuses to start without it (see check_prod_ready).
BOOTSTRAP_PASSWORD = os.getenv("OFIS_BOOTSTRAP_PASSWORD", "")

# ── LLM (ТЗ §6) ──────────────────────────────────────────────────────────────
# Дефолт — stub: система працює БЕЗ жодного ключа, на детермінованому екстракторі.
# Провайдер вмикається змінною оточення, код не змінюється.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "stub")     # stub | groq | openai | selfhost
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

# Sessions (server-side, see app/authkit.py)
# Secure + __Host- cookies everywhere except local dev (browsers treat http://localhost as secure).
SECURE_COOKIES = ENV != "dev" or os.getenv("OFIS_SECURE_COOKIES") == "1"
SESSION_TTL = int(os.getenv("OFIS_SESSION_TTL", str(60 * 60 * 24 * 7)))   # absolute, 7 days
SESSION_IDLE = int(os.getenv("OFIS_SESSION_IDLE", str(60 * 60 * 8)))      # idle, 8 hours


def check_prod_ready() -> None:
    """Fail-fast: у проді не стартуємо з порожніми секретами (ТЗ §8, O3)."""
    if not IS_PROD:
        return
    missing = [k for k, v in {
        "OFIS_SECRET": SECRET,
    }.items() if not v]
    if missing:
        raise RuntimeError(
            "OFIS_ENV=production, але не задані секрети: " + ", ".join(missing)
            + ". Задайте їх у DATA_DIR/secrets.env або в змінних оточення."
        )
    if SECRET.startswith("dev-") or len(SECRET) < 32:
        raise RuntimeError("OFIS_SECRET must be a random value of at least 32 characters.")
    if IS_DEMO:
        raise RuntimeError("OFIS_ENV cannot be both demo and production.")
