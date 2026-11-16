"""Restore constraints lost by partitioned ETL swaps.

Revision ID: 065_restore_etl_constraints
Revises: 064
Create Date: 2026-06-13

The generic staging table used ``LIKE ... EXCLUDING INDEXES``. PostgreSQL
implements PRIMARY KEY and UNIQUE constraints with indexes, so those
constraints disappeared after the first swap. The ETL now restores them before
every swap; this migration repairs already-swapped production tables.

DPE is intentionally excluded here because an older ETL converted it to a
partitioned table while its original UNIQUE(numero_dpe) requires a flat table.
The corrected DPE pipeline rebuilds it flat and restores both constraints on its
next run.
"""

from alembic import op

revision = "065_restore_etl_constraints"
down_revision = "064"
branch_labels = None
depends_on = None


def _restore_partitioned_primary_key(table: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = '{table}'::regclass
                  AND contype = 'p'
            ) THEN
                ALTER TABLE {table}
                ADD PRIMARY KEY (id, code_departement);
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    for table in ("adresses", "parcelles", "mutations", "foncier_pm"):
        _restore_partitioned_primary_key(table)


def downgrade() -> None:
    # These constraints are part of the original table contracts. Removing
    # them would recreate the corruption this migration repairs.
    pass
