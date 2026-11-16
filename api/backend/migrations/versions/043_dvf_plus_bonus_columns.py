"""Add 5 DVF+ bonus columns to mutations partitioned table.

Columns: code_type_local, code_nature_culture, nature_culture,
code_nature_culture_speciale, nature_culture_speciale.

ADD COLUMN IF NOT EXISTS propagates to all 102 partitions automatically.

Revision ID: 043_dvf_plus_bonus
Revises: 042_fix_equip_pk_geog
Create Date: 2026-03-18
"""

from alembic import op

revision = "043_dvf_plus_bonus"
down_revision = "042_fix_equip_pk_geog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS code_type_local VARCHAR(5)"
    )
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS code_nature_culture VARCHAR(5)"
    )
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS nature_culture VARCHAR(100)"
    )
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS code_nature_culture_speciale VARCHAR(5)"
    )
    op.execute(
        "ALTER TABLE mutations "
        "ADD COLUMN IF NOT EXISTS nature_culture_speciale VARCHAR(100)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE mutations DROP COLUMN IF EXISTS nature_culture_speciale"
    )
    op.execute(
        "ALTER TABLE mutations "
        "DROP COLUMN IF EXISTS code_nature_culture_speciale"
    )
    op.execute(
        "ALTER TABLE mutations DROP COLUMN IF EXISTS nature_culture"
    )
    op.execute(
        "ALTER TABLE mutations DROP COLUMN IF EXISTS code_nature_culture"
    )
    op.execute(
        "ALTER TABLE mutations DROP COLUMN IF EXISTS code_type_local"
    )
