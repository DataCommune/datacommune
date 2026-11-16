"""Second-home surtax (THRS) and syndic mandate columns for the logement report.

Revision ID: 110_logement_thrs_mandat
Revises: 109_permis_dossier_destination_key
Create Date: 2026-10-31

Plan 04.7-08. Two public sources already carry facts the logement report
needs and the ETLs used to drop:

- ``fiscalite_locale``: DGFiP ``IND_MAJOTHRS`` (OUI/NON) and
  ``THSURTAXRSTAU`` (surtax rate, in percent of the contribution: 5 to 60) become
  ``majoration_thrs`` and ``taux_majoration_thrs``.
- ``copropriete``: RNIC ``mandat_en_cours`` and ``date_fin_dernier_mandat``
  keep their names.

All four columns are nullable and NULL means "not imported yet", until the
next import of each dataset (the ETL always writes ``mandat_en_cours``, with
``non_connu`` when the source is empty, so a NULL there is never a source
value). No backfill. Both pipelines build their staging table with
``LIKE <production>``, so the swap keeps the columns once they exist here;
the flat staging tables and INSERT lists are updated in the pipelines.
Downgrade drops the four columns (their data is re-importable).
"""

import importlib.util
from pathlib import Path

from alembic import op

revision: str = "110_logement_thrs_mandat"
down_revision: str = "109_permis_dossier_destination_key"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add the four nullable columns."""
    op.execute("""
        ALTER TABLE fiscalite_locale
            ADD COLUMN IF NOT EXISTS majoration_thrs BOOLEAN,
            ADD COLUMN IF NOT EXISTS taux_majoration_thrs NUMERIC
    """)
    op.execute("""
        ALTER TABLE copropriete
            ADD COLUMN IF NOT EXISTS mandat_en_cours TEXT,
            ADD COLUMN IF NOT EXISTS date_fin_dernier_mandat DATE
    """)


# app.commune.dependencies.PUBLICATION_LOCK_ID, copied as a literal
# (migrations never import app code).
PUBLICATION_LOCK_ID = 289093595696


def _migration_106():
    """Migration 106 read from its file: its view chain and its re-anchoring."""
    path = Path(__file__).with_name("106_eligibilite_fixe_par_immeuble.py")
    spec = importlib.util.spec_from_file_location("migration_106", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade() -> None:
    """Drop the four columns.

    The commune synthese reads ``fiscalite_locale`` through ``SELECT *``: once
    it was created after this revision (migration 111, or its downgrade), it
    depends on the two fiscal columns. The chain is dropped first, then
    recreated as migration 106 left it, under the publication lock, with its
    journal re-anchored (04.7-09).
    """
    bind = op.get_bind()
    helpers = _migration_106()
    op.execute("SET LOCAL lock_timeout = '5s'")
    bind.exec_driver_sql(f"SELECT pg_advisory_xact_lock({PUBLICATION_LOCK_ID})")
    anciens = helpers.oids_avant(bind, list(helpers.VIEW_NAMES))
    op.execute("DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese CASCADE")
    op.execute("""
        ALTER TABLE copropriete
            DROP COLUMN IF EXISTS date_fin_dernier_mandat,
            DROP COLUMN IF EXISTS mandat_en_cours
    """)
    op.execute("""
        ALTER TABLE fiscalite_locale
            DROP COLUMN IF EXISTS taux_majoration_thrs,
            DROP COLUMN IF EXISTS majoration_thrs
    """)
    for statement in helpers.STATEMENTS:
        bind.exec_driver_sql(statement)
    helpers.reancrer(bind, anciens)
