"""Tests: the /v1/resolve « dvf » dataset is scoped to the EXACT parcel.

Regression guard for the wrong-data bug: the fiche « sur ce bien » DVF block used
a 500m proximity radius (dvf_par_adresse rayon_m=500), which surfaced neighbours'
sales and mislabeled the most recent one as this bien's last sale. Phase 45
extends the guard: DVF is now fed by the CANONICAL id_parcelle resolved once by
the router (dvf_par_id_parcelle), never by a point/centroid re-resolution.

Levels:
  1. Wiring: the resolve « dvf » dispatch consumes the canonical id_parcelle.
  2. Query shape: dvf_par_id_parcelle filters by id, carries no point/radius;
     parcelle_par_point is a deterministic candidates API (no silent LIMIT 1).
  3. Behavioural double: two parcels, a neighbour mutation, a boundary point
     and a concave parcel whose centroid falls in the neighbour — the
     neighbour's sale never leaks into the response (case ids only, D-05).

Uses AsyncMock + dependency_overrides (no real DB). type=gps bypasses the geocoder.
"""

import contextlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import check_quota, check_quota_hybrid
from app.auth.models import ApiKeyInfo
from app.cadastre import queries as cadastre_queries
from app.dvf import queries as dvf_queries
from app.dvf.schemas import EVIDENCE_TYPE_BIEN_UTILISATEUR, DvfAttribution
from app.main import app
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import FULL_SCOPE

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _published_source_double(monkeypatch):
    """Model admitted DVF stock; publication SQL has separate PostGIS tests."""
    monkeypatch.setattr(
        "app.resolve.multi.guard_publication", AsyncMock(return_value=None)
    )


_LAT = 46.000001
_LON = -1.000001  # synthetic point (dept 85 double), not a real address
_GPS_URL = f"/v1/resolve?type=gps&q={_LAT},{_LON}&datasets=dvf"

# --- Behavioural double: two adjacent parcels sharing a boundary point ------
# CASE-DBL-P1 is concave: its centroid falls inside CASE-DBL-P2 (the neighbour).
_ID_P1 = "85047000AD0123"
_ID_P2 = "85047000AD0124"
_BOUNDARY = (_LAT, _LON)  # a point on the shared boundary -> two candidates
_CENTROID_P1 = (46.000002, -1.000002)  # concave: geometrically inside P2

_PARCELS = {
    _ID_P1: {
        "id_parcelle": _ID_P1,
        "commune": "85047",
        "prefixe": "000",
        "section": "AD",
        "numero": "0123",
        "contenance": 500,
        "centroide_lat": _CENTROID_P1[0],
        "centroide_lon": _CENTROID_P1[1],
        "geometrie_geojson": "{}",
    },
    _ID_P2: {
        "id_parcelle": _ID_P2,
        "commune": "85047",
        "prefixe": "000",
        "section": "AD",
        "numero": "0124",
        "contenance": 480,
        "centroide_lat": 46.000003,
        "centroide_lon": -1.000003,
        "geometrie_geojson": "{}",
    },
}

# Point -> ordered parcel candidates (stable order, cardinality inspected).
_POINT_INDEX: dict[tuple[float, float], list[str]] = {
    _BOUNDARY: [_ID_P1, _ID_P2],
    _CENTROID_P1: [_ID_P2],  # concave trap: centroid resolves to the NEIGHBOUR
}

# Parcel -> mutations. The neighbour P2 carries a sale that must NEVER leak.
_MUT_P1 = {"id_mutation": "CASE-DBL-MUT-OWN", "id_parcelle": _ID_P1}
_MUT_NEIGHBOUR = {"id_mutation": "CASE-DBL-MUT-NEIGHBOUR", "id_parcelle": _ID_P2}
_MUTATIONS = {_ID_P1: [_MUT_P1], _ID_P2: [_MUT_NEIGHBOUR]}


async def _double_parcelle_par_point(db, lat, lon, *args, **kwargs):
    ids = _POINT_INDEX.get((lat, lon), [])
    return [_PARCELS[i] for i in ids]


