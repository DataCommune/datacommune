"""Add RNB/BAN-confidence fields to diagnostics_dpe (R4-01, Plan A'').

Revision ID: 078_dpe_rnb_fields
Revises: 077_dpe_identifiant_ban_index
Create Date: 2026-08-04

RESEARCH-L1-RATTACHEMENT-DPE-2026-07-31 §1.4 (Plan A'') -- three ADEME source
columns ingested by the DPE ETL (etl_dpe/config.py, same run as the swap):
  - id_rnb: RNB building identifier (e.g. ``42P8B8HVDY1D``), ~58,6 % coverage
    on dept 44 and growing. Enables a future building-level exact join
    (``dpe.id_rnb = <RNB building of the parcel>``).
  - provenance_id_rnb: how ADEME obtained id_rnb. The dominant value
    ("Reprise RNB", 187 725/202 284 on dept 44) is an AUTOMATIC match derived
    from the SAME BAN geocoding as ``identifiant_ban`` -- CORRELATED with the
    BAN key, never an independent confirmation of it. Usage: catch-up join,
    not proof (RESEARCH-L1 §3).
  - score_ban: ADEME geocoding confidence, float in [0, 1] (0,52-0,98
    observed) -- usable as a confidence indicator, unlike the incoherent
    ``statut_geocodage`` flag which must never be filtered on.

Deployment ordering (critical): ``diagnostics_dpe_staging`` is recreated each
ETL run via ``LIKE diagnostics_dpe INCLUDING ALL``, and swap() INSERT SELECTs
these columns from the flat staging. This migration MUST therefore be applied
BEFORE the next DPE ETL refresh (otherwise the INSERT SELECT targets missing
columns and the run fails). Existing rows stay NULL until that refresh
backfills them.

Production: full 44/85 DPE refresh required after applying this migration,
via the ADR-0003 procedure (``alembic upgrade head`` as a supervised
migrate-before-cutover runbook step, never at boot). Tracked as an open item
of session S7 (Lot B, PLAN-RESTES-2026-08-02 §6).

Additive ADD COLUMN IF NOT EXISTS on the partitioned parent (cascades to the
102 partitions, same precedent as migration 075): no table rewrite, no index,
no long lock expected.
"""

from alembic import op

revision = "078_dpe_rnb_fields"
down_revision = "077_dpe_identifiant_ban_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ADD COLUMN IF NOT EXISTS id_rnb VARCHAR(32), "
        "ADD COLUMN IF NOT EXISTS provenance_id_rnb TEXT, "
        "ADD COLUMN IF NOT EXISTS score_ban NUMERIC;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "DROP COLUMN IF EXISTS id_rnb, "
        "DROP COLUMN IF EXISTS provenance_id_rnb, "
        "DROP COLUMN IF EXISTS score_ban;"
    )
