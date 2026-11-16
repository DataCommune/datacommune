"""Create carte_scolaire table for school district mapping.

Revision ID: 011_scolaire
Revises: 010_eligibilite
Create Date: 2026-02-10

Single (non-partitioned) table -- ~520k rows, address-segment-based
mapping of streets to sector colleges.  Lookup is by code_insee + voie
+ numero + parite.

Data source: Carte Scolaire des Colleges Publics, Ministere de l'Education
Nationale (data.gouv.fr).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011_scolaire"
down_revision: str = "010_eligibilite"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create carte_scolaire table with address and establishment indexes."""
    op.execute("""
        CREATE TABLE carte_scolaire (
            id SERIAL PRIMARY KEY,
            code_region TEXT,
            code_departement TEXT,
            code_postal TEXT,
            code_insee TEXT NOT NULL,
            libelle_commune TEXT,
            lieu_dit TEXT,
            type_et_libelle TEXT,
            n_de_voie_debut REAL,
            n_de_voie_fin REAL,
            parite TEXT,
            code_rne TEXT NOT NULL,
            secteur_unique TEXT
        );
    """)

    # B-tree index on code_insee (primary lookup key)
    op.execute(
        "CREATE INDEX idx_scolaire_code_insee "
        "ON carte_scolaire (code_insee);"
    )

    # Composite index on (code_insee, code_rne) for join optimization
    op.execute(
        "CREATE INDEX idx_scolaire_code_insee_rne "
        "ON carte_scolaire (code_insee, code_rne);"
    )


def downgrade() -> None:
    """Drop carte_scolaire table and all indexes."""
    op.execute("DROP TABLE IF EXISTS carte_scolaire CASCADE;")
