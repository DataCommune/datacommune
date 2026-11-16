"""Create matview_metadata table for tracking materialized view refresh state.

Addresses review #1 finding #14: /v1/health currently reports derniere_maj=None
for every matview because it queries pg_matviews (which has no refresh timestamp).
This migration introduces a lightweight metadata table that the scheduler writes
on every refresh (success or failure), giving the health endpoint real timestamps.

Revision ID: 063_matview_metadata
Revises: 062_subscriptions_source_backfill
Create Date: 2026-04-17
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "063_matview_metadata"
down_revision = "062_subscriptions_source_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS matview_metadata (
            matview_name VARCHAR(100) PRIMARY KEY,
            last_refresh_at TIMESTAMPTZ,
            last_refresh_duration_seconds DOUBLE PRECISION,
            last_refresh_status VARCHAR(20) CHECK (last_refresh_status IN ('success', 'failed')),
            last_error TEXT,
            freshness_threshold_days INT NOT NULL DEFAULT 2
        )
    """)
    op.execute("""
        INSERT INTO matview_metadata (matview_name, freshness_threshold_days) VALUES
            ('v_commune_synthese', 2),
            ('communes_lookup', 2)
        ON CONFLICT (matview_name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS matview_metadata")
