"""Create fiscalite_locale table for local tax rates per commune.

Revision ID: 027_fiscalite_locale
Revises: 026_zonage_abc
Create Date: 2026-03-12

Single non-partitioned table -- ~35k rows (one per commune per year).
Key tax rates extracted from the REI (Recensement des Elements
d'Imposition) published by DGFiP.

Data source: DGFiP, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "027_fiscalite_locale"
down_revision: str = "026_zonage_abc"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create fiscalite_locale table with indexes."""
    op.execute("""
        CREATE TABLE fiscalite_locale (
            id SERIAL PRIMARY KEY,
            code_insee VARCHAR(5) NOT NULL,
            nom_commune TEXT,
            code_departement VARCHAR(3),
            nom_departement TEXT,
            nom_region TEXT,
            nom_epci TEXT,
            taux_tfpb NUMERIC(6, 5),
            taux_tfpnb NUMERIC(6, 5),
            taux_th_rs NUMERIC(6, 5),
            taux_teom NUMERIC(6, 5),
            base_tfpb BIGINT,
            produit_tfpb BIGINT,
            annee INTEGER NOT NULL,
            UNIQUE (code_insee, annee)
        );
    """)

    op.execute(
        "CREATE INDEX idx_fiscalite_code_insee "
        "ON fiscalite_locale (code_insee);"
    )

    op.execute(
        "CREATE INDEX idx_fiscalite_code_departement "
        "ON fiscalite_locale (code_departement);"
    )


def downgrade() -> None:
    """Drop fiscalite_locale table."""
    op.execute("DROP TABLE IF EXISTS fiscalite_locale CASCADE;")
