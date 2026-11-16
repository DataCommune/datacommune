"""04.10-02 (SEM-01, SEM-02, LIEU-01): the JSON routes of the map on PostGIS.

``GET /v1/carte/parcelles/{z}/{x}/{y}`` serves every parcel of our base that
touches one tile, whole, with its surface, a label point and its number of
sales. Proven here on synthetic squares laid on arbitrary tiles: nothing
depends on where the tiles are, only on their edges.

- a tile is never cut: no cap, a parcel astride two tiles is whole in both;
- out of coverage, zero parcel, missing surface and failed read are four
  distinct answers; an outage of the sales count never hides the parcels;
- the number of sales is the count of ``dvf_compte_par_id_parcelle``;
- a degraded answer is never stored (``no-store``), a stable one is public.

``GET /v1/carte/{ban_id}/contexte`` serves the BAN point of an address, the
bounds the map may be moved within and the parcel that contains the point,
never the nearest one, with the states and the period a tile of the place
serves.

The scope is written by the production path and read by the real scope
dependency; only the session is overridden.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_carte_parcelles_contract.py
"""

import json
import math
import re
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg.rows import dict_row
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.carte.router import carte_router
from app.common.exceptions import register_exception_handlers
from app.database import get_db
from app.dvf.queries import dvf_compte_par_id_parcelle
from app.rate_limit import _bucket_windows, _buckets
from tests.integration.instance_scope_fixture import install_scope_at
from tests.integration.test_adresse_ventes_contract import (
    Api,
    Mutation,
    _rect,
    injected,
    insert_adresse,
    insert_mutation,
    link_parcelles,
    migrate,
    publish,
)

pytestmark = pytest.mark.destructive

TOUT = ("ban", "cadastre", "dvf")
HEBERGES = ("44", "85")
PERIODE = "2021-2025"
MILLESIME = "2026-07"
Z = 16
# Arbitrary tiles: nothing depends on where they are, only on their edges.
TUILE = (32500, 23000)
VOISINE = (TUILE[0] + 1, TUILE[1])
DENSE = (TUILE[0], TUILE[1] + 2)
# No departement drawn there: the sea, or a land we hold no boundary of.
EN_MER = (TUILE[0] + 20, TUILE[1])
# A departement of the referential that the scope of the test does not host.
AILLEURS = (TUILE[0], TUILE[1] + 10)
# West half in a hosted departement, east half in the other one.
MIXTE = (TUILE[0] + 10, TUILE[1] + 10)
# Half side of a square as a share of the tile side.
DEMI = 0.04
NOMBRE_DENSE = 200

AVEC_SURFACE = "85000000ZA0001"
SANS_SURFACE = "85000000ZA0002"
A_CHEVAL = "85000000ZA0003"
CHEZ_LA_VOISINE = "85000000ZA0004"
NON_HEBERGEE = "49000000ZA0001"
MIXTE_HEBERGEE = "85000000ZB0001"
MIXTE_NON_HEBERGEE = "49000000ZB0001"
SURFACE = 450
# Two parcels sharing one edge, in a tile of their own.
FRONTIERE = (TUILE[0] + 2, TUILE[1])
A_L_OUEST = "85000000ZC0001"
A_L_EST = "85000000ZC0002"
# Synthetic BAN keys: the first two characters are the departement.
ADRESSE = "85000_0001_00001"
ADRESSE_FRONTIERE = "85000_0002_00001"
ADRESSE_HORS_PARCELLE = "85000_0003_00001"
ADRESSE_CONTRADICTOIRE = "85000_0004_00001"
ADRESSE_AILLEURS = "49000_0001_00001"
INCONNUE = "85000_0001_00099"
VOIE = "85000_0001"


def bornes(tuile: tuple[int, int]) -> tuple[float, float, float, float]:
    """West, south, east and north edges of a zoom-16 tile, in degrees."""
    n = 2**Z

    def lat(rang: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * rang / n))))

    x, y = tuile
    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def cadre(tuile: tuple[int, int], x0: float, y0: float, x1: float, y1: float):
    """Corners of two shares of the tile, counted from its south-west corner."""
    ouest, sud, est, nord = bornes(tuile)
    largeur, hauteur = est - ouest, nord - sud
    return (
        ouest + x0 * largeur,
        sud + y0 * hauteur,
        ouest + x1 * largeur,
        sud + y1 * hauteur,
    )


def carre(tuile: tuple[int, int], fx: float, fy: float, demi: float = DEMI) -> str:
    """A square centred at the share (*fx*, *fy*) of the tile."""
    return _rect(*cadre(tuile, fx - demi, fy - demi, fx + demi, fy + demi))


