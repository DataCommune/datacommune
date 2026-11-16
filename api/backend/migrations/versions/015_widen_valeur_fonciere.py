"""Widen mutations.valeur_fonciere from NUMERIC(12,2) to NUMERIC(15,2).

Revision ID: 015_widen_valeur_fonciere
Revises: 014_widen_adresses_rep
Create Date: 2026-02-11

DVF data contains transactions exceeding 10^10 (e.g. large commercial deals).
NUMERIC(15,2) supports up to 9,999,999,999,999.99.
ALTER TYPE on partitioned table propagates to all 103 partitions automatically.
"""

from alembic import op

revision: str = "015_widen_valeur_fonciere"
down_revision: str = "014_widen_adresses_rep"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mutations ALTER COLUMN valeur_fonciere TYPE NUMERIC(15, 2);"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE mutations ALTER COLUMN valeur_fonciere TYPE NUMERIC(12, 2);"
    )
