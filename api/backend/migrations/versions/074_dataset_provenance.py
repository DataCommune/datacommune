"""Add nullable upstream source facts to dataset_metadata.

Revision ID: 074_dataset_provenance
Revises: 073_semantic_contracts
Create Date: 2026-07-21

PROV-01/PROV-03 (Phase 44, D-02/D-03): persist only authentic upstream facts
that the code cannot honestly reconstruct. ``last_success_at`` (etl_runs)
proves a DataCommune import succeeded; it does NOT prove the source published a
new millesime. This migration adds three nullable columns so a pipeline can
declare, when it truly knows them, an upstream period label and/or upstream
publication date, plus the effective department coverage when it differs from
the catalog policy.

Additive and null-safe (T-44-02-DB): three ADD COLUMN, no backfill, no UPDATE.
The columns default to NULL so an unmigrated pipeline yields ``unknown`` at the
assembly layer rather than an invented value. Never derive these from
``last_updated_at`` or ``NOW()``.

Downgrade removes exactly those three columns.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "074_dataset_provenance"
down_revision: str = "073_semantic_contracts"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "dataset_metadata"


def upgrade() -> None:
    """Add nullable upstream source facts. No backfill."""
    op.add_column(
        TABLE,
        sa.Column("source_period_label", sa.String(length=120), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("source_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column(
            "effective_departments",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove exactly the three columns added by upgrade()."""
    op.drop_column(TABLE, "effective_departments")
    op.drop_column(TABLE, "source_updated_at")
    op.drop_column(TABLE, "source_period_label")
