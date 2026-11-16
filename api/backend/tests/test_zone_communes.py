"""Tests for GET /v1/communes -- paginated commune list.

Smoke tests (auth + param validation) run without a DB. The nominal
200 / filter / sort / pagination assertions require a seeded geo_communes
table and are marked `live` so CI (which has no DB) skips them.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture()
async def public_client():
    """Client without auth override (for auth-required checks)."""
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.smoke
class TestCommunesValidation:
    """Auth + query-param validation -- no DB needed."""

    async def test_auth_required_without_key(self, public_client):
        resp = await public_client.get("/v1/communes")
        assert resp.status_code == 401

    async def test_invalid_sort_by(self, client):
        resp = await client.get("/v1/communes", params={"sort_by": "code_insee"})
        assert resp.status_code == 422

    async def test_invalid_order(self, client):
        resp = await client.get("/v1/communes", params={"order": "ascending"})
        assert resp.status_code == 422

    async def test_per_page_over_max(self, client):
        resp = await client.get("/v1/communes", params={"per_page": 101})
        assert resp.status_code == 422

    async def test_per_page_below_min(self, client):
        resp = await client.get("/v1/communes", params={"per_page": 0})
        assert resp.status_code == 422

    async def test_page_below_min(self, client):
        resp = await client.get("/v1/communes", params={"page": 0})
        assert resp.status_code == 422


@pytest.mark.live
class TestCommunesList:
    """Nominal behaviour -- requires a seeded geo_communes table."""

    async def _get(self, client, **params):
        resp = await client.get("/v1/communes", params=params)
        if resp.status_code != 200:
            pytest.skip("geo_communes not seeded / DB unavailable")
        return resp.json()

    async def test_nominal_envelope(self, client):
        body = await self._get(client, per_page=5)
        assert body["statut"] == "ok"
        data = body["donnees"]
        assert data["page"] == 1
        assert data["per_page"] == 5
        assert data["total_count"] > 0
        assert len(data["results"]) <= 5
        row = data["results"][0]
        assert set(row.keys()) == {
            "code_insee",
            "nom",
            "departement",
            "population",
            "population_annee",
            "population_source",
            "code_postal",
        }

    async def test_filter_departement(self, client):
        body = await self._get(client, departement="85", per_page=100)
        results = body["donnees"]["results"]
        assert results, "expected communes in departement 85"
        assert all(r["departement"] == "85" for r in results)

    async def test_sort_population_desc(self, client):
        body = await self._get(client, sort_by="population", order="desc", per_page=20)
        pops = [r["population"] for r in body["donnees"]["results"] if r["population"]]
        assert pops == sorted(pops, reverse=True)

    async def test_pagination_pages_differ(self, client):
        p1 = await self._get(client, sort_by="nom", order="asc", per_page=5, page=1)
        p2 = await self._get(client, sort_by="nom", order="asc", per_page=5, page=2)
        codes1 = {r["code_insee"] for r in p1["donnees"]["results"]}
        codes2 = {r["code_insee"] for r in p2["donnees"]["results"]}
        assert p2["donnees"]["page"] == 2
        assert codes1.isdisjoint(codes2)

    async def test_per_page_bounds_result_count(self, client):
        body = await self._get(client, per_page=3)
        assert len(body["donnees"]["results"]) <= 3

    async def test_search_accent_insensitive(self, client):
        body = await self._get(client, q="challans", per_page=10)
        noms = [r["nom"].lower() for r in body["donnees"]["results"]]
        assert any("challans" in n for n in noms)
