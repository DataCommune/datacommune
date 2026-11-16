"""api_keys.status state machine column.

Adds a status VARCHAR(20) column with CHECK constraint and partial index.
This supports the quick-key state machine (pending -> active | revoked)
and rejects non-active keys at the auth dependency level.

Revision ID: 064
Revises: 063_matview_metadata
Create Date: 2026-04-17

Design notes:
- DEFAULT 'active' means existing rows immediately comply — no functional
  change for live users on upgrade.
- Backfill: rows where revoked=TRUE carry status='revoked' for consistency.
- The legacy `revoked` BOOLEAN column is NOT dropped in this migration.
  Deprecation deferred to Phase 22+ (RESEARCH Open Question 4: keep both
  columns in sync for one deploy cycle).
- Partial index on status='active' speeds up the hot count_user_active_keys
  query path.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "064"
down_revision = "063_matview_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add column with DEFAULT 'active' so existing rows backfill immediately.
    op.execute("""
        ALTER TABLE api_keys
        ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'
    """)
    op.execute("""
        ALTER TABLE api_keys
        ADD CONSTRAINT api_keys_status_check
        CHECK (status IN ('pending', 'active', 'revoked'))
    """)
    # Backfill: rows where revoked=TRUE must now carry status='revoked'
    # to stay consistent with the new column.
    op.execute("UPDATE api_keys SET status = 'revoked' WHERE revoked = TRUE")
    op.create_index(
        "idx_api_keys_status",
        "api_keys",
        ["status"],
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("idx_api_keys_status", table_name="api_keys")
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_status_check")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS status")