def point(tuile: tuple[int, int], fx: float, fy: float) -> tuple[float, float]:
    """Longitude and latitude of the share (*fx*, *fy*) of the tile."""
    lon, lat, _, _ = cadre(tuile, fx, fy, fx, fy)
    return lon, lat


def territoire(*cadres: tuple[float, float, float, float]) -> str:
    """A departement made of rectangles (never a real boundary)."""
    polygones = ", ".join(
        f"(({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}))"
        for x0, y0, x1, y1 in cadres
    )
    return f"MULTIPOLYGON({polygones})"


def dense(rang: int) -> tuple[str, str]:
    """Parcel *rang* of the dense tile: a 20 x 10 grid of small squares."""
    colonne, ligne = rang % 20, rang // 20
    return (
        f"85000000ZD{rang:04d}",
        carre(DENSE, 0.05 + colonne * 0.045, 0.05 + ligne * 0.09, demi=0.015),
    )


def insert_parcelle(conn, id_: int, id_parcelle: str, wkt: str, contenance=None):
    conn.execute(
        "INSERT INTO parcelles (id, code_departement, id_parcelle, commune, "
        "contenance, geometrie) VALUES (%s, %s, %s, %s, %s, "
        "ST_GeomFromText(%s, 4326))",
        (id_, id_parcelle[:2], id_parcelle, id_parcelle[:5], contenance, wkt),
    )


# Sales: three distinct positioned mutations on the parcel with a surface (one
# shared with the parcel astride, one whose DVF row is duplicated), and one of
# unknown position, which is in no count (D-03).
SEULE = Mutation("2021-T-1", date(2021, 2, 10), 180_000, 80, (AVEC_SURFACE,))
A_DEUX = Mutation("2024-T-2", date(2024, 6, 1), 230_000, 95, (AVEC_SURFACE, A_CHEVAL))
DOUBLEE = Mutation("2023-T-3", date(2023, 9, 5), 201_000, 90, (AVEC_SURFACE,))
SANS_POSITION = Mutation("2022-T-4", date(2022, 4, 4), 150_000, 70, (AVEC_SURFACE,))


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    migrate(isolated_pg_dsn)
    with psycopg.connect(isolated_pg_dsn) as conn:
        heberge = territoire(
            cadre(TUILE, -0.5, -2.5, 2.5, 1.5), cadre(MIXTE, -0.5, -0.5, 0.5, 1.5)
        )
        autre = territoire(
            cadre(AILLEURS, -0.5, -0.5, 1.5, 1.5), cadre(MIXTE, 0.5, -0.5, 1.5, 1.5)
        )
        for code, nom, wkt in (("85", "Hébergé", heberge), ("49", "Autre", autre)):
            conn.execute(
                "INSERT INTO geo_departements (code, nom, geometry) VALUES "
                "(%s, %s, ST_GeomFromText(%s, 4326))",
                (code, nom, wkt),
            )
        parcelles = {
            AVEC_SURFACE: (carre(TUILE, 0.25, 0.25), SURFACE),
            SANS_SURFACE: (carre(TUILE, 0.25, 0.75), None),
            # Centre in the tile, east side in its neighbour.
            A_CHEVAL: (carre(TUILE, 0.99, 0.50), 120),
            CHEZ_LA_VOISINE: (carre(VOISINE, 0.50, 0.50), 300),
            NON_HEBERGEE: (carre(AILLEURS, 0.50, 0.50), 200),
            MIXTE_HEBERGEE: (carre(MIXTE, 0.25, 0.50), 210),
            MIXTE_NON_HEBERGEE: (carre(MIXTE, 0.75, 0.50), 220),
            A_L_OUEST: (_rect(*cadre(FRONTIERE, 0.20, 0.20, 0.25, 0.30)), 60),
            A_L_EST: (_rect(*cadre(FRONTIERE, 0.25, 0.20, 0.30, 0.30)), 70),
            **{dense(rang)[0]: (dense(rang)[1], 50) for rang in range(NOMBRE_DENSE)},
        }
        adresses = (
            (ADRESSE, point(TUILE, 0.25, 0.25)),
            (ADRESSE_FRONTIERE, point(FRONTIERE, 0.25, 0.25)),
            (ADRESSE_HORS_PARCELLE, point(TUILE, 0.60, 0.10)),
            # Two BAN rows of one key that disagree on the point.
            (ADRESSE_CONTRADICTOIRE, point(TUILE, 0.70, 0.10)),
            (ADRESSE_CONTRADICTOIRE, point(TUILE, 0.80, 0.10)),
            (ADRESSE_AILLEURS, point(AILLEURS, 0.50, 0.50)),
        )
        for rang, (ban_id, lon_lat) in enumerate(adresses):
            insert_adresse(conn, 95_300_001 + rang, ban_id, lon_lat)
        for rang, (id_parcelle, (wkt, contenance)) in enumerate(parcelles.items()):
            insert_parcelle(conn, 97_100_001 + rang, id_parcelle, wkt, contenance)

        ident = iter(range(98_300_001, 98_400_000))
        for mutation in (SEULE, A_DEUX):
            insert_mutation(conn, next(ident), mutation)
            link_parcelles(conn, mutation)
        insert_mutation(conn, next(ident), DOUBLEE)
        insert_mutation(conn, next(ident), DOUBLEE)
        link_parcelles(conn, DOUBLEE)
        insert_mutation(conn, next(ident), SANS_POSITION, point=(None, None))
        link_parcelles(conn, SANS_POSITION)
        for nom, libelle in (("dvf", PERIODE), ("cadastre", MILLESIME)):
            conn.execute(
                "INSERT INTO dataset_metadata (dataset_name, status, "
                "source_period_label) VALUES (%s, 'disponible', %s) "
                "ON CONFLICT (dataset_name) DO UPDATE SET source_period_label = "
                "EXCLUDED.source_period_label",
                (nom, libelle),
            )
    return isolated_pg_dsn


