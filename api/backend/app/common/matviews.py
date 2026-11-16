"""Repair of the expected materialized views after an ``_old`` table drop.

An ETL swap renames ``<table>`` to ``<table>_old`` and promotes the staging
table. A materialized view stays bound to its source tables by OID, so it
follows the renamed ``_old`` table; the later ``DROP TABLE <table>_old
CASCADE`` therefore takes the matview down with it, and nothing rebuilds it
until the next daily refresh job (incident API-4: seven hours of 500 on
``/v1/commune``).

This module holds the single probe of ``pg_matviews`` and the single rebuild
routine, shared by the hourly ``cleanup_old_tables`` job and its inline twin
in the ETL preflight (``PipelineRunner._inline_cleanup_old_tables``). One
implementation for both call sites: two would diverge.

Contract, inherited from the ``recreate_*`` DDL helpers: *the caller owns the
commit*. This module never commits and never swallows an exception. Callers
run it inside the same transaction as the DROPs, so a failed rebuild rolls
the DROPs back instead of committing a schema without its matviews.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import psycopg

from app.common.communes_lookup_ddl import recreate_communes_lookup
from app.commune.matview_ddl import recreate_commune_synthese

# Matviews that must always exist in production, mapped to their canonical
# DDL rebuilder.
#
# ``v_commune_synthese`` also covers the intermediate matviews it LEFT JOINs
# (``v_commune_dvf``, ``v_commune_dpe``, ...): a CASCADE reaching an
# intermediate takes the synthese down with it, and recreate_commune_synthese
# rebuilds the whole chain (intermediates first, then the synthese). Probing
# the synthese alone is therefore enough for either destruction order -- no
# need to multiply the probes. ``v_departement_synthese`` (04.7-10) is rebuilt
# by the same routine: the swapped tables it reads (mutations_grain,
# diagnostics_dpe, demographie) are read by the synthese chain too, so a
# CASCADE taking it down takes the synthese as well; its referential tables are
# replaced in place, never dropped. ``v_commune_dvf_annuel`` (migration 115)
# likewise: it reads mutations_grain, as v_commune_dvf does.
EXPECTED_MATVIEWS: dict[str, Callable[[psycopg.Connection[Any]], None]] = {
    "v_commune_synthese": recreate_commune_synthese,
    "communes_lookup": recreate_communes_lookup,
}

_PROBE_SQL = (
    "SELECT matviewname FROM pg_matviews "
    "WHERE matviewname = ANY(%s) AND schemaname = current_schema()"
)


def recreate_missing_matviews(
    conn: psycopg.Connection[Any],
    log: logging.Logger,
) -> list[str]:
    """Rebuild every expected matview missing from ``pg_matviews``.

    Returns the names rebuilt, in registry order, for the caller to log.
    Returns an empty list on a healthy schema: the probe runs once and no DDL
    is issued (idempotence).

    Does NOT commit and does NOT catch: an exception propagates so the caller
    can roll the whole transaction back.
    """
    expected = list(EXPECTED_MATVIEWS)
    rows = conn.execute(_PROBE_SQL, (expected,)).fetchall()
    present = {row[0] for row in rows}
    missing = [name for name in expected if name not in present]
    if not missing:
        return []

    for name in missing:
        log.warning("Matview %s is missing — rebuilding it", name)
        started = time.monotonic()
        try:
            EXPECTED_MATVIEWS[name](conn)
            # Repair and freshness share the caller's transaction. A later
            # rebuild failure rolls this success back along with all DDL.
            conn.execute(
                """
                INSERT INTO matview_metadata (
                    matview_name, last_refresh_at,
                    last_refresh_duration_seconds, last_refresh_status, last_error
                ) VALUES (%s, NOW(), %s, 'success', NULL)
                ON CONFLICT (matview_name) DO UPDATE SET
                    last_refresh_at = EXCLUDED.last_refresh_at,
                    last_refresh_duration_seconds =
                        EXCLUDED.last_refresh_duration_seconds,
                    last_refresh_status = 'success', last_error = NULL
                """,
                (name, time.monotonic() - started),
            )
        except Exception:
            log.exception("Failed to rebuild missing matview %s", name)
            raise

    log.info("Rebuilt %d missing matview(s): %s", len(missing), ", ".join(missing))
    return missing
