"""Enable pg_stat_statements for query-level statistics.

Revision ID: 086_enable_pg_stat_statements
Revises: 085_recreate_idx_equipements_geom_geog
Create Date: 2026-08-23

The 2026-08-23 production inventory (DB-HYGIENE-01 prod plan) found the
extension available (contrib 1.11 ships with postgis/postgis:17-3.5) but not
installed, and ``shared_preload_libraries`` empty. Without it the only
query-level visibility is ``api_request_log`` (HTTP side) — nothing attributes
DB time to individual normalized statements, which is what every slow-query
diagnosis so far had to reconstruct by hand with EXPLAIN. This migration is
one half of the fix; the other half is the compose change shipped in the same
commit, which adds ``-c shared_preload_libraries=pg_stat_statements`` to the
``db`` service command (``docker-compose.vps.yml`` + the ETL-window overlay
``docker-compose.vps-etl.yml``).

The two halves are deliberately independent:

- ``CREATE EXTENSION`` succeeds even while the library is NOT preloaded: it
  only registers the SQL objects (the ``pg_stat_statements`` view and
  functions). Querying the view before the preload is active fails with an
  explicit "pg_stat_statements must be loaded via shared_preload_libraries"
  error — a loud, self-describing state, not a silent one.
- The preload takes effect at the next restart of the ``db`` container.
  ``shared_preload_libraries`` cannot be changed on a running postmaster;
  ``docker compose up -d`` (the CD cutover step) recreates the container
  because its command line changed, so the release deploy itself performs the
  restart. No manual SIGHUP dance, but a brief db downtime at cutover —
  the API pool reconnects, same as every image bump.

Notes:

- ``IF NOT EXISTS``: re-running on a database where an operator already
  installed the extension by hand is the intended no-op.
- Lock: ``CREATE EXTENSION`` only inserts catalog rows and creates objects in
  schema ``public``; no user table is touched.
- Deployment follows ADR-0003 (D1): supervised migrate-before-cutover.
"""

from alembic import op

revision: str = "086_enable_pg_stat_statements"
down_revision: str = "085_recreate_idx_equipements_geom_geog"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade() -> None:
    # Drops the view/functions and the collected statistics with them. The
    # in-memory counters survive until the preload is also removed from the
    # compose command, but nothing can read them without the extension — so
    # dropping it really does return to the pre-086 observable state.
    op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
