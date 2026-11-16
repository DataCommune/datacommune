"""Store the BAN address an orphan DPE names by its exact raw address.

Revision ID: 104_dpe_rattachement_adresse_brute
Revises: 103_commune_indicator_cells_national
Create Date: 2026-10-18

Plan 04.1-13 (criterion 6 of phase 4.1, D-127, block H of 04-MESURE-DPE).
``diagnostics_dpe.ban_id_adresse_brute`` holds the local BAN key that the raw
address of an orphan document (ADEME key of a commune or a street, absent from
the BAN) names by strict equality, single candidate and identical street code
(``app.etl_dpe.adresse_brute``). NULL for every other document. It never
replaces ``identifiant_ban`` (the ADEME key, kept as published) and never
names a dwelling.

The DPE import fills it on its candidate at every refresh; existing rows stay
NULL until the next DPE import. The partial index has its counterpart in
``DpePipeline._create_staging_indexes`` (the swap renames the candidate over
the served table: a migration index without it would not survive a refresh).

Downgrade drops the index and the column; the values are rebuilt by the next
DPE import. Applied on local disposable PostGIS only; the served base of the
work base container is migrated with Ianis's agreement (plan 04.1-19).
"""

from alembic import op

revision: str = "104_dpe_rattachement_adresse_brute"
down_revision: str = "103_commune_indicator_cells_national"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ADD COLUMN IF NOT EXISTS ban_id_adresse_brute TEXT;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dpe_ban_id_adresse_brute "
        "ON diagnostics_dpe (ban_id_adresse_brute) "
        "WHERE ban_id_adresse_brute IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dpe_ban_id_adresse_brute;")
    op.execute(
        "ALTER TABLE diagnostics_dpe DROP COLUMN IF EXISTS ban_id_adresse_brute;"
    )
