"""Create bdnb_batiment_parcelle: the building groups of each parcel (BDNB export).

Revision ID: 113_bdnb_batiment_parcelle
Revises: 112_departement_synthese
Create Date: 2026-11-02

Plan 04.7-19. Materializes locally what the ``construction`` theme of the
address report fetched LIVE from api.bdnb.io for every report (open tier,
10 000 requests a month): the building groups standing on the parcel of the
address, with their year and materials. The ``bdnb_batiment_parcelle`` ETL fills
it; the ``/v1/bdnb*`` routes stay a proxy of the API.

Source: Base de Données Nationale des Bâtiments (BDNB), open-data millesime
exports, one zip per departement
``https://open-data.s3.fr-par.scw.cloud/bdnb_millesime_<m>/millesime_<m>_dep<xx>/
open_data_millesime_<m>_dep<xx>_csv.zip``, listed on data.gouv.fr.
Producer: CSTB. Licence: **Licence Ouverte 2.0** (catalogue entry ``bdnb`` of
``app.common.source_catalog``). Citation requested by the CSTB: « Base de
Données Nationale des Bâtiments (BDNB) - Millésime <code> - CSTB ».

Grain: ONE ROW PER (building group, parcel) RELATION of
``csv/rel_batiment_groupe_parcelle.csv``, principal or not, with the
attributes of the group in ``csv/batiment_groupe_ffo_bat.csv`` copied in by a
LEFT join (a group without a row there keeps NULL attributes). This is the
rule the API applies when it is read by parcel (04.7-19-RESEARCH § 3): every
relation of the parcel, never only the principal ones.

Columns:
  - batiment_groupe_id VARCHAR(32): BDNB building group id (22 chars observed).
  - parcelle_id VARCHAR(14): cadastral parcel id, the read key of the theme.
    A longer value fails the load rather than being truncated.
  - code_departement VARCHAR(3): departement of the source FILE (one export per
    departement; ``2a`` is stored ``2A``). Drives the scope gates and the
    fidelity counts, never a spatial claim.
  - parcelle_principale BOOLEAN: the relation is the principal parcel of the
    group (OR of the files when a relation appears in two of them).
  - annee_construction SMALLINT, mat_mur_txt TEXT, mat_toit_txt TEXT: from
    ``batiment_groupe_ffo_bat`` (fichiers fonciers), « BDNB OPEN » columns.

No millesime column: every run replaces the whole table, so one generation
holds one millesime, read in ``dataset_metadata.source_period_label``.

Index -- one access path, the theme's ``WHERE parcelle_id = $1``:
  UNIQUE (parcelle_id, batiment_groupe_id), named so that its staging twin
  (``_staging`` suffix) stays under 63 characters: the leading column serves
  the theme, the uniqueness guards the natural key. No index on
  code_departement: only the fidelity tool reads it, by a scan.

Non-partitioned (about 1.4 M rows for 44+85, about 40 M nationally), like
``rnb_liens``. Table is created EMPTY: the ETL fills it, and ADR-0003 ordering
applies (migrate before the first run).
"""

from alembic import op

revision: str = "113_bdnb_batiment_parcelle"
down_revision: str = "112_departement_synthese"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the bdnb_batiment_parcelle table and its read index."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS bdnb_batiment_parcelle (
            id BIGSERIAL PRIMARY KEY,
            batiment_groupe_id VARCHAR(32) NOT NULL,
            parcelle_id VARCHAR(14) NOT NULL,
            code_departement VARCHAR(3) NOT NULL,
            parcelle_principale BOOLEAN NOT NULL,
            annee_construction SMALLINT,
            mat_mur_txt TEXT,
            mat_toit_txt TEXT
        );
    """)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bdnb_bp_parcelle_groupe "
        "ON bdnb_batiment_parcelle (parcelle_id, batiment_groupe_id);"
    )


def downgrade() -> None:
    """Drop the bdnb_batiment_parcelle table (its index goes with it)."""
    op.execute("DROP TABLE IF EXISTS bdnb_batiment_parcelle CASCADE;")
