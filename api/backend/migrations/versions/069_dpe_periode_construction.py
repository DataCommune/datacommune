"""Add periode_construction to diagnostics_dpe.

Revision ID: 069_dpe_periode_construction
Revises: 068_fix_commune_dvf_price_m2
Create Date: 2026-07-02

The ADEME source fills annee_construction (exact year) only ~63% of the time,
but periode_construction (construction bracket, e.g. "1948-1974") is present on
~100% of records. Ingesting it lets the fiche fall back to the bracket when the
exact year is missing. The staging table is recreated each run via
``LIKE diagnostics_dpe INCLUDING ALL`` so this ALTER on production is enough;
existing rows stay NULL until the next DPE ETL refresh backfills them.
"""

from alembic import op

revision = "069_dpe_periode_construction"
down_revision = "068_fix_commune_dvf_price_m2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ADD COLUMN IF NOT EXISTS periode_construction VARCHAR(50);"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe DROP COLUMN IF EXISTS periode_construction;"
    )
