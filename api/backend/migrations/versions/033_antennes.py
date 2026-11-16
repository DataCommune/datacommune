"""Create antennes table for ANFR mobile network antennas.

Revision ID: 033_antennes
Revises: 032_permis
Create Date: 2026-03-12

Non-partitioned table -- ~250k rows (antenna systems: tech x band x site).
Mobile network infrastructure: 2G/3G/4G/5G antennas.

Data source: ANFR, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "033_antennes"
down_revision: str = "032_permis"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create antennes table with indexes."""
    op.execute("""
        CREATE TABLE antennes (
            id SERIAL PRIMARY KEY,
            operateur TEXT,
            generation TEXT,
            bande_frequence TEXT,
            numero_station TEXT,
            numero_support TEXT,
            code_insee VARCHAR(5),
            adresse TEXT,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            date_mise_en_service DATE,
            statut TEXT,
            geom GEOMETRY(POINT, 4326)
        );
    """)

    op.execute(
        "CREATE INDEX idx_antennes_code_insee "
        "ON antennes (code_insee);"
    )

    op.execute(
        "CREATE INDEX idx_antennes_generation "
        "ON antennes (generation);"
    )

    op.execute(
        "CREATE INDEX idx_antennes_operateur "
        "ON antennes (operateur);"
    )

    op.execute(
        "CREATE INDEX idx_antennes_geom "
        "ON antennes USING GIST (geom);"
    )


def downgrade() -> None:
    """Drop antennes table."""
    op.execute("DROP TABLE IF EXISTS antennes CASCADE;")
