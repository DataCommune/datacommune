"""Tests for /v1/resolve enrichment with connectivite + scolaire datasets.

Covers review #1 finding #19: _dispatch_db must thread nom_voie + numero from
BAN's reverse-geocoder into eligibilite_par_adresse and college_de_secteur.
When BAN lacks those fields, a machine-readable ADRESSE_INCOMPLETE warning is
attached to meta.avertissements instead of silently returning coarse data.

Tests use AsyncMock + dependency_overrides (Phase 15/16 pattern) — no real DB.
GPS mode (type=gps) bypasses the geocoder in _resolve_adresse.

Key patching strategy:
  - app.resolve.router.reverse_geocoder: for _resolve_gps address enrichment
  - app.resolve.router.parcelle_par_point: for _resolve_gps parcelle lookup
  - app.resolve.multi.reverse_geocoder: for pre-resolve in query_multiple_datasets
  - app.resolve.multi.eligibilite_par_adresse: dataset query (imported in multi)
  - app.resolve.multi.college_de_secteur: dataset query (imported in multi)
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import check_quota, check_quota_hybrid
from app.auth.models import ApiKeyInfo
from app.main import app
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import FULL_SCOPE

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _unit_catalog_and_logging(monkeypatch):
    # These dispatch tests use query doubles; real guards have PostGIS coverage.
    monkeypatch.setattr("app.resolve.multi.guard_publication", AsyncMock())
    monkeypatch.setattr(
        "app.middleware.RequestLoggingMiddleware._log_to_db", AsyncMock()
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_client() -> AsyncClient:
    """HTTP client with auth bypassed, no real DB needed."""
    from datetime import UTC, datetime

    fake = ApiKeyInfo(
        id=1,
        name="test-key",
        quota_monthly=999999,
        revoked=False,
        created_at=datetime.now(UTC),
    )
    app.dependency_overrides[check_quota] = lambda: fake
    app.dependency_overrides[check_quota_hybrid] = lambda: fake
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# GPS coordinates used across all tests.
# type=gps bypasses the geocoder (no real DB needed for initial resolution).
_LAT = 48.8692
_LON = 2.3315
_GPS_URL_BASE = f"/v1/resolve?type=gps&q={_LAT},{_LON}"


def _ban_row(
    code_commune: str = "75002",
    nom_voie: str | None = "rue de la paix",
    numero: str | None = "1",
) -> dict:
    """Build a minimal BAN reverse-geocoder row."""
    return {
        "code_commune": code_commune,
        "ban_id": f"{code_commune}_xxx",
        "nom_voie": nom_voie,
        "numero": numero,
        "latitude": _LAT,
        "longitude": _LON,
        "nom_commune": "Paris Test",
        "code_postal": "75000",
        "distance_m": 5.0,
        "rep": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_resolve_threads_voie_numero_into_connectivite():
    """When BAN returns nom_voie + numero, thread them into eligibilite_par_adresse.

    The resolve response must carry meta.avertissements == [].
    """
    row = _ban_row(nom_voie="rue de la paix", numero="1")

    async with _auth_client() as client:
        with (
            patch("app.resolve.router.reverse_geocoder", AsyncMock(return_value=[row])),
            patch("app.resolve.router.parcelle_par_point", AsyncMock(return_value=[])),
            patch("app.resolve.router.parcelles_proches", AsyncMock(return_value=[])),
            patch("app.resolve.multi.reverse_geocoder", AsyncMock(return_value=[row])),
            patch(
                "app.resolve.multi.eligibilite_par_adresse",
                AsyncMock(return_value=[]),
            ) as mock_elig,
        ):
            resp = await client.get(f"{_GPS_URL_BASE}&datasets=connectivite")

    assert resp.status_code == 200
    body = resp.json()

    donnees = body.get("donnees", [])
    assert donnees, f"resolve must return at least one result; body={body!r}"
    result = donnees[0]

    # avertissements must be empty — full address was available
    assert result.get("avertissements") == [], (
        f"expected empty avertissements, got {result.get('avertissements')!r}"
    )

    # nom_voie + numero must have been passed as kwargs
    mock_elig.assert_called_once()
    _, kwargs = mock_elig.call_args
    assert kwargs.get("nom_voie") == "rue de la paix", (
        f"expected nom_voie='rue de la paix', kwargs={kwargs!r}"
    )
    assert kwargs.get("numero") == "1", f"expected numero='1', kwargs={kwargs!r}"

    app.dependency_overrides.clear()


async def test_resolve_warns_on_incomplete_address_connectivite():
    """When BAN lacks nom_voie/numero, avertissements gets ADRESSE_INCOMPLETE.

    The warning must identify dataset='connectivite' and code='ADRESSE_INCOMPLETE'.
    """
    row = _ban_row(nom_voie=None, numero=None)

    async with _auth_client() as client:
        with (
            patch("app.resolve.router.reverse_geocoder", AsyncMock(return_value=[row])),
            patch("app.resolve.router.parcelle_par_point", AsyncMock(return_value=[])),
            patch("app.resolve.router.parcelles_proches", AsyncMock(return_value=[])),
            patch("app.resolve.multi.reverse_geocoder", AsyncMock(return_value=[row])),
            patch(
                "app.resolve.multi.eligibilite_par_adresse",
                AsyncMock(return_value=[]),
            ),
        ):
            resp = await client.get(f"{_GPS_URL_BASE}&datasets=connectivite")

    assert resp.status_code == 200
    body = resp.json()
    donnees = body.get("donnees", [])
    assert donnees, f"resolve must return results; body={body!r}"

    avs = donnees[0].get("avertissements", [])
    assert any(
        a["dataset"] == "connectivite" and a["code"] == "ADRESSE_INCOMPLETE"
        for a in avs
    ), f"expected ADRESSE_INCOMPLETE for connectivite, got {avs!r}"

    app.dependency_overrides.clear()


async def test_resolve_scolaire_same_pattern():
    """Symmetric test: college_de_secteur receives nom_voie + numero (as int).

    When BAN provides full address, no ADRESSE_INCOMPLETE warning is emitted.
    """
    row = _ban_row(code_commune="75004", nom_voie="rue de rivoli", numero="10")

    async with _auth_client() as client:
        with (
            patch("app.resolve.router.reverse_geocoder", AsyncMock(return_value=[row])),
            patch("app.resolve.router.parcelle_par_point", AsyncMock(return_value=[])),
            patch("app.resolve.router.parcelles_proches", AsyncMock(return_value=[])),
            patch("app.resolve.multi.reverse_geocoder", AsyncMock(return_value=[row])),
            patch(
                "app.resolve.multi.college_de_secteur",
                AsyncMock(return_value=[]),
            ) as mock_scolaire,
        ):
            resp = await client.get(f"{_GPS_URL_BASE}&datasets=scolaire")

    assert resp.status_code == 200
    body = resp.json()
    donnees = body.get("donnees", [])
    assert donnees, f"resolve must return results; body={body!r}"

    # No scolaire warning expected — address is complete
    avs = donnees[0].get("avertissements", [])
    assert not any(a.get("dataset") == "scolaire" for a in avs), (
        f"unexpected scolaire warning when address is complete: {avs!r}"
    )

    # college_de_secteur must have received nom_voie + numero (as int)
    mock_scolaire.assert_called_once()
    _, kwargs = mock_scolaire.call_args
    assert kwargs.get("nom_voie") == "rue de rivoli"
    assert kwargs.get("numero") == 10, (
        f"expected numero=10 (int from '10'), got {kwargs.get('numero')!r}"
    )

    app.dependency_overrides.clear()


async def test_resolve_backward_compat_avertissements_defaults_to_empty():
    """meta.avertissements must be present and default to [] on a clean resolve.

    Existing consumers that don't inspect avertissements are unaffected.
    Consumers that do inspect always get a list (never a missing key).
    """
    row = _ban_row(nom_voie="rue saint-honore", numero="5")

    async with _auth_client() as client:
        with (
            patch("app.resolve.router.reverse_geocoder", AsyncMock(return_value=[row])),
            patch("app.resolve.router.parcelle_par_point", AsyncMock(return_value=[])),
            patch("app.resolve.router.parcelles_proches", AsyncMock(return_value=[])),
            patch("app.resolve.multi.reverse_geocoder", AsyncMock(return_value=[row])),
            patch(
                "app.resolve.multi.eligibilite_par_adresse",
                AsyncMock(
                    return_value=[{"code_insee": "75002", "technologie": "FTTH"}]
                ),
            ),
        ):
            resp = await client.get(f"{_GPS_URL_BASE}&datasets=connectivite")

    assert resp.status_code == 200
    body = resp.json()
    donnees = body.get("donnees", [])
    assert donnees, f"resolve must return results; body={body!r}"

    result = donnees[0]
    # Field must exist (always present) and be an empty list (address was complete)
    assert "avertissements" in result, (
        "avertissements field must always be present in ResultatMultiResolution"
    )
    assert result["avertissements"] == [], (
        f"expected empty list when address complete, got {result['avertissements']!r}"
    )

    app.dependency_overrides.clear()
