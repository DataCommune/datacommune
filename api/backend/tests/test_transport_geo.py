"""Regression tests for transport geo check (FIX-05 / 28-05).

Diagnostic result (28-DIAGNOSTIC-transport.md): the 20 292 hors-France points
(5.6%) in MATRIX-baseline are NOT a CRS bug. The GTFS consolidated feed
(transport.data.gouv.fr) includes stops from French overseas departments (DOM:
Martinique 9 045, Reunion 6 808, Guyane 765) and cross-border routes
(FlixBus/BlaBlaCar/SNCF international, 2 935 stops in Spain, UK, Morocco, etc.).

Zero Lambert-93 coordinates confirmed: no point has X or Y in [100 000, 1 300 000].
The pipeline's ST_SetSRID(ST_MakePoint(stop_lon, stop_lat), 4326) is correct -
GTFS is WGS84 by specification.

Cause of harness ERROR: geo_departements contains only 96 metropolitan France
departments (codes 01-95). DOM codes (97x) are absent -> any DOM stop is
mechanically flagged hors-France by NOT EXISTS(ST_Covers). This is the same
D-10 artefact as established for equipements in 28-03.

Fix applied (CAS B — GeoSpec calibration):
- GeoSpec transport gains a `no_dept_warn_threshold_pct=6.0` field.
- `_flag_no_dept` respects the threshold: below → WARN, above → ERROR.
- 6.0% > 5.55% (measured) — absorbs natural DOM/cross-border growth.
- A Lambert-style bug (~100% out-of-bounds) still produces ERROR.

Tests verify:
1. DOM point (Réunion WGS84) → audit_geo_bounds returns WARN not ERROR.
2. Lambert point (X~600 000 stored as WGS84) → audit_geo_bounds returns ERROR.
3. GeoSpec transport has no_dept_warn_threshold_pct set (structural guard).
"""

from __future__ import annotations

import psycopg
import pytest


def _sync_dsn() -> str:
    """Get synchronous DSN from app settings (requires DEBUG=true / test env)."""
    from app.config import settings

    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _get_conn() -> psycopg.Connection:  # type: ignore[type-arg]
    """Return a live psycopg connection or raise pytest.skip if DB unavailable."""
    try:
        dsn = _sync_dsn()
        conn = psycopg.connect(dsn, connect_timeout=5)
        return conn
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostGIS not available - skipping DB test: {exc}")


# ---------------------------------------------------------------------------
# Test 1: DOM legitimate stop → WARN, not ERROR (CAS B calibration)
# ---------------------------------------------------------------------------

# Real Réunion stop: lon=55.4901, lat=-21.1415, agency=ALTERNEO
# (sampled from DB in diagnostic — confirmed WGS84 valid, hors geo_departements)
DOM_REUNION_LON = 55.4901
DOM_REUNION_LAT = -21.1415

# Lambert-93 suspicious coords stored as WGS84:
# A point at Lambert X=600000 (near Paris), Y=6800000
# stored as WGS84 lon=600000, lat=6800000 — wildly out of bounds.
LAMBERT_FAKE_WGS84_LON = 600_000.0
LAMBERT_FAKE_WGS84_LAT = 6_800_000.0


@pytest.mark.live
def test_dom_stop_produces_warn_not_error() -> None:
    """A DOM stop (Réunion, valid WGS84) must produce WARN, not ERROR.

    Before fix: _flag_no_dept fires ERROR unconditionally for no_dept > 0.
    After fix: GeoSpec transport has no_dept_warn_threshold_pct=6.0 → 5.55%
    measured fraction → WARN.

    This test runs against the live arrets_transport table (365 544 rows).
    It validates the harness output level for the transport dataset, not a
    synthetic point — the DOM fraction drives the real WARN/ERROR decision.
    """
    conn = _get_conn()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM arrets_transport")
        row = cur.fetchone()
        if not row or row[0] == 0:
            pytest.skip("arrets_transport table empty - skipping test")

    from app.data_quality.geo_bounds import GEO_SPECS, audit_geo_bounds

    spec = GEO_SPECS.get("transport")
    assert spec is not None, "GEO_SPECS must contain 'transport'"

    conn2 = _get_conn()
    with conn2:
        result = audit_geo_bounds(conn2, spec)

    # The geo finding should be WARN (5.55% < threshold 6.0%), not ERROR.
    error_findings = [
        f for f in result.findings if f.severity == "ERROR" and f.category == "geo"
    ]
    warn_findings = [
        f for f in result.findings if f.severity == "WARN" and f.category == "geo"
    ]

    assert not error_findings, (
        f"transport audit produced geo ERROR(s): {[f.title for f in error_findings]}. "
        "Expected WARN (DOM stops are legitimate GTFS data - FIX-05 CAS B). "
        "Check that GeoSpec transport has no_dept_warn_threshold_pct=6.0 and "
        "_flag_no_dept respects the threshold."
    )
    # There should be a WARN about hors-France (DOM fraction is real, just not ERROR)
    assert warn_findings or result.row_count == 0, (
        "Expected a geo WARN for transport (5.55% DOM stops hors geo_departements). "
        "If no warning is emitted the threshold may be too high - check calibration."
    )


