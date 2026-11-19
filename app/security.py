"""App-level glue over :mod:`app.authkit` (shared with the Grants portal).

Passwords are argon2id; legacy ``sha256(SECRET + pw)`` hashes are upgraded on
the first successful login. Sessions are server-side rows (see ``AuthSession``).
"""
from . import authkit, config

KIT = authkit.KitConfig(
    app_slug="ofis",
    secret=config.SECRET,
    secure_cookies=config.SECURE_COOKIES,
    session_ttl=config.SESSION_TTL,
    idle_timeout=config.SESSION_IDLE,
    # OFIS has no token API: every route, /api/* included, authenticates with the
    # session cookie, so only the CSP report sink is exempt from CSRF.
    csrf_exempt_prefixes=("/csp-report",),
    # map tiles are the only third-party resource the UI loads
    csp_extra={"img-src": ["https://*.tile.openstreetmap.org"]},
)
LIMITER = authkit.LoginLimiter(KIT)


def hash_pw(password: str) -> str:
    """argon2id hash for a new password."""
    return authkit.hash_password(password)


def check_pw(stored: str, password: str) -> tuple[bool, str | None]:
    return authkit.verify_password(stored, password, legacy_secret=config.SECRET)

