"""Add partial B-tree index on diagnostics_dpe.id_rnb.

Revision ID: 080_dpe_id_rnb_index
Revises: 079_rnb_liens
Create Date: 2026-08-05

PLAN-RATTACHEMENT-DPE-2026-08-05, Lot C2 — ``id_rnb`` was added by migration
078 with an explicit "no index: nothing reads this column yet". Lot C2 puts it
on a query path (``dpe_appartements_par_preuve_batiment``: the orphan-key
branch of ``GET /v1/bien/logements`` joins it against the RNB buildings
carrying the resolved BAN key), so that premise no longer holds.

Measured on production 2026-08-05, without this index:

- 32-34 ms and ~13 220 buffers (~103 Mo) per orphan fiche in dept 44,
  17-21 ms and ~4 540 buffers in dept 85;
- the planner reads the WHOLE departmental slice (345 139 rows in 44,
  126 227 in 85) to hash-join it against a set of 2 buildings and return 0
  rows, and recruits 2 parallel workers per call in 44;
- expected with the index: ~7 to 80 rows read, sub-millisecond, no worker.

The window matters because the branch is not rare (orphan keys are 8,7 % of
dept 44 and 20 % of dept 85) and because its success rate is LOW: 9 818
retrievable keys out of 711 117 resolvable orphan ones, i.e. 1,38 % (measured
on the full population 2026-08-05). 98,6 % of the calls pay full price for
nothing -- which is acceptable at 0,2 ms and is not at 33 ms. This index is
the viability condition of the Lot C2 path, not an optimisation of it.

Notes:
- PARTIAL on ``id_rnb IS NOT NULL``: null_frac = 0,4465 measured, so 44,65 %
  of the rows can never join and are kept out of the index for free. Expected
  size 7-10 Mo (261 129 entries, avg_width 13) against 224 Mo for the table.
- NOT composite with ``code_departement`` (n_distinct = 2, one id_rnb yields
  7,1 appartement DPE on average, p95 = 26: the department filter then applies
  to a handful of rows) nor with ``type_batiment`` (it would save ~4 Mo and
  make the index useless to any future ``maison`` path).
- diagnostics_dpe is NOT partitioned, whatever migration 077 and the project
  CLAUDE.md state (verified in pg_class on production 2026-08-05: relkind =
  'r', relispartition = false, 0 child partition -- the 102 partitions are
  real, but on ``adresses``). CREATE INDEX CONCURRENTLY would therefore be
  available here, unlike on a partitioned parent; it is deliberately NOT used,
  to stay on the precedent of migrations 072 and 077 (plain CREATE INDEX,
  IF NOT EXISTS-safe) and because ~261 k entries build in seconds. Should the
  ACCESS EXCLUSIVE window prove too long, the operator may build it
  CONCURRENTLY by hand beforehand -- this migration is a no-op in that case.
- Production deployment follows ADR-0003 (D1): ``alembic upgrade head`` is a
  supervised migrate-before-cutover step of the deploy runbook, never implicit
  at boot.
- Additive only: id_rnb already exists (migration 078), no ALTER.
"""

from alembic import op

revision: str = "080_dpe_id_rnb_index"
down_revision: str = "079_rnb_liens"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dpe_id_rnb "
        "ON diagnostics_dpe (id_rnb) WHERE id_rnb IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dpe_id_rnb")
