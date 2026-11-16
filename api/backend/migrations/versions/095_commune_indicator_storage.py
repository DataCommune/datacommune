"""Persist immutable commune indicator decisions and synthetic results."""

from alembic import op

revision = "095_commune_indicator_storage"
down_revision = "094_publication_generations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE commune_indicator_manifests (
            id BIGSERIAL PRIMARY KEY,
            dataset_name TEXT NOT NULL CHECK (dataset_name IN ('dvf', 'dpe')),
            source_generation UUID NOT NULL
                REFERENCES etl_publications(generation),
            manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
            manifest_sha256 TEXT NOT NULL UNIQUE
                CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),
            qualified_at TIMESTAMPTZ NOT NULL,
            CHECK (manifest->>'dataset_name' IS NOT DISTINCT FROM dataset_name),
            CHECK (manifest->>'source_generation'
                   IS NOT DISTINCT FROM source_generation::text)
        );

        CREATE TABLE commune_indicator_result_builds (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            build_sha256 TEXT NOT NULL UNIQUE
                CHECK (build_sha256 ~ '^[a-f0-9]{64}$'),
            header JSONB NOT NULL CHECK (jsonb_typeof(header) = 'object'),
            inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE commune_indicator_result_cells (
            build_id BIGINT NOT NULL
                REFERENCES commune_indicator_result_builds(id),
            dataset_name TEXT NOT NULL CHECK (dataset_name IN ('dvf', 'dpe')),
            code_insee TEXT NOT NULL CHECK (code_insee ~ '^(44|85)[0-9]{3}$'),
            public_payload JSONB NOT NULL
                CHECK (jsonb_typeof(public_payload) = 'object'),
            private_binding JSONB NOT NULL
                CHECK (jsonb_typeof(private_binding) = 'object'),
            PRIMARY KEY (build_id, dataset_name, code_insee)
        );

        CREATE FUNCTION reject_indicator_rewrite() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'immutable_indicator_registry' USING ERRCODE = '55000';
        END
        $$;

        CREATE FUNCTION forbid_result_changes() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'immutable_results' USING ERRCODE = '55000';
        END
        $$;

        CREATE TRIGGER immutable_indicator_registry
            BEFORE UPDATE OR DELETE OR TRUNCATE ON commune_indicator_manifests
            FOR EACH STATEMENT EXECUTE FUNCTION reject_indicator_rewrite();
        CREATE TRIGGER immutable_results
            BEFORE UPDATE OR DELETE OR TRUNCATE
            ON commune_indicator_result_builds
            FOR EACH STATEMENT EXECUTE FUNCTION forbid_result_changes();
        CREATE TRIGGER immutable_results
            BEFORE UPDATE OR DELETE OR TRUNCATE
            ON commune_indicator_result_cells
            FOR EACH STATEMENT EXECUTE FUNCTION forbid_result_changes();
    """)


def downgrade() -> None:
    """Destroy only storage introduced by this revision (disposable DB only)."""
    op.execute("""
        DROP TABLE commune_indicator_result_cells;
        DROP TABLE commune_indicator_result_builds;
        DROP TABLE commune_indicator_manifests;
        DROP FUNCTION forbid_result_changes();
        DROP FUNCTION reject_indicator_rewrite();
    """)
