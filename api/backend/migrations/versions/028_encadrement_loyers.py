"""Create encadrement_loyers table for rent control reference prices.

Revision ID: 028_encadrement_loyers
Revises: 027_fiscalite_locale
Create Date: 2026-03-12

Non-partitioned table -- ~18k rows (Paris quartiers x pieces x epoque
x type_location x annee). Spatial queries via PostGIS point column.

Data source: DRIHL / data.gouv.fr, ODbL.
"""

from alembic import op

revision: str = "028_encadrement_loyers"
down_revision: str = "027_fiscalite_locale"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create encadrement_loyers table with indexes."""
    op.execute("""
        CREATE TABLE encadrement_loyers (
            id SERIAL PRIMARY KEY,
            annee INTEGER NOT NULL,
            secteur INTEGER,
            numero_quartier INTEGER,
            nom_quartier TEXT,
            nb_pieces TEXT NOT NULL,
            epoque_construction TEXT NOT NULL,
            type_location TEXT NOT NULL,
            loyer_reference NUMERIC(6, 1),
            loyer_reference_majore NUMERIC(6, 1),
            loyer_reference_minore NUMERIC(6, 1),
            ville TEXT NOT NULL,
            code_insee_quartier TEXT,
            geom GEOMETRY(POINT, 4326),
            UNIQUE (
                annee, ville, numero_quartier,
                nb_pieces, epoque_construction, type_location
            )
        );
    """)

    op.execute(
        "CREATE INDEX idx_encadrement_ville "
        "ON encadrement_loyers (ville);"
    )

    op.execute(
        "CREATE INDEX idx_encadrement_annee "
        "ON encadrement_loyers (annee);"
    )

    op.execute(
        "CREATE INDEX idx_encadrement_geom "
        "ON encadrement_loyers USING GIST (geom);"
    )

    op.execute(
        "CREATE INDEX idx_encadrement_quartier "
        "ON encadrement_loyers (code_insee_quartier);"
    )


def downgrade() -> None:
    """Drop encadrement_loyers table."""
    op.execute("DROP TABLE IF EXISTS encadrement_loyers CASCADE;")
