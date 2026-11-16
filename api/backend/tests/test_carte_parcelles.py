"""04.10-02: the JSON routes of the interactive map, without a database.

``GET /v1/carte/parcelles/{z}/{x}/{y}`` serves the parcels of one tile. What is
proven here is what does not need PostGIS: the route has its own per-visitor
bucket (never the one of the map images, never the quota of the address page),
no API key, bounded path parameters whose refusal reflects nothing of the
input, and a cache header chosen from the served states. The reader is a stub;
the session fails on any statement.

``GET /v1/carte/{ban_id}/contexte`` says where the map of an address opens and
how far it may be moved: the bounds are a pure function, proven here at every
served latitude; the route shares the bucket of the tiles and is never stored.

The contract of the four map routes is shared with the front by three served
envelopes (``contracts/carte/exemples``): each one loads into ``ReponseApi`` of
its model and comes back identical.

The spatial reads themselves are proven on PostGIS
(``tests/integration/test_carte_parcelles_contract.py``).
"""

import json
import math
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from starlette.routing import Match

from app.adresse.router import BAN_ID_PATTERN
from app.carte import proxy, router
from app.common.schemas import ReponseApi
from app.config import settings
from app.database import get_db
from app.main import app
from app.rate_limit import _bucket_windows, _buckets
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import FULL_SCOPE

pytestmark = pytest.mark.unit

GABARIT_TUILE = "/v1/carte/parcelles/{z}/{x}/{y}"
GABARIT_IMAGE = "/v1/carte/{ban_id}/{couche}.png"
GABARIT_CONTEXTE = "/v1/carte/{ban_id}/contexte"
BAN_ID = "85191_0420_00012"
CONTEXTE = f"/v1/carte/{BAN_ID}/contexte"
TUILE = "/v1/carte/parcelles/16/32500/23000"
TITRE_VALIDATION = "Erreur de validation des donnees"


def _tuile(ventes: str = "value"):
    """A served tile of one synthetic parcel; *ventes* is the state of the sales."""
    from app.carte.schemas import (
        CollectionParcelles,
        EntiteParcelle,
        EtatParcelles,
        EtatVentes,
        GeometrieMultiPolygone,
        ProprietesParcelle,
        TuileParcelles,
    )

    compte = 2 if ventes == "value" else None
    return TuileParcelles(
        parcelles=EtatParcelles(etat="value"),
        ventes=EtatVentes(etat=ventes, periode="2021-2025", annee_debut=2021),
        cadastre_millesime="2026-07",
        collection=CollectionParcelles(
            features=[
                EntiteParcelle(
                    id="00000000ZZ0001",
                    geometry=GeometrieMultiPolygone(
                        coordinates=[[[[0.0, 0.0], [0.1, 0.0], [0.1, 0.1], [0.0, 0.0]]]]
                    ),
                    properties=ProprietesParcelle(
                        id_parcelle="00000000ZZ0001",
                        contenance=450,
                        etiquette=(0.07, 0.03),
                        ventes=compte,
                    ),
                )
            ]
        ),
    )


def _contexte(ventes: str = "value"):
    """A served context around a synthetic point."""
    from app.carte.parcelles import emprise_de_deplacement
    from app.carte.schemas import (
        CentreCarte,
        ContexteCarte,
        EtatParcelles,
        EtatVentes,
        FondCarte,
        ParcelleAdresse,
    )

    return ContexteCarte(
        centre=CentreCarte(lon=-1.4, lat=46.6),
        emprise=emprise_de_deplacement(-1.4, 46.6),
        parcelle_adresse=ParcelleAdresse(etat="value", id_parcelle="00000000ZZ0001"),
        parcelles=EtatParcelles(etat="value"),
        ventes=EtatVentes(etat=ventes, periode="2021-2025", annee_debut=2021),
        cadastre_millesime="2026-07",
        fond=FondCarte(attribution="(c) IGN", licence="Licence Ouverte 2.0"),
    )