async def _double_parcelle_par_reference(db, ref, *args, **kwargs):
    row = _PARCELS.get(ref)
    return [row] if row else []


async def _double_dvf_par_id_parcelle(db, id_parcelle, *args, **kwargs):
    return list(_MUTATIONS.get(id_parcelle, []))


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
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_BAN_ROW = {
    "code_commune": "85047",
    "ban_id": "85047_synthetic",
    "nom_voie": "rue synthetique",
    "numero": "1",
    "latitude": _LAT,
    "longitude": _LON,
    "nom_commune": "Commune-Test",
    "code_postal": "85300",
    "distance_m": 5.0,
    "rep": None,
}


async def test_resolve_dvf_uses_canonical_id_not_radius_nor_point():
    """CASE-DVF-WIRE-01: the « dvf » dispatch consumes the canonical
    id_parcelle (dvf_par_id_parcelle) — never dvf_par_adresse (radius) nor a
    second point-based dvf_par_parcelle resolution."""
    dvf_id_mock = AsyncMock(return_value=[{"id_parcelle": _ID_P1}])
    dvf_point_mock = AsyncMock(return_value=[{"id_parcelle": "OTHER"}])
    dvf_radius_mock = AsyncMock(return_value=[{"id_parcelle": "OTHER"}])

    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_PARCELS[_ID_P1]]),
            ),
            patch("app.resolve.multi.dvf_par_id_parcelle", dvf_id_mock),
            patch("app.resolve.multi.dvf_par_parcelle", dvf_point_mock),
            patch("app.dvf.queries.dvf_par_adresse", dvf_radius_mock),
        ):
            resp = await client.get(_GPS_URL)

    assert resp.status_code == 200, resp.text
    dvf_id_mock.assert_called_once()
    assert dvf_id_mock.call_args.args[1] == _ID_P1, "CASE-DVF-WIRE-01 canonical id"
    dvf_point_mock.assert_not_called()
    dvf_radius_mock.assert_not_called()

    app.dependency_overrides.clear()


async def test_cadastre_entry_feeds_dvf_by_reference_never_by_centroid():
    """CASE-DBL-CAD-02: cadastre input on the concave parcel P1. The centroid
    falls inside neighbour P2 — feeding DVF by centroid would return the
    neighbour's sale. The canonical reference must win."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.parcelle_par_reference",
                AsyncMock(side_effect=_double_parcelle_par_reference),
            ),
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(side_effect=_double_parcelle_par_point),
            ),
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(side_effect=_double_parcelle_par_reference),
            ),
            patch(
                "app.resolve.multi.parcelle_par_point",
                AsyncMock(side_effect=_double_parcelle_par_point),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(side_effect=_double_dvf_par_id_parcelle),
            ),
        ):
            resp = await client.get(f"/v1/resolve?q={_ID_P1}&datasets=cadastre,dvf")

    assert resp.status_code == 200, "CASE-DBL-CAD-02 HTTP"
    result = resp.json()["donnees"][0]
    assert result["id_parcelle"] == _ID_P1, "CASE-DBL-CAD-02 authoritative ref"
    dvf_rows = result["datasets"]["dvf"]
    ids = [r["id_mutation"] for r in dvf_rows]
    assert ids == ["CASE-DBL-MUT-OWN"], "CASE-DBL-CAD-02 own sale only"
    assert "CASE-DBL-MUT-NEIGHBOUR" not in resp.text, (
        "CASE-DBL-CAD-02 neighbour sale must never leak"
    )

    app.dependency_overrides.clear()


async def test_boundary_point_never_leaks_a_neighbour_mutation():
    """CASE-DBL-GPS-03: a GPS point on the shared boundary yields TWO parcel
    candidates. No silent pick: DVF stays absent and neither parcel's sale is
    served as this bien's."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(side_effect=_double_parcelle_par_point),
            ),
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(side_effect=_double_parcelle_par_reference),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(side_effect=_double_dvf_par_id_parcelle),
            ),
        ):
            resp = await client.get(
                f"/v1/resolve?type=gps&q={_BOUNDARY[0]},{_BOUNDARY[1]}&datasets=dvf"
            )

    assert resp.status_code == 200, "CASE-DBL-GPS-03 HTTP"
    result = resp.json()["donnees"][0]
    assert result["resolution"] == "ambiguous", "CASE-DBL-GPS-03 explicit ambiguity"
    assert result["id_parcelle"] is None, "CASE-DBL-GPS-03 no silent selection"
    assert "CASE-DBL-MUT-NEIGHBOUR" not in resp.text, "CASE-DBL-GPS-03 leak"
    assert "CASE-DBL-MUT-OWN" not in resp.text, "CASE-DBL-GPS-03 leak (either side)"

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Query shape (SQL-level guards)
# ---------------------------------------------------------------------------


