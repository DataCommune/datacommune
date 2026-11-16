"""Add B-tree index on diagnostics_dpe.identifiant_ban.

Revision ID: 077_dpe_identifiant_ban_index
Revises: 076_copropriete_parcelle_insee
Create Date: 2026-07-31

RESEARCH-L1-RATTACHEMENT-DPE-2026-07-31 (step 2) — ``identifiant_ban`` becomes
the PRIMARY DPE attachment key of ``GET /v1/bien/logements``
(``dpe_appartements_par_ban``, equality filter on that column). Migration 075
added the column with an explicit "No index added: logement access is via the
spatial join on ``position``" — that spatial access path is precisely the L1
bug (an ADEME DPE position is the BAN entrance point, frequently on the
roadway and outside every parcel), so the premise no longer holds and the
equality lookup needs its own B-tree.

Notes:
- CORRECTION 2026-08-05: the paragraph below claimed diagnostics_dpe is
  LIST-partitioned by code_departement (102 partitions, migration 006). It is
  NOT -- verified in pg_class on production: relkind = 'r',
  relispartition = false, zero child partition. The 102 departmental
  partitions are real, but they are on ``adresses``; migration 006 creates
  diagnostics_dpe as a plain table. The claim is left visible rather than
  deleted because it propagated (see the project CLAUDE.md and the Lot C2
  query comments, both corrected the same day) and a silent edit would let it
  come back. Nothing in this migration depended on it: the index built and
  works either way.
- CREATE INDEX CONCURRENTLY is NOT used here. The original reason given (
  unsupported on a partitioned parent) was wrong along with the premise; the
  valid one remains: it cannot run inside Alembic's per-migration transaction
  without an autocommit block. The build therefore takes an ACCESS EXCLUSIVE
  lock on the table for its duration.
- Production deployment follows ADR-0003 (D1): ``alembic upgrade head`` is run
  as a supervised, migrate-before-cutover step of the deploy runbook
  (``api/docs/deploy-runbook.md``), never implicitly at boot. If the lock
  window proves too long (precedent: migration 065), the operator may build
  the per-partition indexes CONCURRENTLY beforehand and attach them; this
  migration stays IF NOT EXISTS-safe in that case.
- Additive only: identifiant_ban already exists (migration 075), no ALTER.
"""

from alembic import op

revision: str = "077_dpe_identifiant_ban_index"
down_revision: str = "076_copropriete_parcelle_insee"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dpe_identifiant_ban "
        "ON diagnostics_dpe (identifiant_ban)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dpe_identifiant_ban")
