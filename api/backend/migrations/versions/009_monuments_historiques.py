"""Create monuments_historiques table for protected heritage sites.

Revision ID: 009_monuments
Revises: 008_batiments
Create Date: 2026-02-10

Single (non-partitioned) table -- only ~45k rows, no benefit from
partitioning. Data sourced from data.culture.gouv.fr (Merimee base).
Point geometry built from coordonnees_finales lat/lon field.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009_monuments"
down_revision: str = "008_batiments"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create monuments_historiques table with spatial and lookup indexes."""
    op.execute("""
        CREATE TABLE monuments_historiques (
            id SERIAL PRIMARY KEY,
            reference TEXT NOT NULL,
            denomination TEXT,
            adresse TEXT,
            commune TEXT,
            code_insee VARCHAR(5),
            code_departement VARCHAR(3),
            protection TEXT,
            date_protection TEXT,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            position GEOMETRY(Point, 4326)
        );
    """)

    # Spatial index on position for ST_DWithin queries
    op.execute(
        "CREATE INDEX idx_monuments_position "
        "ON monuments_historiques USING GIST (position);"
    )

    # B-tree indexes for common lookup patterns
    op.execute(
        "CREATE INDEX idx_monuments_code_insee "
        "ON monuments_historiques (code_insee);"
    )

    op.execute(
        "CREATE INDEX idx_monuments_reference "
        "ON monuments_historiques (reference);"
    )


def downgrade() -> None:
    """Drop monuments_historiques table and all indexes."""
    op.execute("DROP TABLE IF EXISTS monuments_historiques CASCADE;")
