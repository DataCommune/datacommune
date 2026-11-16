"""Check that the commune view chain carries the application DDL (read-only).

For a base built by the migrations or restored from a dump: compares
``pg_matviews.definition`` of the eight views of the chain (``v_commune_*``,
``v_commune_synthese`` last) with ``app.commune.matview_ddl``. Nothing is
created, dropped or refreshed (``commune_view_drift`` rolls back).

Usage (from ``api/backend/``, the target base in ``DATABASE_URL``):
    uv run python -m scripts.check_commune_views

Exit 0: ``COMMUNE VIEWS OK``. Exit 1: one line ``COMMUNE VIEW DRIFT <name>``
per view; repair with a commune-source publication or
``recreate_commune_synthese`` (migration 098 does it once on migrated bases).

Expected drift without change of meaning: a column added to one of the five
tables the synthesis reads with ``SELECT *`` (demographie, revenus, vacance,
fiscalite_locale, indicateurs_loyers) changes the deparsed definition until
the next commune-source publication rebuilds the chain.
"""

import os
import sys

import psycopg

from app.commune.matview_ddl import commune_view_drift


def main(dsn: str | None = None) -> int:
    # The target base only: no application settings are loaded.
    dsn = (dsn or os.environ["DATABASE_URL"]).replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        drift = commune_view_drift(conn)
    for name in drift:
        print(f"COMMUNE VIEW DRIFT {name}")
    if drift:
        return 1
    print("COMMUNE VIEWS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
