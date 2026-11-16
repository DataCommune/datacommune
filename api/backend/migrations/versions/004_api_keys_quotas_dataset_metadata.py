"""Create api_keys, quota_usage, and dataset_metadata tables.

Revision ID: 004_auth
Revises: 003_cadastre
Create Date: 2026-02-09
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_auth"
down_revision: str = "003_cadastre"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create authentication and metadata tables."""
    # API keys table
    op.execute("""
        CREATE TABLE api_keys (
            id SERIAL PRIMARY KEY,
            key_hash VARCHAR(64) NOT NULL UNIQUE,
            name VARCHAR(100) NOT NULL,
            quota_monthly INTEGER NOT NULL DEFAULT 10000,
            revoked BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    # Monthly quota usage (composite PK on api_key_id + month)
    op.execute("""
        CREATE TABLE quota_usage (
            api_key_id INTEGER NOT NULL REFERENCES api_keys(id),
            month VARCHAR(7) NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (api_key_id, month)
        );
    """)

    # Dataset metadata for monitoring ETL status
    op.execute("""
        CREATE TABLE dataset_metadata (
            dataset_name VARCHAR(50) PRIMARY KEY,
            status VARCHAR(20) NOT NULL DEFAULT 'unknown',
            last_updated_at TIMESTAMPTZ,
            row_count BIGINT,
            last_import_duration_seconds DOUBLE PRECISION
        );
    """)

    # Seed known datasets
    op.execute("""
        INSERT INTO dataset_metadata (dataset_name, status)
        VALUES ('ban', 'unknown'), ('cadastre', 'unknown');
    """)


def downgrade() -> None:
    """Drop tables in reverse dependency order."""
    op.execute("DROP TABLE IF EXISTS quota_usage;")
    op.execute("DROP TABLE IF EXISTS api_keys;")
    op.execute("DROP TABLE IF EXISTS dataset_metadata;")
