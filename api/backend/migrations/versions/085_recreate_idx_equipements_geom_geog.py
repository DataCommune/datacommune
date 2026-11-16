"""Recreate idx_equipements_geom_geog, lost in production by the ETL swaps.

Revision ID: 085_recreate_idx_equipements_geom_geog
Revises: 084_drop_duplicate_staging_indexes
Create Date: 2026-08-23

The 2026-08-23 production inventory (DB-HYGIENE-01 prod plan, step 1) found
``idx_equipements_geom_geog`` ABSENT: created by migration 037, lost by the
partition swap of 041, restored by 042 — and then lost again, silently, by
every ``equipements`` ETL run since. The pre-hygiene pipeline never rebuilt
this index on staging, so the atomic table swap replaced the indexed table
with an unindexed one: the exact failure mode migration 042 had repaired once
by hand. DB-HYGIENE-01 fixed the root cause — the index is now in
``STAGING_INDEX_SPECS`` (``app/etl_equipements/pipeline.py``), so every future
staging table carries it through the swap. This migration restores it on the
production table that is live today, without waiting for the next ETL run.

Definition: byte-for-byte the one from migrations 037 and 042, so the pipeline
rename (``rename_indexes_after_swap``) and any future ``DROP INDEX`` by name
keep finding it.

Notes:

- ``IF NOT EXISTS``: on a fresh install migration 042 already created it, and
  if an equipements ETL run lands between merge and CD, the swap now promotes
  the staging twin under this very name. Both cases are the intended no-op.
- No ``CONCURRENTLY``, twice over: Alembic runs migrations inside a
  transaction, where ``CREATE INDEX CONCURRENTLY`` is forbidden — and
  ``equipements`` is partitioned by department (migration 041), and PostgreSQL
  does not support ``CONCURRENTLY`` on a partitioned parent at all (same
  constraint migration 037 documents for ``adresses``). The plain build takes
  a SHARE lock per partition: reads proceed, writes block — and the only
  writer is the scheduled ETL, which is not running during a supervised
  deploy. Current scope is departments 44/85 (~100k rows), a build of seconds.
- Deployment follows ADR-0003 (D1): supervised migrate-before-cutover.
"""

from alembic import op

revision: str = "085_recreate_idx_equipements_geom_geog"
down_revision: str = "084_drop_duplicate_staging_indexes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Exact definition of migrations 037 and 042.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_equipements_geom_geog "
        "ON equipements USING GIST (geography(geom))"
    )


def downgrade() -> None:
    # Symmetric with 042's downgrade. Note that dropping it re-opens the
    # geography-query regression this migration closes — but "index absent"
    # is genuinely the pre-085 production state, so this is a real downgrade,
    # not a destructive one.
    op.execute("DROP INDEX IF EXISTS idx_equipements_geom_geog")
