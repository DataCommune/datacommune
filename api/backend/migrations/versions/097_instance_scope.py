"""Persist the hosted instance scope and record it on every ETL run (D-21).

Two tables hold the scope: one row per hosted catalogue dataset, one row per
hosted departement. A fresh database hosts nothing: no seed rows, imports fail
explicitly until an operator writes the scope. ``etl_runs`` gains the scope
read at run start and the fingerprint of every downloaded source file.
"""

from alembic import op

revision = "097_instance_scope"
down_revision = "096_commune_indicator_build_activation"
branch_labels = None
depends_on = None

# Same generation as migration 001 (and app.etl.base.DEPARTEMENTS): migrations
# never import application code, so the list is frozen here with the schema.
DEPARTEMENTS = [
    *[f"{i:02d}" for i in range(1, 20)],  # 01-19
    "2A",
    "2B",  # Corse
    *[str(i) for i in range(21, 96)],  # 21-95
    *[str(i) for i in range(971, 977)],  # DOM: 971-976
]


def upgrade() -> None:
    codes = ", ".join(f"'{code}'" for code in DEPARTEMENTS)
    op.execute(f"""
        CREATE TABLE instance_scope_datasets (
            dataset TEXT PRIMARY KEY CHECK (dataset ~ '^[a-z_]+$'),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE instance_scope_departements (
            code_departement TEXT PRIMARY KEY
                CHECK (code_departement IN ({codes})),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE FUNCTION dc_departement_from_insee(code TEXT) RETURNS TEXT
            LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
            AS $$
                SELECT CASE WHEN left(code, 2) = '97'
                            THEN left(code, 3)
                            ELSE left(code, 2) END
            $$;
        ALTER TABLE etl_runs
            ADD COLUMN scope_snapshot JSONB,
            ADD COLUMN source_files JSONB;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE etl_runs
            DROP COLUMN source_files,
            DROP COLUMN scope_snapshot;
        DROP FUNCTION dc_departement_from_insee(TEXT);
        DROP TABLE instance_scope_departements;
        DROP TABLE instance_scope_datasets;
    """)
