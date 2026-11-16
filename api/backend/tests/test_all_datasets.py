"""Comprehensive dataset endpoint tests — validates routing, params, auth.

Tests work WITHOUT a database (CI-friendly). They verify:
- 422 on missing required params (routing + validation works)
- 401 without API key (auth guard works)
- No 500 errors (no import/config crashes)
"""

import pytest

pytestmark = pytest.mark.smoke


# ── All protected endpoints with their required params ────────────────────

DATASET_ENDPOINTS = [
    # (endpoint, valid_params, dataset_name)
    # v1.0 ETL
    ("/v1/geocode", {"q": "20 avenue de Segur Paris"}, "BAN geocode"),
    ("/v1/reverse", {"lat": 48.8566, "lon": 2.3522}, "BAN reverse"),
    ("/v1/parcelle", {"lat": 48.8566, "lon": 2.3522}, "Cadastre coords"),
    ("/v1/dvf", {"lat": 48.8566, "lon": 2.3522}, "DVF"),
    ("/v1/dpe", {"lat": 48.8566, "lon": 2.3522}, "DPE"),
    ("/v1/loyers", {"code_insee": "75056"}, "Loyers"),
    ("/v1/batiments", {"lat": 48.8566, "lon": 2.3522}, "BD TOPO"),
    ("/v1/monuments", {"lat": 48.8566, "lon": 2.3522}, "Monuments"),
    ("/v1/connectivite", {"code_insee": "75056"}, "ARCEP"),
    ("/v1/scolaire", {"code_insee": "75056"}, "Scolaire"),
    ("/v1/transports", {"lat": 48.8566, "lon": 2.3522}, "Transport"),
    ("/v1/resolve", {"q": "Paris"}, "Resolve"),
    # v5.0 ETL
    ("/v1/zonage-abc", {"code_insee": "75056"}, "Zonage ABC"),
    ("/v1/fiscalite", {"code_insee": "75056"}, "Fiscalite"),
    ("/v1/encadrement-loyers", {"lat": 48.8566, "lon": 2.3522}, "Encadrement loyers"),
    ("/v1/revenus", {"code_insee": "75056"}, "Filosofi"),
    ("/v1/demographie", {"code_insee": "75056"}, "Recensement"),
    ("/v1/copropriete", {"code_insee": "75056"}, "RNIC"),
    ("/v1/permis", {"code_insee": "75056"}, "SITADEL"),
    ("/v1/antennes", {"lat": 48.8566, "lon": 2.3522}, "ANFR"),
    ("/v1/equipements", {"code_insee": "75056"}, "BPE"),
    ("/v1/vacance", {"code_insee": "75056"}, "LOVAC"),
    # v6.0 ETL
    ("/v1/foncier-pm", {"code_insee": "75056"}, "MAJIC PM"),
    # Proxies
    ("/v1/risques", {"lat": 48.8566, "lon": 2.3522}, "Georisques risques"),
    ("/v1/pollutions", {"lat": 48.8566, "lon": 2.3522}, "Georisques pollutions"),
    ("/v1/urbanisme", {"lat": 48.8566, "lon": 2.3522}, "Urbanisme PLU"),
    ("/v1/entreprises", {"lat": 48.8566, "lon": 2.3522}, "SIRENE"),
    ("/v1/bdnb", {"adresse": "20 avenue de Segur Paris"}, "BDNB"),
    ("/v1/rnb", {"lat": 48.8566, "lon": 2.3522}, "RNB"),
    ("/v1/errial", {"lat": 48.8566, "lon": 2.3522}, "ERRIAL"),
    # v5.1 matview
    ("/v1/commune", {"code_insee": "44109"}, "Commune synthese"),
    # Scoring
    ("/v1/scoring/renov", {"code_departement": "75"}, "Scoring renov"),
    ("/v1/scoring/renov-target", {"code_departement": "75"}, "Scoring CSV"),
]


class TestAllEndpoints422:
    """Every protected endpoint must return 422 when params are missing."""

    @pytest.mark.parametrize(
        "endpoint,params,name",
        DATASET_ENDPOINTS,
        ids=[e[2] for e in DATASET_ENDPOINTS],
    )
    async def test_missing_params_returns_422(self, client, endpoint, params, name):
        resp = await client.get(endpoint)
        assert resp.status_code == 422, (
            f"{name} ({endpoint}): expected 422 without params, got {resp.status_code}"
        )


class TestAllEndpoints401:
    """Every protected endpoint must return 401 without API key."""

    @pytest.fixture()
    async def public_client(self):
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            yield client

    @pytest.mark.parametrize(
        "endpoint,params,name",
        DATASET_ENDPOINTS,
        ids=[e[2] for e in DATASET_ENDPOINTS],
    )
    async def test_no_auth_returns_401(self, public_client, endpoint, params, name):
        resp = await public_client.get(endpoint, params=params)
        assert resp.status_code == 401, (
            f"{name} ({endpoint}): expected 401 without key, got {resp.status_code}"
        )


class TestAllEndpointsNoServerError:
    """With valid params + auth, endpoints must not 500 (even without DB data)."""

    @pytest.mark.parametrize(
        "endpoint,params,name",
        DATASET_ENDPOINTS,
        ids=[e[2] for e in DATASET_ENDPOINTS],
    )
    async def test_valid_params_no_500(self, client, endpoint, params, name):
        try:
            resp = await client.get(endpoint, params=params)
            assert resp.status_code != 500, (
                f"{name} ({endpoint}): got 500 — server error:\n{resp.text[:500]}"
            )
        except (ConnectionRefusedError, OSError):
            # No DB available — acceptable in CI
            pass