@pytest.fixture
async def api(migrated) -> AsyncIterator[Api]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(carte_router)
    application.dependency_overrides[get_db] = db
    transport = httpx.ASGITransport(app=application)
    _buckets.clear()
    _bucket_windows.clear()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield Api(c, statements)
    finally:
        await engine.dispose()
        _buckets.clear()
        _bucket_windows.clear()


def scope(dsn, datasets=TOUT):
    install_scope_at(dsn, sorted(datasets), HEBERGES)


def lectures(api: Api, table: str) -> list[str]:
    """Statements reading *table* itself, not a table whose name ends with it."""
    nom = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(table)}(?![A-Za-z0-9_])")
    return [sql for sql in api.statements if nom.search(sql)]


# The publication of DVF follows the table it measured: a test that replaces
# ``mutations_grain`` publishes the one in place, as an import does.
PUBLIER_DVF = (
    "INSERT INTO etl_publications (generation, dataset_name, table_name, status, "
    "relation_oids) VALUES (gen_random_uuid(), 'dvf', 'mutations_grain', "
    "'published', jsonb_build_object('mutations_grain', "
    "to_regclass('mutations_grain')::oid))"
)
GABARIT_TUILE = "/v1/carte/parcelles/{z}/{x}/{y}"
STABLE = "public, max-age=3600"


async def tuile(api: Api, ou: tuple[int, int], status: int = 200, cache=STABLE):
    response = await api.client.get(f"/v1/carte/parcelles/{Z}/{ou[0]}/{ou[1]}")
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == cache
    if status != 200:
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["instance"] == GABARIT_TUILE
        return response.json()
    return response.json()["donnees"]


def par_id(donnees: dict) -> dict[str, dict]:
    entites = donnees["collection"]["features"]
    assert [e["id"] for e in entites] == sorted(e["id"] for e in entites)
    assert all(e["id"] == e["properties"]["id_parcelle"] for e in entites)
    return {e["id"]: e for e in entites}


def emprise_de(entite: dict) -> tuple[float, float, float, float]:
    points = [
        point
        for polygone in entite["geometry"]["coordinates"]
        for anneau in polygone
        for point in anneau
    ]
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    return min(lons), min(lats), max(lons), max(lats)


# --- The tile, whole ---------------------------------------------------------------