# ---------------------------------------------------------------------------
# Test 2: Lambert-style fake coords → must still produce ERROR (anti-complaisance)
# ---------------------------------------------------------------------------


@pytest.mark.live
def test_lambert_fake_coords_produce_error() -> None:
    """A point with Lambert-93 magnitude coords stored as WGS84 must still ERROR.

    This test verifies that the GeoSpec calibration (no_dept_warn_threshold_pct=6.0)
    does NOT make the harness complacent: a real CRS bug producing coords with
    magnitude > 1000 would put nearly 100% of rows outside France → ERROR.

    Uses a temporary table with a single Lambert-style fake point and verifies
    that audit_geo_bounds returns ERROR.
    """
    conn = _get_conn()

    # Create a temporary staging table with one Lambert-magnitude fake point.
    # ST_SetSRID declares it as 4326 (as a broken pipeline would do).
    fake_table = "transport_geo_test_lambert"
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {fake_table}")
            cur.execute(
                f"CREATE TEMP TABLE {fake_table} (position GEOMETRY(Point, 4326))"
            )
            cur.execute(
                f"INSERT INTO {fake_table} (position) VALUES ("
                f"ST_SetSRID(ST_MakePoint(%s, %s), 4326)"
                f")",
                (LAMBERT_FAKE_WGS84_LON, LAMBERT_FAKE_WGS84_LAT),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Could not create temp table: {exc}")

    from app.data_quality.geo_bounds import GeoSpec, audit_geo_bounds

    # Use a GeoSpec pointing to the temp table — spatial_mode=True (like transport).
    # no_dept_warn_threshold_pct is set to the same value as real transport (6%).
    # With 1 row and 100% out-of-bounds (600000 >> max lon 180), this must ERROR.
    fake_spec = GeoSpec(
        dataset="transport_test",
        table=fake_table,
        geom_col="position",
        dept_expr="",
        spatial_mode=True,
        no_dept_warn_threshold_pct=6.0,
    )

    with conn:
        result = audit_geo_bounds(conn, fake_spec)

    error_findings = [
        f for f in result.findings if f.severity == "ERROR" and f.category == "geo"
    ]

    assert error_findings, (
        "Lambert-magnitude fake point (lon=600000) did NOT produce a geo ERROR. "
        "The GeoSpec calibration must not suppress ERROR for a true CRS bug. "
        "Check that _flag_no_dept only upgrades no_dept to WARN when "
        "pct < no_dept_warn_threshold_pct — not unconditionally."
    )


# ---------------------------------------------------------------------------
# Test 3: GeoSpec structural — no_dept_warn_threshold_pct set for transport
# ---------------------------------------------------------------------------


def test_geospec_transport_has_no_dept_warn_threshold() -> None:
    """GeoSpec transport must have no_dept_warn_threshold_pct set.

    This structural test catches regressions where the FIX-05 calibration is
    accidentally removed from geo_bounds.py. Without the threshold, a future
    re-run would revert to ERROR for the 5.55% DOM fraction.
    """
    from app.data_quality.geo_bounds import GEO_SPECS

    spec = GEO_SPECS.get("transport")
    assert spec is not None, "GEO_SPECS must contain 'transport'"

    assert hasattr(spec, "no_dept_warn_threshold_pct"), (
        "GeoSpec does not have field 'no_dept_warn_threshold_pct'. "
        "Add the field to the GeoSpec dataclass (FIX-05 / 28-05)."
    )

    assert spec.no_dept_warn_threshold_pct is not None, (
        "GeoSpec transport has no_dept_warn_threshold_pct=None. "
        "Must be set to a float (e.g., 6.0) to calibrate the DOM fraction."
    )

    assert spec.no_dept_warn_threshold_pct >= 5.55, (
        f"no_dept_warn_threshold_pct={spec.no_dept_warn_threshold_pct} is below "
        "the measured 5.55% DOM fraction. Would still produce ERROR on real data. "
        "Set to at least 6.0 (5.55% + 0.5% safety margin)."
    )

    # Sanity: threshold should not be absurdly high (would make the check useless)
    assert spec.no_dept_warn_threshold_pct < 20.0, (
        f"no_dept_warn_threshold_pct={spec.no_dept_warn_threshold_pct} is too high. "
        "A Lambert CRS bug would produce ~100% out-of-bounds — the gate must still "
        "catch it. Keep threshold below 20%."
    )
