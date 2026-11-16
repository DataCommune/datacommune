"""CORE-01: CORS wildcard + allow_credentials must refuse to boot (fail-fast).

Before this fix, a misconfigured CORS_ORIGINS=["*"] in production only
produced a startup warning while Starlette reflected any Origin header with
Access-Control-Allow-Credentials: true — a generalized CSRF on the JWT
session cookie. The boot must now fail loudly, aligned with the existing
fail-fast policy of config.py.
"""

import pytest

from app.config import settings
from app.main import app, lifespan


@pytest.mark.unit
async def test_boot_refused_on_cors_wildcard(monkeypatch):
    """lifespan must raise RuntimeError when CORS_ORIGINS contains '*'."""
    monkeypatch.setattr(settings, "cors_origins", ["*"])
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        async with lifespan(app):
            pass


@pytest.mark.unit
async def test_boot_allowed_with_explicit_origins(monkeypatch):
    """Explicit origins must not trigger the fail-fast (reaches DB check)."""
    monkeypatch.setattr(
        settings,
        "cors_origins",
        ["https://datacommune.fr", "https://dev.datacommune.fr"],
    )
    with pytest.raises(Exception) as exc_info:
        # No local database in unit tests: the lifespan proceeds past the
        # CORS gate and fails later on the DB connectivity check — which is
        # the proof the wildcard gate did not fire. Locale-safe: we assert
        # the failure is NOT the CORS RuntimeError, whatever the OS message.
        async with lifespan(app):
            pass
    assert not isinstance(exc_info.value, RuntimeError)
