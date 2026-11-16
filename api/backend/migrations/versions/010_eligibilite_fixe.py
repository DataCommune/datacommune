"""Create eligibilite_fixe table for Arcep broadband eligibility data.

Revision ID: 010_eligibilite
Revises: 009_monuments
Create Date: 2026-02-10

Single (non-partitioned) table for fibre/ADSL/THD eligibility per building.
Data sourced from data.arcep.fr Ma Connexion Internet (base_imb + actuel join).
Lookup is by code_insee + voie (no GPS coordinates in source data).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010_eligibilite"
down_revision: str = "009_monuments"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create eligibilite_fixe table with address and technology indexes."""
    op.execute("""
        CREATE TABLE eligibilite_fixe (
            id SERIAL PRIMARY KEY,
            id_imb TEXT NOT NULL,
            code_insee TEXT NOT NULL,
            voie TEXT,
            numero TEXT,
            complement TEXT,
            technologie TEXT NOT NULL,
            debit_max REAL,
            operateur TEXT,
            code_departement TEXT NOT NULL
        );
    """)

    # B-tree index on code_insee for commune-level lookup
    op.execute(
        "CREATE INDEX idx_eligibilite_code_insee "
        "ON eligibilite_fixe (code_insee);"
    )

    # Composite index on (code_insee, voie) for address lookup
    op.execute(
        "CREATE INDEX idx_eligibilite_code_insee_voie "
        "ON eligibilite_fixe (code_insee, voie);"
    )

    # B-tree index on id_imb for join operations
    op.execute(
        "CREATE INDEX idx_eligibilite_id_imb "
        "ON eligibilite_fixe (id_imb);"
    )


def downgrade() -> None:
    """Drop eligibilite_fixe table and all indexes."""
    op.execute("DROP TABLE IF EXISTS eligibilite_fixe CASCADE;")
