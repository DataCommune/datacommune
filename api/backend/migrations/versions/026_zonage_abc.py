"""Create zonage_abc table for housing tension zones.

Revision ID: 026_zonage_abc
Revises: 025_saved_searches
Create Date: 2026-03-12

Single non-partitioned table -- only ~35k rows (one per commune).
Classifies communes into zones Abis, A, B1, B2, C per decree.

Data source: Ministere de la Transition Ecologique, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "026_zonage_abc"
down_revision: str = "025_saved_searches"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create zonage_abc table with unique constraint and indexes."""
    op.execute("""
        CREATE TABLE zonage_abc (
            id SERIAL PRIMARY KEY,
            code_insee VARCHAR(5) NOT NULL,
            nom_commune TEXT,
            code_departement VARCHAR(3),
            zone VARCHAR(4) NOT NULL,
            reclassement BOOLEAN DEFAULT FALSE,
            date_arrete DATE,
            UNIQUE (code_insee)
        );
    """)

    op.execute(
        "CREATE INDEX idx_zonage_abc_code_insee "
        "ON zonage_abc (code_insee);"
    )

    op.execute(
        "CREATE INDEX idx_zonage_abc_zone "
        "ON zonage_abc (zone);"
    )

    op.execute(
        "CREATE INDEX idx_zonage_abc_code_departement "
        "ON zonage_abc (code_departement);"
    )


def downgrade() -> None:
    """Drop zonage_abc table and all indexes."""
    op.execute("DROP TABLE IF EXISTS zonage_abc CASCADE;")