class SessionSansLecture:
    """Any statement fails the test: these routes are proven without SQL."""

    async def execute(self, statement, params=None):
        raise AssertionError("no statement is expected in a unit test")

    async def rollback(self):
        return None


def _vider():
    proxy.CACHE.clear()
    proxy.NEGATIF.clear()
    proxy.EN_COURS.clear()
    _buckets.clear()
    _bucket_windows.clear()


@pytest.fixture(autouse=True)
def lecteur(monkeypatch):
    """Empty caches and buckets; the tile reader is a stub (built when called)."""
    _vider()
    stub = AsyncMock(side_effect=lambda *args, **kwargs: _tuile())
    # ``raising=False``: before the route exists the tests fail on what they
    # assert; ``test_the_route_hands_the_tile_to_the_reader`` proves the patch holds.
    monkeypatch.setattr(router, "lire_tuile", stub, raising=False)
    yield stub
    _vider()


@pytest.fixture(autouse=True)
def contexte(monkeypatch):
    """The context reader is a stub too; the bounds it serves are the real ones."""
    stub = AsyncMock(side_effect=lambda *args, **kwargs: _contexte())
    monkeypatch.setattr(router, "lire_contexte", stub, raising=False)
    return stub


@pytest.fixture()
async def client():
    async def _db():
        yield SessionSansLecture()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


def _routes_de(chemin: str) -> list[str]:
    """Templates of the API routes that fully match *chemin*."""
    scope = {"type": "http", "path": chemin, "method": "GET"}
    return [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.matches(scope)[0] == Match.FULL
    ]


def _route(gabarit: str) -> APIRoute:
    return next(r for r in app.routes if isinstance(r, APIRoute) and r.path == gabarit)


