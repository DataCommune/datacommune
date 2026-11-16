"""Normalize users.email to lowercase + enforce case-insensitive uniqueness.

Background: auth router, billing webhook and SQL lookups previously mixed
case-sensitive and case-insensitive handling of emails. A user registered as
`Alice@Example.com` could not be retrieved via login(`alice@example.com`),
and Stripe `checkout.session.completed` webhooks failed to rattach
subscriptions when the email case differed.

This migration:

1. Pre-checks for collisions on lower(email) and aborts with a clear error
   if any are found — manual deduplication is required before running.
2. Lowercases every existing users.email row.
3. Replaces the case-sensitive UNIQUE constraint + idx_users_email with a
   single functional UNIQUE INDEX on lower(email).

Revision ID: 060_users_email_lowercase
Revises: 058_admin_audit_log
Create Date: 2026-04-15
"""

from alembic import op

revision = "060_users_email_lowercase"
down_revision = "058_admin_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Abort if collisions would violate the new functional unique index.
    collisions = conn.execute(
        _sql("""
            SELECT lower(email) AS e, array_agg(id ORDER BY id) AS ids
            FROM users
            GROUP BY lower(email)
            HAVING COUNT(*) > 1
        """)
    ).fetchall()
    if collisions:
        detail = "; ".join(f"{row.e} -> ids={row.ids}" for row in collisions)
        raise RuntimeError(
            "Cannot apply 060: email case-collisions detected. "
            "Merge or delete duplicates before retrying. Collisions: " + detail
        )

    # 2. Normalize existing rows.
    op.execute("UPDATE users SET email = lower(email) WHERE email <> lower(email)")

    # 3. Replace the case-sensitive unique constraint + index.
    op.execute("DROP INDEX IF EXISTS idx_users_email")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key")
    op.execute(
        "CREATE UNIQUE INDEX ix_users_email_lower ON users (lower(email))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_email_lower")
    op.execute("ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email)")
    op.execute("CREATE INDEX idx_users_email ON users (email)")


def _sql(query: str):
    """Thin helper to keep the upgrade() flow readable."""
    from sqlalchemy import text

    return text(query)
