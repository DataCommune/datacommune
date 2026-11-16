"""Drop the id_rnb index: its only reader, the Lot C2 path, was removed.

Revision ID: 082_drop_dpe_id_rnb_index
Revises: 081_dpe_restore_identifiant_ban_index
Create Date: 2026-08-06

Migration 080 created ``idx_dpe_id_rnb`` as the "viability condition" of the Lot
C2 building-proof path (``dpe_appartements_par_preuve_batiment``). That path was
REMOVED on 2026-08-06 after being measured against production data, so the index
now has no reader at all.

Why the path went away -- the index inherits the reasoning. Measured over the
115 orphan-branch fiches of the sweep corpus: it fired on 1 fiche, yielded 0
card there (the street-number filter excludes what it finds, because rows
fetched by BUILDING carry the building's OTHER street numbers), and its
non-empty result SHORT-CIRCUITED the full spatial fallback in the ``if not
rows`` cascade of ``_charge_dpe`` -- that fiche served 0 card where the fallback
served 3. Benefit 0 cards, cost 3 cards. Full write-up in
the internal note MESURE-MARGINAL-C2-2026-08-06.

Why drop it rather than leave it in place:

- ``diagnostics_dpe`` is refreshed by an atomic table swap, and the index was
  also added to ``DpePipeline._create_staging_indexes`` (2026-08-05) so it would
  survive. Left there it would be REBUILT on every refresh -- ~261 129 entries
  -- to serve no query;
- an index on a column is read by the next person as "this column is on a query
  path". That is exactly the class of stale declaration this project has been
  paying for (the missing 077 index, the inoperative street filter, the declared
  but unused ``_NUMERIC_FIELDS``).

``id_rnb`` the COLUMN stays (migration 078) and the ETL keeps filling it: it is
corroboration data, and Lot A's coverage measurement (44: 58,61 %, 85: 46,62 %)
is what a future, MEASURED attachment rule would build on. Only the index goes.

Notes:
- Two names are dropped, both IF EXISTS. ``idx_dpe_id_rnb`` is what migration
  080 created and what production currently carries (verified 2026-08-05:
  4 544 kB). ``idx_dpe_staging_id_rnb`` is what the pipeline would have left
  behind after a refresh -- the DPE pipeline does not rename its indexes after
  the swap, so both spellings are legitimate states of the same index. Dropping
  only one would leave the other alive on a database that had been refreshed.
- No index rebuild, no table rewrite: DROP INDEX takes a brief ACCESS EXCLUSIVE
  lock on the table and returns.
- Reversible: ``downgrade`` recreates the migration-080 shape (partial, on
  ``id_rnb IS NOT NULL``). Reinstating the C2 path would need the index back,
  which is precisely why the downgrade is faithful rather than a no-op.
- Deployment follows ADR-0003 (D1): supervised migrate-before-cutover.
"""

from alembic import op

revision: str = "082_drop_dpe_id_rnb_index"
down_revision: str = "081_dpe_restore_identifiant_ban_index"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dpe_id_rnb")
    op.execute("DROP INDEX IF EXISTS idx_dpe_staging_id_rnb")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dpe_id_rnb "
        "ON diagnostics_dpe (id_rnb) WHERE id_rnb IS NOT NULL"
    )
