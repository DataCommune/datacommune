"""Parcels per zoom-16 tile, as measured by ``scripts/densite_tuiles.sql`` (04.10-01).

The parcel route of the map serves a tile whole, without a cap: the densest
tile of the widest served zoom is measured on the real base before the route
is designed. This file proves the measuring statement on synthetic squares
laid on arbitrary tiles: which tile comes first, what is counted in it, and
that the bounded form (one departement) reads one partition only.

The statement is read from ``api/backend/scripts/`` and played as the
orchestrator plays it: one read-only session, an optional ``SET``.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_carte_densite_tuiles.py
"""

import json
import math
import re
from pathlib import Path

import psycopg
import pytest
from psycopg import sql as psql
from psycopg.rows import dict_row

from tests.integration.test_adresse_ventes_contract import (
    _rect,
    insert_parcelle,
    migrate,
)

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
ENONCE = BACKEND / "scripts" / "densite_tuiles.sql"
Z = 16
# Arbitrary tiles: nothing depends on where they are, only on their edges.
DENSE = (32500, 23000)
VOISINE = (DENSE[0] + 1, DENSE[1])
LOINTAINE = (DENSE[0] + 10, DENSE[1] + 10)
# Half side of a square as a share of the tile side.
DEMI = 0.04


def bornes(tuile: tuple[int, int]) -> tuple[float, float, float, float]:
    """West, south, east and north edges of a zoom-16 tile, in degrees."""
    n = 2**Z

    def lat(rang: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * rang / n))))

    x, y = tuile
    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def carre(tuile: tuple[int, int], fx: float, fy: float) -> str:
    """A square centred at the share (*fx*, *fy*) of the tile, from its south-west."""
    ouest, sud, est, nord = bornes(tuile)
    largeur, hauteur = est - ouest, nord - sud
    return _rect(
        ouest + (fx - DEMI) * largeur,
        sud + (fy - DEMI) * hauteur,
        ouest + (fx + DEMI) * largeur,
        sud + (fy + DEMI) * hauteur,
    )


# id_parcelle -> square. The first two characters are the partition.
DANS_LA_DENSE = {
    "85000000ZZ0001": carre(DENSE, 0.25, 0.25),
    "85000000ZZ0002": carre(DENSE, 0.25, 0.75),
    # Centre in the dense tile, east side in its neighbour.
    "85000000ZZ0003": carre(DENSE, 0.99, 0.50),
}
PARCELLES = {
    **DANS_LA_DENSE,
    "85000000ZZ0004": carre(VOISINE, 0.50, 0.50),
    # One far tile shared by two departements.
    "85000000ZZ0005": carre(LOINTAINE, 0.70, 0.50),
    "44000000ZZ0001": carre(LOINTAINE, 0.30, 0.50),
}


@pytest.fixture(scope="module")
def peuplee(isolated_pg_dsn):
    migrate(isolated_pg_dsn)
    with psycopg.connect(isolated_pg_dsn) as conn:
        for rang, (id_parcelle, wkt) in enumerate(PARCELLES.items(), start=1):
            insert_parcelle(conn, rang, id_parcelle, wkt)
    return isolated_pg_dsn


def _regler(conn, departement: str) -> None:
    conn.execute(
        psql.SQL("SET densite.departement = {}").format(psql.Literal(departement))
    )


