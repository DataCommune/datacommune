"""Change eligibilite_fixe.debit_max from real to text.

Revision ID: 017_arcep_debit_max_to_text
Revises: 016_widen_dpe_numeric
Create Date: 2026-02-13

Arcep changed classe_debit_descendant from numeric values to text class
labels (e.g. 'BHD8', 'THD100', 'THD1G', 'THD30') in their 2026 data.
"""

from alembic import op

revision: str = "017_arcep_debit_max_to_text"
down_revision: str = "016_widen_dpe_numeric"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE eligibilite_fixe "
        "ALTER COLUMN debit_max TYPE TEXT "
        "USING debit_max::TEXT;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE eligibilite_fixe "
        "ALTER COLUMN debit_max TYPE REAL "
        "USING CASE WHEN debit_max ~ E'^\\d+\\.?\\d*$' "
        "THEN debit_max::REAL END;"
    )
