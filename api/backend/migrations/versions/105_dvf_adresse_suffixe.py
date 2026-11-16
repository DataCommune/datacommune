"""Keep the suffix of the declared DVF address (« 2 B »).

Revision ID: 105_dvf_adresse_suffixe
Revises: 104_dpe_rattachement_adresse_brute
Create Date: 2026-10-19

Plan 04.1-14 (report C44, D-127). ``adresse_suffixe`` of the geo-DVF file is
kept in `mutations` (each line, as published) and in `mutations_grain` (the
suffix of the line whose number and street the mutation keeps). Both columns
are NULL here: no value is invented, the next DVF import fills them. The
reconstruction then comments the `mutations_grain` column
(``app.etl_dvf.config.MARQUEUR_ADRESSE_SUFFIXE``); a base migrated but not
yet reimported has the column without the comment, read by the address sales
route as « suffix unknown », never as « no suffix ».

No index: the address sales read the suffix among the sales of one commune
and one number only. Both tables are partitioned; the column reaches the
partitions from the parent.

Downgrade drops both columns; the values are rebuilt by the next DVF import.
Applied on local disposable PostGIS only; the served base of the work base is
migrated with Ianis's agreement (plan 04.1-19).
"""

from alembic import op

revision: str = "105_dvf_adresse_suffixe"
down_revision: str = "104_dpe_rattachement_adresse_brute"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE mutations ADD COLUMN IF NOT EXISTS adresse_suffixe TEXT;")
    op.execute(
        "ALTER TABLE mutations_grain ADD COLUMN IF NOT EXISTS adresse_suffixe TEXT;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mutations_grain DROP COLUMN IF EXISTS adresse_suffixe;")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS adresse_suffixe;")
