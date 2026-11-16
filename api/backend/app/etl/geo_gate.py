"""Shared bounds-gate helper for ETL swap operations (D-03 / D-21).

check_bounds_gate is called by each pipeline's swap() after the staging
INSERT but before swap_tables().  It counts geometries that fall outside
France+DOM-TOM and aborts the swap when the fraction exceeds a threshold.

Contract (identical to GATE-01 _check_min_row_count in runner.py):
  - Returns str  : abort — message describing the violation
  - Returns None : pass — swap may proceed

The reference table geo_departements covers France métropolitaine + DROM
(codes 971-974 and 976, built from their communes since plan 04.1-06).
Testing geometry containment against this table IS the France+DOM test
(D-10) — no mainland-only bbox needed.
"""

from __future__ import annotations

import logging

import psycopg

logger = logging.getLogger(__name__)

# Reference table: 96 metropolitan polygons + the 5 DROM (04.1-06).
_GEO_REF_TABLE = "geo_departements"


def check_bounds_gate(
    dsn: str,
    staging_table: str,
    geom_col: str,
    dataset_name: str,
    threshold_pct: float,
    log: logging.Logger,
) -> str | None:
    """GATE-BOUNDS: abort the swap when too many geometries fall outside France+DOM.

    Opens its own psycopg connection so it can be called from any pipeline
    after Phase A commit, before Phase B swap_tables().

    Counts geometries whose ST_PointOnSurface is NOT covered by any polygon
    in geo_departements (France+DOM).  Returns an error string if the
    out-of-bounds fraction exceeds threshold_pct, None otherwise.

    Args:
        dsn:            Sync DSN string (postgresql://user:pass@host/db).
        staging_table:  Name of the staging table to inspect.
        geom_col:       Name of the geometry column.
        dataset_name:   Human-readable dataset name used in log/error messages.
        threshold_pct:  Maximum acceptable out-of-bounds fraction in percent.
                        Align with OUT_OF_DEPT_ERROR_PCT=5.0 from geo_bounds.py.
        log:            Logger instance from the calling pipeline.

    Returns:
        str if gate fails (abort), None if gate passes.
    """
    # Single scan: count total non-null geometries and how many fall outside
    # France+DOM.  ST_PointOnSurface is used so polygons and points are
    # handled uniformly (same approach as _flag_no_dept in geo_bounds.py).
    query = f"""
        SELECT
            count(*) FILTER (WHERE {geom_col} IS NOT NULL) AS total,
            count(*) FILTER (
                WHERE {geom_col} IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM {_GEO_REF_TABLE} d
                      WHERE ST_Covers(d.geometry, ST_PointOnSurface({geom_col}))
                  )
            ) AS out_of_bounds
        FROM {staging_table}
    """

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(query)
        row = cur.fetchone()

    total: int = row[0] if row else 0
    out_of_bounds: int = row[1] if row else 0

    # Empty staging table: not an abort condition — GATE-01 row-count handles that.
    if total == 0:
        log.info(
            "GATE-BOUNDS %s: staging table %s is empty — skipping bounds check",
            dataset_name,
            staging_table,
        )
        return None

    pct = out_of_bounds / total * 100

    if pct > threshold_pct:
        msg = (
            f"GATE-BOUNDS {dataset_name}: {out_of_bounds:,}/{total:,} "
            f"({pct:.1f}%) hors France+DOM > seuil {threshold_pct}% "
            f"— swap aborté"
        )
        log.error(msg)
        return msg

    log.info(
        "GATE-BOUNDS %s passed: %d/%d (%.1f%%) hors France+DOM <= seuil %.1f%%",
        dataset_name,
        out_of_bounds,
        total,
        pct,
        threshold_pct,
    )
    return None