def _appels(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _appels(dep)


# --- The tile is served ------------------------------------------------------


async def test_the_route_hands_the_tile_to_the_reader(client, lecteur):
    reponse = await client.get(TUILE)

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("application/json")
    corps = reponse.json()
    assert set(corps) == {"statut", "donnees", "meta"}
    assert corps["donnees"]["collection"]["type"] == "FeatureCollection"
    (entite,) = corps["donnees"]["collection"]["features"]
    assert entite["type"] == "Feature" and entite["id"] == "00000000ZZ0001"
    assert entite["properties"]["etiquette"] == [0.07, 0.03]
    assert lecteur.await_count == 1
    assert lecteur.await_args.args[-3:] == (16, 32500, 23000)


# --- Own bucket, outside the image bucket and the page quota -----------------


async def test_the_parcel_bucket_is_its_own_and_answers_429_with_retry_after(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "carte_parcelles_rate_limit_per_ip", 2)
    # The image bucket is smaller: were it spent here, the second call would stop.
    monkeypatch.setattr(settings, "carte_rate_limit_per_ip", 1)

    statuts = [(await client.get(TUILE)).status_code for _ in range(2)]
    refusee = await client.get(TUILE)

    assert statuts == [200, 200]
    assert refusee.status_code == 429
    assert re.fullmatch(r"[1-9][0-9]*", refusee.headers["retry-after"])
    assert refusee.headers["cache-control"] == "no-store"
    assert refusee.headers["content-type"].startswith("application/problem+json")
    assert "donnees" not in refusee.text
    assert any(cle.startswith("carte:parcelles:ip:") for cle in _buckets)
    assert not any(cle.startswith("carte:ip:") for cle in _buckets)


async def test_the_parcel_bucket_is_per_visitor(client, monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "carte_parcelles_rate_limit_per_ip", 1)
    un = {"CF-Connecting-IP": "203.0.113.10"}
    autre = {"CF-Connecting-IP": "203.0.113.11"}

    assert (await client.get(TUILE, headers=un)).status_code == 200
    assert (await client.get(TUILE, headers=un)).status_code == 429
    assert (await client.get(TUILE, headers=autre)).status_code == 200


async def test_the_parcel_bucket_is_spent_before_any_read(client, lecteur, monkeypatch):
    monkeypatch.setattr(settings, "carte_parcelles_rate_limit_per_ip", 1)
    await client.get(TUILE)
    lecteur.reset_mock()

    refusee = await client.get("/v1/carte/parcelles/17/65000/46000")

    assert refusee.status_code == 429
    lecteur.assert_not_awaited()


def test_the_route_has_no_api_key_and_no_quota_dependency():
    from app.auth.dependencies import check_quota_hybrid

    route = _route(GABARIT_TUILE)
    appels = set(_appels(route.dependant))

    assert check_quota_hybrid not in appels
    assert router.limite_parcelles in appels
    assert router.limite_carte not in appels
    assert "security" not in app.openapi()["paths"][GABARIT_TUILE]["get"]


def test_the_limits_of_the_map_are_the_planned_ones():
    defauts = {nom: champ.default for nom, champ in type(settings).model_fields.items()}

    # The settings of the static map did not move.
    assert defauts["carte_rate_limit_per_ip"] == 60
    assert defauts["carte_rate_limit_amont"] == 600
    assert defauts["carte_rate_limit_resolution"] == 600
    assert defauts["carte_cache_negatif_ttl_secondes"] == 600
    assert defauts["carte_cache_negatif_entrees"] == 10_000
    assert defauts["carte_cache_octets"] == 64 * 1024 * 1024
    assert defauts["carte_cache_ttl_secondes"] == 1_814_400
    assert defauts["carte_image_max_octets"] == 2 * 1024 * 1024
    assert defauts["carte_wms_url"] == "https://data.geopf.fr/wms-r/wms"
    # The parcels of a view are several tiles: their own, wider, bucket.
    assert defauts["carte_parcelles_rate_limit_per_ip"] == 600
    assert defauts["carte_parcelles_cache_secondes"] == 3600
    # A tile is served whole: no setting caps its parcels.
    carte = [nom for nom in defauts if nom.startswith("carte_")]
    assert not [nom for nom in carte if "plafond" in nom or "max_parcelles" in nom]


# --- Bounded path, nothing of the input reflected ----------------------------


@pytest.mark.parametrize(
    ("chemin", "saisie"),
    [
        ("15/16250/11500", "16250"),
        ("20/524287/524286", "524287"),
        ("16/65536/23000", "65536"),
        ("16/32500/65536", "65536"),
        ("17/131072/46000", "131072"),
        ("19/524288/46000", "524288"),
        ("16/-7654/23000", "7654"),
        ("16/zzqqxx/23000", "zzqqxx"),
        ("16/32500/23000.75", "23000.75"),
        ("16/98765432109876543210/23000", "98765432109876543210"),
    ],
)
async def test_a_tile_out_of_bounds_is_a_constant_problem(
    client, lecteur, chemin, saisie
):
    reponse = await client.get(f"/v1/carte/parcelles/{chemin}")

    assert reponse.status_code == 422
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    corps = reponse.json()
    assert corps["title"] == TITRE_VALIDATION
    assert corps["instance"] == GABARIT_TUILE
    assert saisie not in reponse.text
    lecteur.assert_not_awaited()


@pytest.mark.parametrize("chemin", ["16/65535/65535", "19/524287/0", "16/0/0"])
async def test_the_edges_of_the_grid_are_served(client, chemin):
    assert (await client.get(f"/v1/carte/parcelles/{chemin}")).status_code == 200


async def test_the_query_string_is_ignored(client, lecteur):
    reponse = await client.get(TUILE, params={"z": "3", "limite": "999999"})

    assert reponse.status_code == 200
    assert lecteur.await_args.args[-3:] == (16, 32500, 23000)


# --- The tile path never reaches the image route -----------------------------


def test_each_map_path_matches_one_route_only():
    assert _routes_de("/v1/carte/parcelles/16/1/2") == [GABARIT_TUILE]
    assert _routes_de("/v1/carte/85191_0420_00012/plan.png") == [GABARIT_IMAGE]
    assert _routes_de(CONTEXTE) == [GABARIT_CONTEXTE]
    assert _routes_de(f"{CONTEXTE}.png") == [GABARIT_IMAGE]
    # Look-alikes of a tile path are no route at all.
    assert _routes_de("/v1/carte/parcelles/16/1") == []
    assert _routes_de("/v1/carte/parcelles/16/1/2/3") == []


@pytest.mark.parametrize("segment", ["parcelles", "fond", "parcelle", "contexte"])
def test_no_map_segment_can_be_read_as_a_ban_key(segment):
    assert re.match(BAN_ID_PATTERN, segment) is None


async def test_a_tile_request_never_calls_the_image_reader(client, monkeypatch):
    point = AsyncMock()
    monkeypatch.setattr(router, "point_de_la_cle", point)

    reponse = await client.get("/v1/carte/parcelles/16/1/2")

    assert reponse.status_code == 200
    assert not reponse.content.startswith(b"\x89PNG")
    point.assert_not_awaited()


# --- Cache header chosen from the states -------------------------------------


async def test_a_stable_tile_is_public_for_the_configured_time(client, monkeypatch):
    monkeypatch.setattr(settings, "carte_parcelles_cache_secondes", 1234)

    reponse = await client.get(TUILE)

    assert reponse.headers["cache-control"] == "public, max-age=1234"
    assert reponse.headers["x-content-type-options"] == "nosniff"


async def test_a_tile_out_of_coverage_is_stable_too(client, lecteur):
    lecteur.side_effect = lambda *a, **k: _tuile(ventes="out_of_coverage")

    reponse = await client.get(TUILE)

    assert reponse.headers["cache-control"] == "public, max-age=3600"


async def test_a_degraded_tile_is_never_stored(client, lecteur):
    lecteur.side_effect = lambda *a, **k: _tuile(ventes="temporary_error")

    reponse = await client.get(TUILE)

    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"
    assert reponse.json()["donnees"]["collection"]["features"]


async def test_a_failed_cadastre_read_is_a_constant_503(client, lecteur):
    from app.carte.parcelles import ParcellesIndisponiblesError

    lecteur.side_effect = ParcellesIndisponiblesError()

    reponse = await client.get(TUILE)

    assert reponse.status_code == 503
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    assert reponse.json() == {
        "type": "about:blank",
        "title": "Parcelles momentanement indisponibles",
        "status": 503,
        "instance": GABARIT_TUILE,
    }
    assert "features" not in reponse.text


# --- OpenAPI ------------------------------------------------------------------


def test_the_tile_route_is_documented_in_ascii():
    operation = app.openapi()["paths"][GABARIT_TUILE]["get"]

    assert set(operation["responses"]) >= {"200", "422", "429", "503"}
    assert operation["summary"].isascii() and operation["description"].isascii()
    assert {p["name"] for p in operation["parameters"]} == {"z", "x", "y"}
    bornes = {p["name"]: p["schema"] for p in operation["parameters"]}
    assert (bornes["z"]["minimum"], bornes["z"]["maximum"]) == (16, 19)


# --- Bounds the map may be moved within (C6) ---------------------------------

# Size of the world at zoom 16 in the projection of the map, and the widest
# view of the dialog (UI-SPEC 2.2): 912 x 520 px.
MONDE_Z16 = 256 * 2**16
DEMI_VUE = (456, 260)


def _pixel(lon: float, lat: float) -> tuple[float, float]:
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2
    return (lon + 180) / 360 * MONDE_Z16, y * MONDE_Z16


def _degres(px: float, py: float) -> tuple[float, float]:
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / MONDE_Z16))))
    return px / MONDE_Z16 * 360 - 180, lat


