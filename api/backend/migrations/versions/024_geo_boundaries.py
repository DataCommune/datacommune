"""Create geo_departements, geo_communes, geo_sections tables for vector tiles.

PostGIS geometry tables used by Martin tile server to serve MVT vector tiles.
Departements (101 rows), communes (~35K), sections (~600K).

Revision ID: 024_geo_boundaries
Revises: 023_billing
Create Date: 2026-03-03
"""

from alembic import op

revision: str = "024_geo_boundaries"
down_revision: str = "023_billing"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create geo boundary tables with GIST indexes."""
    op.execute("""
        CREATE TABLE geo_departements (
            code VARCHAR(3) PRIMARY KEY,
            nom VARCHAR(100) NOT NULL,
            geometry GEOMETRY(MultiPolygon, 4326) NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX idx_geo_dept_geom ON geo_departements USING GIST (geometry)"
    )

    op.execute("""
        CREATE TABLE geo_communes (
            code VARCHAR(5) PRIMARY KEY,
            nom VARCHAR(200) NOT NULL,
            departement_code VARCHAR(3) NOT NULL,
            population INTEGER,
            geometry GEOMETRY(MultiPolygon, 4326) NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX idx_geo_comm_geom ON geo_communes USING GIST (geometry)"
    )
    op.execute(
        "CREATE INDEX idx_geo_comm_dept ON geo_communes (departement_code)"
    )

    op.execute("""
        CREATE TABLE geo_sections (
            id VARCHAR(20) PRIMARY KEY,
            code VARCHAR(10) NOT NULL,
            commune_code VARCHAR(5) NOT NULL,
            departement_code VARCHAR(3) NOT NULL,
            geometry GEOMETRY(MultiPolygon, 4326) NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX idx_geo_sect_geom ON geo_sections USING GIST (geometry)"
    )
    op.execute(
        "CREATE INDEX idx_geo_sect_commune ON geo_sections (commune_code)"
    )


def downgrade() -> None:
    """Drop geo boundary tables."""
    op.execute("DROP TABLE IF EXISTS geo_sections")
    op.execute("DROP TABLE IF EXISTS geo_communes")
    op.execute("DROP TABLE IF EXISTS geo_departements")