async def test_tracer_a_tile_serves_its_three_parcels_whole(api, migrated):
    scope(migrated)

    donnees = await tuile(api, TUILE)

    assert donnees["parcelles"] == {"etat": "value", "motif": None}
    assert donnees["ventes"] == {
        "etat": "value",
        "motif": None,
        "periode": PERIODE,
        "annee_debut": 2021,
    }
    assert donnees["cadastre_millesime"] == MILLESIME
    assert donnees["collection"]["type"] == "FeatureCollection"
    entites = par_id(donnees)
    assert set(entites) == {AVEC_SURFACE, SANS_SURFACE, A_CHEVAL}
    for entite in entites.values():
        assert entite["type"] == "Feature"
        assert entite["geometry"]["type"] == "MultiPolygon"
        assert set(entite["properties"]) == {
            "id_parcelle",
            "contenance",
            "etiquette",
            "ventes",
        }
        # The label point lies inside the parcel (here a square: inside its box).
        ouest, sud, est, nord = emprise_de(entite)
        lon, lat = entite["properties"]["etiquette"]
        assert ouest < lon < est and sud < lat < nord
    assert entites[AVEC_SURFACE]["properties"]["contenance"] == SURFACE
    # A missing surface is served as missing, never as zero.
    assert entites[SANS_SURFACE]["properties"]["contenance"] is None
    assert [entites[i]["properties"]["ventes"] for i in sorted(entites)] == [3, 0, 1]


async def test_the_parcel_astride_is_whole_in_both_tiles(api, migrated):
    scope(migrated)

    ici = par_id(await tuile(api, TUILE))
    voisine = par_id(await tuile(api, VOISINE))

    assert set(voisine) == {A_CHEVAL, CHEZ_LA_VOISINE}
    assert ici[A_CHEVAL] == voisine[A_CHEVAL]
    # Whole: the square reaches past the east edge of the first tile and is
    # served with its four corners, to six decimals.
    ouest, sud, est, nord = cadre(
        TUILE, 0.99 - DEMI, 0.5 - DEMI, 0.99 + DEMI, 0.5 + DEMI
    )
    assert est > bornes(TUILE)[2] > ouest
    assert emprise_de(ici[A_CHEVAL]) == pytest.approx((ouest, sud, est, nord), abs=1e-6)
    (anneau,) = ici[A_CHEVAL]["geometry"]["coordinates"][0]
    assert all(round(v, 6) == v for point in anneau for v in point)


async def test_a_dense_tile_is_served_without_a_cap(api, migrated):
    scope(migrated)

    entites = par_id(await tuile(api, DENSE))

    assert len(entites) == NOMBRE_DENSE
    assert set(entites) == {dense(rang)[0] for rang in range(NOMBRE_DENSE)}
    assert {e["properties"]["ventes"] for e in entites.values()} == {0}


async def test_the_number_of_sales_is_the_count_of_the_parcel_page(api, migrated):
    """The population of ``dvf_compte_par_id_parcelle``: dot and panel agree."""
    scope(migrated)

    servis = {
        id_parcelle: entite["properties"]["ventes"]
        for ou in (TUILE, VOISINE)
        for id_parcelle, entite in par_id(await tuile(api, ou)).items()
    }

    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    try:
        async with async_sessionmaker(engine)() as session:
            attendus = {
                id_parcelle: (await dvf_compte_par_id_parcelle(session, id_parcelle))[
                    "nombre"
                ]
                for id_parcelle in servis
            }
    finally:
        await engine.dispose()
    assert servis == attendus
    # Three distinct positioned mutations: the duplicated row counts once, the
    # mutation of unknown position not at all, the shared one on both parcels.
    assert servis == {
        AVEC_SURFACE: 3,
        SANS_SURFACE: 0,
        A_CHEVAL: 1,
        CHEZ_LA_VOISINE: 0,
    }


async def test_a_tile_carries_a_count_only_never_a_sale(api, migrated):
    """T-04.10-02: no price, no date, no mutation identifier in a tile."""
    scope(migrated)

    reponse = await api.client.get(f"/v1/carte/parcelles/{Z}/{TUILE[0]}/{TUILE[1]}")
    brut = reponse.text

    assert reponse.status_code == 200 and AVEC_SURFACE in brut
    for interdit in ("2021-T-1", "2024-T-2", "id_mutation", "180000", "230000"):
        assert interdit not in brut
    assert "2024-06-01" not in brut and "valeur_fonciere" not in brut


# --- Four distinct answers ---------------------------------------------------------


async def test_a_tile_at_sea_is_zero_parcel_without_reading_the_cadastre(api, migrated):
    scope(migrated)
    api.statements.clear()

    donnees = await tuile(api, EN_MER)

    assert donnees["parcelles"] == {"etat": "value", "motif": None}
    assert donnees["collection"] == {"type": "FeatureCollection", "features": []}
    assert lectures(api, "parcelles") == []
    assert lectures(api, "geo_departements") != []


