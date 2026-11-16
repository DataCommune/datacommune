"""Add geography functional GIST indexes for ST_DWithin performance.

Revision ID: 021_geog_indexes
Revises: 020_scoring_filter
Create Date: 2026-02-24

Spatial queries using ST_DWithin on geometry(Point,4326) columns require a
sequential scan across all 102 partitions because ST_DWithin casts geometry
to geography internally.  Functional GIST indexes on geography(column)
allow the planner to use an index scan instead.

Note: All 4 tables are LIST-partitioned (102 dept partitions).
CREATE INDEX CONCURRENTLY is not supported on partitioned tables.
Regular CREATE INDEX on a partitioned parent cascades to all child
partitions automatically (PostgreSQL 11+).
"""

from alembic import op

revision = "021_geog_indexes"
down_revision = "020_scoring_filter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All 4 tables are partitioned — CONCURRENTLY is not supported.
    # Regular CREATE INDEX on partitioned parent cascades to all partitions.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_adresses_position_geog "
        "ON adresses USING GIST (geography(position))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dpe_position_geog "
        "ON diagnostics_dpe USING GIST (geography(position))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mutations_position_geog "
        "ON mutations USING GIST (geography(position))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_batiments_geometrie_geog "
        "ON batiments USING GIST (geography(geometrie))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_adresses_position_geog")
    op.execute("DROP INDEX IF EXISTS idx_dpe_position_geog")
    op.execute("DROP INDEX IF EXISTS idx_mutations_position_geog")
    op.execute("DROP INDEX IF EXISTS idx_batiments_geometrie_geog")
