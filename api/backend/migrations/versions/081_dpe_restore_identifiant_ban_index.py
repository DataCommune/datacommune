"""Restore the identifiant_ban index destroyed by an ETL swap.

Revision ID: 081_dpe_restore_identifiant_ban_index
Revises: 080_dpe_id_rnb_index
Create Date: 2026-08-05

Migration 077 created ``idx_dpe_identifiant_ban``. It is NOT in production
anymore -- verified 2026-08-05 in ``pg_indexes``/``pg_class``: absent, while
``alembic current`` still reports head.

Cause: the DPE ETL swaps a freshly built staging table over the production one
(``app/etl_dpe/pipeline.py``), and the indexes that survive are exactly those
``_create_staging_indexes`` rebuilds. That list was written before migrations
077 and 080 and did not know about them, so the first refresh after 077
silently dropped its index. The surviving indexes all carry ``_staging`` names,
which is the visible fingerprint of that swap.

Consequence being fixed: ``dpe_appartements_par_ban`` -- the PRIMARY DPE
attachment path of ``GET /v1/bien/logements``, an equality filter on
``identifiant_ban`` -- has been running unindexed on 471 366 rows.

This migration only repairs the current state. The recurrence is fixed in the
same changeset by adding both index definitions to ``_create_staging_indexes``;
without that, the next refresh would drop this index again, and would also drop
``idx_dpe_id_rnb`` (migration 080) which conditions the Lot C2 path.

Note on naming: the pipeline rebuilds its indexes under ``idx_dpe_staging_*``
names and does not rename them after the swap (unlike the rnb_liens pipeline,
which does). Production will therefore end up with
``idx_dpe_staging_identifiant_ban`` after the next refresh rather than the name
created here. Same column, same shape, no duplicate: the swap drops whatever
the old table carried. Left as is to stay consistent with the six pre-existing
indexes rather than renaming eight objects on a hot table.

Notes:
- IF NOT EXISTS: no-op on any database where the index survived.
- diagnostics_dpe is NOT partitioned (see migration 080), so the ACCESS
  EXCLUSIVE lock is a single plain index build of a few seconds.
- Deployment follows ADR-0003 (D1): supervised migrate-before-cutover.
"""

from alembic import op

revision: str = "081_dpe_restore_identifiant_ban_index"
down_revision: str = "080_dpe_id_rnb_index"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dpe_identifiant_ban "
        "ON diagnostics_dpe (identifiant_ban)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dpe_identifiant_ban")
