"""Create partitioned mutations table for DVF property transactions.

Revision ID: 005_dvf
Revises: 004_auth
Create Date: 2026-02-10
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005_dvf"
down_revision: str = "004_auth"
branch_labels: str | None = None
depends_on: str | None = None

# All 102 departement codes:
# 01-19 (19), 2A+2B (2), 21-95 (75), 971-976 (6) = 102
DEPARTEMENTS = [
    *[f"{i:02d}" for i in range(1, 20)],  # 01-19
    "2A",
    "2B",  # Corse
    *[str(i) for i in range(21, 96)],  # 21-95
    *[str(i) for i in range(971, 977)],  # DOM: 971-976
]


def upgrade() -> None:
    """Create partitioned mutations table with spatial and temporal indexes."""
    # Create partitioned parent table
    op.execute("""
        CREATE TABLE mutations (
            id BIGSERIAL,
            code_departement VARCHAR(3) NOT NULL,
            id_mutation TEXT NOT NULL,
            date_mutation DATE NOT NULL,
            nature_mutation VARCHAR(50),
            valeur_fonciere NUMERIC(12, 2),
            adresse_numero VARCHAR(10),
            adresse_nom_voie TEXT,
            code_postal VARCHAR(5),
            code_commune VARCHAR(5),
            nom_commune TEXT,
            id_parcelle VARCHAR(14),
            type_local VARCHAR(50),
            surface_reelle_bati INTEGER,
            nombre_pieces_principales INTEGER,
            surface_terrain INTEGER,
            longitude DOUBLE PRECISION,
            latitude DOUBLE PRECISION,
            position GEOMETRY(Point, 4326),
            PRIMARY KEY (id, code_departement)
        ) PARTITION BY LIST (code_departement);
    """)

    # Create 102 partitions (one per departement)
    for dept in DEPARTEMENTS:
        safe_name = dept.lower()
        op.execute(
            f"CREATE TABLE mutations_{safe_name} "
            f"PARTITION OF mutations FOR VALUES IN ('{dept}');"
        )

    # Default partition for unexpected codes
    op.execute(
        "CREATE TABLE mutations_default PARTITION OF mutations DEFAULT;"
    )

    # Spatial index on position (propagates to all partitions)
    op.execute(
        "CREATE INDEX idx_mutations_position "
        "ON mutations USING GIST (position);"
    )

    # B-tree index on commune code
    op.execute(
        "CREATE INDEX idx_mutations_code_commune "
        "ON mutations (code_commune);"
    )

    # B-tree index on mutation ID
    op.execute(
        "CREATE INDEX idx_mutations_id_mutation "
        "ON mutations (id_mutation);"
    )

    # B-tree index on mutation date
    op.execute(
        "CREATE INDEX idx_mutations_date_mutation "
        "ON mutations (date_mutation);"
    )

    # Composite index for temporal queries scoped by department
    op.execute(
        "CREATE INDEX idx_mutations_dept_date "
        "ON mutations (code_departement, date_mutation);"
    )


def downgrade() -> None:
    """Drop mutations table and all partitions."""
    op.execute("DROP TABLE IF EXISTS mutations CASCADE;")
