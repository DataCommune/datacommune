"""Identity contract matrix for /v1/resolve (Phase 45-01, DATA-06/GEO-02).

The three input modes (adresse, GPS, cadastre) must expose the same canonical
identity: normalized address when available, coordinates, ``code_insee`` and
``id_parcelle``. Resolution outcomes are deterministic (D-04):

  - exactly one parcel candidate  -> ``exact``
  - several parcel candidates     -> explicit ``ambiguous`` (no silent pick)
  - provably outside 44/85 scope  -> typed absence ``out_of_coverage``
  - nothing found inside scope    -> typed absence ``not_reported``

A cadastre reference stays authoritative: DVF is fed directly by the canonical
``id_parcelle``; the centroid never selects a second parcel (concave geometry).

All tests are offline (AsyncMock + dependency_overrides). Failure messages
reference synthetic case ids (CASE-*) only — never a real address (D-05).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import check_quota, check_quota_hybrid
from app.auth.models import ApiKeyInfo
from app.dpe.inventory_service import DpeInventoryServiceResult
from app.dpe.schemas import DpeInventoryDistribution, DpeInventoryPage
from app.main import app
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import SCOPE_44_85

pytestmark = pytest.mark.asyncio

# --- Synthetic geometry double (no real address, no real coordinates) --------

_LAT = 46.000001
_LON = -1.000001

_P1 = {
    "id_parcelle": "85047000AD0123",
    "commune": "85047",
    "prefixe": "000",
    "section": "AD",
    "numero": "0123",
    "contenance": 500,
    "centroide_lat": _LAT,
    "centroide_lon": _LON,
    "geometrie_geojson": "{}",
}
_P2 = {
    "id_parcelle": "85047000AD0124",
    "commune": "85047",
    "prefixe": "000",
    "section": "AD",
    "numero": "0124",
    "contenance": 480,
    "centroide_lat": _LAT,
    "centroide_lon": _LON,
    "geometrie_geojson": "{}",
}

_BAN_ROW = {
    "ban_id": "85047_sentinelle_0001",
    "numero": "1",
    "rep": None,
    "nom_voie": "rue sentinelle-synthetique",
    "code_postal": "85300",
    "code_commune": "85047",
    "nom_commune": "Commune-Test",
    "latitude": _LAT,
    "longitude": _LON,
    "score": 0.98,
    "distance_m": 4.0,
}


def _auth_client() -> AsyncClient:
    fake = ApiKeyInfo(
        id=1,
        name="test-key",
        quota_monthly=999999,
        revoked=False,
        created_at=datetime.now(UTC),
    )
    app.dependency_overrides[check_quota] = lambda: fake
    app.dependency_overrides[check_quota_hybrid] = lambda: fake
    app.dependency_overrides[get_instance_scope] = lambda: SCOPE_44_85
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _scope_44_85(monkeypatch: pytest.MonkeyPatch):
    """Instance coverage proof (44/85): served by ``_auth_client``."""
    # These source doubles represent admitted stock; retain their error oracles.
    monkeypatch.setattr(
        "app.resolve.multi.guard_publication", AsyncMock(return_value=None)
    )
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mode adresse
# ---------------------------------------------------------------------------


async def test_adresse_exact_exposes_full_canonical_identity():
    """CASE-ADR-EXACT-01: one parcel candidate -> exact identity, all fields."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
        ):
            resp = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"
            )

    assert resp.status_code == 200, "CASE-ADR-EXACT-01 HTTP"
    body = resp.json()
    result = body["donnees"][0]
    assert result["code_insee"] == "85047", "CASE-ADR-EXACT-01 code_insee"
    assert result["id_parcelle"] == _P1["id_parcelle"], "CASE-ADR-EXACT-01 parcelle"
    assert result["resolution"] == "exact", "CASE-ADR-EXACT-01 issue"
    assert body["meta"]["resolution"]["issue"] == "exact", "CASE-ADR-EXACT-01 meta"


