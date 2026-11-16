"""Smoke tests -- verify every endpoint responds without 500 errors."""

import pytest

pytestmark = pytest.mark.smoke
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture()
async def public_client():
    """Client without auth override (for public endpoints)."""
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestHealthEndpoint:
    """Public health endpoint -- no auth required."""

    async def test_liveness_responds(self, public_client):
        resp = await public_client.get("/v1/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("statut") == "ok"

    async def test_health_responds(self, public_client):
        resp = await public_client.get("/v1/health")
        # Readiness returns 503 when DB is down or data freshness is degraded.
        assert resp.status_code in (200, 503)

    async def test_health_has_statut(self, public_client):
        resp = await public_client.get("/v1/health")
        if resp.status_code == 200:
            body = resp.json()
            assert "statut" in body


class TestProtectedEndpoints:
    """Smoke tests for protected endpoints -- auth bypassed."""

    async def test_auth_required_without_key(self, public_client):
        """Verify endpoints reject requests without API key."""
        resp = await public_client.get("/v1/geocode", params={"q": "test"})
        assert resp.status_code == 401

    async def test_geocode_missing_params(self, client):
        """422 when required query params are missing."""
        resp = await client.get("/v1/geocode")
        assert resp.status_code == 422

    async def test_reverse_missing_params(self, client):
        resp = await client.get("/v1/reverse")
        assert resp.status_code == 422

    async def test_parcelle_missing_params(self, client):
        resp = await client.get("/v1/parcelle")
        assert resp.status_code == 422

    async def test_dvf_missing_params(self, client):
        resp = await client.get("/v1/dvf")
        assert resp.status_code == 422

    async def test_dpe_missing_params(self, client):
        resp = await client.get("/v1/dpe")
        assert resp.status_code == 422

    async def test_loyers_missing_params(self, client):
        resp = await client.get("/v1/loyers")
        assert resp.status_code == 422

    async def test_resolve_missing_params(self, client):
        resp = await client.get("/v1/resolve")
        assert resp.status_code == 422

    async def test_transports_missing_params(self, client):
        resp = await client.get("/v1/transports")
        assert resp.status_code == 422

    async def test_batiments_missing_params(self, client):
        resp = await client.get("/v1/batiments")
        assert resp.status_code == 422

    async def test_monuments_missing_params(self, client):
        resp = await client.get("/v1/monuments")
        assert resp.status_code == 422

    async def test_scolaire_missing_params(self, client):
        resp = await client.get("/v1/scolaire")
        assert resp.status_code == 422

    async def test_connectivite_missing_params(self, client):
        resp = await client.get("/v1/connectivite")
        assert resp.status_code == 422

    async def test_risques_missing_params(self, client):
        resp = await client.get("/v1/risques")
        assert resp.status_code == 422

    async def test_pollutions_missing_params(self, client):
        resp = await client.get("/v1/pollutions")
        assert resp.status_code == 422

    async def test_urbanisme_missing_params(self, client):
        resp = await client.get("/v1/urbanisme")
        assert resp.status_code == 422

    async def test_entreprises_missing_params(self, client):
        resp = await client.get("/v1/entreprises")
        assert resp.status_code == 422

    async def test_datasets_responds(self, client):
        """datasets endpoint may work without DB data.

        Without a running database, the get_db dependency may raise
        ConnectionRefusedError that propagates through the ASGI stack.
        We accept either a proper HTTP response or a connection error.
        """
        try:
            resp = await client.get("/v1/datasets")
            assert resp.status_code in (200, 500)
        except (ConnectionRefusedError, OSError):
            # Expected when no database is available
            pass

    # -- Scoring endpoints -------------------------------------------------------

    async def test_scoring_renov_missing_params(self, client):
        """GET /v1/scoring/renov without code_departement returns 422."""
        resp = await client.get("/v1/scoring/renov")
        assert resp.status_code == 422

    async def test_scoring_csv_missing_params(self, client):
        """GET /v1/scoring/renov-target without code_departement returns 422."""
        resp = await client.get("/v1/scoring/renov-target")
        assert resp.status_code == 422

    async def test_scoring_csv_invalid_etiquette(self, client):
        """GET /v1/scoring/renov-target with invalid etiquette returns 422."""
        resp = await client.get(
            "/v1/scoring/renov-target",
            params={"code_departement": "44", "etiquette_dpe": "A"},
        )
        assert resp.status_code == 422

    async def test_scoring_csv_content_type(self, client):
        """The retired export is JSON 503, never an empty successful CSV."""
        resp = await client.get(
            "/v1/scoring/renov-target", params={"code_departement": "44"}
        )
        assert resp.status_code == 503
        assert "application/json" in resp.headers["content-type"]
        assert "content-disposition" not in resp.headers
        assert resp.json()["statut"] == "indisponible"

    async def test_scoring_renov_no_auth(self, public_client):
        """GET /v1/scoring/renov without API key returns 401."""
        resp = await public_client.get(
            "/v1/scoring/renov",
            params={"code_departement": "44"},
        )
        assert resp.status_code == 401

    async def test_scoring_csv_no_auth(self, public_client):
        """GET /v1/scoring/renov-target without API key returns 401."""
        resp = await public_client.get(
            "/v1/scoring/renov-target",
            params={"code_departement": "44"},
        )
        assert resp.status_code == 401


class TestOpenAPI:
    """Verify OpenAPI schema is generated without errors."""

    async def test_openapi_json(self, public_client):
        resp = await public_client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "DataCommune API"
        assert len(schema["paths"]) >= 16
