"""Synthetic regressions: a fuzzy suggestion cannot silently become a property."""

from unittest.mock import AsyncMock, patch

import pytest

from app.common.exceptions import ErreurApiError
from app.main import app
from app.resolve.router import _resolve_adresse
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import SCOPE_44_85

pytestmark = pytest.mark.asyncio

_ROW = {
    "ban_id": "44001_synthetic_00012",
    "numero": "12",
    "rep": None,
    "nom_voie": "Rue des Étoiles",
    "code_postal": "44170",
    "code_commune": "44001",
    "nom_commune": "Ville-Sentinelle",
    "latitude": 46.000001,
    "longitude": -1.000001,
    "score": 1.0,
}


@pytest.mark.parametrize(
    ("query", "rows"),
    [
        ("Ville-Sentinelle", [_ROW]),
        ("Rue des Étoiles 44170 Ville-Sentinelle", [_ROW]),
        ("Rue des Étoiles 44170", [_ROW]),
        ("Rue des Étoiles 44170", []),
        ("44170 Ville-Sentinelle", []),
        ("44170 Ville-Sentinelle", [_ROW]),
        ("12 Rue des Étoiles", [_ROW]),
        ("14 Rue des Étoiles 44170", [_ROW]),
        ("12bis Rue des Étoiles 44170", [_ROW]),
        ("12 b Rue des Étoiles 44170", [{**_ROW, "rep": "bis"}]),
        ("12 Rue des Étoiles 44170", [{**_ROW, "rep": "bis"}]),
        ("12 Rue des Étoiles 44270", [_ROW]),
        ("12 Rue des Étoiles 44170 Autre-Ville", [_ROW]),
        ("12 Rue des Nuages 44170 Ville-Sentinelle", [_ROW]),
        ("Les Étoiles 44170 Ville-Sentinelle", [_ROW]),
        ("Rue des Étoiles 44170", [{**_ROW, "numero": 0}]),
        (
            "12 Rue des Étoiles 44170",
            [
                _ROW,
                {
                    **_ROW,
                    "ban_id": "44002_synthetic_00012",
                    "nom_commune": "Autre-Ville",
                    "code_commune": "44002",
                },
            ],
        ),
        (
            "12 Rue des Étoiles 44170 Ville-Sentinelle",
            [_ROW, {**_ROW, "longitude": -1.100001}],
        ),
        (
            "12 Rue des Étoiles 44170 Ville-Sentinelle",
            [{**_ROW, "ban_id": f"44001_synthetic_{n}"} for n in range(21)],
        ),
    ],
)
async def test_unestablished_address_never_reads_parcels(query, rows):
    parcel = AsyncMock(return_value=[])
    snap = AsyncMock(return_value=[])
    with (
        patch("app.resolve.router.geocoder", AsyncMock(return_value=rows)),
        patch("app.resolve.router.parcelle_par_point", parcel),
        patch("app.resolve.router._snap_parcel_candidates", snap),
        pytest.raises(ErreurApiError) as caught,
    ):
        await _resolve_adresse(AsyncMock(), query)

    assert caught.value.code == 422
    assert caught.value.message == "Adresse à préciser"
    assert query not in caught.value.detail
    parcel.assert_not_awaited()
    snap.assert_not_awaited()


@pytest.mark.parametrize(
    ("query", "row"),
    [
        ("12 Rue des Étoiles 44170 Ville-Sentinelle", _ROW),
        ("12 rue des etoiles, 44170", _ROW),
        ("12 rue des etoiles Ville Sentinelle", _ROW),
        ("12-rue-des-etoiles-44170-ville-sentinelle", _ROW),
        ("12bis rue des etoiles 44170", {**_ROW, "rep": "bis"}),
        ("12 bis rue des etoiles 44170", {**_ROW, "rep": "bis"}),
        ("12B rue des etoiles 44170", {**_ROW, "rep": "b"}),
        ("12 b rue des etoiles 44170", {**_ROW, "rep": "b"}),
        (
            "12 rue des etoiles Paris 1er",
            {
                **_ROW,
                "nom_commune": "Paris 1er Arrondissement",
                "code_commune": "75101",
                "code_postal": "75001",
            },
        ),
        (
            "12 rue des etoiles Paris",
            {
                **_ROW,
                "nom_commune": "Paris 1er Arrondissement",
                "code_commune": "75101",
                "code_postal": "75001",
            },
        ),
        (
            "12-l-orangerie-44170-ville-sentinelle",
            {**_ROW, "nom_voie": "l\u2019Orangerie"},
        ),
        (
            "12 a travers champs 44170",
            {**_ROW, "nom_voie": "A Travers Champs"},
        ),
        (
            "12 d orfeuille 44170",
            {**_ROW, "nom_voie": "D\u2019Orfeuille"},
        ),
        (
            "Les Étoiles 44170 Ville-Sentinelle",
            {**_ROW, "numero": None, "nom_voie": "Les Étoiles"},
        ),
        (
            "Les Étoiles Ville-Sentinelle",
            {**_ROW, "numero": "", "nom_voie": "Les Étoiles"},
        ),
    ],
)
async def test_explicit_address_preserves_number_suffix_and_unnumbered_places(
    query, row
):
    parcel = AsyncMock(return_value=[])
    with (
        patch("app.resolve.router.geocoder", AsyncMock(return_value=[row])),
        patch("app.resolve.router.parcelle_par_point", parcel),
        patch("app.resolve.router._snap_parcel_candidates", AsyncMock(return_value=[])),
    ):
        result = await _resolve_adresse(AsyncMock(), query)

    assert len(result) == 1
    assert result[0].ban_id == row["ban_id"]
    parcel.assert_awaited_once()


