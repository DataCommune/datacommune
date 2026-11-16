"""Seed dataset_metadata for majic_pm (MAJIC personnes morales).

Adds the majic_pm entry so GET /v1/datasets includes it.
Cron: yearly (July 1st at 03:00) — MAJIC files refreshed ~annually.

Revision ID: 045_majic_pm_metadata
Revises: 044_foncier_pm
Create Date: 2026-03-18
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "045_majic_pm_metadata"
down_revision: str = "044_foncier_pm"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Insert majic_pm into dataset_metadata."""
    op.execute("""
        INSERT INTO dataset_metadata
            (dataset_name, status, cron_schedule, freshness_threshold_days)
        VALUES
            ('majic_pm', 'disponible', '0 3 1 7 *', 545)
        ON CONFLICT (dataset_name) DO UPDATE SET
            status = EXCLUDED.status,
            cron_schedule = EXCLUDED.cron_schedule,
            freshness_threshold_days = EXCLUDED.freshness_threshold_days;
    """)


def downgrade() -> None:
    """Remove majic_pm from dataset_metadata."""
    op.execute("DELETE FROM dataset_metadata WHERE dataset_name = 'majic_pm';")
