"""Widen diagnostics_dpe numeric columns to avoid overflow.

Revision ID: 016_widen_dpe_numeric
Revises: 015_widen_valeur_fonciere
Create Date: 2026-02-12

ADEME DPE data contains extreme values for surface and energy consumption
(e.g. large commercial buildings). Widen to avoid numeric field overflow
during ETL stage.
"""

from alembic import op

revision: str = "016_widen_dpe_numeric"
down_revision: str = "015_widen_valeur_fonciere"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # surface_habitable: 8,2 -> 12,2 (up to 9_999_999_999.99 m²)
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ALTER COLUMN surface_habitable TYPE NUMERIC(12, 2);"
    )
    # conso_5_usages_ep: 10,2 -> 14,2
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ALTER COLUMN conso_5_usages_ep TYPE NUMERIC(14, 2);"
    )
    # emission_ges_5_usages: 10,2 -> 14,2
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ALTER COLUMN emission_ges_5_usages TYPE NUMERIC(14, 2);"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ALTER COLUMN surface_habitable TYPE NUMERIC(8, 2);"
    )
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ALTER COLUMN conso_5_usages_ep TYPE NUMERIC(10, 2);"
    )
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ALTER COLUMN emission_ges_5_usages TYPE NUMERIC(10, 2);"
    )
