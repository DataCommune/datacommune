"""Create indicateurs_loyers table for rental price indicators.

Revision ID: 007_loyers
Revises: 006_dpe
Create Date: 2026-02-10

Single (non-partitioned) table -- only ~35k rows, no benefit from
partitioning. Data keyed by commune code (code_insee), resolved via
GPS -> parcelles -> commune subquery (no spatial column needed).

Data source: Estimations ANIL, a partir des donnees du Groupe SeLoger
et de leboncoin. Published by Ministere de la Transition Ecologique.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_loyers"
down_revision: str = "006_dpe"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create indicateurs_loyers table with unique constraint and indexes."""
    op.execute("""
        CREATE TABLE indicateurs_loyers (
            id SERIAL PRIMARY KEY,
            code_insee VARCHAR(5) NOT NULL,
            nom_commune TEXT,
            code_departement VARCHAR(3),
            code_epci VARCHAR(9),
            code_region VARCHAR(2),
            type_habitat VARCHAR(20) NOT NULL,
            loyer_m2 NUMERIC(6, 2),
            loyer_m2_bas NUMERIC(6, 2),
            loyer_m2_haut NUMERIC(6, 2),
            r2 NUMERIC(4, 3),
            nb_observations INTEGER,
            annee INTEGER NOT NULL,
            UNIQUE (code_insee, type_habitat, annee)
        );
    """)

    # B-tree index on code_insee for commune-level lookups
    op.execute(
        "CREATE INDEX idx_loyers_code_insee "
        "ON indicateurs_loyers (code_insee);"
    )

    # B-tree index on code_departement for department-level aggregation
    op.execute(
        "CREATE INDEX idx_loyers_code_departement "
        "ON indicateurs_loyers (code_departement);"
    )


def downgrade() -> None:
    """Drop indicateurs_loyers table and all indexes."""
    op.execute("DROP TABLE IF EXISTS indicateurs_loyers CASCADE;")
