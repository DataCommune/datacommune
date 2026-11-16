"""Backfill subscriptions.source for rows created before Phase 15 grant-plan.

No schema change -- column added in migration 058 as VARCHAR(50) NULL.
This migration is pure UPDATE: existing rows with source IS NULL are
classified as 'stripe' (real Stripe sub_xxx ids) or 'manual_grant' (defensive,
for pre-058 manual_grant_<user_id> ids that may exist if any were inserted
before migration 058 shipped).

BILL-03: after this migration, every subscription row has a non-NULL source,
and `upsert_subscription()` hardcodes 'stripe' on future INSERTs (see
billing/queries.py patch in the same plan 18-01).

Revision ID: 062_subscriptions_source_backfill
Revises: 061_api_log_indexes
Create Date: 2026-04-16
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "062_subscriptions_source_backfill"
down_revision = "061_api_log_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Stripe-origin rows: any subscription NOT using the manual_grant_<uid>
    # fake sub id convention established in Phase 15 admin_grant_plan().
    op.execute(
        """
        UPDATE subscriptions
           SET source = 'stripe'
         WHERE source IS NULL
           AND stripe_subscription_id NOT LIKE 'manual_grant_%'
        """
    )
    # Defensive pass: any legacy manual_grant row that somehow escaped
    # migration 058 + Phase 15 grant-plan path. Should be 0 rows on prod
    # but harmless if it matches.
    op.execute(
        """
        UPDATE subscriptions
           SET source = 'manual_grant'
         WHERE source IS NULL
           AND stripe_subscription_id LIKE 'manual_grant_%'
        """
    )


def downgrade() -> None:
    # Revert only rows we touched (identified by the two source values).
    op.execute(
        """
        UPDATE subscriptions
           SET source = NULL
         WHERE source IN ('stripe', 'manual_grant')
        """
    )
