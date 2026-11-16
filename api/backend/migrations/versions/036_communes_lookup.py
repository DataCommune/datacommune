"""Create communes_lookup materialized view for fast city detection.

Revision ID: 036_communes_lookup
Revises: 035_vacance
Create Date: 2026-03-13

The geocoder city detection queries 26M adresses rows to match
nom_commune via unaccent(lower()). This matview pre-computes
~35K distinct commune names for sub-millisecond lookups.
"""

from alembic import op

revision: str = "036_communes_lookup"
down_revision: str = "035_vacance"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "DROP MATERIALIZED VIEW IF EXISTS communes_lookup CASCADE"
    )
    op.execute("""
        CREATE MATERIALIZED VIEW communes_lookup AS
        SELECT DISTINCT
            nom_commune,
            unaccent(lower(nom_commune)) AS nom_commune_norm
        FROM adresses
        WHERE nom_commune IS NOT NULL
        WITH DATA
    """)
    op.execute("""
        CREATE INDEX idx_communes_lookup_norm
        ON communes_lookup (nom_commune_norm)
    """)
    # Unique index on nom_commune for REFRESH CONCURRENTLY
    op.execute("""
        CREATE UNIQUE INDEX idx_communes_lookup_uniq
        ON communes_lookup (nom_commune)
    """)
    op.execute("""
        CREATE INDEX idx_communes_lookup_nom
        ON communes_lookup (nom_commune)
    """)


def downgrade() -> None:
    op.execute(
        "DROP MATERIALIZED VIEW IF EXISTS communes_lookup CASCADE"
    )
