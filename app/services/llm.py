# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Vendor-agnostic LLM-клієнт (ТЗ §6, п.3–4).

Один інтерфейс `complete()` поверх OpenAI-сумісного HTTP. Провайдер — з ENV,
код не змінюється. Дефолт `stub` означає: LLM немає, працює детермінований
екстрактор — і система від цього не зупиняється.

Без сторонніх залежностей (urllib), щоб не тягнути SDK заради одного виклику.
"""
import json
import urllib.error
import urllib.request

from .. import config


class LLMUnavailable(Exception):
    """LLM вимкнено, недоступне або відповіло помилкою. Не фатально."""


def available() -> bool:
    return config.LLM_PROVIDER != "stub" and bool(config.LLM_API_KEY)


def complete(system: str, user: str, timeout: int | None = None) -> dict:
    """Повертає розпарсений JSON. Кидає LLMUnavailable — і викликач іде у fallback."""
    if not available():
        raise LLMUnavailable(
            "LLM вимкнено (LLM_PROVIDER=stub або немає LLM_API_KEY) — "
            "працює детермінований екстрактор")

    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    body = {
        "model": config.LLM_MODEL,
        "temperature": 0,                       # витяг даних — не творчість
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    headers = {
        "Content-Type": "application/json",
        # Cloudflare перед деякими провайдерами (напр. Groq) блокує дефолтний
        # "Python-urllib" і віддає 403/1010 — грабля sto_v2.
        "User-Agent": "Mozilla/5.0 (compatible; ofis/1.0)",
        "Authorization": f"Bearer {config.LLM_API_KEY}",
    }
    if not url.startswith("https://") and not url.startswith("http://localhost"):
        raise LLMUnavailable("LLM_BASE_URL must be https:// (or http://localhost for a self-hosted model)")
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),  # noqa: S310 — scheme checked above  # nosec B310
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.LLM_TIMEOUT) as r:  # noqa: S310  # nosec B310
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        raise LLMUnavailable(f"немає звʼязку з LLM: {e}") from e
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"провайдер віддав не JSON: {e}") from e

    try:
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise LLMUnavailable(f"відповідь не розібралась: {e}") from e
