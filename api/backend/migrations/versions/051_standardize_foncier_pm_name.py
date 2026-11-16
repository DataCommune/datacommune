"""Standardize dataset_metadata name: majic_pm -> foncier_pm.

All layers now use 'foncier_pm' as the canonical name (API module, scheduler,
DB table). This migration aligns the dataset_metadata row to match.

Revision ID: 051_standardize_foncier_pm_name
Revises: 050_foncier_pm_groupe_varchar
Create Date: 2026-04-04
"""

from alembic import op

revision: str = "051_standardize_foncier_pm_name"
down_revision: str = "050_foncier_pm_groupe_varchar"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Rename dataset_metadata entry from majic_pm to foncier_pm."""
    op.execute(
        "UPDATE dataset_metadata SET dataset_name = 'foncier_pm' "
        "WHERE dataset_name = 'majic_pm'"
    )


def downgrade() -> None:
    """Revert dataset_metadata entry from foncier_pm to majic_pm."""
    op.execute(
        "UPDATE dataset_metadata SET dataset_name = 'majic_pm' "
        "WHERE dataset_name = 'foncier_pm'"
    )
