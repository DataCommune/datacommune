"""Record future publication attempts without inventing legacy generations."""

from alembic import op

revision = "094_publication_generations"
down_revision = "093_permis_source_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE etl_publications (
            id BIGSERIAL PRIMARY KEY,
            generation UUID NOT NULL UNIQUE,
            dataset_name TEXT NOT NULL,
            table_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'failed')),
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            relation_oids JSONB NOT NULL DEFAULT '{}'::jsonb,
            row_count BIGINT CHECK (row_count >= 0),
            failure_type TEXT
        )
    """)
    op.execute(
        "CREATE INDEX ix_etl_publications_latest ON etl_publications(dataset_name, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE etl_publications")
