"""Store the DPE source columns decided by the owner for plan 04-15.

Revision ID: 100_dpe_source_columns
Revises: 099_geo_arrondissements
Create Date: 2026-10-06

Owner decisions C1 to C3 of 2026-10-06
(internal tracking,
04-DECISIONS-COLONNES-DPE), taken on the 04-14 inventory:

- C1: ``version_dpe``, ``date_derniere_modification_dpe``,
  ``date_reception_dpe``, ``date_visite_diagnostiqueur`` were requested,
  archived and dropped before staging (the only import loss 04-14 proved).
  Stored as published, ``TEXT``: no cast can fail the swap or silently lose a
  malformed source value;
- C2: ``conso_5_usages_par_m2_ep``, ``emission_ges_5_usages_par_m2``, the ADEME
  per-m2 values of landmarks 1 and 2, exact ``NUMERIC`` (no bound, no scale);
- C3: ``numero_dpe_immeuble_associe``, ``numero_immatriculation_copropriete``,
  stored before the first archived DPE import and never served without a
  later decision.

Same ``MAPPING_VERSION`` change (``04.15-44-fields-v1``): one migration, one
column list (P-ARCHIVE-DPE, option B). The staging table is recreated each run
by ``LIKE diagnostics_dpe INCLUDING ALL``, so this ALTER on production is
enough; existing rows stay NULL until the next DPE import (04-20). No index:
no reader looks a document up by these columns.
"""

from alembic import op

revision: str = "100_dpe_source_columns"
down_revision: str = "099_geo_arrondissements"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ADD COLUMN IF NOT EXISTS version_dpe TEXT, "
        "ADD COLUMN IF NOT EXISTS date_derniere_modification_dpe TEXT, "
        "ADD COLUMN IF NOT EXISTS date_reception_dpe TEXT, "
        "ADD COLUMN IF NOT EXISTS date_visite_diagnostiqueur TEXT, "
        "ADD COLUMN IF NOT EXISTS conso_5_usages_par_m2_ep NUMERIC, "
        "ADD COLUMN IF NOT EXISTS emission_ges_5_usages_par_m2 NUMERIC, "
        "ADD COLUMN IF NOT EXISTS numero_dpe_immeuble_associe TEXT, "
        "ADD COLUMN IF NOT EXISTS numero_immatriculation_copropriete TEXT;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "DROP COLUMN IF EXISTS version_dpe, "
        "DROP COLUMN IF EXISTS date_derniere_modification_dpe, "
        "DROP COLUMN IF EXISTS date_reception_dpe, "
        "DROP COLUMN IF EXISTS date_visite_diagnostiqueur, "
        "DROP COLUMN IF EXISTS conso_5_usages_par_m2_ep, "
        "DROP COLUMN IF EXISTS emission_ges_5_usages_par_m2, "
        "DROP COLUMN IF EXISTS numero_dpe_immeuble_associe, "
        "DROP COLUMN IF EXISTS numero_immatriculation_copropriete;"
    )
