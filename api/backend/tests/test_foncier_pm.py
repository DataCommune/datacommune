"""Tests for foncier_pm (MAJIC personnes morales) endpoint."""

import pytest

from app.foncier_pm.schemas import ResultatFoncierPm

# ── Unit tests ──────────────────────────────────────────────────────


class TestResultatFoncierPmSchema:
    """Unit tests for the ResultatFoncierPm Pydantic model."""

    @pytest.mark.unit
    def test_resultat_schema_valid(self):
        """Full record with all fields populated."""
        obj = ResultatFoncierPm(
            code_parcelle="75101000AB0001",
            code_commune="75056",
            nom_commune="Paris",
            adresse="12 Rue de Rivoli",
            contenance_parcelle=2500,
            nature_culture="S",
            code_droit="P",
            numero_siren="552032534",
            groupe_personne=1,
            forme_juridique_abregee="SA",
            denomination="SOCIETE EXEMPLE SAS",
        )
        assert obj.code_parcelle == "75101000AB0001"
        assert obj.code_commune == "75056"
        assert obj.contenance_parcelle == 2500
        assert obj.denomination == "SOCIETE EXEMPLE SAS"

    @pytest.mark.unit
    def test_resultat_schema_optional_fields(self):
        """Minimal record — only required fields."""
        obj = ResultatFoncierPm(
            code_parcelle="01001000AA0001",
            code_commune="01001",
        )
        assert obj.nom_commune is None
        assert obj.adresse is None
        assert obj.contenance_parcelle is None
        assert obj.numero_siren is None
        assert obj.denomination is None

    @pytest.mark.unit
    def test_siren_varchar(self):
        """SIREN with letter prefix is valid (VARCHAR column)."""
        obj = ResultatFoncierPm(
            code_parcelle="01001000AA0001",
            code_commune="01001",
            numero_siren="U123456789",
        )
        assert obj.numero_siren == "U123456789"


# ── Smoke tests (ASGI transport, no DB required for 422) ────────────


@pytest.mark.smoke
class TestFoncierPmSmoke:
    """Smoke tests hitting the ASGI app."""

    async def test_foncier_pm_no_params_422(self, client):
        """GET /v1/foncier-pm without params -> 422."""
        resp = await client.get("/v1/foncier-pm")
        assert resp.status_code == 422

    async def test_foncier_pm_code_insee(self, client):
        """GET /v1/foncier-pm?code_insee=75056 -> 200 structure."""
        resp = await client.get(
            "/v1/foncier-pm",
            params={"code_insee": "75056"},
        )
        # 200 (data or empty) or 404 (no parcelle found for GPS)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert "donnees" in body
            assert "pagination" in body
            assert "meta" in body
