"""Add is_default column to api_keys for hybrid auth.

Default keys are auto-created at email verification and cannot be revoked.
They enable JWT cookie-based auth on /v1/* endpoints (hybrid auth).

Revision ID: 055_api_keys_is_default
Revises: 054_validation_runs
Create Date: 2026-04-06
"""

from alembic import op

revision = "055_api_keys_is_default"
down_revision = "054_validation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE api_keys ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS is_default")
