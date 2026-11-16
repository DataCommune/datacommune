"""Create permis table for SITADEL building permits.

Revision ID: 032_permis
Revises: 031_copropriete
Create Date: 2026-03-12

Non-partitioned table -- ~500k rows (housing permits since 2017).
Building permit data from SITADEL/SDES.

Data source: SDES / Ministere, Licence Ouverte 2.0.
"""

from alembic import op

revision: str = "032_permis"
down_revision: str = "031_copropriete"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create permis table with indexes."""
    op.execute("""
        CREATE TABLE permis (
            id SERIAL PRIMARY KEY,
            numero_dossier TEXT,
            type_autorisation TEXT,
            date_depot DATE,
            date_decision DATE,
            code_commune VARCHAR(5),
            nom_commune TEXT,
            code_departement VARCHAR(3),
            nature_projet TEXT,
            nb_logements_crees INTEGER,
            surface_plancher NUMERIC(12, 2),
            type_construction TEXT,
            destination TEXT,
            annee INTEGER NOT NULL,
            UNIQUE (numero_dossier)
        );
    """)

    op.execute(
        "CREATE INDEX idx_permis_code_commune "
        "ON permis (code_commune);"
    )

    op.execute(
        "CREATE INDEX idx_permis_annee "
        "ON permis (annee);"
    )

    op.execute(
        "CREATE INDEX idx_permis_code_departement "
        "ON permis (code_departement);"
    )


def downgrade() -> None:
    """Drop permis table."""
    op.execute("DROP TABLE IF EXISTS permis CASCADE;")