class _FakeResult:
    def __iter__(self):
        return iter(())


class _CaptureSession:
    def __init__(self, captured: dict):
        self._captured = captured

    async def execute(self, sql, params):
        self._captured["sql"] = str(sql)
        self._captured["params"] = params
        return _FakeResult()


async def test_dvf_par_parcelle_filters_by_id_parcelle_without_radius():
    """Audited legacy wrapper: joins mutations on id_parcelle, no radius."""
    captured: dict = {}
    await dvf_queries.dvf_par_parcelle(_CaptureSession(captured), _LAT, _LON, limit=5)

    sql = captured["sql"]
    assert "id_parcelle = parc.id_parcelle" in sql, sql
    assert "ST_DWithin" not in sql, "parcel query must not be a proximity radius"
    assert "rayon" not in captured["params"], captured["params"]


async def test_dvf_par_id_parcelle_has_no_point_no_radius_no_centroid():
    """CASE-SQL-04: the canonical DVF query filters by id_parcelle only —
    no ST_DWithin, no rayon, no point/centroid parameters at all."""
    captured: dict = {}
    await dvf_queries.dvf_par_id_parcelle(_CaptureSession(captured), _ID_P1, limit=5)

    sql = captured["sql"]
    params = captured["params"]
    assert "id_parcelle = :id_parcelle" in sql, sql
    assert "ST_DWithin" not in sql, "canonical DVF query must not use a radius"
    assert "ST_MakePoint" not in sql, "canonical DVF query must not take a point"
    assert "rayon" not in params, params
    assert "lat" not in params and "lon" not in params, params
    assert params["id_parcelle"] == _ID_P1, params


async def test_parcelle_par_point_is_a_deterministic_candidates_api():
    """CASE-SQL-05: the point lookup returns ALL intersecting parcels in a
    stable order — a boundary point or concave geometry is never reduced by a
    silent LIMIT 1."""
    captured: dict = {}
    await cadastre_queries.parcelle_par_point(_CaptureSession(captured), _LAT, _LON)

    sql = captured["sql"]
    assert "LIMIT 1" not in sql, "candidates API must not silently truncate"
    assert "ORDER BY id_parcelle" in sql, "candidates must have a stable order"
    assert "ST_DWithin" not in sql, "point-in-polygon, not a radius search"


# ---------------------------------------------------------------------------
# Attribution wiring (Phase 57 plan 12, D-16/D-37): the response carries ONE
# decision, computed once, beside the history it qualifies.
# ---------------------------------------------------------------------------

