"""Add composite index on subscriptions(user_id, status).

The get_active_subscription() query filters on both columns —
without this index every lookup does a seq scan on subscriptions.

Revision ID: 040_sub_idx
Revises: 039_v5_dataset_metadata
Create Date: 2026-03-17
"""

from alembic import op

revision: str = "040_sub_idx"
down_revision: str = "039_v5_dataset_metadata"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "idx_subscriptions_user_status ON subscriptions(user_id, status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_subscriptions_user_status")
