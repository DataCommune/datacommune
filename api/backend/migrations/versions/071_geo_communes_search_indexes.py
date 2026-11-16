"""Index the commune-search expression on geo_communes.

Revision ID: 071_geo_communes_search_indexes
Revises: 070_dpe_energy_cost_fields
Create Date: 2026-07-04

DETTE #5 — search_communes / list_communes (zone/queries.py) filter geo_communes
(~35K rows) on the normalized name expression

    regexp_replace(immutable_unaccent(lower(nom)), '[-\\s]+', ' ', 'g')

with no index, so every call seq-scans all rows and recomputes the regexp +
unaccent per row.

Two obstacles handled here:

1. ``unaccent`` is only STABLE, so an index cannot reference it directly. We add
   an IMMUTABLE, schema-qualified wrapper ``public.immutable_unaccent`` (2-arg
   form, dictionary pinned so it is safe to assert immutability) and switch the
   queries to call it — the query expression must match the index expression
   byte-for-byte for the planner to use it.

2. Two LIKE shapes hit the same expression:
   - search_communes: prefix ``LIKE :q || '%'`` → B-tree with text_pattern_ops.
   - list_communes:   substring ``LIKE '%' || :q || '%'`` → trigram GIN.

Both indexes are built on the normalized *expression*, not on raw ``nom``: the
queries never filter on the bare column, so an index on ``nom`` alone would be
dead weight the planner would ignore. pg_trgm and unaccent are already installed
(migrations 037 / 036).
"""

from alembic import op

revision = "071_geo_communes_search_indexes"
down_revision = "070_dpe_energy_cost_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IMMUTABLE unaccent wrapper: the 2-arg form pins the dictionary so the
    # result no longer depends on search_path, letting us declare it IMMUTABLE
    # and reference it from a functional index.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.immutable_unaccent(text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        STRICT
        AS $$
            SELECT public.unaccent('public.unaccent'::regdictionary, $1)
        $$
        """
    )

    # B-tree functional index for the prefix search (search_communes).
    op.execute(
        r"""
        CREATE INDEX IF NOT EXISTS idx_geo_communes_nom_search
        ON geo_communes (
            regexp_replace(public.immutable_unaccent(lower(nom)), '[-\s]+', ' ', 'g')
            text_pattern_ops
        )
        """
    )

    # Trigram GIN index for the substring search (list_communes).
    op.execute(
        r"""
        CREATE INDEX IF NOT EXISTS idx_geo_communes_nom_trgm
        ON geo_communes USING GIN (
            regexp_replace(public.immutable_unaccent(lower(nom)), '[-\s]+', ' ', 'g')
            gin_trgm_ops
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_geo_communes_nom_trgm")
    op.execute("DROP INDEX IF EXISTS idx_geo_communes_nom_search")
    op.execute("DROP FUNCTION IF EXISTS public.immutable_unaccent(text)")