async def test_a_departement_not_hosted_is_out_of_coverage_without_cadastre_read(
    api, migrated
):
    scope(migrated)
    api.statements.clear()

    donnees = await tuile(api, AILLEURS)

    assert donnees["parcelles"] == {
        "etat": "out_of_coverage",
        "motif": "territoire_hors_perimetre",
    }
    assert donnees["ventes"]["etat"] == "out_of_coverage"
    assert donnees["ventes"]["motif"] == "territoire_hors_perimetre"
    assert donnees["collection"]["features"] == []
    assert lectures(api, "parcelles") == []
    assert lectures(api, "mutations_grain") == []


async def test_the_cadastre_not_hosted_is_out_of_coverage_without_cadastre_read(
    api, migrated
):
    scope(migrated, ("ban", "dvf"))
    api.statements.clear()

    donnees = await tuile(api, TUILE)

    assert donnees["parcelles"] == {
        "etat": "out_of_coverage",
        "motif": "dataset_non_heberge",
    }
    assert donnees["collection"]["features"] == []
    assert donnees["cadastre_millesime"] is None
    assert lectures(api, "parcelles") == []
    # No departement to qualify: not even the referential is read.
    assert lectures(api, "geo_departements") == []


async def test_dvf_not_hosted_serves_the_parcels_without_any_count(api, migrated):
    scope(migrated, ("ban", "cadastre"))
    api.statements.clear()

    donnees = await tuile(api, TUILE)

    assert donnees["parcelles"] == {"etat": "value", "motif": None}
    assert donnees["ventes"] == {
        "etat": "out_of_coverage",
        "motif": "dataset_non_heberge",
        "periode": None,
        "annee_debut": None,
    }
    entites = par_id(donnees)
    assert set(entites) == {AVEC_SURFACE, SANS_SURFACE, A_CHEVAL}
    # Not counted is null, never zero.
    assert {e["properties"]["ventes"] for e in entites.values()} == {None}
    assert lectures(api, "mutations_grain") == []
    assert lectures(api, "mutation_parcelles") == []


async def test_a_tile_astride_the_scope_serves_the_hosted_side_and_says_so(
    api, migrated
):
    scope(migrated)
    api.statements.clear()

    donnees = await tuile(api, MIXTE)

    # The parcels of the hosted departement, with the reason the rest is absent.
    assert donnees["parcelles"] == {
        "etat": "value",
        "motif": "territoire_hors_perimetre",
    }
    assert set(par_id(donnees)) == {MIXTE_HEBERGEE}
    assert MIXTE_NON_HEBERGEE not in str(donnees)


async def test_a_refused_dvf_publication_keeps_the_parcels_and_is_not_stored(
    api, migrated
):
    scope(migrated)
    publish(migrated, "failed")
    try:
        donnees = await tuile(api, TUILE, cache="no-store")
    finally:
        publish(migrated, "published")

    assert donnees["parcelles"] == {"etat": "value", "motif": None}
    assert donnees["ventes"] == {
        "etat": "temporary_error",
        "motif": "publication_non_validee",
        "periode": None,
        "annee_debut": None,
    }
    entites = par_id(donnees)
    assert set(entites) == {AVEC_SURFACE, SANS_SURFACE, A_CHEVAL}
    assert {e["properties"]["ventes"] for e in entites.values()} == {None}
    assert entites[AVEC_SURFACE]["properties"]["contenance"] == SURFACE
    # Back to a stable answer once the publication is valid again.
    assert (await tuile(api, TUILE))["ventes"]["etat"] == "value"


async def test_a_failed_sales_read_keeps_the_parcels_and_is_not_stored(api, migrated):
    scope(migrated)
    with injected(
        migrated,
        "ALTER TABLE mutation_parcelles RENAME TO mutation_parcelles_panne",
        restore=("ALTER TABLE mutation_parcelles_panne RENAME TO mutation_parcelles",),
    ):
        donnees = await tuile(api, TUILE, cache="no-store")

    assert donnees["ventes"]["etat"] == "temporary_error"
    assert donnees["ventes"]["motif"] == "lecture_indisponible"
    assert set(par_id(donnees)) == {AVEC_SURFACE, SANS_SURFACE, A_CHEVAL}


async def test_a_failed_cadastre_read_is_a_503_never_an_empty_tile(api, migrated):
    scope(migrated)
    with injected(
        migrated,
        "ALTER TABLE parcelles RENAME TO parcelles_panne",
        restore=("ALTER TABLE parcelles_panne RENAME TO parcelles",),
    ):
        probleme = await tuile(api, TUILE, 503, cache="no-store")

    assert probleme["title"] == "Parcelles momentanement indisponibles"
    assert "features" not in probleme and "donnees" not in probleme