# Latitudes of the served territories, north to south; longitudes are arbitrary.
POINTS = [
    (2.4, 51.0),
    (-1.4, 46.5),
    (9.2, 41.3),
    (-61.5, 16.0),
    (-53.0, 5.0),
    (55.5, -21.0),
]


@pytest.mark.parametrize(("lon", "lat"), POINTS)
def test_the_bounds_hold_the_widest_view_at_zoom_16(lon, lat):
    from app.carte.parcelles import emprise_de_deplacement

    emprise = emprise_de_deplacement(lon, lat)

    px, py = _pixel(lon, lat)
    for dx in (-DEMI_VUE[0], DEMI_VUE[0]):
        for dy in (-DEMI_VUE[1], DEMI_VUE[1]):
            coin_lon, coin_lat = _degres(px + dx, py + dy)
            assert emprise.ouest <= coin_lon <= emprise.est
            assert emprise.sud <= coin_lat <= emprise.nord
    assert emprise.ouest < lon < emprise.est and emprise.sud < lat < emprise.nord
    for borne in (emprise.ouest, emprise.sud, emprise.est, emprise.nord):
        assert round(borne, 6) == borne


def test_the_bounds_are_about_two_kilometres_wide_in_mainland_france():
    from app.carte.parcelles import emprise_de_deplacement

    emprise = emprise_de_deplacement(-1.4, 46.5)

    rayon = 6_371_008.8
    largeur = math.radians(emprise.est - emprise.ouest) * rayon
    largeur *= math.cos(math.radians(46.5))
    hauteur = math.radians(emprise.nord - emprise.sud) * rayon
    assert 1900 < largeur < 2300
    assert 1900 < hauteur < 2300


