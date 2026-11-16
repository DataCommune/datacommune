"""Second and third parcels of a copropriete registration, and their count.

Revision ID: 116_copropriete_parcelles_secondaires
Revises: 115_commune_dvf_annuel
Create Date: 2026-11-11

KI-20. The RNIC cites up to three cadastral parcels per registration
(``reference_cadastrale_1`` to ``_3``) and says how many the condominium
stands on (``nombre_parcelles``). Only the first was kept (migration 076):
an address standing on the second or the third parcel read « no registered
condominium ». Measured at the source on 2026-11-11: 90 941 registrations
of 657 108 cite a second parcel, 41 920 a third.

- ``id_parcelle_2``, ``id_parcelle_3`` (VARCHAR(14)): derived like
  ``id_parcelle_1`` (``etl_copropriete.pipeline.derive_id_parcelle``).
- ``nb_parcelles`` (INTEGER): the count declared by the registration; above
  three, the following parcels are not published by the source.

All three are nullable and NULL means "not imported yet" or "not declared";
no backfill, the next import of ``copropriete`` fills them. The pipeline
builds its staging table with ``LIKE copropriete``, so the swap keeps the
columns once they exist here; its staging indexes carry the two new names.
Downgrade drops the columns (their data is re-importable).
"""

from alembic import op

revision: str = "116_copropriete_parcelles_secondaires"
down_revision: str = "115_commune_dvf_annuel"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE copropriete
            ADD COLUMN IF NOT EXISTS id_parcelle_2 VARCHAR(14),
            ADD COLUMN IF NOT EXISTS id_parcelle_3 VARCHAR(14),
            ADD COLUMN IF NOT EXISTS nb_parcelles INTEGER
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_copropriete_id_parcelle_2 "
        "ON copropriete (id_parcelle_2)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_copropriete_id_parcelle_3 "
        "ON copropriete (id_parcelle_3)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_copropriete_id_parcelle_2")
    op.execute("DROP INDEX IF EXISTS idx_copropriete_id_parcelle_3")
    op.execute("""
        ALTER TABLE copropriete
            DROP COLUMN IF EXISTS nb_parcelles,
            DROP COLUMN IF EXISTS id_parcelle_3,
            DROP COLUMN IF EXISTS id_parcelle_2
    """)
