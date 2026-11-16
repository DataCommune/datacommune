"""Keep commencement separate and track new source generations.

Legacy rows remain unqualified. No guessed dates or provenance backfill.
"""

from alembic import op

revision = "093_permis_source_dates"
down_revision = "092_loyers_prediction_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE permis ADD COLUMN date_commencement DATE")
    op.execute("ALTER TABLE permis ADD COLUMN source_sha256 TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE permis DROP COLUMN source_sha256")
    op.execute("ALTER TABLE permis DROP COLUMN date_commencement")
