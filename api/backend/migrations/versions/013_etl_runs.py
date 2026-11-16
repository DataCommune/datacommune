"""Create etl_runs table for ETL execution history tracking.

Revision ID: 013_etl_runs
Revises: 012_transports
Create Date: 2026-02-11

Tracks every ETL pipeline execution with status, duration, row counts
and error messages. Used by PipelineRunner and exposed via /v1/datasets.
"""

from alembic import op

revision: str = "013_etl_runs"
down_revision: str = "012_transports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE etl_runs (
            id BIGSERIAL PRIMARY KEY,
            dataset_name VARCHAR(50) NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'running',
            total_rows_loaded BIGINT DEFAULT 0,
            total_duration_seconds DOUBLE PRECISION,
            error_message TEXT,
            stages JSONB
        );

        CREATE INDEX idx_etl_runs_dataset
            ON etl_runs (dataset_name);
        CREATE INDEX idx_etl_runs_started
            ON etl_runs (started_at DESC);
        CREATE INDEX idx_etl_runs_dataset_started
            ON etl_runs (dataset_name, started_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS etl_runs CASCADE;")
