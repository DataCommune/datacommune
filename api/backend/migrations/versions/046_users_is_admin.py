"""Add is_admin flag to users table.

Revision ID: 046_users_is_admin
Revises: 045_majic_pm_metadata
Create Date: 2026-03-27
"""

from alembic import op

revision: str = "046_users_is_admin"
down_revision: str = "045_majic_pm_metadata"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add is_admin boolean column to users table."""
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    """Remove is_admin column."""
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_admin")
