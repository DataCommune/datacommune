"""Preserve rental prediction provenance without backfilling historical claims.

Revision ID: 092_loyers_prediction_metadata
Revises: 091_dpe_source_lifecycle

Historical rows retain NULL metadata until a verified source reimport.
Dependent commune views are rebuilt by the existing publication hook after ETL.
"""

from alembic import op

revision = "092_loyers_prediction_metadata"
down_revision = "091_dpe_source_lifecycle"
branch_labels = None
depends_on = None

_COLUMNS = {
    "id_zone": "TEXT",
    "type_prediction": "TEXT",
    "nb_observations_commune": "INTEGER",
    "nb_observations_maille": "INTEGER",
    "r2_ajuste": "NUMERIC",
    "trimestre_reference": "SMALLINT",
    "charges_comprises": "BOOLEAN",
    "meuble": "BOOLEAN",
    "surface_reference_m2": "NUMERIC",
    "surface_piece_reference_m2": "NUMERIC",
}


def upgrade() -> None:
    for name, kind in _COLUMNS.items():
        op.execute(f"ALTER TABLE indicateurs_loyers ADD COLUMN {name} {kind}")


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE indicateurs_loyers DROP COLUMN {name}")
