"""Persist the explicit active synthetic indicator build event."""

from alembic import op

revision = "096_commune_indicator_build_activation"
down_revision = "095_commune_indicator_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE commune_indicator_build_activations (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            event_type TEXT NOT NULL CHECK (event_type IN ('activate', 'revoke')),
            build_id BIGINT REFERENCES commune_indicator_result_builds(id),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK ((event_type = 'activate') = (build_id IS NOT NULL))
        );
        CREATE TRIGGER immutable_indicator_activation
            BEFORE UPDATE OR DELETE OR TRUNCATE
            ON commune_indicator_build_activations
            FOR EACH STATEMENT EXECUTE FUNCTION forbid_result_changes();
    """)


def downgrade() -> None:
    """Remove only the activation journal; preserve every 095 object."""
    op.execute("DROP TABLE commune_indicator_build_activations")
