"""Create users table, extend api_keys and api_request_log for auth.

Revision ID: 022_users
Revises: 021_geog_indexes
Create Date: 2026-02-27

Adds:
- users table (email/password auth, developer accounts)
- api_keys.user_id FK (nullable for existing CLI-created keys)
- api_keys.key_prefix (Stripe-style dc_live_/dc_test_ visible prefix)
- api_keys.environment (live/test distinction)
- api_request_log.api_key_id (best-effort logging, no FK)
"""

from alembic import op

revision: str = "022_users"
down_revision: str = "021_geog_indexes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create users table and extend api_keys + api_request_log."""
    # Users table for developer accounts
    op.execute("""
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            name VARCHAR(100),
            company VARCHAR(100),
            is_verified BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX idx_users_email ON users (email)")

    # Extend api_keys: link to user (nullable for existing CLI keys)
    op.execute(
        "ALTER TABLE api_keys "
        "ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
    )

    # Visible prefix for dashboard display (e.g. "dc_live_Ab3x9...")
    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS key_prefix VARCHAR(50)")

    # Environment distinction: live vs test
    op.execute(
        "ALTER TABLE api_keys "
        "ADD COLUMN IF NOT EXISTS environment VARCHAR(10) NOT NULL DEFAULT 'live'"
    )

    # Alembic owns this table so fresh databases do not depend on app startup.
    op.execute("""
        CREATE TABLE IF NOT EXISTS api_request_log (
            id BIGSERIAL PRIMARY KEY,
            ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            method VARCHAR(10),
            path VARCHAR(200),
            status_code SMALLINT,
            duration_ms FLOAT,
            client_ip INET,
            api_key_id BIGINT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_api_log_ts ON api_request_log (ts DESC)")

    # Best-effort request logging by key id (no FK to avoid perf impact).
    # No-op when the table was just created above with api_key_id BIGINT.
    op.execute(
        "ALTER TABLE api_request_log ADD COLUMN IF NOT EXISTS api_key_id INTEGER"
    )


def downgrade() -> None:
    """Remove auth columns and users table."""
    op.execute("ALTER TABLE api_request_log DROP COLUMN IF EXISTS api_key_id")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS environment")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS key_prefix")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS user_id")
    op.execute("DROP INDEX IF EXISTS idx_users_email")
    op.execute("DROP TABLE IF EXISTS users")
