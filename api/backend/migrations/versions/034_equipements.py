"""Create equipements table for INSEE BPE facilities data.

Revision ID: 034_equipements
Revises: 033_antennes
Create Date: 2026-03-12

Non-partitioned table -- ~2.5M rows.
Geolocalized permanent facilities (commerce, health, education, sports).

Data source: INSEE BPE, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "034_equipements"
down_revision: str = "033_antennes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create equipements table with indexes."""
    op.execute("""
        CREATE TABLE equipements (
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
        );
    """)

    op.execute(
        "CREATE INDEX idx_equipements_code_commune "
        "ON equipements (code_commune);"
    )

    op.execute(
        "CREATE INDEX idx_equipements_type "
        "ON equipements (type_equipement);"
    )

    op.execute(
        "CREATE INDEX idx_equipements_domaine "
        "ON equipements (domaine);"
    )

    op.execute(
        "CREATE INDEX idx_equipements_geom "
        "ON equipements USING GIST (geom);"
    )

    op.execute(
        "CREATE INDEX idx_equipements_dept "
        "ON equipements (code_departement);"
    )


def downgrade() -> None:
    """Drop equipements table."""
    op.execute("DROP TABLE IF EXISTS equipements CASCADE;")