_MUT_APPARTEMENT = {
    "id_mutation": "CASE-DVF-ATTR-OWN",
    "date_mutation": "2024-06-01",
    "nature_mutation": "Vente",
    "valeur_fonciere": 250000.0,
    "type_local": "Appartement",
    "surface_reelle_bati": 62,
    "nombre_pieces_principales": 3,
    "surface_terrain": None,
    "prix_m2": 4032.0,
    "adresse_numero": "1",
    "adresse_nom_voie": "rue synthetique",
    "code_postal": "85300",
    "nom_commune": "Commune-Test",
    "id_parcelle": _ID_P1,
    "code_type_local": "2",
    "longitude": _LON,
    "latitude": _LAT,
    "classe_mutation": "residentiel_appartement",
    "prix_m2_raison": None,
    "mutation_mono_local": True,
    "nb_locaux_maison": 0,
    "nb_locaux_appartement": 1,
    "nb_locaux_commercial": 0,
    "nb_lots_source": 1,
    "nb_parcelles": 1,
    "hors_stats_vefa": False,
    "surface_bati_totale": 62,
}


async def _resolve_dvf(rows: list[dict], decide=None, type_bien: str | None = None):
    """Run a GPS-mode resolve whose « dvf » dataset returns *rows*.

    ``type_bien`` appends the DECLARED dwelling type to the query string
    (D-41); omitted, the historical URL is sent unchanged.
    """
    async with _auth_client() as client:
        contexte = [
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_PARCELS[_ID_P1]]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(return_value=rows),
            ),
        ]
        if decide is not None:
            contexte.append(patch("app.resolve.multi.decide_dvf_attribution", decide))
        with contextlib.ExitStack() as stack:
            for gestionnaire in contexte:
                stack.enter_context(gestionnaire)
            url = _GPS_URL if type_bien is None else f"{_GPS_URL}&type_bien={type_bien}"
            return await client.get(url)


async def test_la_decision_dvf_est_calculee_une_seule_fois_par_resolution():
    """CASE-DVF-ATTR-WIRE-01: one decision per resolution carrying « dvf » —
    never re-derived per consumer, never re-derived per row."""
    decide = MagicMock(
        return_value=DvfAttribution(status="absente", reason="aucune_mutation")
    )
    resp = await _resolve_dvf([_MUT_APPARTEMENT], decide=decide)

    assert resp.status_code == 200, resp.text
    assert decide.call_count == 1, "la décision est calculée exactement une fois"
    assert decide.call_args.kwargs["type_bien"] is None, (
        "le type du bien vient de l'identité résolue, qui n'en porte aucun : "
        "l'inconnu est passé EXPLICITEMENT, jamais deviné"
    )

    app.dependency_overrides.clear()


async def test_la_reponse_porte_la_decision_et_garde_l_historique():
    """CASE-DVF-ATTR-WIRE-02: `datasets.dvf` keeps carrying the parcel history
    (D-11/D-17); the decision qualifies it and never replaces it."""
    resp = await _resolve_dvf([_MUT_APPARTEMENT])

    assert resp.status_code == 200, resp.text
    result = resp.json()["donnees"][0]
    attribution = result["dvf_attribution"]
    assert attribution is not None, "la réponse porte la décision"
    assert attribution["rule_version"], "la version de règle est publiée"
    ids = [row["id_mutation"] for row in result["datasets"]["dvf"]]
    assert ids == ["CASE-DVF-ATTR-OWN"], "l'historique de parcelle est intact"
    assert result["dataset_states"]["dvf"]["presence"] == "value"

    app.dependency_overrides.clear()


async def test_sans_type_de_bien_resolu_la_reponse_ne_publie_aucun_prix():
    """CASE-DVF-ATTR-WIRE-03: /v1/resolve resolves an address and a parcel, not
    a dwelling type. The decision says so instead of attributing the latest
    sale — and carries no mutation payload."""
    resp = await _resolve_dvf([_MUT_APPARTEMENT])

    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["status"] == "non_attribuable"
    assert attribution["reason"] == "type_bien_inconnu"
    assert attribution["mutation"] is None
    assert attribution["preuve_locale"] is None

    app.dependency_overrides.clear()


async def test_une_parcelle_sans_mutation_porte_une_absence_expliquee():
    """CASE-DVF-ATTR-WIRE-04 (D-16): the absence AND its reason travel."""
    resp = await _resolve_dvf([])

    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["status"] == "absente"
    assert attribution["reason"] == "aucune_mutation"

    app.dependency_overrides.clear()


