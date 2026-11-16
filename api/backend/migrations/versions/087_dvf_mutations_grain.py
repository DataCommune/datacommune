"""Add DVF source columns (D-02) and create mutations_grain + mutation_parcelles.

Revision ID: 087_dvf_mutations_grain
Revises: 086_enable_pg_stat_statements
Create Date: 2026-08-24

Phase 57 (DVF au grain mutation et attribution residentielle), R1 schema
foundation. Without this schema no reconstruction of the complete mutation is
possible -- the blocking invariant of the phase (reconstruct BEFORE any
filtering/classification) has nowhere to write its output. Three moves, in
dependency order:

1. D-02 / DVF-02 -- enrich ``mutations`` (the raw, per-line DVF table) with
   the 12 source columns the ETL currently throws away
   (``api/backend/app/etl_dvf/config.py`` COLUMNS_TO_KEEP), so the D-02 full
   re-run (2020-2026, depts 44+85) can populate them: ``numero_disposition``,
   ``nombre_lots``, ``lot1_numero``..``lot5_numero``,
   ``lot1_surface_carrez``..``lot5_surface_carrez``. ``numero_disposition``
   is kept as an AUDIT column only -- it never enters any price computation
   (RESEARCH Pattern 1: ``id_mutation`` alone is the pricing grain).
   ``nombre_lots`` varies PER LINE in the real geo-dvf file, not per
   disposition as the DGFiP notice describes for the raw file (RESEARCH
   Pitfall 3, verified on id_mutation 2024-532458) -- it is therefore never
   summed or maxed here or anywhere downstream (DVF-02: "sans deduire un
   nombre de lots depuis COUNT(*)"), only ever read as per-local metadata
   joined at the detail level (D-08). ``ADD COLUMN IF NOT EXISTS`` with no
   non-null DEFAULT does not rewrite the table on PostgreSQL 11+ (brief
   ACCESS EXCLUSIVE only) and propagates to all 102 partitions automatically
   -- same pattern already proven by migration 043 on this same table.

2. D-01 -- create ``mutations_grain``: ONE ROW PER MUTATION, fully qualified
   (value counted once, surfaces summed across the mutation's lines, a
   classification class in a column). Fiche and stats read ONLY this table
   once populated (D-03: ``mutations`` remains the raw per-line source,
   coexisting, never dropped). Partitioned ``LIST (code_departement)``, same
   scheme as ``mutations`` (migration 005) and the same 102-department +
   DEFAULT partition set, because every consumer of this table already
   filters or joins on ``code_departement`` the same way ``mutations`` does.

   Multi-commune mutations (rare, D-09): ``code_commune`` is the MIN
   ``code_commune`` across the mutation's lines, so the mutation is counted
   exactly once in that commune's stats; ``nb_communes`` makes the
   multi-commune case visible to any consumer instead of hiding it.
   ``position`` is the centroid of the mutation's lines' non-null points.

   Indexes -- one per access path served, no speculative index:
     - ``idx_mutations_grain_id_mutation`` on ``(id_mutation)``: the
       line-level detail join back to ``mutations`` (D-08).
     - ``idx_mutations_grain_code_commune`` on ``(code_commune)``: the
       ``v_commune_dvf`` matview (D-14) and ``/v1/zone`` by commune (D-25).
     - ``idx_mutations_grain_date`` on ``(date_mutation)``: the matview's
       12-month rolling window.
     - ``idx_mutations_grain_position`` GIST on ``(position)``: the KNN sort
       used by ``/v1/dvf`` (mirrors ``idx_mutations_position`` on
       ``mutations``, migration 005).
     - ``idx_mutations_grain_position_geog`` GIST on
       ``(geography(position))``: geography-cast ``ST_DWithin`` (mirrors
       ``idx_mutations_position_geog`` on ``mutations``, migration 021).

3. D-09 -- create ``mutation_parcelles``: the link table for mutations that
   span more than one cadastral parcel, so a multi-parcel mutation is
   representable without duplicating the mutation row itself. Flat,
   non-partitioned table (analogous to ``rnb_liens``, migration 079) --
   the expected row volume on the 44+85 instance does not warrant
   department partitioning. ``UNIQUE (id_mutation, id_parcelle)`` is the
   natural key; two access-path indexes:
     - ``idx_mutation_parcelles_id_parcelle`` on ``(id_parcelle)``: "all
       mutations touching this parcel".
     - ``idx_mutation_parcelles_id_mutation`` on ``(id_mutation)``: "all
       parcels of this mutation" (labels the wider-than-one-parcel case).

Both new tables are created EMPTY. Neither this migration nor any other
writes a row into them: the ETL (Phase 57 plan 06) populates
``mutations_grain`` and ``mutation_parcelles`` on its next run, after this
migration has shipped (ADR-0003 D1: supervised migrate-before-cutover, same
ordering discipline as 079_rnb_liens).
"""

from alembic import op

revision: str = "087_dvf_mutations_grain"
down_revision: str = "086_enable_pg_stat_statements"
branch_labels: str | None = None
depends_on: str | None = None

# All 102 departement codes (mirrors migration 005_dvf_mutations -- redefined
# locally on purpose, migrations never import from one another).
DEPARTEMENTS = [
    *[f"{i:02d}" for i in range(1, 20)],  # 01-19
    "2A",
    "2B",  # Corse
    *[str(i) for i in range(21, 96)],  # 21-95
    *[str(i) for i in range(971, 977)],  # DOM: 971-976
]