async def test_a_failed_read_of_the_departements_is_a_503_too(api, migrated):
    scope(migrated)
    with injected(
        migrated,
        "ALTER TABLE geo_departements RENAME TO geo_departements_panne",
        restore=("ALTER TABLE geo_departements_panne RENAME TO geo_departements",),
    ):
        probleme = await tuile(api, TUILE, 503, cache="no-store")

    assert probleme["status"] == 503


# --- Served, never hard-coded ------------------------------------------------------


async def test_period_start_year_and_vintage_come_from_the_base(api, migrated):
    scope(migrated)
    autre_periode = (
        "UPDATE dataset_metadata SET source_period_label = '2019-2030' "
        "WHERE dataset_name = 'dvf'",
        "UPDATE dataset_metadata SET source_period_label = '2031-01' "
        "WHERE dataset_name = 'cadastre'",
        "UPDATE mutations_grain SET date_mutation = DATE '2019-05-06' "
        "WHERE id_mutation = '2021-T-1'",
    )
    rendu = (
        f"UPDATE dataset_metadata SET source_period_label = '{PERIODE}' "
        "WHERE dataset_name = 'dvf'",
        f"UPDATE dataset_metadata SET source_period_label = '{MILLESIME}' "
        "WHERE dataset_name = 'cadastre'",
        "UPDATE mutations_grain SET date_mutation = DATE '2021-02-10' "
        "WHERE id_mutation = '2021-T-1'",
    )

    with injected(migrated, *autre_periode, restore=rendu):
        donnees = await tuile(api, TUILE)

    assert donnees["ventes"]["periode"] == "2019-2030"
    assert donnees["ventes"]["annee_debut"] == 2019
    assert donnees["cadastre_millesime"] == "2031-01"


async def test_unknown_period_start_year_and_vintage_are_null(api, migrated):
    scope(migrated)
    inconnus = (
        "UPDATE dataset_metadata SET source_period_label = NULL "
        "WHERE dataset_name IN ('dvf', 'cadastre')",
        "ALTER TABLE mutations_grain RENAME TO mutations_grain_garde",
        "CREATE TABLE mutations_grain (LIKE mutations_grain_garde)",
        PUBLIER_DVF,
    )
    rendu = (
        f"UPDATE dataset_metadata SET source_period_label = '{PERIODE}' "
        "WHERE dataset_name = 'dvf'",
        f"UPDATE dataset_metadata SET source_period_label = '{MILLESIME}' "
        "WHERE dataset_name = 'cadastre'",
        "DROP TABLE mutations_grain",
        "ALTER TABLE mutations_grain_garde RENAME TO mutations_grain",
        PUBLIER_DVF,
    )

    with injected(migrated, *inconnus, restore=rendu):
        donnees = await tuile(api, TUILE)

    # Nothing is invented: an empty DVF table has no first year, a source
    # without a declared label nor an import has no period.
    assert donnees["ventes"] == {
        "etat": "value",
        "motif": None,
        "periode": None,
        "annee_debut": None,
    }
    assert donnees["cadastre_millesime"] is None
    assert {e["properties"]["ventes"] for e in par_id(donnees).values()} == {0}


def test_the_fixture_is_what_the_tests_say(migrated):
    """The tiles are told apart by the base itself, not by the arithmetic above."""
    with psycopg.connect(migrated, row_factory=dict_row) as conn:
        comptes = {
            ou: conn.execute(
                "SELECT count(*) AS n FROM parcelles WHERE ST_Intersects(geometrie, "
                "ST_Transform(ST_TileEnvelope(%s, %s, %s), 4326))",
                (Z, *ou),
            ).fetchone()["n"]
            for ou in (TUILE, VOISINE, DENSE, EN_MER, AILLEURS, MIXTE, FRONTIERE)
        }
    assert comptes == {
        TUILE: 3,
        VOISINE: 2,
        DENSE: NOMBRE_DENSE,
        EN_MER: 0,
        AILLEURS: 1,
        MIXTE: 2,
        FRONTIERE: 2,
    }


# --- Context of an address ---------------------------------------------------------

GABARIT_CONTEXTE = "/v1/carte/{ban_id}/contexte"
MONDE_Z16 = 256 * 2**16


async def contexte(api: Api, ban_id: str, status: int = 200) -> dict:
    response = await api.client.get(f"/v1/carte/{ban_id}/contexte")
    assert response.status_code == status, response.text
    # A context is never stored, a problem neither.
    assert response.headers["cache-control"] == "no-store"
    if status != 200:
        assert response.headers["content-type"].startswith("application/problem+json")
        assert ban_id not in response.text
        assert response.json()["instance"] == GABARIT_CONTEXTE
        return response.json()
    return response.json()["donnees"]


