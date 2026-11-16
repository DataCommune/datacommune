"""Create demographie table for INSEE census population data.

Revision ID: 030_demographie
Revises: 029_revenus
Create Date: 2026-03-12

Non-partitioned table -- ~35k rows (one per commune).
Population, households, housing counts from the census.

Data source: INSEE Recensement, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "030_demographie"
down_revision: str = "029_revenus"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create demographie table with indexes."""
    op.execute("""
        CREATE TABLE demographie (
            id SERIAL PRIMARY KEY,
            code_insee VARCHAR(5) NOT NULL,
            nom_commune TEXT,
            code_departement VARCHAR(3),
            code_region VARCHAR(3),
            population INTEGER,
            population_hommes INTEGER,
            population_femmes INTEGER,
            nb_menages INTEGER,
            nb_logements INTEGER,
            nb_residences_principales INTEGER,
            nb_logements_vacants INTEGER,
            nb_residences_secondaires INTEGER,
            superficie NUMERIC(10, 2),
            densite NUMERIC(10, 2),
            annee INTEGER NOT NULL,
            UNIQUE (code_insee, annee)
        );
    """)

    op.execute(
        "CREATE INDEX idx_demographie_code_insee "
        "ON demographie (code_insee);"
    )

    op.execute(
        "CREATE INDEX idx_demographie_code_departement "
        "ON demographie (code_departement);"
    )


def downgrade() -> None:
    """Drop demographie table."""
    op.execute("DROP TABLE IF EXISTS demographie CASCADE;")
