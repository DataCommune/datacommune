"""Drop the indexes plan 04.1-23 proved unused.

Revision ID: 107_drop_unused_indexes
Revises: 106_eligibilite_fixe_par_immeuble
Create Date: 2026-10-20

Ianis's decision of 2026-10-20 ("Finir l'EXPLAIN", no removal without proof):
an index leaves only when the registry of plan 04.1-23
(``04.1-INDEX-EXPLAIN.md``, decision ``retiré``) shows it in none of the 272
plans of ``explain-23.json``, carrying no constraint. The exact set, with the
work base definitions taken from ``catalogue-index-23.json``, is the independent
reference ``tests/fixtures/index_removal_107.json``; the DVF and DPE pipelines
stop declaring the same indexes so the next publication does not bring them
back. No column, constraint or kept index is touched (D-102, LIEU-03).

Upgrade drops each index by its bare name, without ``IF EXISTS``: a missing
name means a base that differs from the catalogue, and the migration then
fails and rolls back instead of passing silently. Dropping a partitioned
parent index drops the partition indexes attached to it.

Downgrade recreates each index with the shape of the catalogue on the named
table, never on the parent alone: on a partitioned table PostgreSQL then
builds the parent, one index per partition present at that moment, and
attaches each one (precedents 072 and 087). That rebuilds the whole tree for
any partition set (102 departements plus default on a fresh chain, 44, 85 and
default on the work base) without listing partitions. Replaying the catalogue
definition as such would create an invalid parent with no child.

Single transaction (``migrations/env.py``), no concurrent build. Applied on
local disposable PostGIS only; the work base is migrated at plan 04.1-26 with
Ianis's agreement (D-119).
"""

from alembic import op

revision: str = "107_drop_unused_indexes"
down_revision: str = "106_eligibilite_fixe_par_immeuble"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DROP INDEX idx_mutations_position")
    op.execute("DROP INDEX idx_mutations_position_geog")
    op.execute("DROP INDEX idx_mutations_grain_position")
    op.execute("DROP INDEX idx_mutation_parcelles_id_mutation")
    op.execute("DROP INDEX idx_dpe_position")


def downgrade() -> None:
    op.execute(
        'CREATE INDEX idx_mutations_position ON mutations USING gist ("position")'
    )
    op.execute(
        "CREATE INDEX idx_mutations_position_geog "
        'ON mutations USING gist (geography("position"))'
    )
    op.execute(
        "CREATE INDEX idx_mutations_grain_position "
        'ON mutations_grain USING gist ("position")'
    )
    op.execute(
        "CREATE INDEX idx_mutation_parcelles_id_mutation "
        "ON mutation_parcelles USING btree (id_mutation)"
    )
    op.execute(
        'CREATE INDEX idx_dpe_position ON diagnostics_dpe USING gist ("position")'
    )
