"""Add geography GIST indexes, nom_voie trigram index, enrich communes_lookup.

Revision ID: 037_geog_trgm_communes
Revises: 036_communes_lookup
Create Date: 2026-03-13

Three changes:

1. GIST geography indexes on equipements.geom and antennes.geom
   for ST_DWithin spatial queries (same pattern as migration 021).

2. GIN trigram index on adresses.nom_voie for fast street name
   matching in geocode (Strategy A/C: nom_voie % :street_part).

3. Enrich communes_lookup matview with code_commune column so
   geocode can map city name -> code_commune(s) for partition
   pruning instead of scanning all 102 partitions.

Notes:
- All tables are LIST-partitioned by dept (102 partitions).
- CREATE INDEX CONCURRENTLY is NOT supported on partitioned tables.
- Regular CREATE INDEX on partitioned parent cascades to all
  child partitions automatically (PostgreSQL 11+).
- The trigram index on adresses (26M rows, 102 partitions) takes
  5-15 minutes and blocks writes on adresses during creation.
  Run outside ETL windows.
- The pg_trgm extension must already be installed (used by geocode).
"""

from alembic import op

revision: str = "037_geog_trgm_communes"
down_revision: str = "036_communes_lookup"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # --- Section 1: Geography GIST indexes for spatial queries ---
    # equipements table -- 2.5M rows, partitioned by dept
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_equipements_geom_geog "
        "ON equipements USING GIST (geography(geom))"
    )
    # antennes table -- 250K rows, partitioned by dept
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_antennes_geom_geog "
        "ON antennes USING GIST (geography(geom))"
    )

    # --- Section 2: GIN trigram index on adresses.nom_voie ---
    # adresses table -- 26M rows, 102 partitions
    # NOT CONCURRENTLY -- partitioned tables don't support it
    # Enables fast trigram matching: nom_voie % :street_part
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_adresses_nom_voie_trgm "
        "ON adresses USING GIN (nom_voie gin_trgm_ops)"
    )

    # --- Section 3: Enrich communes_lookup with code_commune ---
    # Drop existing matview (from migration 036) and recreate with
    # code_commune included. Uses DISTINCT (nom_commune, code_commune)
    # to preserve all codes per city -- important for PLM cities
    # where Paris has 20 arrondissement codes for the same nom_commune.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS communes_lookup CASCADE")
    op.execute("""
        CREATE MATERIALIZED VIEW communes_lookup AS
        SELECT DISTINCT
            nom_commune,
            unaccent(lower(nom_commune)) AS nom_commune_norm,
            code_commune
        FROM adresses
        WHERE nom_commune IS NOT NULL
        WITH DATA
    """)
    # Index on nom_commune_norm for _detect_city lookups
    op.execute("""
        CREATE INDEX idx_communes_lookup_norm
        ON communes_lookup (nom_commune_norm)
    """)
    # Unique index for REFRESH CONCURRENTLY (must include code_commune now)
    op.execute("""
        CREATE UNIQUE INDEX idx_communes_lookup_uniq
        ON communes_lookup (nom_commune, code_commune)
    """)


def downgrade() -> None:
    # Drop new indexes
    op.execute("DROP INDEX IF EXISTS idx_equipements_geom_geog")
    op.execute("DROP INDEX IF EXISTS idx_antennes_geom_geog")
    op.execute("DROP INDEX IF EXISTS idx_adresses_nom_voie_trgm")

    # Restore communes_lookup without code_commune (036 state)
    op.execute("DROP MATERIALIZED VIEW IF EXISTS communes_lookup CASCADE")
    op.execute("""
        CREATE MATERIALIZED VIEW communes_lookup AS
        SELECT DISTINCT
            nom_commune,
            unaccent(lower(nom_commune)) AS nom_commune_norm
        FROM adresses
        WHERE nom_commune IS NOT NULL
        WITH DATA
    """)
    op.execute(
        "CREATE INDEX idx_communes_lookup_norm "
        "ON communes_lookup (nom_commune_norm)"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_communes_lookup_uniq "
        "ON communes_lookup (nom_commune)"
    )
    op.execute(
        "CREATE INDEX idx_communes_lookup_nom "
        "ON communes_lookup (nom_commune)"
    )
