"""Create vacance table for LOVAC vacant housing data.

Revision ID: 035_vacance
Revises: 034_equipements
Create Date: 2026-03-12

Non-partitioned table -- ~35k rows (one per commune per year).
Vacant housing rates from CEREMA LOVAC open data.

Data source: CEREMA LOVAC, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "035_vacance"
down_revision: str = "034_equipements"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create vacance table with indexes."""
    op.execute("""
        CREATE TABLE vacance (
            id SERIAL PRIMARY KEY,
            code_commune VARCHAR(5) NOT NULL,
            nom_commune TEXT,
            code_departement VARCHAR(3),
            nom_departement TEXT,
            nom_region TEXT,
            nb_logements_prives INTEGER,
            nb_logements_vacants INTEGER,
            nb_vacants_moins_2ans INTEGER,
            nb_vacants_plus_2ans INTEGER,
            taux_vacance NUMERIC(5, 2),
            taux_vacance_moins_2ans NUMERIC(5, 2),
            taux_vacance_plus_2ans NUMERIC(5, 2),
            annee INTEGER NOT NULL,
            UNIQUE (code_commune, annee)
        );
    """)

    op.execute(
        "CREATE INDEX idx_vacance_code_commune "
        "ON vacance (code_commune);"
    )

    op.execute(
        "CREATE INDEX idx_vacance_code_departement "
        "ON vacance (code_departement);"
    )


def downgrade() -> None:
    """Drop vacance table."""
    op.execute("DROP TABLE IF EXISTS vacance CASCADE;")
