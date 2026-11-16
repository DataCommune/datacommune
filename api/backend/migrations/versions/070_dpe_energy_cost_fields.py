"""Add energy cost / heating energy / envelope insulation to diagnostics_dpe.

Revision ID: 070_dpe_energy_cost_fields
Revises: 069_dpe_periode_construction
Create Date: 2026-07-02

High-value ADEME fields for the bien fiche, all ~100% filled at source but not
previously ingested (we kept 15 of 230 columns):
  - cout_total_5_usages: estimated yearly energy cost (EUR) — the most telling
    metric for a buyer.
  - type_energie_principale_chauffage: main heating energy (gas/electricity/...).
  - qualite_isolation_enveloppe: envelope insulation summary (bonne/moyenne/...).

The staging table is recreated each run via ``LIKE diagnostics_dpe INCLUDING ALL``
so this ALTER on production is enough; existing rows stay NULL until the next DPE
ETL refresh backfills them.
"""

from alembic import op

revision = "070_dpe_energy_cost_fields"
down_revision = "069_dpe_periode_construction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ADD COLUMN IF NOT EXISTS cout_total_5_usages NUMERIC(12, 2), "
        "ADD COLUMN IF NOT EXISTS type_energie_principale_chauffage VARCHAR(100), "
        "ADD COLUMN IF NOT EXISTS qualite_isolation_enveloppe VARCHAR(50);"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "DROP COLUMN IF EXISTS cout_total_5_usages, "
        "DROP COLUMN IF EXISTS type_energie_principale_chauffage, "
        "DROP COLUMN IF EXISTS qualite_isolation_enveloppe;"
    )
