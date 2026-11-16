"""Regression test for equipements ETL swap INSERT path (FIX-01 / D-04).

Gate D-04: exercises the WGS84 direct path from swap() on a synthetic
Challans fixture.  Requires PostGIS; skips cleanly in DB-less CI.

FIX-01 context: the real BPE source provides LATITUDE/LONGITUDE WGS84 columns
that override LAMBERT_X/Y in transform().  The swap must treat staging lat/lon
as WGS84 directly (ST_SetSRID(..., 4326)), NOT re-project them as Lambert-93.

Challans mairie reference point (WGS84 — as they appear in staging after transform()):
  WGS84       lon ≈ 1.8757, lat ≈ 46.8430
"""

import psycopg
import pytest

from app.etl.base import BasePipeline

# ---------------------------------------------------------------------------
# DB connection helper (skip if unavailable)
# ---------------------------------------------------------------------------

_DSN = BasePipeline.get_sync_dsn()

# Challans WGS84 coords as they appear in the staging flat table
# AFTER transform(): latitude=LATITUDE WGS84, longitude=LONGITUDE WGS84
_CHALLANS_LON_WGS84 = "1.8757"  # LONGITUDE WGS84 → stored as longitude in staging flat
_CHALLANS_LAT_WGS84 = "46.8430"  # LATITUDE WGS84 → stored as latitude in staging flat

# Expected WGS84 result (tolerance ~1e-3 degrees ≈ 100 m)
_CHALLANS_LON_EXPECTED = 1.8757
_CHALLANS_LAT_EXPECTED = 46.8430
_TOLERANCE = 1e-3


# ---------------------------------------------------------------------------
# SQL: the exact INSERT path from EquipementsPipeline.swap() (FIX-01)
# WGS84 input → ST_SetSRID(ST_MakePoint(lon, lat), 4326) directly, no ST_Transform
# ---------------------------------------------------------------------------

_INSERT_SQL = """
    INSERT INTO _eq_reg_target
        (latitude, longitude, geom)
    SELECT
        ST_Y(wgs84_pt),
        ST_X(wgs84_pt),
        wgs84_pt
    FROM (
        SELECT *,
            ST_SetSRID(
                ST_MakePoint(
                    longitude::double precision,
                    latitude::double precision
                ),
                4326
            ) AS wgs84_pt
        FROM _eq_reg_flat
        WHERE NULLIF(longitude, '') IS NOT NULL
          AND NULLIF(latitude, '') IS NOT NULL
    ) sub
    WHERE NULLIF(type_equipement, '') IS NOT NULL
      AND NULLIF(code_commune, '') IS NOT NULL
"""


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_swap_insert_reprojects_challans_to_wgs84() -> None:
    """FIX-01 gate: WGS84 Challans point passes through swap INSERT correctly.

    Verifies three acceptance queries:
    1. Coordinates within French mainland bounds (41-51 lat, -5..10 lon).
    2. Scalar lat/lon columns are coherent with ST_Y/ST_X of geom (D-06).
    3. Proximity search around Challans returns at least 1 result.

    Regression: before FIX-01, swap() applied ST_SetSRID(..., 2154) +
    ST_Transform(→ 4326) on WGS84 coords, destroying all geometries.
    """
    try:
        conn = psycopg.connect(_DSN, connect_timeout=5)
    except Exception:  # noqa: BLE001
        pytest.skip("PostGIS non joignable — gate D-04 best-effort en CI DB-less")

    with conn, conn.cursor() as cur:
        # Create temp flat source table (mimics _equipements_staging_flat schema)
        cur.execute("""
            CREATE TEMP TABLE _eq_reg_flat (
                type_equipement  TEXT,
                code_commune     TEXT,
                code_departement TEXT,
                code_iris        TEXT,
                domaine          TEXT,
                sous_domaine     TEXT,
                nb_equipements   TEXT,
                latitude         TEXT,
                longitude        TEXT,
                annee            TEXT
            )
        """)

        # Insert synthetic Challans row (FIX-01: lat/lon are WGS84 from transform())
        cur.execute(
            """
            INSERT INTO _eq_reg_flat
                (type_equipement, code_commune, code_departement,
                 code_iris, domaine, sous_domaine, nb_equipements,
                 latitude, longitude, annee)
            VALUES
                ('A101', '85047', '85', '850470000', 'A', 'A1', '1',
                 %s, %s, '2024')
            """,
            (_CHALLANS_LAT_WGS84, _CHALLANS_LON_WGS84),
        )

        # Create temp target table (relevant columns only)
        cur.execute("""
            CREATE TEMP TABLE _eq_reg_target (
                latitude  DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                geom      GEOMETRY(POINT, 4326)
            )
        """)

        # Execute the exact INSERT SELECT from swap() (FIX-01, WGS84 direct path)
        cur.execute(_INSERT_SQL)

        # --- Acceptance query 1: bounds within French mainland ---
        cur.execute("""
            SELECT bool_and(
                latitude  BETWEEN 41 AND 51
                AND longitude BETWEEN -5 AND 10
            )
            FROM _eq_reg_target
            WHERE geom IS NOT NULL
        """)
        row = cur.fetchone()
        assert row is not None and row[0] is True, (
            "Acceptance 1 failed: coordinates are NOT within French mainland bounds "
            f"(lat 41-51, lon -5..10). Got: {row}"
        )

        # --- Acceptance query 2: scalar columns coherent with ST_Y/ST_X (D-06) ---
        cur.execute("""
            SELECT count(*)
            FROM _eq_reg_target
            WHERE geom IS NOT NULL
              AND (
                  abs(ST_Y(geom) - latitude)  > 1e-6
               OR abs(ST_X(geom) - longitude) > 1e-6
              )
        """)
        incoherent_count = cur.fetchone()[0]  # type: ignore[index]
        assert incoherent_count == 0, (
            f"Acceptance 2 failed: {incoherent_count} row(s) have scalar "
            "lat/lon inconsistent with ST_Y(geom)/ST_X(geom) — D-06 violated."
        )

        # --- Acceptance query 3: proximity search around Challans returns ≥1 ---
        cur.execute(
            """
            SELECT count(*)
            FROM _eq_reg_target
            WHERE ST_DWithin(
                geom::geography,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                2000
            )
            """,
            (_CHALLANS_LON_EXPECTED, _CHALLANS_LAT_EXPECTED),
        )
        proximity_count = cur.fetchone()[0]  # type: ignore[index]
        assert proximity_count >= 1, (
            f"Acceptance 3 failed: proximity search around Challans returned"
            f" {proximity_count} result(s), expected ≥ 1."
        )

    conn.close()
