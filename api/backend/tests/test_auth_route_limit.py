"""C45 (Ianis, 2026-10-10, « Oui, 10/min sur la connexion »): 10 a minute per IP.

The exposed authentication routes (login, account creation, e-mail verification,
password reset, password change, key by e-mail) share one bucket per IP, apart
from the navigation ceiling of C36 (120 a minute), which does not change. The
10th request of the minute is admitted, the 11th refused (429), whatever the
payload: an invalid one counts as much as a password guess. Real limiter, real
middleware; only the database is replaced.

The bucket is per IP (C52, A1): one IP that saturates its bucket never blocks
another, whether the IP is the peer (direct mode) or the forwarded one behind a
trusted proxy (``trust_proxy_headers``); an untrusted ``X-Forwarded-For`` never
opens a new bucket.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.rate_limit import AUTH_BUCKET_PREFIX, _buckets

pytestmark = pytest.mark.unit

AUTH_LIMIT_PER_MINUTE = 10
NAVIGATION_CEILING_PER_MINUTE = 120
# Valid JSON, refused by the schema: no account is ever looked up.
INVALID_LOGIN = {"email": "pas-un-email", "password": ""}

# The other exposed authentication routes, each with a payload of its schema.
OTHER_AUTH_ROUTES = [
    ("POST", "/auth/register", {"email": "pas-un-email", "password": ""}),
    ("GET", "/auth/verify?token=invalide", None),
    ("POST", "/auth/resend-verification", {"email": "pas-un-email"}),
    ("POST", "/auth/forgot-password", {"email": "pas-un-email"}),
    ("POST", "/auth/reset-password", {"token": "invalide", "new_password": ""}),
    ("POST", "/auth/quick-key", {"email": "pas-un-email"}),
    ("PUT", "/auth/password", {"current_password": "", "new_password": ""}),
]


async def _mock_db():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    yield session


@pytest.fixture()
async def client():
    app.dependency_overrides[get_db] = _mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _logins(client: AsyncClient, n: int) -> list[int]:
    statuses = []
    for _ in range(n):
        response = await client.post("/auth/login", json=INVALID_LOGIN)
        statuses.append(response.status_code)
    return statuses


@pytest.mark.asyncio
async def test_the_10th_login_of_the_minute_passes_and_the_11th_is_refused(client):
    assert await _logins(client, AUTH_LIMIT_PER_MINUTE) == [422] * AUTH_LIMIT_PER_MINUTE

    refused = await client.post("/auth/login", json=INVALID_LOGIN)

    assert refused.status_code == 429
    assert refused.json()["title"] == "Trop de demandes"
    assert int(refused.headers["Retry-After"]) > 0
    assert refused.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_password_guesses_count_like_invalid_payloads(client):
    with (
        patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("app.auth.router.verify_password", return_value=False),
    ):
        statuses = []
        for _ in range(AUTH_LIMIT_PER_MINUTE + 1):
            response = await client.post(
                "/auth/login",
                json={"email": "personne@example.com", "password": "mauvais"},
            )
            statuses.append(response.status_code)

    assert statuses == [401] * AUTH_LIMIT_PER_MINUTE + [429]


@pytest.mark.parametrize(("method", "path", "payload"), OTHER_AUTH_ROUTES)
@pytest.mark.asyncio
async def test_every_exposed_authentication_route_shares_the_bucket(
    client, method, path, payload
):
    before = await client.request(method, path, json=payload)
    assert before.status_code != 429
    assert await _logins(client, AUTH_LIMIT_PER_MINUTE - 1) == [422] * (
        AUTH_LIMIT_PER_MINUTE - 1
    )

    refused = await client.request(method, path, json=payload)

    assert refused.status_code == 429


@pytest.mark.asyncio
async def test_the_limit_leaves_navigation_and_the_session_routes_alone(client):
    assert settings.rate_limit_per_ip == NAVIGATION_CEILING_PER_MINUTE
    await _logins(client, AUTH_LIMIT_PER_MINUTE + 1)

    assert (await client.get("/billing/plans")).status_code == 200
    # Reading the session is not an authentication attempt.
    assert (await client.get("/auth/me")).status_code == 401
    assert (await client.post("/auth/logout")).status_code == 200


# --- Isolation per IP (C52, A1) -------------------------------------------------

# Documentation addresses (RFC 5737): peers, and IPs forwarded by a proxy.
PAIR_A = "203.0.113.1"
PAIR_B = "203.0.113.2"
PROXY = "203.0.113.10"
TRANSMISE_A = "198.51.100.1"
TRANSMISE_B = "198.51.100.2"


@asynccontextmanager
async def _client_from(peer_ip: str):
    """A client whose requests come from the peer *peer_ip*."""
    app.dependency_overrides[get_db] = _mock_db
    transport = ASGITransport(app=app, client=(peer_ip, 50000))
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def _login(client: AsyncClient, headers: dict[str, str] | None = None) -> int:
    response = await client.post("/auth/login", json=INVALID_LOGIN, headers=headers)
    return response.status_code


@pytest.mark.asyncio
async def test_one_saturated_peer_ip_never_blocks_another(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    async with _client_from(PAIR_A) as a, _client_from(PAIR_B) as b:
        assert await _logins(a, AUTH_LIMIT_PER_MINUTE) == [422] * (
            AUTH_LIMIT_PER_MINUTE
        )
        assert await _login(a) == 429

        assert await _login(b) == 422
        assert await _login(a) == 429

    assert f"{AUTH_BUCKET_PREFIX}{PAIR_A}" in _buckets
    assert f"{AUTH_BUCKET_PREFIX}{PAIR_B}" in _buckets


@pytest.mark.asyncio
async def test_behind_a_trusted_proxy_the_forwarded_ip_owns_the_bucket(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    via_a = {"X-Forwarded-For": f"{TRANSMISE_A}, {PROXY}"}
    async with _client_from(PROXY) as proxy:
        for _ in range(AUTH_LIMIT_PER_MINUTE):
            assert await _login(proxy, via_a) == 422
        assert await _login(proxy, via_a) == 429

        # Another forwarded IP through the same proxy is admitted.
        assert await _login(proxy, {"X-Forwarded-For": TRANSMISE_B}) == 422
        # CF-Connecting-IP is read first: it names the bucket, whatever the
        # X-Forwarded-For that comes with it.
        assert await _login(proxy, {"CF-Connecting-IP": TRANSMISE_A}) == 429
        assert (
            await _login(
                proxy,
                {"CF-Connecting-IP": "198.51.100.3", "X-Forwarded-For": TRANSMISE_A},
            )
            == 422
        )

    assert f"{AUTH_BUCKET_PREFIX}{PROXY}" not in _buckets


@pytest.mark.asyncio
async def test_an_untrusted_forwarded_header_opens_no_new_bucket(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    async with _client_from(PAIR_A) as a:
        statuts = [
            await _login(a, {"X-Forwarded-For": f"198.51.100.{index}"})
            for index in range(1, AUTH_LIMIT_PER_MINUTE + 2)
        ]

    assert statuts == [422] * AUTH_LIMIT_PER_MINUTE + [429]
    assert [cle for cle in _buckets if cle.startswith(AUTH_BUCKET_PREFIX)] == [
        f"{AUTH_BUCKET_PREFIX}{PAIR_A}"
    ]
