"""Registration closed by the API itself (05-07 lot 5), not only by the proxy.

The public stack closes /auth/register, /auth/quick-key and
/auth/resend-verification at Caddy (api/Caddyfile.public,
infra/public/tests/test-inscription-fermee.sh). With ``REGISTRATION_ENABLED=false``
the API answers 404 on the same three routes before reading the body or the
database, so a request that bypasses the proxy creates nothing either. The other
routes of /auth (login, reset) are not concerned.
"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import get_db
from app.main import app

EMAIL = "personne@example.invalid"
FERMEES = (
    ("/auth/register", {"email": EMAIL, "password": "a" * 24}),
    ("/auth/quick-key", {"email": EMAIL}),
    ("/auth/resend-verification", {"email": EMAIL}),
)
COMPOSE_PUBLIC = Path(__file__).resolve().parents[2] / "docker-compose.public.yml"


async def _db_interdite():
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=AssertionError("database read"))
    session.commit = AsyncMock(side_effect=AssertionError("database write"))
    yield session


@pytest.fixture()
async def client():
    app.dependency_overrides[get_db] = _db_interdite
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(("chemin", "corps"), FERMEES)
async def test_closed_registration_answers_404_without_touching_the_database(
    client, monkeypatch, chemin, corps
):
    monkeypatch.setattr(settings, "registration_enabled", False)

    reponse = await client.post(chemin, json=corps)

    assert reponse.status_code == 404


@pytest.mark.asyncio
async def test_closed_registration_leaves_login_alone(client, monkeypatch):
    monkeypatch.setattr(settings, "registration_enabled", False)

    reponse = await client.post("/auth/login", json={})

    # A malformed login is still a login: validated (422), never hidden by the 404.
    assert reponse.status_code == 422


def test_open_by_default_for_the_working_stacks():
    assert type(settings).model_fields["registration_enabled"].default is True


def test_the_public_stack_closes_registration_in_the_api():
    texte = COMPOSE_PUBLIC.read_text(encoding="utf-8")
    api = texte.split("\n  api:\n", 1)[1].split("\n  frontend:\n", 1)[0]
    assert 'REGISTRATION_ENABLED: "false"' in api
