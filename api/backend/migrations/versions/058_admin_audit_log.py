"""Add admin_audit_log table, last_login on users, source on subscriptions.

Creates the foundational schema for the Phase 15 admin backoffice:
  - admin_audit_log: append-only mutation log for all /admin/* actions
  - users.last_login: tracked on explicit POST /auth/login success only
  - subscriptions.source: nullable tag for manual_grant vs Stripe origin

Revision ID: 058_admin_audit_log
Revises: 059_commune_synthese_add_nom_dept_region
Create Date: 2026-04-15
"""

from alembic import op

revision = "058_admin_audit_log"
down_revision = "059_commune_synthese_add_nom_dept_region"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add last_login to users (nullable — NULL = never logged in)
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMPTZ"
    )

    # 2. Add source to subscriptions (nullable — Stripe rows stay NULL until Phase 18 backfill)
    op.execute(
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS source VARCHAR(50)"
    )

    # 3. Create admin_audit_log (append-only, infinite retention)
    #    BIGSERIAL for id — BIGINT range handles infinite audit rows without overflow.
    op.execute("""
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id              BIGSERIAL PRIMARY KEY,
            admin_user_id   INTEGER NOT NULL REFERENCES users(id),
            action          TEXT NOT NULL,
            target_type     TEXT NOT NULL,
            target_id       TEXT NOT NULL,
            before          JSONB,
            after           JSONB,
            reason          TEXT NOT NULL,
            ip              INET,
            user_agent      TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # 4. Composite indexes to support the 4 audit viewer filter dimensions:
    #    admin / action / target / date
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_admin_time "
        "ON admin_audit_log (admin_user_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_action_time "
        "ON admin_audit_log (action, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_target "
        "ON admin_audit_log (target_type, target_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS admin_audit_log")
    # Indexes are dropped automatically with the table (CASCADE via PK + DROP TABLE)
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS source")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_login")
