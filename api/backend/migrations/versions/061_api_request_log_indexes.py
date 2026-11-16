"""Add composite indexes on api_request_log to support admin filter queries.

Supports Phase 17 /admin/requests endpoint filtering by:
- user_id via JOIN on api_keys.api_key_id (idx_api_log_key_ts)
- status_code with optional ts range (idx_api_log_status_ts)
- path prefix with optional ts range (idx_api_log_path_ts, text_pattern_ops for LIKE 'prefix%')

Without these indexes, the filter endpoint would full-scan the log table and
violate the <2s response-time requirement (OBS-01).

Revision ID: 061_api_log_indexes
Revises: 060_users_email_lowercase
Create Date: 2026-04-16
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "061_api_log_indexes"
down_revision = "060_users_email_lowercase"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite (api_key_id, ts DESC): supports JOIN-based user filter.
    # /admin/requests filters by k.user_id but api_request_log stores api_key_id;
    # Postgres planner uses this index after resolving user_id -> list of api_key_ids.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_log_key_ts "
        "ON api_request_log (api_key_id, ts DESC)"
    )
    # Composite (status_code, ts DESC): supports status filter with optional ts range.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_log_status_ts "
        "ON api_request_log (status_code, ts DESC)"
    )
    # Composite (path text_pattern_ops, ts DESC): supports LIKE 'prefix%' (sargable)
    # combined with ts range. text_pattern_ops is REQUIRED — the default operator
    # class does NOT support prefix-LIKE index scans under lc_collate != "C".
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_log_path_ts "
        "ON api_request_log (path text_pattern_ops, ts DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_api_log_path_ts")
    op.execute("DROP INDEX IF EXISTS idx_api_log_status_ts")
    op.execute("DROP INDEX IF EXISTS idx_api_log_key_ts")
