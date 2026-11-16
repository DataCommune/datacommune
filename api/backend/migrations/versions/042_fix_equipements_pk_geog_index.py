"""Add missing PK and geography index on equipements, document scoring scope.

After migration 041 partitioned equipements, the PRIMARY KEY and geography
index were not recreated.  This migration restores them and adds a SQL
COMMENT documenting the current scope of scoring_renov_target (depts 44, 33).

Revision ID: 042_fix_equip_pk_geog
Revises: 041_partition_equip
Create Date: 2026-03-17
"""

from alembic import op

revision: str = "042_fix_equip_pk_geog"
down_revision: str = "041_partition_equip"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Remove duplicate (id, code_departement) rows that would block PK creation
    op.execute(
        "DELETE FROM equipements a USING equipements b "
        "WHERE a.ctid < b.ctid "
        "AND a.id = b.id "
        "AND a.code_departement = b.code_departement"
    )

    # 2. Composite PK — only add if not already present (idempotent)
    op.execute(
        "DO $$ "
        "BEGIN "
        "  IF NOT EXISTS ( "
        "    SELECT 1 FROM pg_constraint "
        "    WHERE conrelid = 'equipements'::regclass AND contype = 'p' "
        "  ) THEN "
        "    ALTER TABLE equipements ADD PRIMARY KEY (id, code_departement); "
        "  END IF; "
        "END $$;"
    )

    # 3. Geography index for ST_DWithin queries (was in 037, lost by 041 swap)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_equipements_geom_geog "
        "ON equipements USING GIST (geography(geom))"
    )

    # 4. Document scoring matview scope limitation
    op.execute(
        "COMMENT ON MATERIALIZED VIEW scoring_renov_target IS "
        "'v1 MVP: departments 44 and 33 only. Expand scope before adding new depts.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON MATERIALIZED VIEW scoring_renov_target IS NULL")
    op.execute("DROP INDEX IF EXISTS idx_equipements_geom_geog")
    op.execute("ALTER TABLE equipements DROP CONSTRAINT IF EXISTS equipements_pkey")
