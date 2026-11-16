"""Create saved_searches table for server-side search persistence.

Stores search inputs (addresses + datasets) per user for named search saving.
JSONB columns for flexible address/dataset storage.

Revision ID: 025_saved_searches
Revises: 024_geo_boundaries
Create Date: 2026-03-04
"""

from alembic import op

revision: str = "025_saved_searches"
down_revision: str = "024_geo_boundaries"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create saved_searches table with user FK and composite index."""
    op.execute("""
        CREATE TABLE saved_searches (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            addresses JSONB NOT NULL,
            datasets JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX idx_saved_searches_user "
        "ON saved_searches (user_id, created_at DESC)"
    )


def downgrade() -> None:
    """Drop saved_searches table and index."""
    op.execute("DROP INDEX IF EXISTS idx_saved_searches_user")
    op.execute("DROP TABLE IF EXISTS saved_searches")
