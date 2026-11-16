"""Partition equipements table by code_departement (102 partitions).

WARNING: This migration copies ~2.5M rows and temporarily doubles disk usage
for the equipements table. Run during off-hours and verify free disk space
beforehand (df -h). Expected duration: 1-5 minutes.

Revision ID: 041_partition_equip
Revises: 040_sub_idx
Create Date: 2026-03-17
"""

from alembic import op

revision: str = "041_partition_equip"
down_revision: str = "040_sub_idx"
branch_labels: str | None = None
depends_on: str | None = None

DEPARTEMENTS: list[str] = [
    *[f"{i:02d}" for i in range(1, 20)],
    "2A",
    "2B",
    *[str(i) for i in range(21, 96)],
    *[str(i) for i in range(971, 977)],
]


def upgrade() -> None:
    """Replace equipements with a partitioned version."""
    # 1. Create new partitioned table
    op.execute("""
        CREATE TABLE equipements_new (
            id SERIAL,
            type_equipement VARCHAR(10) NOT NULL,
            code_commune VARCHAR(5) NOT NULL,
            code_departement VARCHAR(3) NOT NULL,
            code_iris VARCHAR(9),
            domaine TEXT,
            sous_domaine TEXT,
            nb_equipements INTEGER DEFAULT 1,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            geom GEOMETRY(POINT, 4326),
            annee INTEGER NOT NULL
        ) PARTITION BY LIST (code_departement);
    """)

    # 2. Create 102 partitions
    for dept in DEPARTEMENTS:
        safe = dept.lower()
        op.execute(
            f"CREATE TABLE equipements_new_{safe} "
            f"PARTITION OF equipements_new "
            f"FOR VALUES IN ('{dept}')"
        )
    op.execute(
        "CREATE TABLE equipements_new_default "
        "PARTITION OF equipements_new DEFAULT"
    )

    # 3. Copy data
    op.execute("""
        INSERT INTO equipements_new
            (id, type_equipement, code_commune, code_departement,
             code_iris, domaine, sous_domaine, nb_equipements,
             latitude, longitude, geom, annee)
        SELECT id, type_equipement, code_commune,
               COALESCE(code_departement, LEFT(code_commune, 2)),
               code_iris, domaine, sous_domaine, nb_equipements,
               latitude, longitude, geom, annee
        FROM equipements
    """)

    # 4. Atomic swap
    op.execute("DROP TABLE equipements CASCADE")
    op.execute("ALTER TABLE equipements_new RENAME TO equipements")

    # 5. Rename partitions
    for dept in DEPARTEMENTS:
        safe = dept.lower()
        op.execute(
            f"ALTER TABLE IF EXISTS equipements_new_{safe} "
            f"RENAME TO equipements_{safe}"
        )
    op.execute(
        "ALTER TABLE IF EXISTS equipements_new_default "
        "RENAME TO equipements_default"
    )

    # 6. Recreate indexes
    op.execute(
        "CREATE INDEX idx_equipements_code_commune "
        "ON equipements (code_commune)"
    )
    op.execute(
        "CREATE INDEX idx_equipements_type "
        "ON equipements (type_equipement)"
    )
    op.execute(
        "CREATE INDEX idx_equipements_domaine "
        "ON equipements (domaine)"
    )
    op.execute(
        "CREATE INDEX idx_equipements_geom "
        "ON equipements USING GIST (geom)"
    )
    op.execute(
        "CREATE INDEX idx_equipements_dept "
        "ON equipements (code_departement)"
    )


def downgrade() -> None:
    """Revert to non-partitioned equipements (data loss if partitions differ)."""
    op.execute("""
        CREATE TABLE equipements_flat (
            id SERIAL PRIMARY KEY,
            type_equipement VARCHAR(10) NOT NULL,
            code_commune VARCHAR(5) NOT NULL,
            code_departement VARCHAR(3),
            code_iris VARCHAR(9),
            domaine TEXT,
            sous_domaine TEXT,
            nb_equipements INTEGER DEFAULT 1,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            geom GEOMETRY(POINT, 4326),
            annee INTEGER NOT NULL
        )
    """)
    op.execute("INSERT INTO equipements_flat SELECT * FROM equipements")
    op.execute("DROP TABLE equipements CASCADE")
    op.execute("ALTER TABLE equipements_flat RENAME TO equipements")
    op.execute(
        "CREATE INDEX idx_equipements_code_commune ON equipements (code_commune)"
    )
    op.execute(
        "CREATE INDEX idx_equipements_type ON equipements (type_equipement)"
    )
    op.execute(
        "CREATE INDEX idx_equipements_domaine ON equipements (domaine)"
    )
    op.execute(
        "CREATE INDEX idx_equipements_geom ON equipements USING GIST (geom)"
    )
    op.execute(
        "CREATE INDEX idx_equipements_dept ON equipements (code_departement)"
    )
