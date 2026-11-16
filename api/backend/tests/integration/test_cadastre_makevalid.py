"""Regression gate for cadastre geometry validation (FIX-02 / D-15).

Gate D-15: exercises ST_CollectionExtract(ST_MakeValid(geom), 3) on a real
invalid geometry extracted from the 34 parcelles invalides in the DB.
Runs through the disposable PostGIS harness; connection failures fail the gate.

The fixture 'tests/fixtures/cadastre_invalid_geom.txt' contains a real WKT
exported from the production parcelles table (auto-intersecting MULTIPOLYGON).
ST_MakeValid must repair it into a valid surface.
"""

from pathlib import Path

import pytest

# Path to real invalid geometry fixture (D-15: real geometry, not synthetic)
_FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "cadastre_invalid_geom.txt"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_makevalid_repairs_real_invalid_geometry(pg_conn) -> None:
    """D-15 gate: a real invalid cadastre geometry is repaired by ST_MakeValid.

    Loads the WKT fixture (auto-intersecting MULTIPOLYGON from parcelles),
    runs ST_CollectionExtract(ST_MakeValid(geom), 3) and asserts ST_IsValid.
    """
    wkt = _FIXTURE_PATH.read_text().strip()

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT ST_IsValid(
                ST_CollectionExtract(
                    ST_MakeValid(ST_GeomFromText(%s, 4326)),
                    3
                )
            )
            """,
            (wkt,),
        )
        row = cur.fetchone()
        assert row is not None and row[0] is True, (
            f"ST_MakeValid did not repair the real invalid geometry. Result: {row}"
        )


@pytest.mark.smoke
def test_makevalid_preserves_valid_geometry(pg_conn) -> None:
    """Round-trip test: a valid geometry passes through ST_MakeValid unchanged.

    A simple valid polygon should remain valid and non-empty after the pipeline
    transform (ST_CollectionExtract(ST_MakeValid(geom), 3)).
    """
    # Simple valid polygon (square in WGS84 roughly over France)
    valid_wkt = "POLYGON((2.0 47.0, 2.1 47.0, 2.1 47.1, 2.0 47.1, 2.0 47.0))"

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ST_IsValid(
                    ST_CollectionExtract(
                        ST_MakeValid(ST_GeomFromText(%s, 4326)),
                        3
                    )
                ),
                ST_IsEmpty(
                    ST_CollectionExtract(
                        ST_MakeValid(ST_GeomFromText(%s, 4326)),
                        3
                    )
                )
            """,
            (valid_wkt, valid_wkt),
        )
        row = cur.fetchone()
        assert row is not None, "No result returned"
        is_valid, is_empty = row[0], row[1]
        assert is_valid is True, (
            f"Valid geometry became invalid after round-trip: {row}"
        )
        assert is_empty is False, "Valid geometry became empty after round-trip"


@pytest.mark.smoke
def test_makevalid_excludes_degenerate_geometry(pg_conn) -> None:
    """Degenerate geometry (point WKT) becomes empty after ST_CollectionExtract.

    A POINT geometry has no polygon surface — ST_CollectionExtract(geom, 3)
    (type 3 = polygons only) must return NULL or empty, confirming that the
    pipeline WHERE filter would exclude such rows.
    """
    # A POINT geometry: after ST_CollectionExtract(..., 3) it has no polygon surface
    point_wkt = "POINT(2.0 47.0)"

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ST_CollectionExtract(
                    ST_MakeValid(ST_GeomFromText(%s, 4326)),
                    3
                ) IS NULL
                OR ST_IsEmpty(
                    ST_CollectionExtract(
                        ST_MakeValid(ST_GeomFromText(%s, 4326)),
                        3
                    )
                )
            """,
            (point_wkt, point_wkt),
        )
        row = cur.fetchone()
        assert row is not None and row[0] is True, (
            "Expected point geometry to be excluded (empty/null polygon extract), "
            f"but got: {row}"
        )
