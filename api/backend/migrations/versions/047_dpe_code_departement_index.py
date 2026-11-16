"""Add index on diagnostics_dpe.code_departement for zone queries.

Without this index, zone/resolve and zone/stats queries at departement
level perform a sequential scan on 14M rows.

Revision ID: 047_dpe_code_departement_index
Revises: 046_users_is_admin
Create Date: 2026-03-28
"""

from alembic import op

revision: str = "047_dpe_code_departement_index"
down_revision: str = "046_users_is_admin"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create btree index on code_departement for departement-level lookups."""
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dpe_code_departement "
        "ON diagnostics_dpe (code_departement);"
    )


def downgrade() -> None:
    """Drop the departement index."""
    op.execute("DROP INDEX IF EXISTS idx_dpe_code_departement;")
