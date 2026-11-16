"""Add logement-identity fields to diagnostics_dpe.

Revision ID: 075_dpe_logement_fields
Revises: 074_dataset_provenance
Create Date: 2026-07-27

Phase 48 (Multi-logements honnete), Wave 0 non-negociable (CONTEXT D-Wave0):
without these 5 columns the list of logements inside a multi-unit building is
literally unconstructible -- the only DPE access path today is
dpe_par_point(500 m radius), which would fabricate an invented list
(regression of Phase 45's "no radius" guarantee):
  - numero_etage_appartement: floor number reported by the diagnostician.
  - complement_adresse_logement: free-text unit complement -- the ONLY usable
    floor source (D-21): 34 411 / 36 154 non-empty numero_etage_appartement
    values are 0 in Vendee (85), including real 4th-floor units. Deliberately
    DISTINCT from complement_adresse_batiment, which is excluded: it is the
    diagnostician's internal folder reference (e.g. "48398 - GUYAU",
    "2026LRY047"), never a logement discriminant (Pitfall 2).
  - identifiant_ban: BAN address identifier, future exact-join key.
  - numero_dpe_remplace: lets the dedup service (D-05, Plan 48-03) perform an
    EXACT superseded-DPE pass upstream of the heuristic (etage, surface,
    complement) key.
  - date_fin_validite_dpe: validity end date read from source so the expired
    badge (D-08) is never recomputed as +10 years -- the DPE is proof of
    existence, not a live diagnosis.

The staging table is recreated each run via ``LIKE diagnostics_dpe INCLUDING ALL``
so this ALTER on production is enough; existing rows stay NULL until the next DPE
ETL refresh backfills them (Pitfall 8) -- the production backfill run is tracked
separately in Plan 48-10.

No index added: logement access is via the spatial join on ``position``,
already covered by ``idx_dpe_position``.
"""

from alembic import op

revision = "075_dpe_logement_fields"
down_revision = "074_dataset_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ADD COLUMN IF NOT EXISTS numero_etage_appartement INTEGER, "
        "ADD COLUMN IF NOT EXISTS complement_adresse_logement TEXT, "
        "ADD COLUMN IF NOT EXISTS identifiant_ban VARCHAR(50), "
        "ADD COLUMN IF NOT EXISTS numero_dpe_remplace TEXT, "
        "ADD COLUMN IF NOT EXISTS date_fin_validite_dpe DATE;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "DROP COLUMN IF EXISTS numero_etage_appartement, "
        "DROP COLUMN IF EXISTS complement_adresse_logement, "
        "DROP COLUMN IF EXISTS identifiant_ban, "
        "DROP COLUMN IF EXISTS numero_dpe_remplace, "
        "DROP COLUMN IF EXISTS date_fin_validite_dpe;"
    )