async def test_sans_parcelle_canonique_rien_n_est_cherche_ni_attribue():
    """CASE-DVF-ATTR-WIRE-05: an ambiguous point resolves no parcel. Nothing
    was looked up, and the reason says exactly that — not « aucune vente »."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(side_effect=_double_parcelle_par_point),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(side_effect=_double_dvf_par_id_parcelle),
            ),
        ):
            resp = await client.get(
                f"/v1/resolve?type=gps&q={_BOUNDARY[0]},{_BOUNDARY[1]}&datasets=dvf"
            )

    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["status"] == "absente"
    assert attribution["reason"] == "parcelle_indisponible"
    assert attribution["mutation"] is None

    app.dependency_overrides.clear()


@pytest.mark.parametrize("failure_boundary", ["query", "publication"])
async def test_une_panne_dvf_n_est_jamais_lisible_comme_une_absence_de_vente(
    failure_boundary,
):
    """CASE-DVF-ATTR-WIRE-06 (D-16, doctrine DPE D-13/D-14): a caught failure
    is the caller's fact and stays typed as an error."""
    query = AsyncMock(side_effect=RuntimeError("boom"))
    admission = AsyncMock(
        side_effect=RuntimeError("publication unavailable")
        if failure_boundary == "publication"
        else None,
        return_value=None,
    )
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_PARCELS[_ID_P1]]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                query,
            ),
            patch("app.resolve.multi.guard_publication", admission),
        ):
            resp = await client.get(_GPS_URL)

    admission.assert_awaited_once()
    if failure_boundary == "publication":
        query.assert_not_awaited()
    else:
        query.assert_awaited_once()
    assert resp.status_code == 200, resp.text
    result = resp.json()["donnees"][0]
    attribution = result["dvf_attribution"]
    assert attribution["status"] == "error"
    assert attribution["reason"] == "temporary_error"
    assert "boom" not in resp.text, "aucune fuite d'exception amont"
    assert "publication unavailable" not in resp.text

    app.dependency_overrides.clear()


async def test_un_bloc_ecarte_par_l_invariant_de_parcelle_emporte_sa_decision():
    """CASE-DVF-ATTR-WIRE-07 (T-57-45): when the fail-closed cross-check
    scrubs a divergent DVF block, the decision computed FROM it cannot
    survive — otherwise the fiche would read the rejected sale as its KPI."""
    etranger = {**_MUT_APPARTEMENT, "id_parcelle": _ID_P2}
    resp = await _resolve_dvf([etranger])

    assert resp.status_code == 200, resp.text
    result = resp.json()["donnees"][0]
    assert not result["datasets"].get("dvf"), "le bloc divergent est écarté"
    assert result["dvf_attribution"]["status"] == "error"
    assert result["dvf_attribution"]["mutation"] is None

    app.dependency_overrides.clear()


async def test_le_contrat_dpe_n_est_pas_touche_par_le_champ_additif():
    """CASE-DVF-ATTR-WIRE-08: `dvf_attribution` is additive — a resolution
    without « dvf » carries None, and RESOLUTION_RAISONS is untouched."""
    from app.resolve.schemas import RESOLUTION_RAISONS

    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_PARCELS[_ID_P1]]),
            ),
            patch(
                "app.resolve.multi.parcelle_par_reference",
                AsyncMock(return_value=[_PARCELS[_ID_P1]]),
            ),
        ):
            resp = await client.get(
                f"/v1/resolve?type=gps&q={_LAT},{_LON}&datasets=cadastre"
            )

    result = resp.json()["donnees"][0]
    assert result["dvf_attribution"] is None
    assert "ambiguous" in RESOLUTION_RAISONS and "not_reported" in RESOLUTION_RAISONS

    app.dependency_overrides.clear()


