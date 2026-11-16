"""Add account-level quota tracking table.

Moves quota enforcement from per-key to per-account.
quota_usage_account tracks (user_id, month) instead of (api_key_id, month).
Existing quota_usage table kept for per-key analytics.

Revision ID: 056_quota_usage_account
Revises: 055_api_keys_is_default
Create Date: 2026-04-06
"""

from alembic import op

revision = "056_quota_usage_account"
down_revision = "055_api_keys_is_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE quota_usage_account (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            month VARCHAR(7) NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, month)
        )
    """)

    # Migrate existing per-key usage to per-account (aggregate)
    op.execute("""
        INSERT INTO quota_usage_account (user_id, month, request_count)
        SELECT k.user_id, q.month, SUM(q.request_count)
        FROM quota_usage q
        JOIN api_keys k ON k.id = q.api_key_id
        WHERE k.user_id IS NOT NULL
        GROUP BY k.user_id, q.month
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quota_usage_account")
