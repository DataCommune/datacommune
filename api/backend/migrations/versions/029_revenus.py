"""Create revenus table for Filosofi household income data.

Revision ID: 029_revenus
Revises: 028_encadrement_loyers
Create Date: 2026-03-12

Non-partitioned table -- ~35k rows (one per commune per year).
Median income, poverty rates, income quartiles per commune.

Data source: INSEE Filosofi, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "029_revenus"
down_revision: str = "028_encadrement_loyers"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create revenus table with indexes."""
    op.execute("""
        CREATE TABLE revenus (
            id SERIAL PRIMARY KEY,
            code_insee VARCHAR(5) NOT NULL,
            nom_commune TEXT,
            code_departement VARCHAR(3),
            nom_departement TEXT,
            nom_region TEXT,
            nb_menages_fiscaux INTEGER,
            revenu_median INTEGER,
            revenu_q1 INTEGER,
            revenu_q3 INTEGER,
            revenu_d1 INTEGER,
            revenu_d9 INTEGER,
            indice_gini NUMERIC(5, 4),
            taux_pauvrete NUMERIC(5, 2),
            part_imposee NUMERIC(5, 2),
            annee INTEGER NOT NULL,
            UNIQUE (code_insee, annee)
        );
    """)

    op.execute(
        "CREATE INDEX idx_revenus_code_insee "
        "ON revenus (code_insee);"
    )

    op.execute(
        "CREATE INDEX idx_revenus_code_departement "
        "ON revenus (code_departement);"
    )


def downgrade() -> None:
    """Drop revenus table."""
    op.execute("DROP TABLE IF EXISTS revenus CASCADE;")