def test_the_bounds_are_wider_on_the_ground_near_the_equator():
    """A square in pixels, not in metres: the plan says so, Ianis decides at plan 11."""
    from app.carte.parcelles import emprise_de_deplacement

    nord = emprise_de_deplacement(2.4, 51.0)
    equateur = emprise_de_deplacement(-53.0, 5.0)

    assert (equateur.nord - equateur.sud) > (nord.nord - nord.sud)
    assert (equateur.est - equateur.ouest) == pytest.approx(
        nord.est - nord.ouest, abs=2e-6
    )


def test_the_half_side_of_the_bounds_is_a_setting():
    defauts = {nom: champ.default for nom, champ in type(settings).model_fields.items()}

    assert defauts["carte_emprise_demi_cote_px_z16"] == 640


# --- Context of an address ----------------------------------------------------


async def test_the_context_is_served_and_never_stored(client, contexte):
    reponse = await client.get(CONTEXTE)

    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"
    corps = reponse.json()
    assert set(corps) == {"statut", "donnees", "meta"}
    assert set(corps["donnees"]) == {
        "centre",
        "emprise",
        "parcelle_adresse",
        "parcelles",
        "ventes",
        "cadastre_millesime",
        "fond",
    }
    assert contexte.await_count == 1
    assert contexte.await_args.args[-1] == BAN_ID


async def test_the_context_spends_the_bucket_of_the_parcels(client, monkeypatch):
    monkeypatch.setattr(settings, "carte_parcelles_rate_limit_per_ip", 2)
    monkeypatch.setattr(settings, "carte_rate_limit_per_ip", 1)

    assert (await client.get(CONTEXTE)).status_code == 200
    assert (await client.get(TUILE)).status_code == 200
    refusee = await client.get(CONTEXTE)

    assert refusee.status_code == 429
    assert re.fullmatch(r"[1-9][0-9]*", refusee.headers["retry-after"])
    assert refusee.headers["cache-control"] == "no-store"
    assert not any(cle.startswith("carte:ip:") for cle in _buckets)


def test_the_context_has_no_api_key_and_no_quota_dependency():
    from app.auth.dependencies import check_quota_hybrid

    appels = set(_appels(_route(GABARIT_CONTEXTE).dependant))

    assert check_quota_hybrid not in appels
    assert router.limite_parcelles in appels
    operation = app.openapi()["paths"][GABARIT_CONTEXTE]["get"]
    assert "security" not in operation
    assert operation["summary"].isascii() and operation["description"].isascii()
    assert {p["name"] for p in operation["parameters"]} == {"ban_id"}
    assert set(operation["responses"]) >= {"200", "404", "422", "429", "503"}


