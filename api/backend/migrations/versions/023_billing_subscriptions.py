"""Create subscriptions + stripe_events tables, add stripe_customer_id to users.

NOTE: Existing api_keys.quota_monthly values are NOT modified (grandfathering
intentional). Quota changes only happen going forward via webhook handlers
when a user subscribes or changes plan.

Revision ID: 023_billing
Revises: 022_users
Create Date: 2026-02-27
"""

from alembic import op

revision: str = "023_billing"
down_revision: str = "022_users"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add billing tables and stripe_customer_id column."""
    # Add stripe_customer_id to users
    op.execute(
        "ALTER TABLE users "
        "ADD COLUMN stripe_customer_id VARCHAR(255) UNIQUE"
    )
    op.execute(
        "CREATE INDEX idx_users_stripe_customer "
        "ON users (stripe_customer_id)"
    )

    # Subscriptions table
    op.execute("""
        CREATE TABLE subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            stripe_subscription_id VARCHAR(255) UNIQUE NOT NULL,
            stripe_price_id VARCHAR(255) NOT NULL,
            plan_name VARCHAR(50) NOT NULL DEFAULT 'free',
            status VARCHAR(50) NOT NULL DEFAULT 'active',
            current_period_start TIMESTAMPTZ,
            current_period_end TIMESTAMPTZ,
            cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute(
        "CREATE INDEX idx_subscriptions_user "
        "ON subscriptions (user_id)"
    )
    op.execute(
        "CREATE INDEX idx_subscriptions_stripe_sub "
        "ON subscriptions (stripe_subscription_id)"
    )

    # Stripe events deduplication table
    op.execute("""
        CREATE TABLE stripe_events (
            event_id VARCHAR(255) PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    """Remove billing tables and stripe_customer_id column."""
    op.execute("DROP TABLE IF EXISTS stripe_events")
    op.execute("DROP TABLE IF EXISTS subscriptions")
    op.execute("DROP INDEX IF EXISTS idx_users_stripe_customer")
    op.execute(
        "ALTER TABLE users DROP COLUMN IF EXISTS stripe_customer_id"
    )
