"""Create etl_old_tables for deferred cleanup of swapped production tables.

Revision ID: 049_etl_old_tables
Revises: 048_chat_tables
"""

from alembic import op

revision = "049_etl_old_tables"
down_revision = "048_chat_tables"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS etl_old_tables (
            id SERIAL PRIMARY KEY,
            table_name TEXT NOT NULL UNIQUE,
            dataset_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_etl_old_tables_created "
        "ON etl_old_tables (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS etl_old_tables CASCADE")
