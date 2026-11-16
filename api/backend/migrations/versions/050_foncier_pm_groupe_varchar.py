"""Change foncier_pm.groupe_personne from SMALLINT to VARCHAR(3).

MAJIC PM source data has alphanumeric group codes (e.g. "9A", "0A", "5A")
that cannot be cast to SMALLINT. Change to VARCHAR(3) to match actual data.

Revision ID: 050_foncier_pm_groupe_varchar
Revises: 049_etl_old_tables
Create Date: 2026-04-02
"""

from alembic import op

revision: str = "050_foncier_pm_groupe_varchar"
down_revision: str = "049_etl_old_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Change groupe_personne column from SMALLINT to VARCHAR(3)."""
    op.execute(
        "ALTER TABLE foncier_pm "
        "ALTER COLUMN groupe_personne TYPE VARCHAR(3) "
        "USING groupe_personne::text"
    )


def downgrade() -> None:
    """Revert groupe_personne to SMALLINT (lossy for alphanumeric values)."""
    op.execute(
        "ALTER TABLE foncier_pm "
        "ALTER COLUMN groupe_personne TYPE SMALLINT "
        "USING NULLIF(groupe_personne, '')::smallint"
    )
