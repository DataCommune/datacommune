"""Add real INSEE code, exact parcel link and strict habitation lot count to copropriete.

Revision ID: 076_copropriete_parcelle_insee
Revises: 075_dpe_logement_fields
Create Date: 2026-07-27

D-20 (Phase 48 CONTEXT) -- the copropriete signal (D1) is broken in production today:
``copropriete.code_commune`` holds the POSTAL code instead of the INSEE code
(live proof, 2026-07-25: ``GET /v1/copropriete?code_insee=44190`` returns 3
coproprietes located in Clisson, not in the requested commune). This makes D1
("copro RNC present" per D-03) unimplementable as a parcel-exact signal, and no
column links a copropriete to a cadastral parcel at all.

Columns added:
  - code_insee (VARCHAR(5)): the real INSEE code, sourced from the RNIC
    ``code_officiel_commune`` column (2026 schema) -- distinct from the existing
    ``code_commune`` column, which is preserved unchanged (additive migration,
    existing read path ``copropriete_par_codes`` keeps working as-is).
  - id_parcelle_1 (VARCHAR(14)): the primary cadastral parcel, derived from RNIC
    ``reference_cadastrale_1`` (or recomposed from the parcel-part columns) by
    the ETL pipeline -- never guessed, NULL when it cannot be derived exactly.
  - longitude / latitude (DOUBLE PRECISION): RNIC-provided coordinates.
  - nb_lots_habitation_strict (INTEGER): RNIC column 34
    (``nombre_lots_habitation``), habitation lots ONLY -- distinct from the
    existing ``nb_lots_habitation`` column, which is fed by RNIC column 33
    (habitation + bureaux + commerces) and is preserved unchanged.

Indexes: idx_copropriete_id_parcelle_1 (parcel join, mirrors migration 072's
B-tree on mutations.id_parcelle) and idx_copropriete_code_insee (commune
lookup on the corrected code).

All 5 new columns stay NULL until the next copropriete ETL refresh (Plan
48-10) backfills them -- same precedent as migration 070's docstring.
"""

from alembic import op

revision: str = "076_copropriete_parcelle_insee"
down_revision: str = "075_dpe_logement_fields"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE copropriete "
        "ADD COLUMN IF NOT EXISTS code_insee VARCHAR(5), "
        "ADD COLUMN IF NOT EXISTS id_parcelle_1 VARCHAR(14), "
        "ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION, "
        "ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION, "
        "ADD COLUMN IF NOT EXISTS nb_lots_habitation_strict INTEGER;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_copropriete_id_parcelle_1 "
        "ON copropriete (id_parcelle_1)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_copropriete_code_insee "
        "ON copropriete (code_insee)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_copropriete_id_parcelle_1")
    op.execute("DROP INDEX IF EXISTS idx_copropriete_code_insee")
    op.execute(
        "ALTER TABLE copropriete "
        "DROP COLUMN IF EXISTS code_insee, "
        "DROP COLUMN IF EXISTS id_parcelle_1, "
        "DROP COLUMN IF EXISTS longitude, "
        "DROP COLUMN IF EXISTS latitude, "
        "DROP COLUMN IF EXISTS nb_lots_habitation_strict;"
    )
