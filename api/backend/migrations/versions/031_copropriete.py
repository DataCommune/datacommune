"""Create copropriete table for RNIC registry data.

Revision ID: 031_copropriete
Revises: 030_demographie
Create Date: 2026-03-12

Non-partitioned table -- ~528k rows.
Copropriete registry with syndic, lots, financial data.

Data source: ANAH / RNIC, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "031_copropriete"
down_revision: str = "030_demographie"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create copropriete table with indexes."""
    op.execute("""
        CREATE TABLE copropriete (
            id SERIAL PRIMARY KEY,
            numero_immatriculation TEXT NOT NULL UNIQUE,
            nom_copropriete TEXT,
            adresse TEXT,
            code_commune VARCHAR(5),
            code_postal VARCHAR(5),
            nb_lots_total INTEGER,
            nb_lots_habitation INTEGER,
            nb_lots_commerciaux INTEGER,
            nb_lots_stationnement INTEGER,
            nom_syndic TEXT,
            type_syndic TEXT,
            periode_construction TEXT,
            annee_reglement INTEGER
        );
    """)

    op.execute(
        "CREATE INDEX idx_copropriete_code_commune "
        "ON copropriete (code_commune);"
    )

    op.execute(
        "CREATE INDEX idx_copropriete_code_postal "
        "ON copropriete (code_postal);"
    )


def downgrade() -> None:
    """Drop copropriete table."""
    op.execute("DROP TABLE IF EXISTS copropriete CASCADE;")