async def test_adresse_multiple_candidates_is_explicitly_ambiguous():
    """CASE-ADR-AMBIG-02: several parcels -> ambiguous, no silent LIMIT 1 pick."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1, _P2]),
            ),
        ):
            resp = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"
            )

    assert resp.status_code == 200, "CASE-ADR-AMBIG-02 HTTP"
    body = resp.json()
    result = body["donnees"][0]
    assert result["id_parcelle"] is None, "CASE-ADR-AMBIG-02 no silent selection"
    assert result["resolution"] == "ambiguous", "CASE-ADR-AMBIG-02 issue"
    assert result["parcelles_candidates"] == [
        _P1["id_parcelle"],
        _P2["id_parcelle"],
    ], "CASE-ADR-AMBIG-02 candidates in stable order"
    assert body["meta"]["resolution"]["issue"] == "ambiguous", "CASE-ADR-AMBIG-02 meta"


async def test_adresse_out_of_scope_postal_returns_out_of_coverage():
    """CASE-ADR-OOC-03: postal 75001 outside 44/85 -> typed absence, never the
    best in-scope neighbour from the fuzzy geocoder."""
    geocode_mock = AsyncMock(return_value=[_BAN_ROW])
    async with _auth_client() as client:
        with patch("app.resolve.router.geocoder", geocode_mock):
            resp = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-hors-zone+75001"
            )

    assert resp.status_code == 200, "CASE-ADR-OOC-03 HTTP"
    body = resp.json()
    assert body["donnees"] == [], "CASE-ADR-OOC-03 no best-neighbour result"
    assert body["meta"]["resolution"]["issue"] == "out_of_coverage", (
        "CASE-ADR-OOC-03 typed absence"
    )
    geocode_mock.assert_not_called()


async def test_adresse_not_found_in_scope_returns_not_reported():
    """CASE-ADR-ABS-04: in-scope postal but no geocoder hit -> not_reported."""
    async with _auth_client() as client:
        with patch("app.resolve.router.geocoder", AsyncMock(return_value=[])):
            resp = await client.get(
                "/v1/resolve?type=adresse&q=99+rue+inconnue-synthetique+85300"
            )

    assert resp.status_code == 200, "CASE-ADR-ABS-04 HTTP"
    body = resp.json()
    assert body["donnees"] == [], "CASE-ADR-ABS-04 empty donnees"
    assert body["meta"]["resolution"]["issue"] == "not_reported", (
        "CASE-ADR-ABS-04 typed absence"
    )


# ---------------------------------------------------------------------------
# Mode GPS
# ---------------------------------------------------------------------------


async def test_gps_exact_exposes_full_canonical_identity():
    """CASE-GPS-EXACT-05: BAN + one parcel -> code_insee and id_parcelle set."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
        ):
            resp = await client.get(f"/v1/resolve?type=gps&q={_LAT},{_LON}")

    assert resp.status_code == 200, "CASE-GPS-EXACT-05 HTTP"
    result = resp.json()["donnees"][0]
    assert result["code_insee"] == "85047", "CASE-GPS-EXACT-05 code_insee"
    assert result["id_parcelle"] == _P1["id_parcelle"], "CASE-GPS-EXACT-05 parcelle"
    assert result["resolution"] == "exact", "CASE-GPS-EXACT-05 issue"


async def test_gps_without_ban_takes_code_insee_from_parcel():
    """CASE-GPS-PARC-06: no BAN address, one parcel -> code_insee from cadastre."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
        ):
            resp = await client.get(f"/v1/resolve?type=gps&q={_LAT},{_LON}")

    assert resp.status_code == 200, "CASE-GPS-PARC-06 HTTP"
    result = resp.json()["donnees"][0]
    assert result["code_insee"] == "85047", "CASE-GPS-PARC-06 code_insee from parcel"
    assert result["id_parcelle"] == _P1["id_parcelle"], "CASE-GPS-PARC-06 parcelle"


async def test_gps_boundary_point_is_explicitly_ambiguous():
    """CASE-GPS-AMBIG-07: a point on a parcel boundary intersects two parcels
    and must never be reduced by a silent LIMIT 1."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1, _P2]),
            ),
        ):
            resp = await client.get(f"/v1/resolve?type=gps&q={_LAT},{_LON}")

    assert resp.status_code == 200, "CASE-GPS-AMBIG-07 HTTP"
    result = resp.json()["donnees"][0]
    assert result["id_parcelle"] is None, "CASE-GPS-AMBIG-07 no silent selection"
    assert result["resolution"] == "ambiguous", "CASE-GPS-AMBIG-07 issue"
    assert result["parcelles_candidates"] == [
        _P1["id_parcelle"],
        _P2["id_parcelle"],
    ], "CASE-GPS-AMBIG-07 candidates"