@pytest.mark.parametrize(
    "cle",
    ["pas-une-cle", "85191_0420_00012 x", "7" * 60, "parcelles", "85191_0420_0001z"],
)
async def test_a_malformed_ban_key_is_a_constant_problem(client, contexte, cle):
    reponse = await client.get(f"/v1/carte/{cle}/contexte")

    assert reponse.status_code == 422
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    corps = reponse.json()
    assert corps["title"] == TITRE_VALIDATION
    assert corps["instance"] == GABARIT_CONTEXTE
    assert cle not in reponse.text
    contexte.assert_not_awaited()


@pytest.mark.parametrize(
    ("issue", "statut", "code"),
    [
        ("inconnue", 404, "adresse_inconnue"),
        ("voie", 404, "voie_sans_numero"),
        ("panne", 503, "adresse_indisponible"),
        ("sans_point", 404, "adresse_inconnue"),
    ],
)
async def test_address_errors_of_the_context_are_constant_problems(
    client, contexte, issue, statut, code
):
    from app.adresse.service import (
        AdresseInconnueError,
        AdresseIndisponibleError,
        VoieSansNumeroError,
    )

    effets = {
        "inconnue": AdresseInconnueError(),
        "voie": VoieSansNumeroError(),
        "panne": AdresseIndisponibleError(),
    }
    # ``sans_point``: no single BAN point (out of coverage, rows that disagree).
    contexte.side_effect = effets.get(issue, lambda *args, **kwargs: None)

    reponse = await client.get(CONTEXTE)

    assert reponse.status_code == statut
    assert reponse.headers["content-type"].startswith("application/problem+json")
    assert reponse.headers["cache-control"] == "no-store"
    assert reponse.json()["code"] == code
    assert reponse.json()["instance"] == GABARIT_CONTEXTE
    assert BAN_ID not in reponse.text


async def test_a_degraded_context_is_not_stored_either(client, contexte):
    contexte.side_effect = lambda *a, **k: _contexte(ventes="temporary_error")

    reponse = await client.get(CONTEXTE)

    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"


# --- The shared contract: three served envelopes ------------------------------

DEPOT = Path(__file__).resolve().parents[3]
EXEMPLES = DEPOT / "contracts/carte/exemples"
NOMS = ["contexte.json", "tuile-parcelles.json", "ventes-parcelle.json"]


def _exemple(nom: str) -> dict:
    return json.loads((EXEMPLES / nom).read_text(encoding="utf-8"))


def _modele(nom: str):
    from app.carte import schemas

    return {
        "contexte.json": schemas.ContexteCarte,
        "tuile-parcelles.json": schemas.TuileParcelles,
        "ventes-parcelle.json": schemas.VentesParcelleCarte,
    }[nom]


def _cles(valeur) -> set[str]:
    """Every key of a JSON value, at any depth."""
    if isinstance(valeur, dict):
        return set(valeur) | {c for v in valeur.values() for c in _cles(v)}
    if isinstance(valeur, list):
        return {c for v in valeur for c in _cles(v)}
    return set()


@pytest.mark.parametrize("nom", NOMS)
def test_each_example_is_a_served_envelope_that_comes_back_identical(nom):
    exemple = _exemple(nom)

    assert list(exemple) == ["statut", "donnees", "meta"]
    servi = ReponseApi[_modele(nom)].model_validate(exemple).model_dump(mode="json")
    # Same keys in the same order, same numbers: nothing dropped, nothing added.
    assert json.dumps(servi, ensure_ascii=False) == json.dumps(
        exemple, ensure_ascii=False
    )
    assert exemple["meta"]["source"]


