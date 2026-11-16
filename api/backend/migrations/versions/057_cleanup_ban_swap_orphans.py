"""Cleanup orphans from partial ban swap (run #101, 2026-04-14).

Run #101 completed Phase B (atomic table swap) but failed in Phase C at
the index rename step because idx_adresses_position already existed on
adresses_old. This left prod in a degraded state:

- adresses (new prod, 26M rows) carries idx_adresses_staging_* index names
- adresses_old (previous prod) carries idx_adresses_* index names
- Partitions of adresses still named adresses_staging_NN
- Sequence adresses_staging_id_seq still owns adresses.id

This migration forces the cleanup that Phase C could not complete and
drops adresses_old (rollback window already past its 1h TTL).

Revision ID: 057_cleanup_ban_swap_orphans
Revises: 056_quota_usage_account
Create Date: 2026-04-14
"""

from alembic import op

revision = "057_cleanup_ban_swap_orphans"
down_revision = "056_quota_usage_account"
branch_labels = None
depends_on = None

_INDEX_RENAMES = [
    ("idx_adresses_staging_position", "idx_adresses_position"),
    ("idx_adresses_staging_position_geog", "idx_adresses_position_geog"),
    ("idx_adresses_staging_ban_id", "idx_adresses_ban_id"),
    ("idx_adresses_staging_search_trgm", "idx_adresses_search_trgm"),
    ("idx_adresses_staging_search_fts", "idx_adresses_search_fts"),
    ("idx_adresses_staging_code_postal", "idx_adresses_code_postal"),
    ("idx_adresses_staging_code_commune", "idx_adresses_code_commune"),
]


def upgrade() -> None:
    # Drop orphaned _old (frees its indexes, sequence, and partitions via CASCADE)
    op.execute("DROP TABLE IF EXISTS adresses_old CASCADE")

    # Sequence: the old adresses_id_seq may still exist as an orphan after the
    # CASCADE drop above if it was the default owner of the old adresses.id
    op.execute("DROP SEQUENCE IF EXISTS adresses_id_seq CASCADE")
    op.execute(
        "ALTER SEQUENCE IF EXISTS adresses_staging_id_seq RENAME TO adresses_id_seq"
    )

    # Rename 7 indexes from staging-named to prod-named
    for old_name, new_name in _INDEX_RENAMES:
        op.execute(f"ALTER INDEX IF EXISTS {old_name} RENAME TO {new_name}")

    # Rename every partition of adresses whose name still starts with
    # "adresses_staging_" to the clean "adresses_<suffix>" form. Covers
    # dept partitions (01..95, 2a, 2b, 971..976) and the default catch-all.
    op.execute("""
        DO $$
        DECLARE
            r record;
            new_name text;
        BEGIN
            FOR r IN
                SELECT c.relname AS old_name
                FROM pg_inherits i
                JOIN pg_class c ON i.inhrelid = c.oid
                JOIN pg_class p ON i.inhparent = p.oid
                WHERE p.relname = 'adresses'
                  AND c.relname LIKE 'adresses_staging_%'
            LOOP
                new_name := 'adresses_' || substring(r.old_name from 'adresses_staging_(.*)');
                EXECUTE format('ALTER TABLE %I RENAME TO %I', r.old_name, new_name);
            END LOOP;
        END $$;
    """)


def downgrade() -> None:
    # This migration is a forward-only cleanup of a one-time production
    # incident. Downgrade is a no-op — the previous state was degraded
    # and there is nothing sensible to restore.
    pass