def _pixel(lon: float, lat: float) -> tuple[float, float]:
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2
    return (lon + 180) / 360 * MONDE_Z16, y * MONDE_Z16


async def test_the_context_serves_the_ban_point_its_bounds_and_its_parcel(
    api, migrated
):
    scope(migrated)
    api.statements.clear()

    donnees = await contexte(api, ADRESSE)

    assert lectures(api, "adresses") != [] and lectures(api, "parcelles") != []
    lon, lat = point(TUILE, 0.25, 0.25)
    assert donnees["centre"] == {"lon": pytest.approx(lon), "lat": pytest.approx(lat)}
    emprise = donnees["emprise"]
    assert emprise["ouest"] < lon < emprise["est"]
    assert emprise["sud"] < lat < emprise["nord"]
    # A square of 640 px around the point at zoom 16: the widest view fits.
    ouest, nord = _pixel(emprise["ouest"], emprise["nord"])
    est, sud = _pixel(emprise["est"], emprise["sud"])
    px, py = _pixel(lon, lat)
    for ecart in (px - ouest, est - px, py - nord, sud - py):
        assert ecart == pytest.approx(640, abs=0.1)
    assert donnees["parcelle_adresse"] == {
        "etat": "value",
        "id_parcelle": AVEC_SURFACE,
        "motif": None,
        "nombre_parcelles": None,
    }
    assert donnees["parcelles"] == {"etat": "value", "motif": None}
    assert donnees["fond"] == {
        "attribution": "\u00a9 IGN (Plan IGN)",
        "licence": "Licence Ouverte 2.0",
    }


async def test_the_context_says_what_a_tile_of_the_address_says(api, migrated):
    scope(migrated)

    du_contexte = await contexte(api, ADRESSE)
    de_la_tuile = await tuile(api, TUILE)

    assert du_contexte["ventes"] == de_la_tuile["ventes"]
    assert du_contexte["ventes"] == {
        "etat": "value",
        "motif": None,
        "periode": PERIODE,
        "annee_debut": 2021,
    }
    assert du_contexte["cadastre_millesime"] == de_la_tuile["cadastre_millesime"]
    assert du_contexte["cadastre_millesime"] == MILLESIME
    assert du_contexte["parcelles"] == de_la_tuile["parcelles"]
    # The parcel of the address is one of the parcels its tile draws.
    assert du_contexte["parcelle_adresse"]["id_parcelle"] in par_id(de_la_tuile)


@pytest.mark.parametrize(
    ("ban_id", "motif", "nombre", "lieu"),
    [
        (ADRESSE_HORS_PARCELLE, "point_hors_parcelle", None, (TUILE, 0.60, 0.10)),
        (ADRESSE_FRONTIERE, "parcelles_multiples", 2, (FRONTIERE, 0.25, 0.25)),
    ],
)
async def test_no_parcel_is_chosen_when_the_point_names_none_or_several(
    api, migrated, ban_id, motif, nombre, lieu
):
    scope(migrated)

    donnees = await contexte(api, ban_id)

    # Never the nearest parcel, never one of the two.
    assert donnees["parcelle_adresse"] == {
        "etat": "not_available",
        "id_parcelle": None,
        "motif": motif,
        "nombre_parcelles": nombre,
    }
    assert donnees["parcelles"]["etat"] == "value"
    lon, lat = point(*lieu)
    assert donnees["centre"] == {"lon": pytest.approx(lon), "lat": pytest.approx(lat)}


async def test_a_failed_cadastre_read_is_a_state_of_the_parcel_of_the_address(
    api, migrated
):
    scope(migrated)
    with injected(
        migrated,
        "ALTER TABLE parcelles RENAME TO parcelles_panne",
        restore=("ALTER TABLE parcelles_panne RENAME TO parcelles",),
    ):
        donnees = await contexte(api, ADRESSE)

    assert donnees["parcelle_adresse"] == {
        "etat": "temporary_error",
        "id_parcelle": None,
        "motif": "lecture_indisponible",
        "nombre_parcelles": None,
    }
    # The point and the bounds do not depend on the cadastre.
    assert donnees["centre"]["lon"] == pytest.approx(point(TUILE, 0.25, 0.25)[0])
    assert donnees["emprise"]["ouest"] < donnees["emprise"]["est"]