async def test_gps_nothing_found_returns_typed_absence():
    """CASE-GPS-ABS-08: no BAN, no parcel, nothing nearby -> typed absence."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.resolve.router.parcelles_proches",
                AsyncMock(return_value=[]),
            ),
        ):
            resp = await client.get(f"/v1/resolve?type=gps&q={_LAT},{_LON}")

    assert resp.status_code == 200, "CASE-GPS-ABS-08 HTTP"
    body = resp.json()
    assert body["donnees"] == [], "CASE-GPS-ABS-08 empty donnees"
    assert body["meta"]["resolution"]["issue"] == "not_reported", (
        "CASE-GPS-ABS-08 typed absence"
    )


# ---------------------------------------------------------------------------
# Nearest-parcel fallback (BAN entrance point on the roadway)
# ---------------------------------------------------------------------------


async def test_adresse_snaps_to_the_abutting_parcel_when_point_is_outside():
    """CASE-SNAP-ADR-15: strict lookup empty, one clear nearest parcel (0.0 m
    vs 9.6 m) -> canonical identity instead of a null id_parcelle."""
    nearby = [{**_P1, "distance_m": 0.0}, {**_P2, "distance_m": 9.6}]
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.resolve.router.parcelles_proches",
                AsyncMock(return_value=nearby),
            ),
        ):
            resp = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"
            )

    assert resp.status_code == 200, "CASE-SNAP-ADR-15 HTTP"
    result = resp.json()["donnees"][0]
    assert result["id_parcelle"] == _P1["id_parcelle"], "CASE-SNAP-ADR-15 parcelle"
    assert result["resolution"] == "exact", "CASE-SNAP-ADR-15 issue"


async def test_gps_snap_tie_stays_ambiguous():
    """CASE-SNAP-GPS-16: two parcels within the decision margin -> ambiguous,
    never a silent pick between neighbours."""
    nearby = [{**_P1, "distance_m": 1.0}, {**_P2, "distance_m": 2.5}]
    point_mock = AsyncMock(return_value=[])
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch("app.resolve.router.parcelle_par_point", point_mock),
            patch(
                "app.resolve.router.parcelles_proches",
                AsyncMock(return_value=nearby),
            ),
        ):
            resp = await client.get(f"/v1/resolve?type=gps&q={_LAT},{_LON}")

    assert resp.status_code == 200, "CASE-SNAP-GPS-16 HTTP"
    result = resp.json()["donnees"][0]
    assert result["id_parcelle"] is None, "CASE-SNAP-GPS-16 no silent selection"
    assert result["resolution"] == "ambiguous", "CASE-SNAP-GPS-16 issue"
    assert result["parcelles_candidates"] == [
        _P1["id_parcelle"],
        _P2["id_parcelle"],
    ], "CASE-SNAP-GPS-16 candidates"


async def test_snap_never_runs_when_the_strict_lookup_matched():
    """CASE-SNAP-GUARD-17: a point inside a parcel keeps the strict result —
    the fallback query is not even issued."""
    snap_mock = AsyncMock(return_value=[{**_P2, "distance_m": 0.0}])
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch("app.resolve.router.parcelles_proches", snap_mock),
        ):
            resp = await client.get(f"/v1/resolve?type=gps&q={_LAT},{_LON}")

    assert resp.status_code == 200, "CASE-SNAP-GUARD-17 HTTP"
    result = resp.json()["donnees"][0]
    assert result["id_parcelle"] == _P1["id_parcelle"], "CASE-SNAP-GUARD-17 parcelle"
    snap_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Mode cadastre
# ---------------------------------------------------------------------------


async def test_cadastre_reference_is_authoritative_and_never_recomputed():
    """CASE-CAD-EXACT-09: the explicit reference is the identity; the centroid
    is display-only and never selects a second parcel (concave geometry)."""
    point_mock = AsyncMock(return_value=[_P2])  # centroid falls in the neighbour
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.parcelle_par_reference",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch("app.resolve.router.parcelle_par_point", point_mock),
        ):
            resp = await client.get(f"/v1/resolve?q={_P1['id_parcelle']}")

    assert resp.status_code == 200, "CASE-CAD-EXACT-09 HTTP"
    result = resp.json()["donnees"][0]
    assert result["id_parcelle"] == _P1["id_parcelle"], "CASE-CAD-EXACT-09 parcelle"
    assert result["code_insee"] == "85047", "CASE-CAD-EXACT-09 code_insee"
    assert result["resolution"] == "exact", "CASE-CAD-EXACT-09 issue"
    point_mock.assert_not_called()


async def test_cadastre_out_of_scope_reference_returns_out_of_coverage():
    """CASE-CAD-OOC-10: a 75 reference on a 44/85 instance -> typed absence."""
    ref_mock = AsyncMock(return_value=[_P1])
    async with _auth_client() as client:
        with patch("app.resolve.router.parcelle_par_reference", ref_mock):
            resp = await client.get("/v1/resolve?q=75101000AB0001")

    assert resp.status_code == 200, "CASE-CAD-OOC-10 HTTP"
    body = resp.json()
    assert body["donnees"] == [], "CASE-CAD-OOC-10 empty donnees"
    assert body["meta"]["resolution"]["issue"] == "out_of_coverage", (
        "CASE-CAD-OOC-10 typed absence"
    )
    ref_mock.assert_not_called()


async def test_cadastre_unknown_reference_in_scope_returns_not_reported():
    """CASE-CAD-ABS-11: in-scope department, unknown reference -> not_reported."""
    async with _auth_client() as client:
        with patch(
            "app.resolve.router.parcelle_par_reference",
            AsyncMock(return_value=[]),
        ):
            resp = await client.get("/v1/resolve?q=44109000ZZ9999")

    assert resp.status_code == 200, "CASE-CAD-ABS-11 HTTP"
    body = resp.json()
    assert body["donnees"] == [], "CASE-CAD-ABS-11 empty donnees"
    assert body["meta"]["resolution"]["issue"] == "not_reported", (
        "CASE-CAD-ABS-11 typed absence"
    )


# ---------------------------------------------------------------------------
# Cross-dataset invariant (base == cadastre == dvf[*])
# ---------------------------------------------------------------------------


async def test_enrichment_carries_identical_id_parcelle_everywhere():
    """CASE-INV-12: base, datasets.cadastre and every DVF row share the same
    canonical id_parcelle; DVF is queried by id, never by point/centroid."""
    mutation = {"id_mutation": "syn-001", "id_parcelle": _P1["id_parcelle"]}
    dvf_id_mock = AsyncMock(return_value=[mutation])
    dvf_point_mock = AsyncMock(return_value=[{"id_parcelle": "NEIGHBOUR"}])
    dvf_radius_mock = AsyncMock(return_value=[{"id_parcelle": "NEIGHBOUR"}])

    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(return_value=[_P1]),
            ),
            patch("app.resolve.multi.dvf_par_id_parcelle", dvf_id_mock),
            patch("app.resolve.multi.dvf_par_parcelle", dvf_point_mock),
            patch("app.dvf.queries.dvf_par_adresse", dvf_radius_mock),
        ):
            resp = await client.get(
                f"/v1/resolve?type=gps&q={_LAT},{_LON}&datasets=cadastre,dvf"
            )

    assert resp.status_code == 200, "CASE-INV-12 HTTP"
    result = resp.json()["donnees"][0]
    expected = _P1["id_parcelle"]
    assert result["id_parcelle"] == expected, "CASE-INV-12 base"
    assert result["datasets"]["cadastre"]["id_parcelle"] == expected, (
        "CASE-INV-12 cadastre block"
    )
    dvf_rows = result["datasets"]["dvf"]
    assert dvf_rows and all(r["id_parcelle"] == expected for r in dvf_rows), (
        "CASE-INV-12 every DVF row"
    )
    dvf_id_mock.assert_called_once()
    assert dvf_id_mock.call_args.args[1] == expected, "CASE-INV-12 DVF by id"
    dvf_point_mock.assert_not_called()
    dvf_radius_mock.assert_not_called()


async def test_parcel_invariant_mismatch_fails_closed():
    """CASE-INV-13: a divergent id_parcelle in a dataset block is scrubbed
    (fail-closed) instead of being served as this bien's data."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            # Corrupted lower layer: cadastre block answers with the neighbour.
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(return_value=[_P2]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(return_value=[{"id_parcelle": _P1["id_parcelle"]}]),
            ),
        ):
            resp = await client.get(
                f"/v1/resolve?type=gps&q={_LAT},{_LON}&datasets=cadastre,dvf"
            )

    assert resp.status_code == 200, "CASE-INV-13 HTTP"
    result = resp.json()["donnees"][0]
    assert "cadastre" not in (result["datasets"] or {}), (
        "CASE-INV-13 divergent block must be scrubbed"
    )
    states = result["dataset_states"] or {}
    assert states.get("cadastre", {}).get("presence") == "error", (
        "CASE-INV-13 typed error state"
    )
    erreurs = result["erreurs"] or []
    assert any(e["dataset"] == "cadastre" for e in erreurs), (
        "CASE-INV-13 explicit erreur entry"
    )
    # The coherent DVF block survives.
    assert result["datasets"]["dvf"], "CASE-INV-13 coherent DVF kept"