def test_the_three_examples_tell_one_story():
    from app.carte.parcelles import emprise_de_deplacement

    contexte = _exemple("contexte.json")["donnees"]
    tuile = _exemple("tuile-parcelles.json")["donnees"]
    ventes = _exemple("ventes-parcelle.json")["donnees"]

    centre = contexte["centre"]
    assert contexte["emprise"] == emprise_de_deplacement(**centre).model_dump()
    entites = {e["id"]: e for e in tuile["collection"]["features"]}
    assert list(entites) == sorted(entites)
    # The tile carries the parcel of the address of the context...
    adresse = contexte["parcelle_adresse"]["id_parcelle"]
    assert contexte["parcelle_adresse"]["etat"] == "value" and adresse in entites
    (anneau,) = entites[adresse]["geometry"]["coordinates"][0]
    lons, lats = [p[0] for p in anneau], [p[1] for p in anneau]
    assert min(lons) < centre["lon"] < max(lons)
    assert min(lats) < centre["lat"] < max(lats)
    # ... neighbours with and without a sale, and one without a surface.
    voisines = [e["properties"] for cle, e in entites.items() if cle != adresse]
    assert {p["ventes"] > 0 for p in voisines} == {True, False}
    assert [p["contenance"] for p in voisines].count(None) == 1
    for entite in entites.values():
        (anneau,) = entite["geometry"]["coordinates"][0]
        lon, lat = entite["properties"]["etiquette"]
        assert min(p[0] for p in anneau) < lon < max(p[0] for p in anneau)
        assert min(p[1] for p in anneau) < lat < max(p[1] for p in anneau)
    # One period, one vintage, one count across the three answers.
    assert contexte["ventes"] == tuile["ventes"]
    assert contexte["parcelles"] == tuile["parcelles"]
    assert contexte["cadastre_millesime"] == tuile["cadastre_millesime"]
    assert (ventes["periode"], ventes["annee_debut"]) == (
        tuile["ventes"]["periode"],
        tuile["ventes"]["annee_debut"],
    )
    assert ventes["total"] == entites[adresse]["properties"]["ventes"]
    assert ventes["total"] == len(ventes["ventes"])


def test_the_sales_example_carries_every_case_of_a_group():
    ventes = _exemple("ventes-parcelle.json")["donnees"]

    assert ventes["etat"] == "value" and ventes["par_page"] == 5
    assert len(ventes["groupes"]) == len(ventes["ventes"]) <= ventes["par_page"]
    # A withheld price leaves no amount behind.
    retenues = [carte for carte in ventes["ventes"] if not carte["prix_affiche"]]
    assert retenues
    for carte in retenues:
        assert carte["prix"] is None and carte["prix_m2"] is None
        assert carte["minimisation"]["motif"] == "vente_isolee_recente"
    assert any(carte["prix_affiche"] and carte["prix"] for carte in ventes["ventes"])
    # A sale of this parcel alone has no group.
    assert None in ventes["groupes"]
    groupes = [groupe for groupe in ventes["groupes"] if groupe is not None]
    # Drawn with a served total; incomplete total; too many parcels to draw.
    assert any(g["parcelles"] and g["surface_totale_m2"] for g in groupes)
    assert any(g["surface_totale_m2"] is None for g in groupes)
    assert any(g["parcelles"] is None for g in groupes)
    for groupe in groupes:
        assert groupe["total"] >= 2 and groupe["autres"] == groupe["total"] - 1
        dessinables = (
            groupe["total"] - groupe["absentes_du_cadastre"] - groupe["hors_emprise"]
        )
        if groupe["parcelles"] is not None:
            assert len(groupe["parcelles"]) == dessinables
        if groupe["absentes_du_cadastre"]:
            assert groupe["surface_totale_m2"] is None


def test_no_example_identifies_a_sale():
    for nom in NOMS:
        cles = _cles(_exemple(nom))
        assert not [cle for cle in cles if "mutation" in cle], nom
    brut = (EXEMPLES / "ventes-parcelle.json").read_text(encoding="utf-8")
    assert "id_mutation" not in brut


def test_the_two_new_routes_are_in_the_published_contract():
    contrat = json.loads(
        (DEPOT / "contracts/openapi.v1.json").read_text(encoding="utf-8")
    )

    for gabarit in (GABARIT_TUILE, GABARIT_CONTEXTE):
        operation = contrat["paths"][gabarit]["get"]
        assert operation["summary"].isascii() and operation["description"].isascii()