# --- La provenance du type declare survit a CHAQUE remplacement (D-41) ------
#
# Trois etats sont construits HORS de `app.dvf.attribution` : la parcelle non
# resolue et la panne (`app.resolve.multi`), et le remplacement de l'invariant
# parcellaire (`app.resolve.router`). Chacun est une decision, et chacun doit
# publier la meme provenance que celle qu'il remplace -- sinon la declaration
# du lecteur disparait au premier incident, et le payload devient illisible.


async def test_une_parcelle_indisponible_garde_la_provenance_du_type_declare():
    """CASE-DVF-ATTR-WIRE-09 (D-41) : site `app.resolve.multi`, etat
    `parcelle_indisponible`. « Le type etait declare ET aucune parcelle n'a ete
    resolue » reste un fait distinct de « le type est inconnu »."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(side_effect=_double_parcelle_par_point),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(side_effect=_double_dvf_par_id_parcelle),
            ),
        ):
            resp = await client.get(
                f"/v1/resolve?type=gps&q={_BOUNDARY[0]},{_BOUNDARY[1]}"
                "&datasets=dvf&type_bien=appartement"
            )

    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["status"] == "absente"
    assert attribution["reason"] == "parcelle_indisponible"
    assert attribution["type_bien_source"] == "utilisateur"
    assert attribution["type_bien_consulte"] == "appartement"
    assert EVIDENCE_TYPE_BIEN_UTILISATEUR in attribution["evidence"]

    app.dependency_overrides.clear()


async def test_une_panne_dvf_garde_la_provenance_du_type_declare():
    """CASE-DVF-ATTR-WIRE-10 (D-41/D-16) : site `app.resolve.multi`, etat
    `temporary_error`. Une panne technique n'efface pas ce que l'appelant a
    declare -- et reste illisible comme « aucune vente ici »."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_PARCELS[_ID_P1]]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            resp = await client.get(f"{_GPS_URL}&type_bien=maison")

    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["status"] == "error"
    assert attribution["reason"] == "temporary_error"
    assert attribution["type_bien_source"] == "utilisateur"
    assert attribution["type_bien_consulte"] == "maison"
    assert EVIDENCE_TYPE_BIEN_UTILISATEUR in attribution["evidence"]
    assert "boom" not in resp.text, "aucune fuite d'exception amont"

    app.dependency_overrides.clear()


async def test_l_invariant_de_parcelle_remplace_sans_perdre_la_provenance():
    """CASE-DVF-ATTR-WIRE-11 (T-57-45/D-41) : site `app.resolve.router`. Le
    remplacement fail-closed emporte la mutation rejetee, JAMAIS la declaration
    du lecteur : l'etat `error` publie encore le type consulte et sa source."""
    etranger = {**_MUT_APPARTEMENT, "id_parcelle": _ID_P2}
    resp = await _resolve_dvf([etranger], type_bien="appartement")

    assert resp.status_code == 200, resp.text
    result = resp.json()["donnees"][0]
    assert not result["datasets"].get("dvf"), "le bloc divergent est ecarte"
    attribution = result["dvf_attribution"]
    assert attribution["status"] == "error"
    assert attribution["reason"] == "temporary_error"
    assert attribution["mutation"] is None
    assert attribution["type_bien_consulte"] == "appartement"
    assert attribution["type_bien_source"] == "utilisateur"
    assert EVIDENCE_TYPE_BIEN_UTILISATEUR in attribution["evidence"]

    app.dependency_overrides.clear()


async def test_sans_type_declare_les_trois_remplacements_restent_inconnus():
    """CASE-DVF-ATTR-WIRE-12 : le pendant negatif. Sans `?type_bien=`, aucun des
    trois etats n'invente une provenance -- le comportement d'avant ce
    correctif, verrouille."""
    etranger = {**_MUT_APPARTEMENT, "id_parcelle": _ID_P2}
    resp = await _resolve_dvf([etranger])

    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["status"] == "error"
    assert attribution["type_bien_source"] == "inconnu"
    assert attribution["type_bien_consulte"] is None
    assert attribution["evidence"] == []

    app.dependency_overrides.clear()
