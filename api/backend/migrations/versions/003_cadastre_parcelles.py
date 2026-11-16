"""Create partitioned parcelles table for cadastral data.

Revision ID: 003_cadastre
Revises: 002_ban
Create Date: 2026-02-09
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_cadastre"
down_revision: str = "002_ban"
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
    """Create partitioned parcelles table with spatial and B-tree indexes."""
    # Create partitioned parent table
    op.execute("""
        CREATE TABLE parcelles (
            id BIGSERIAL,
            code_departement VARCHAR(3) NOT NULL,
            id_parcelle VARCHAR(14) NOT NULL,
            commune VARCHAR(5),
            prefixe VARCHAR(3),
            section VARCHAR(2),
            numero VARCHAR(4),
            contenance INTEGER,
            arpente BOOLEAN DEFAULT FALSE,
            created DATE,
            updated DATE,
            geometrie GEOMETRY(MultiPolygon, 4326),
            PRIMARY KEY (id, code_departement)
        ) PARTITION BY LIST (code_departement);
    """)

    # Create 102 partitions (one per departement)
    for dept in DEPARTEMENTS:
        safe_name = dept.lower()
        op.execute(
            f"CREATE TABLE parcelles_{safe_name} "
            f"PARTITION OF parcelles FOR VALUES IN ('{dept}');"
        )

    # Default partition for unexpected codes
    op.execute(
        "CREATE TABLE parcelles_default PARTITION OF parcelles DEFAULT;"
    )

    # Spatial index on geometry (propagates to all partitions)
    op.execute(
        "CREATE INDEX idx_parcelles_geometrie "
        "ON parcelles USING GIST (geometrie);"
    )

    # B-tree index on cadastral reference
    op.execute(
        "CREATE INDEX idx_parcelles_id_parcelle "
        "ON parcelles (id_parcelle);"
    )

    # B-tree index on commune code
    op.execute(
        "CREATE INDEX idx_parcelles_commune "
        "ON parcelles (commune);"
    )


def downgrade() -> None:
    """Drop parcelles table and all partitions."""
    op.execute("DROP TABLE IF EXISTS parcelles CASCADE;")