def jouer(dsn: str, departement: str | None = None) -> list[dict]:
    """Play the statement on a read-only session, bounded or not."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.read_only = True
        if departement is not None:
            _regler(conn, departement)
        return conn.execute(ENONCE.read_text(encoding="utf-8")).fetchall()


def par_tuile(lignes: list[dict]) -> dict[tuple[int, int], dict]:
    return {(ligne["x"], ligne["y"]): ligne for ligne in lignes}


def geojson(dsn: str, id_parcelle: str, decimales: int) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT ST_AsGeoJSON(geometrie, %s) FROM parcelles WHERE id_parcelle = %s",
            (decimales, id_parcelle),
        ).fetchone()[0]


def test_the_dense_tile_comes_first_with_its_parcels_and_geojson_bytes(peuplee):
    lignes = jouer(peuplee)

    assert set(par_tuile(lignes)) == {DENSE, VOISINE, LOINTAINE}
    assert all(ligne["perimetre"] == "national" for ligne in lignes)
    premiere = lignes[0]
    assert (premiere["z"], premiere["x"], premiere["y"]) == (Z, *DENSE)
    assert premiere["parcelles"] == 3

    six = [geojson(peuplee, id_parcelle, 6) for id_parcelle in DANS_LA_DENSE]
    for texte in six:
        for lon, lat in json.loads(texte)["coordinates"][0][0]:
            assert (lon, lat) == (round(lon, 6), round(lat, 6)), texte
    assert premiere["octets_geojson"] == sum(len(t.encode()) for t in six)
    # The squares carry more than six decimals: another precision weighs more.
    neuf = [geojson(peuplee, id_parcelle, 9) for id_parcelle in DANS_LA_DENSE]
    assert premiere["octets_geojson"] < sum(len(t.encode()) for t in neuf)


def test_a_parcel_across_two_tiles_counts_in_both(peuplee):
    tuiles = par_tuile(jouer(peuplee))

    # Its centre is in the dense tile only; it touches both.
    assert tuiles[DENSE]["parcelles_centre"] == 3
    assert tuiles[DENSE]["parcelles"] == 3
    assert tuiles[VOISINE]["parcelles_centre"] == 1
    assert tuiles[VOISINE]["parcelles"] == 2


def test_the_bounded_form_spots_one_departement_and_counts_whole_tiles(peuplee):
    lignes = jouer(peuplee, "44")

    assert [(ligne["x"], ligne["y"]) for ligne in lignes] == [LOINTAINE]
    assert lignes[0]["perimetre"] == "departement=44"
    assert lignes[0]["parcelles_centre"] == 1
    # The weight of a tile is the tile's: the parcel of the other departement counts.
    assert lignes[0]["parcelles"] == 2

    autres = jouer(peuplee, "85")
    assert (autres[0]["x"], autres[0]["y"], autres[0]["parcelles"]) == (*DENSE, 3)
    assert set(par_tuile(autres)) == {DENSE, VOISINE, LOINTAINE}
    assert par_tuile(autres)[LOINTAINE]["parcelles_centre"] == 1


def test_a_reset_setting_is_the_national_form(peuplee):
    with psycopg.connect(peuplee, row_factory=dict_row) as conn:
        conn.read_only = True
        _regler(conn, "44")
        # After a RESET, a custom setting reads as an empty string, not as NULL.
        conn.execute("RESET densite.departement")
        lignes = conn.execute(ENONCE.read_text(encoding="utf-8")).fetchall()

    assert lignes == jouer(peuplee)


def _noeuds(plan: dict):
    yield plan
    for enfant in plan.get("Plans", ()):
        yield from _noeuds(enfant)


def test_the_bounded_form_reads_one_partition_to_spot_the_tiles(peuplee):
    with psycopg.connect(peuplee) as conn:
        conn.read_only = True
        _regler(conn, "44")
        plan = conn.execute(
            "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, FORMAT JSON) "
            + ENONCE.read_text(encoding="utf-8").rstrip().rstrip(";")
        ).fetchone()[0][0]["Plan"]

    noeuds = list(_noeuds(plan))
    # Bounded arm: every other partition is removed when the executor starts.
    assert any(noeud.get("Subplans Removed", 0) >= 100 for noeud in noeuds), plan
    # National arm: gated as a whole, none of its scans runs.
    portes = [noeud for noeud in noeuds if "One-Time Filter" in noeud]
    assert portes, plan
    for porte in portes:
        assert all(
            noeud["Actual Loops"] == 0 for noeud in _noeuds(porte) if noeud is not porte
        ), porte


def test_no_parcel_gives_no_row(peuplee):
    # A departement without any parcel.
    assert jouer(peuplee, "01") == []

    with psycopg.connect(peuplee) as conn:
        conn.execute("DELETE FROM parcelles")
        assert conn.execute(ENONCE.read_text(encoding="utf-8")).fetchall() == []
        conn.rollback()
    assert len(jouer(peuplee)) == 3


def test_the_statement_is_one_select_that_names_no_territory():
    texte = ENONCE.read_text(encoding="utf-8")
    corps = "\n".join(ligne.split("--")[0] for ligne in texte.splitlines()).strip()

    assert corps.count(";") == 1 and corps.endswith(";")
    assert re.match(r"(WITH|SELECT)\b", corps)
    assert not re.search(
        r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|COPY|SET)\b", corps
    )
    # Tile envelopes come from PostGIS, in the tile's own grid.
    assert "ST_TileEnvelope" in corps
    # No departement, commune or coordinate written in the file, comments included.
    assert not re.search(r"'[0-9][0-9AB]", texte)
    assert not re.search(r"\b[0-9]{1,2}\.[0-9]{3,}\b", texte)
