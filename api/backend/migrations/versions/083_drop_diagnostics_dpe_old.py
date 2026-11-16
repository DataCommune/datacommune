"""Drop diagnostics_dpe_old: the DPE re-ingestion backup, kept 4 months too long.

Revision ID: 083_drop_diagnostics_dpe_old
Revises: 082_drop_dpe_id_rnb_index
Create Date: 2026-08-22

``diagnostics_dpe`` is refreshed by an atomic table swap (``app/etl/base.py``):
the previous production table survives under ``<table>_old`` as a rollback
window, is registered in ``etl_old_tables``, and the hourly janitor
(``cleanup_old_tables``, TTL ``ETL_OLD_TABLE_TTL_HOURS``) drops it once the
window closes. ``diagnostics_dpe_old`` is what the Lot A / phase 38 DPE
re-ingestion left behind (internal tracking,
38-04-REINGEST-PROOF).

It never went away, which is the tell that it was never registered: 474 604
rows and 159 MB of a rollback window that expired in April are still sitting in
the database. Same class of orphan as ``adresses_old`` in migration 057 — a
swap that did not finish its bookkeeping — and it is cleaned the same way, by
migration rather than by hand, so the gate of ADR-0003 (D1) carries it to
production instead of an operator's memory.

Safety, verified on production 2026-08-22 (internal note AUDIT-SANTE-DB-2026-08-22
§ 3):

- **Zero database dependency**: no view, no matview, no foreign key
  (``pg_depend`` / ``pg_constraint``).
- **Zero code reference**: the only occurrence of the name in the repository is
  the planning document quoted above.

Notes:
- ``IF EXISTS``: no-op on any environment where the table was already dropped
  by hand, and on every fresh install (it is a swap artefact, no migration ever
  created it).
- No ``CASCADE``, unlike migration 057. The audit proved the dependency set
  empty, so the default ``RESTRICT`` turns anything that appeared since into a
  loud failure at migrate time rather than a silent collateral drop. Migration
  057 needed ``CASCADE`` because it was dropping a partitioned table with known
  orphan indexes and sequences; ``diagnostics_dpe`` is not partitioned (see
  migration 080) and neither is its backup.
- The ``DELETE`` on ``etl_old_tables`` is defensive bookkeeping. The row is
  expected to be absent — had it been there, the janitor would have dropped the
  table months ago — but leaving a registry row pointing at a table that no
  longer exists would make the janitor retry it forever. This mirrors what
  ``EtlPipeline.drop_previous_old_table`` does: drop, then deregister.
- Lock: ``DROP TABLE`` takes an ACCESS EXCLUSIVE lock on ``diagnostics_dpe_old``
  only. Nothing reads it, so there is nothing to contend with; the production
  table ``diagnostics_dpe`` is untouched. No maintenance window needed.
- Deployment follows ADR-0003 (D1): supervised migrate-before-cutover.
"""

from alembic import op

revision: str = "083_drop_diagnostics_dpe_old"
down_revision: str = "082_drop_dpe_id_rnb_index"
branch_labels: str | None = None
depends_on: str | None = None

_OLD_TABLE = "diagnostics_dpe_old"


def upgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_OLD_TABLE}")
    op.execute(f"DELETE FROM etl_old_tables WHERE table_name = '{_OLD_TABLE}'")


def downgrade() -> None:
    # Forward-only, like migration 057. Downgrade is a deliberate no-op: the
    # 474 604 rows are gone and no schema recreation would bring them back.
    #
    # Recreating an empty shell of the table would be worse than nothing — it
    # would advertise a rollback window that holds no rollback data, and the
    # next reader would have to query it to find out. Raising instead would
    # block the only rollback path this project actually uses: ADR-0003 (D2)
    # makes "DB ahead of code" a warning, not a failure, so a rollback here is
    # a PBS snapshot restore plus the previous image, never `alembic downgrade`.
    # Refusing to downgrade would only break the unrelated migrations behind
    # this one.
    pass