def upgrade() -> None:
    """Enrich mutations (D-02), create mutations_grain (D-01) and mutation_parcelles (D-09)."""
    # -- D-02: 12 source columns added to the existing partitioned `mutations`
    # table.
    op.execute(
        "ALTER TABLE mutations ADD COLUMN IF NOT EXISTS numero_disposition VARCHAR(6)"
    )
    op.execute("ALTER TABLE mutations ADD COLUMN IF NOT EXISTS nombre_lots SMALLINT")
    op.execute("ALTER TABLE mutations ADD COLUMN IF NOT EXISTS lot1_numero VARCHAR(20)")
    op.execute("ALTER TABLE mutations ADD COLUMN IF NOT EXISTS lot2_numero VARCHAR(20)")
    op.execute("ALTER TABLE mutations ADD COLUMN IF NOT EXISTS lot3_numero VARCHAR(20)")
    op.execute("ALTER TABLE mutations ADD COLUMN IF NOT EXISTS lot4_numero VARCHAR(20)")
    op.execute("ALTER TABLE mutations ADD COLUMN IF NOT EXISTS lot5_numero VARCHAR(20)")
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS lot1_surface_carrez NUMERIC(10, 2)"
    )
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS lot2_surface_carrez NUMERIC(10, 2)"
    )
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS lot3_surface_carrez NUMERIC(10, 2)"
    )
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS lot4_surface_carrez NUMERIC(10, 2)"
    )
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS lot5_surface_carrez NUMERIC(10, 2)"
    )

    # -- D-01: mutations_grain, partitioned LIST (code_departement) like
    # `mutations` itself. Created EMPTY -- the ETL (57-06) populates it.
    op.execute("""
        CREATE TABLE mutations_grain (
            id BIGSERIAL,
            code_departement VARCHAR(3) NOT NULL,
            id_mutation TEXT NOT NULL,
            date_mutation DATE NOT NULL,
            nature_mutation VARCHAR(50),
            valeur_fonciere NUMERIC(12, 2),
            classe_mutation VARCHAR(32) NOT NULL,
            nb_locaux_maison SMALLINT NOT NULL DEFAULT 0,
            nb_locaux_appartement SMALLINT NOT NULL DEFAULT 0,
            nb_locaux_commercial SMALLINT NOT NULL DEFAULT 0,
            nb_locaux_dependance SMALLINT NOT NULL DEFAULT 0,
            nb_lignes SMALLINT NOT NULL,
            nb_dispositions SMALLINT NOT NULL,
            nb_communes SMALLINT NOT NULL,
            nb_parcelles SMALLINT NOT NULL,
            surface_bati_totale INTEGER,
            surface_terrain_totale INTEGER,
            nombre_pieces_totales SMALLINT,
            mono_local BOOLEAN NOT NULL,
            est_vente BOOLEAN NOT NULL,
            hors_stats_vefa BOOLEAN NOT NULL DEFAULT false,
            valeur_divergente BOOLEAN NOT NULL DEFAULT false,
            code_commune VARCHAR(5),
            nom_commune TEXT,
            adresse_numero VARCHAR(10),
            adresse_nom_voie TEXT,
            code_postal VARCHAR(5),
            position GEOMETRY(Point, 4326),
            PRIMARY KEY (id, code_departement)
        ) PARTITION BY LIST (code_departement);
    """)

    for dept in DEPARTEMENTS:
        safe_name = dept.lower()
        op.execute(
            f"CREATE TABLE mutations_grain_{safe_name} "
            f"PARTITION OF mutations_grain FOR VALUES IN ('{dept}');"
        )

    op.execute(
        "CREATE TABLE mutations_grain_default PARTITION OF mutations_grain DEFAULT;"
    )

    op.execute(
        "CREATE INDEX idx_mutations_grain_id_mutation ON mutations_grain (id_mutation);"
    )
    op.execute(
        "CREATE INDEX idx_mutations_grain_code_commune "
        "ON mutations_grain (code_commune);"
    )
    op.execute(
        "CREATE INDEX idx_mutations_grain_date ON mutations_grain (date_mutation);"
    )
    op.execute(
        "CREATE INDEX idx_mutations_grain_position "
        "ON mutations_grain USING GIST (position);"
    )
    op.execute(
        "CREATE INDEX idx_mutations_grain_position_geog "
        "ON mutations_grain USING GIST (geography(position));"
    )

    # -- D-09: mutation_parcelles, flat non-partitioned link table (analogue
    # rnb_liens, migration 079). Created EMPTY -- the ETL (57-06) populates it.
    op.execute("""
        CREATE TABLE mutation_parcelles (
            id BIGSERIAL PRIMARY KEY,
            id_mutation TEXT NOT NULL,
            id_parcelle VARCHAR(14) NOT NULL,
            code_departement VARCHAR(3) NOT NULL,
            UNIQUE (id_mutation, id_parcelle)
        );
    """)

    op.execute(
        "CREATE INDEX idx_mutation_parcelles_id_parcelle "
        "ON mutation_parcelles (id_parcelle);"
    )
    op.execute(
        "CREATE INDEX idx_mutation_parcelles_id_mutation "
        "ON mutation_parcelles (id_mutation);"
    )


def downgrade() -> None:
    """Drop mutation_parcelles and mutations_grain, then the D-02 columns."""
    # Tables created last in upgrade() go first in downgrade().
    op.execute("DROP TABLE IF EXISTS mutation_parcelles CASCADE;")
    op.execute("DROP TABLE IF EXISTS mutations_grain CASCADE;")

    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot5_surface_carrez")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot4_surface_carrez")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot3_surface_carrez")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot2_surface_carrez")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot1_surface_carrez")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot5_numero")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot4_numero")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot3_numero")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot2_numero")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS lot1_numero")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS nombre_lots")
    op.execute("ALTER TABLE mutations DROP COLUMN IF EXISTS numero_disposition")
