"""Create validation_runs table for post-swap data quality tracking.

Revision ID: 054_validation_runs
Revises: 053_densite_from_cadastre
Create Date: 2026-04-05
"""

from alembic import op

revision = "054_validation_runs"
down_revision = "053_densite_from_cadastre"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS validation_runs (
            id SERIAL PRIMARY KEY,
            dataset_name TEXT NOT NULL,
            run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            row_count_before BIGINT,
            row_count_after BIGINT,
            row_drop_pct NUMERIC(5,2),
            null_rate_findings JSONB DEFAULT '[]',
            passed BOOLEAN NOT NULL DEFAULT TRUE,
            blocked_swap BOOLEAN NOT NULL DEFAULT FALSE,
            notes TEXT
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_validation_runs_dataset "
        "ON validation_runs (dataset_name, run_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS validation_runs CASCADE")
