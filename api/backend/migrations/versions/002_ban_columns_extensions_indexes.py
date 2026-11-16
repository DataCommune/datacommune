"""Add BAN columns, pg_trgm/unaccent extensions, FTS and trigram indexes.

Revision ID: 002_ban
Revises: 001_initial
Create Date: 2026-02-09
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_ban"
down_revision: str = "001_initial"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add search infrastructure: extensions, columns, and indexes."""
    # -- Extensions (idempotent) --
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")

    # -- BAN interoperability column --
    op.execute("ALTER TABLE adresses ADD COLUMN ban_id TEXT;")

    # -- Generated search column (propagates to all partitions automatically) --
    op.execute("""
        ALTER TABLE adresses ADD COLUMN search_text TEXT
        GENERATED ALWAYS AS (
            COALESCE(numero, '') || ' ' ||
            COALESCE(nom_voie, '') || ' ' ||
            COALESCE(code_postal, '') || ' ' ||
            COALESCE(nom_commune, '')
        ) STORED;
    """)

    # -- Indexes (created on parent, propagated to partitions) --
    # Exact lookup by BAN ID
    op.execute(
        "CREATE INDEX idx_adresses_ban_id ON adresses (ban_id);"
    )
    # Trigram index for fuzzy search
    op.execute(
        "CREATE INDEX idx_adresses_search_trgm "
        "ON adresses USING GIN (search_text gin_trgm_ops);"
    )
    # Full-text search index (French dictionary)
    op.execute(
        "CREATE INDEX idx_adresses_search_fts "
        "ON adresses USING GIN (to_tsvector('french', search_text));"
    )
    # Exact lookup by code_postal
    op.execute(
        "CREATE INDEX idx_adresses_code_postal ON adresses (code_postal);"
    )
    # Exact lookup by code_commune
    op.execute(
        "CREATE INDEX idx_adresses_code_commune ON adresses (code_commune);"
    )


def downgrade() -> None:
    """Remove BAN columns, indexes, and extensions."""
    # -- Drop indexes (IF EXISTS for safety) --
    op.execute("DROP INDEX IF EXISTS idx_adresses_code_commune;")
    op.execute("DROP INDEX IF EXISTS idx_adresses_code_postal;")
    op.execute("DROP INDEX IF EXISTS idx_adresses_search_fts;")
    op.execute("DROP INDEX IF EXISTS idx_adresses_search_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_adresses_ban_id;")

    # -- Drop columns --
    op.execute("ALTER TABLE adresses DROP COLUMN IF EXISTS search_text;")
    op.execute("ALTER TABLE adresses DROP COLUMN IF EXISTS ban_id;")

    # -- Drop extensions --
    op.execute("DROP EXTENSION IF EXISTS unaccent CASCADE;")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm CASCADE;")
