"""Add B-tree index on mutations.id_parcelle.

Revision ID: 072_mutations_idx
Revises: 071_geo_communes_search_indexes
Create Date: 2026-07-05

D-05 (Phase 39 OPS-06 positive proof) — dvf_par_parcelle (app/dvf/queries.py:166-218),
called from app/resolve/multi.py:178, joins/filters mutations on id_parcelle with no
index on that column (unlike parcelles.id_parcelle, indexed since migration 003). The
matview refresh for scoring_renov_target (migrations 019/020/066) also filters on
id_parcelle. A simple B-tree index (equality/join key, not a text search pattern) closes
that gap.

Notes:
- mutations is LIST-partitioned by code_departement (102 partitions, migration
  005_dvf_mutations.py).
- CREATE INDEX CONCURRENTLY is NOT supported on partitioned tables in a single
  statement, and cannot run inside Alembic's per-migration transaction anyway.
- Regular CREATE INDEX on the partitioned parent cascades to all child partitions
  automatically (PostgreSQL 11+), same precedent as migration 037.
- Additive only: id_parcelle already exists on mutations, no ALTER/CREATE TABLE.
"""

from alembic import op

revision: str = "072_mutations_idx"
down_revision: str = "071_geo_communes_search_indexes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_mutations_id_parcelle "
        "ON mutations (id_parcelle)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mutations_id_parcelle")
