"""Add cron_schedule and freshness_threshold_days to dataset_metadata.

Revision ID: 018_scheduling_monitoring
Revises: 017_arcep_debit_max_to_text
Create Date: 2026-02-14

Seeds all 10 datasets with their CRON expressions and freshness thresholds
based on publication frequency (see 11-RESEARCH.md CRON schedule reference).
"""

revision = "018_scheduling_monitoring"
down_revision = "017_arcep_debit_max_to_text"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.execute("""
        ALTER TABLE dataset_metadata
            ADD COLUMN IF NOT EXISTS cron_schedule VARCHAR(50),
            ADD COLUMN IF NOT EXISTS freshness_threshold_days INTEGER
    """)

    # Upsert all 10 datasets with CRON + freshness values.
    # INSERT ... ON CONFLICT ensures rows exist even if dataset_metadata
    # was never populated for a given dataset.
    op.execute("""
        INSERT INTO dataset_metadata (dataset_name, status, cron_schedule, freshness_threshold_days)
        VALUES
            ('ban',          'unknown', '0 2 * * 0',         10),
            ('dpe',          'unknown', '0 3 1,15 * *',      21),
            ('transports',   'unknown', '0 4 1 * *',         45),
            ('cadastre',     'unknown', '0 2 15 1,4,7,10 *', 135),
            ('batiments',    'unknown', '0 2 1 1,4,7,10 *',  135),
            ('connectivite', 'unknown', '0 3 1 1,4,7,10 *',  135),
            ('dvf',          'unknown', '0 2 1 5,11 *',      270),
            ('loyers',       'unknown', '0 2 15 3 *',        545),
            ('scolaire',     'unknown', '0 3 15 10 *',       545),
            ('monuments',    'unknown', '0 2 15 1 *',        545)
        ON CONFLICT (dataset_name) DO UPDATE SET
            cron_schedule = EXCLUDED.cron_schedule,
            freshness_threshold_days = EXCLUDED.freshness_threshold_days
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE dataset_metadata
            DROP COLUMN IF EXISTS cron_schedule,
            DROP COLUMN IF EXISTS freshness_threshold_days
    """)
