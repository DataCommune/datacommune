"""Create partitioned batiments table for BD TOPO building data.

Revision ID: 008_batiments
Revises: 007_loyers
Create Date: 2026-02-10

Partitioned by department (102 partitions + default) for BD TOPO
batiment layer. Columns cover height, floors, usage, and footprint
geometry as MultiPolygon (buildings can have complex shapes).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008_batiments"
down_revision: str = "007_loyers"
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
    """Create partitioned batiments table with spatial and attribute indexes."""
    # Create partitioned parent table
    op.execute("""
        CREATE TABLE batiments (
            id BIGSERIAL,
            code_departement VARCHAR(3) NOT NULL,
            cleabs TEXT NOT NULL,
            nature VARCHAR(50),
            usage_1 VARCHAR(50),
            nombre_d_etages INTEGER,
            hauteur NUMERIC(6, 2),
            altitude_minimale_sol NUMERIC(7, 2),
            altitude_maximale_toit NUMERIC(7, 2),
            geometrie GEOMETRY(MultiPolygon, 4326),
            PRIMARY KEY (id, code_departement)
        ) PARTITION BY LIST (code_departement);
    """)

    # Create 102 partitions (one per departement)
    for dept in DEPARTEMENTS:
        safe_name = dept.lower()
        op.execute(
            f"CREATE TABLE batiments_{safe_name} "
            f"PARTITION OF batiments FOR VALUES IN ('{dept}');"
        )

    # Default partition for unexpected codes
    op.execute(
        "CREATE TABLE batiments_default PARTITION OF batiments DEFAULT;"
    )

    # Spatial index on geometrie (propagates to all partitions)
    op.execute(
        "CREATE INDEX idx_batiments_geometrie "
        "ON batiments USING GIST (geometrie);"
    )

    # B-tree index on cleabs (BD TOPO unique identifier)
    op.execute(
        "CREATE INDEX idx_batiments_cleabs "
        "ON batiments (cleabs);"
    )

    # B-tree index on code_departement
    op.execute(
        "CREATE INDEX idx_batiments_code_departement "
        "ON batiments (code_departement);"
    )


def downgrade() -> None:
    """Drop batiments table and all partitions."""
    op.execute("DROP TABLE IF EXISTS batiments CASCADE;")
