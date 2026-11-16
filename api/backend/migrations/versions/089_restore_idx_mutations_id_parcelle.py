"""Restore idx_mutations_id_parcelle, lost in production by the DVF ETL swaps.

Revision ID: 089_restore_mutations_idx
Revises: 088_v_commune_dvf_mutation_grain
Create Date: 2026-08-25

Migration 072 created ``idx_mutations_id_parcelle`` on ``mutations``, but
``DvfPipeline._create_staging_indexes`` never rebuilt it on
``mutations_staging``: the atomic swap therefore replaced an indexed table
with an unindexed one on every DVF run — the same "committed fix, unapplied
live state" failure migration 085 repaired for ``idx_equipements_geom_geog``.
The root cause is closed in the pipeline (plan 57-19, Task 1); this migration
restores the index on the production table that is live today, without waiting
for the next ETL run.

Cost of its absence, measured: ``57-PERF-EXPLAIN.md`` § 6.3 — the Fiche Bien's
nominal ``id_parcelle`` access path runs a sequential partition scan at
19.8 ms instead of an index lookup at 0.04-0.08 ms.

Definition: byte-for-byte the one from migration 072, so
``rename_indexes_after_swap`` and any future ``DROP INDEX`` by name keep
finding it.

Why a migration and not a runbook step — three reasons:

1. ``CREATE INDEX CONCURRENTLY`` is impossible here. ``mutations`` is
   LIST-partitioned by ``code_departement`` (102 partitions, migration 005)
   and PostgreSQL does not support ``CONCURRENTLY`` on a partitioned table —
   written in as many words in migrations 021, 037 and 072. The general
   "always CONCURRENTLY so nothing blocks" rule simply does not apply to this
   table. A plain ``CREATE INDEX`` on the parent cascades to the children
   (PostgreSQL 11+) at the price of an ACCESS EXCLUSIVE lock while it builds.
   Volume concerned in production: 287 261 mutations over two live partitions
   (44 and 85, cf. ``57-CONVERGENCE.md``) — a build counted in seconds, not
   minutes, inside the supervised deploy window (ADR-0003 D1).
2. A migration is the ONLY reproducible channel. ADR-0003 mandates the
   migration gate and the fail-fast at boot when Alembic is not at head, so a
   migration is applied automatically at deploy, on every instance, a fresh
   install included. A runbook step is manual, applies only to today's
   production, and would be forgotten on the next one.
3. It is idempotent. ``CREATE INDEX IF NOT EXISTS``: on a database where the
   index survived (no DVF swap replayed since 072, or a fresh install where
   072 has just created it), this migration is a silent no-op.
"""

from alembic import op

revision: str = "089_restore_mutations_idx"
down_revision: str = "088_v_commune_dvf_mutation_grain"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Exact definition of migration 072.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mutations_id_parcelle "
        "ON mutations (id_parcelle)"
    )


def downgrade() -> None:
    # Deliberate no-op, NOT a DROP INDEX. Migration 072 stays the owner of this
    # index's lifecycle: two migrations disputing the same index is exactly how
    # it gets lost a second time. Downgrading past 089 leaves the index in
    # place, which is the state 072 already guarantees.
    pass
