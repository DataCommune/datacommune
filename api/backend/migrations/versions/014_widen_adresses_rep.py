"""Widen adresses.rep from VARCHAR(5) to VARCHAR(10).

Revision ID: 014_widen_adresses_rep
Revises: 013_etl_runs
Create Date: 2026-02-11

BAN data contains rep values like 'quater', 'quinquies' that exceed 5 chars.
ALTER TYPE on partitioned table propagates to all 103 partitions automatically.
"""

from alembic import op

revision: str = "014_widen_adresses_rep"
down_revision: str = "013_etl_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE adresses ALTER COLUMN rep TYPE VARCHAR(10);"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE adresses ALTER COLUMN rep TYPE VARCHAR(5);"
    )