async def test_multi_path_keeps_the_ban_key():
    """CASE-BAN-18: with `datasets=` the response is rebuilt as a
    ResultatMultiResolution — the BAN attachment key must survive that
    reconstruction, otherwise the Fiche Bien falls back to the spatial path."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(return_value=[]),
            ),
        ):
            resp = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"
                "&datasets=cadastre,dvf"
            )

    assert resp.status_code == 200, "CASE-BAN-18 HTTP"
    result = resp.json()["donnees"][0]
    assert result["ban_id"] == _BAN_ROW["ban_id"], "CASE-BAN-18 ban_id on multi path"


async def test_ban_key_is_identical_with_and_without_datasets():
    """CASE-BAN-19: for the same input, the multi path and the plain path must
    expose the same ban_id — no silent divergence between the two contracts."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(return_value=[]),
            ),
        ):
            plain = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"
            )
            multi = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"
                "&datasets=cadastre,dvf"
            )

    assert plain.status_code == 200, "CASE-BAN-19 HTTP plain"
    assert multi.status_code == 200, "CASE-BAN-19 HTTP multi"
    plain_ban = plain.json()["donnees"][0]["ban_id"]
    multi_ban = multi.json()["donnees"][0]["ban_id"]
    assert plain_ban is not None, "CASE-BAN-19 plain path exposes the key"
    assert multi_ban == plain_ban, "CASE-BAN-19 both paths agree"