@pytest.mark.parametrize(
    ("ban_id", "code"),
    [
        (INCONNUE, "adresse_inconnue"),
        (VOIE, "voie_sans_numero"),
        # No single point: no centre to open the map on.
        (ADRESSE_CONTRADICTOIRE, "adresse_inconnue"),
    ],
)
async def test_an_address_without_a_point_is_a_constant_404(
    api, migrated, ban_id, code
):
    scope(migrated)

    assert (await contexte(api, ban_id, 404))["code"] == code


async def test_the_cadastre_not_hosted_is_out_of_coverage_before_any_read(
    api, migrated
):
    scope(migrated, ("ban", "dvf"))
    api.statements.clear()

    donnees = await contexte(api, ADRESSE)

    hors = {"etat": "out_of_coverage", "motif": "dataset_non_heberge"}
    assert donnees["parcelles"] == hors
    assert donnees["parcelle_adresse"] == {
        **hors,
        "id_parcelle": None,
        "nombre_parcelles": None,
    }
    assert donnees["cadastre_millesime"] is None
    assert donnees["ventes"]["etat"] == "value"
    assert lectures(api, "parcelles") == []
    # The point and the bounds are still served: the map opens on its base map.
    assert donnees["centre"]["lat"] == pytest.approx(point(TUILE, 0.25, 0.25)[1])


async def test_dvf_not_hosted_is_out_of_coverage_before_any_read(api, migrated):
    scope(migrated, ("ban", "cadastre"))
    api.statements.clear()

    donnees = await contexte(api, ADRESSE)

    assert donnees["ventes"] == {
        "etat": "out_of_coverage",
        "motif": "dataset_non_heberge",
        "periode": None,
        "annee_debut": None,
    }
    assert donnees["parcelle_adresse"]["id_parcelle"] == AVEC_SURFACE
    assert lectures(api, "mutations_grain") == []
    assert lectures(api, "mutation_parcelles") == []


@pytest.mark.parametrize(
    ("ban_id", "datasets"),
    [(ADRESSE, ("cadastre", "dvf")), (ADRESSE_AILLEURS, TOUT)],
)
async def test_the_ban_out_of_coverage_is_a_404_without_reading_the_address(
    api, migrated, ban_id, datasets
):
    """No BAN point, no centre: as the static map of the same key (404)."""
    scope(migrated, datasets)
    api.statements.clear()

    probleme = await contexte(api, ban_id, 404)

    assert probleme["code"] == "adresse_inconnue"
    assert lectures(api, "adresses") == []
    assert lectures(api, "parcelles") == []


async def test_a_refused_dvf_publication_leaves_the_context_served(api, migrated):
    scope(migrated)
    publish(migrated, "failed")
    try:
        donnees = await contexte(api, ADRESSE)
    finally:
        publish(migrated, "published")

    assert donnees["ventes"] == {
        "etat": "temporary_error",
        "motif": "publication_non_validee",
        "periode": None,
        "annee_debut": None,
    }
    assert donnees["parcelle_adresse"]["id_parcelle"] == AVEC_SURFACE


# --- The shared contract: a real answer has the key paths of its example -----------

EXEMPLES = Path(__file__).resolve().parents[4] / "contracts/carte/exemples"


def chemins(valeur, prefixe: str = "") -> dict[str, set[str]]:
    """JSON types met at each key path of *valeur*; list items share one path."""
    trouves: dict[str, set[str]] = {prefixe: {type(valeur).__name__}}
    if isinstance(valeur, dict):
        enfants = [(f"{prefixe}.{cle}", v) for cle, v in valeur.items()]
    elif isinstance(valeur, list):
        enfants = [(f"{prefixe}[]", v) for v in valeur]
    else:
        enfants = []
    for chemin, enfant in enfants:
        for cle, types in chemins(enfant, chemin).items():
            trouves.setdefault(cle, set()).update(types)
    return trouves


def exemple(nom: str) -> dict:
    return json.loads((EXEMPLES / nom).read_text(encoding="utf-8"))


async def test_a_real_tile_has_exactly_the_key_paths_of_its_example(api, migrated):
    scope(migrated)

    reponse = await api.client.get(f"/v1/carte/parcelles/{Z}/{TUILE[0]}/{TUILE[1]}")

    assert reponse.status_code == 200
    assert chemins(reponse.json()) == chemins(exemple("tuile-parcelles.json"))


async def test_a_real_context_has_exactly_the_key_paths_of_its_example(api, migrated):
    scope(migrated)

    reponse = await api.client.get(f"/v1/carte/{ADRESSE}/contexte")

    assert reponse.status_code == 200
    assert chemins(reponse.json()) == chemins(exemple("contexte.json"))
