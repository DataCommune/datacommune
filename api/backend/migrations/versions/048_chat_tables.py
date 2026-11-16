"""Create chat_sessions and chat_messages tables for AI tool-layer conversations.

Revision ID: 048_chat_tables
Revises: 047_dpe_code_departement_index
Create Date: 2026-04-01
"""

import sqlalchemy as sa
from alembic import op

revision: str = "048_chat_tables"
down_revision: str = "047_dpe_code_departement_index"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create chat_sessions and chat_messages tables with FK and index."""
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index(
        "idx_chat_messages_session_id",
        "chat_messages",
        ["session_id"],
    )


def downgrade() -> None:
    """Drop chat_messages and chat_sessions tables (FK order)."""
    op.drop_index("idx_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