async def test_ban_key_survives_the_dpe_inventory_path():
    """CASE-BAN-20: the shared inventory consumes, but never rewrites, BAN."""
    inventory = AsyncMock(
        return_value=DpeInventoryServiceResult(
            page=DpeInventoryPage(
                state="absent",
                context_ref="opaque-context",
                total=0,
                distribution=DpeInventoryDistribution(),
                page_size=20,
                has_more=False,
                read_state="complete",
            ),
            apartment_document_count=0,
        )
    )
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch("app.resolve.multi.load_dpe_inventory_result", inventory),
            patch(
                "app.resolve.multi.cles_ban_batiment_rnb",
                AsyncMock(return_value=()),
            ),
        ):
            plain = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"
            )
            multi = await client.get(
                "/v1/resolve?type=adresse&q=1+rue+sentinelle-synthetique+85300"
                "&datasets=dpe"
            )

    assert plain.status_code == 200, "CASE-BAN-20 HTTP plain"
    assert multi.status_code == 200, "CASE-BAN-20 HTTP multi"
    plain_ban = plain.json()["donnees"][0]["ban_id"]
    multi_ban = multi.json()["donnees"][0]["ban_id"]
    assert plain_ban == _BAN_ROW["ban_id"], "CASE-BAN-20 plain path exposes the key"
    assert multi_ban == plain_ban, "CASE-BAN-20 the dpe path does not rewrite it"
    inventory.assert_awaited_once()
    assert inventory.await_args.kwargs["ban_id"] == plain_ban, (
        "CASE-BAN-20 same key fed to DPE"
    )


async def test_dataset_states_and_meta_source_are_preserved():
    """CASE-COMPAT-14: the Phase 44-03 contract (dataset_states + meta.source
    provenance list) survives the identity assembler untouched."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(return_value=[_P1]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(return_value=[]),
            ),
        ):
            resp = await client.get(
                f"/v1/resolve?type=gps&q={_LAT},{_LON}&datasets=cadastre,dvf"
            )

    assert resp.status_code == 200, "CASE-COMPAT-14 HTTP"
    body = resp.json()
    result = body["donnees"][0]
    states = result["dataset_states"]
    assert states["cadastre"]["presence"] == "value", "CASE-COMPAT-14 states"
    assert states["dvf"]["presence"] == "absent", "CASE-COMPAT-14 typed absence"
    assert states["dvf"]["absence_reason"] == "not_reported", "CASE-COMPAT-14 taxonomy"
    sources = body["meta"]["source"]
    assert isinstance(sources, list) and sources, "CASE-COMPAT-14 provenance list"
    assert {"dataset", "periode", "couverture"} <= set(sources[0]), (
        "CASE-COMPAT-14 DatasetProvenance shape"
    )
