"""Create arrets_transport and gares tables for transport proximity queries.

Revision ID: 012_transports
Revises: 011_scolaire
Create Date: 2026-02-10

Two non-partitioned tables:
- arrets_transport: consolidated GTFS stops (~500k rows after dedup)
  with GIST spatial index for ST_DWithin queries.
- gares: SNCF train stations (~3k rows) with GIST spatial index.

Data sources:
- transport.data.gouv.fr (GTFS consolidated stops)
- ressources.data.sncf.com (liste-des-gares)
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012_transports"
down_revision: str = "011_scolaire"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create arrets_transport + gares tables with spatial indexes."""
    # -- arrets_transport table --
    op.execute("""
        CREATE TABLE arrets_transport (
            id SERIAL PRIMARY KEY,
            stop_id TEXT,
            stop_name TEXT NOT NULL,
            position GEOMETRY(Point, 4326) NOT NULL,
            location_type INTEGER DEFAULT 0,
            agency_name TEXT
        );
    """)

    # GIST spatial index for ST_DWithin proximity queries
    op.execute(
        "CREATE INDEX idx_arrets_position "
        "ON arrets_transport USING GIST (position);"
    )

    # B-tree index on stop_name for text search
    op.execute(
        "CREATE INDEX idx_arrets_stop_name "
        "ON arrets_transport (stop_name);"
    )

    # -- gares table --
    op.execute("""
        CREATE TABLE gares (
            id SERIAL PRIMARY KEY,
            nom_gare TEXT NOT NULL,
            commune TEXT,
            code_insee TEXT,
            position GEOMETRY(Point, 4326) NOT NULL,
            code_uic TEXT,
            code_ligne TEXT,
            trigramme TEXT
        );
    """)

    # GIST spatial index for ST_DWithin proximity queries
    op.execute(
        "CREATE INDEX idx_gares_position "
        "ON gares USING GIST (position);"
    )

    # B-tree index on code_insee for commune lookup
    op.execute(
        "CREATE INDEX idx_gares_code_insee "
        "ON gares (code_insee);"
    )


def downgrade() -> None:
    """Drop arrets_transport and gares tables and all indexes."""
    op.execute("DROP TABLE IF EXISTS gares CASCADE;")
    op.execute("DROP TABLE IF EXISTS arrets_transport CASCADE;")