async def test_explicit_commune_selects_its_match_instead_of_the_first_suggestion():
    other = {
        **_ROW,
        "ban_id": "44002_synthetic_00012",
        "nom_commune": "Autre-Ville",
        "code_commune": "44002",
    }
    with (
        patch("app.resolve.router.geocoder", AsyncMock(return_value=[other, _ROW])),
        patch("app.resolve.router.parcelle_par_point", AsyncMock(return_value=[])),
        patch("app.resolve.router._snap_parcel_candidates", AsyncMock(return_value=[])),
    ):
        result = await _resolve_adresse(
            AsyncMock(), "12 Rue des Étoiles 44170 Ville-Sentinelle"
        )

    assert result[0].ban_id == _ROW["ban_id"]


async def test_missing_explicit_numbered_address_keeps_typed_absence():
    with patch("app.resolve.router.geocoder", AsyncMock(return_value=[])):
        assert await _resolve_adresse(AsyncMock(), "99 Rue Introuvable 44170") == []


async def test_identical_duplicate_does_not_create_an_address_choice():
    with (
        patch(
            "app.resolve.router.geocoder", AsyncMock(return_value=[_ROW, dict(_ROW)])
        ),
        patch("app.resolve.router.parcelle_par_point", AsyncMock(return_value=[])),
        patch("app.resolve.router._snap_parcel_candidates", AsyncMock(return_value=[])),
    ):
        result = await _resolve_adresse(AsyncMock(), "12 Rue des Étoiles 44170")

    assert len(result) == 1
    assert result[0].ban_id == _ROW["ban_id"]


async def test_ambiguous_detached_letter_never_picks_a_suffix_or_a_street_word():
    rows = [
        {**_ROW, "rep": "a", "nom_voie": "Travers Champs"},
        {**_ROW, "ban_id": "44001_other_00012", "nom_voie": "A Travers Champs"},
    ]
    with (
        patch("app.resolve.router.geocoder", AsyncMock(return_value=rows)),
        pytest.raises(ErreurApiError) as caught,
    ):
        await _resolve_adresse(AsyncMock(), "12 a travers champs 44170")

    assert caught.value.code == 422


async def test_generic_request_returns_actionable_problem_before_dataset_reads(
    client, monkeypatch
):
    # Replaces the full scope of ``client``, which clears its overrides.
    app.dependency_overrides[get_instance_scope] = lambda: SCOPE_44_85
    parcel = AsyncMock(return_value=[])
    multi = AsyncMock(side_effect=AssertionError("dataset access forbidden"))
    with (
        patch("app.resolve.router.geocoder", AsyncMock(return_value=[_ROW])),
        patch("app.resolve.router.parcelle_par_point", parcel),
        patch("app.resolve.router._snap_parcel_candidates", AsyncMock(return_value=[])),
        patch("app.resolve.router.query_multiple_datasets", multi),
    ):
        response = await client.get(
            "/v1/resolve",
            params={
                "q": "Rue des Étoiles 44170",
                "type": "adresse",
                "datasets": "dpe,cadastre",
            },
        )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Adresse à préciser"
    assert response.json()["instance"] == "/v1/resolve"
    assert "sélectionnez" in response.json()["detail"]
    assert "Rue des Étoiles" not in response.text
    parcel.assert_not_awaited()
    multi.assert_not_awaited()
