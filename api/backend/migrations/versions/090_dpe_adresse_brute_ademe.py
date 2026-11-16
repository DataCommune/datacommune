"""Add raw ADEME address columns + regime_dpe to diagnostics_dpe.

Revision ID: 090_dpe_adresse_brute_ademe
Revises: 089_restore_mutations_idx
Create Date: 2026-08-26

Phase 58 (Rattachement des maisons, bug Reddit n. 3), D-02/D-16: this
migration is what makes the phase's only documentary oracle computable --
comparing the address the diagnostician typed at the time of the DPE against
the normalized BAN label the ADEME geocoder produced for the same record.
Today both are thrown away by the ETL (``etl_dpe/config.py``), so no query
can tell a genuinely mismatched address from a correctly attributed one.
8 raw columns carry that comparison (58-RESEARCH.md Sec.3.1, "Recommandation
de perimetre R1"): ``adresse_brut``, ``adresse_complete_brut``,
``nom_commune_brut``, ``code_postal_brut``, ``adresse_ban``,
``numero_voie_ban``, ``nom_rue_ban``, ``statut_geocodage``. A 9th column,
``regime_dpe``, is plumbing for D-14: it lets the pipeline (58-04) record
whether a diagnostics_dpe row came from the ``dpe03existant`` source or a
future ``dpe02neuf`` source, WITHOUT deciding to ingest the latter -- that
ingestion is a checkpoint D-06 decision, not this plan's.

Why a migration and not a runbook step (ADR-0003): the migration gate and
the fail-fast at boot when Alembic is not at head mean this ALTER is applied
automatically at deploy, on every instance, a fresh install included. A
runbook step is manual and would be forgotten on the next one.

No index added: the access path for these columns is a scan already bounded
by ``idx_dpe_identifiant_ban`` (migrations 077/081) -- the raw columns are
read alongside an already-keyed row, never used to look one up. 58-RESEARCH
Sec.6.1 point 5 confirms no gate of this phase is spatial or otherwise needs
a dedicated index on this data.

``code_postal_brut`` is kept ``TEXT``, never ``INTEGER``, even though ADEME
types the source field as an integer: casting it would silently drop the
leading zero of every postal code below 10000 (58-RESEARCH.md Pitfall 8).

The staging table is recreated each run via ``LIKE diagnostics_dpe INCLUDING
ALL`` (migration 075's pattern), so this ALTER on production is enough;
existing rows stay NULL until the next DPE ETL refresh backfills them -- the
production R1 re-run is tracked separately (58-06/58-07).

Unlike migration 089 (which protects an index another migration already
owns), ``downgrade()`` here is a real ``DROP COLUMN IF EXISTS`` for all 9
columns: no other migration claims them.
"""

from alembic import op

revision: str = "090_dpe_adresse_brute_ademe"
down_revision: str = "089_restore_mutations_idx"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "ADD COLUMN IF NOT EXISTS adresse_brut TEXT, "
        "ADD COLUMN IF NOT EXISTS adresse_complete_brut TEXT, "
        "ADD COLUMN IF NOT EXISTS nom_commune_brut TEXT, "
        "ADD COLUMN IF NOT EXISTS code_postal_brut TEXT, "
        "ADD COLUMN IF NOT EXISTS adresse_ban TEXT, "
        "ADD COLUMN IF NOT EXISTS numero_voie_ban TEXT, "
        "ADD COLUMN IF NOT EXISTS nom_rue_ban TEXT, "
        "ADD COLUMN IF NOT EXISTS statut_geocodage TEXT, "
        "ADD COLUMN IF NOT EXISTS regime_dpe TEXT;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE diagnostics_dpe "
        "DROP COLUMN IF EXISTS adresse_brut, "
        "DROP COLUMN IF EXISTS adresse_complete_brut, "
        "DROP COLUMN IF EXISTS nom_commune_brut, "
        "DROP COLUMN IF EXISTS code_postal_brut, "
        "DROP COLUMN IF EXISTS adresse_ban, "
        "DROP COLUMN IF EXISTS numero_voie_ban, "
        "DROP COLUMN IF EXISTS nom_rue_ban, "
        "DROP COLUMN IF EXISTS statut_geocodage, "
        "DROP COLUMN IF EXISTS regime_dpe;"
    )
