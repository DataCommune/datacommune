"""Create diagnostics_dpe table for DPE energy diagnostics.

Revision ID: 006_dpe
Revises: 005_dvf
Create Date: 2026-02-10

Single (non-partitioned) table -- 14M rows queried by spatial proximity
or code_insee. Partitioning by department adds complexity without clear
benefit for the access patterns used.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006_dpe"
down_revision: str = "005_dvf"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create diagnostics_dpe table with spatial and categorical indexes."""
    op.execute("""
        CREATE TABLE diagnostics_dpe (
            id BIGSERIAL PRIMARY KEY,
            numero_dpe TEXT NOT NULL UNIQUE,
            code_departement VARCHAR(3),
            date_etablissement_dpe DATE,
            annee_construction INTEGER,
            type_batiment VARCHAR(50),
            surface_habitable NUMERIC(8, 2),
            etiquette_dpe CHAR(1),
            etiquette_ges CHAR(1),
            type_installation_chauffage VARCHAR(100),
            conso_5_usages_ep NUMERIC(10, 2),
            emission_ges_5_usages NUMERIC(10, 2),
            code_postal VARCHAR(5),
            code_insee VARCHAR(5),
            nom_commune TEXT,
            longitude DOUBLE PRECISION,
            latitude DOUBLE PRECISION,
            position GEOMETRY(Point, 4326)
        );
    """)

    # Spatial index on position for ST_DWithin queries
    op.execute(
        "CREATE INDEX idx_dpe_position "
        "ON diagnostics_dpe USING GIST (position);"
    )

    # B-tree indexes for common filter/lookup patterns
    op.execute(
        "CREATE INDEX idx_dpe_code_insee "
        "ON diagnostics_dpe (code_insee);"
    )

    op.execute(
        "CREATE INDEX idx_dpe_etiquette_dpe "
        "ON diagnostics_dpe (etiquette_dpe);"
    )

    op.execute(
        "CREATE INDEX idx_dpe_date_etablissement "
        "ON diagnostics_dpe (date_etablissement_dpe);"
    )


def downgrade() -> None:
    """Drop diagnostics_dpe table and all indexes."""
    op.execute("DROP TABLE IF EXISTS diagnostics_dpe CASCADE;")
