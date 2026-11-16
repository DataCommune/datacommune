"""Widen the DPE observation constraint to every hostable departement.

Revision ID: 101_dpe_scope_national
Revises: 100_dpe_source_columns
Create Date: 2026-10-13

Plan 04.1-07 (ROADMAP 4.1 criterion 4, D-122, D-123). Migration 091 made
``ck_dpe_source_observations`` accept the observations of 44 and 85 only,
through ``public.dc_dpe_observations_valid``. This revision replaces that
function with the same body, except the departement list: the 101 codes of
``app.scope.model.DEPARTEMENTS_HEBERGEABLES`` (96 metropolitan departements and
the five DROM; overseas collectivities 975, 977, 978, 98x stay refused). A
closed list is widened, no free pattern is opened. The list is copied here
(a migration never imports the application); a test compares both lists.

Upgrade only widens: no existing row can become invalid, nothing is rewritten.

Downgrade gives the 44/85 function back, and refuses (raises, the transaction
of ``migrations/env.py`` rolls back) while a row of any table carrying the
constraint (served table, candidate, ``_old`` copy) has an observation outside
44/85: it never deletes nor rewrites a row. The message names the count; such
rows must first be withdrawn by a 44/85 reimport.
"""

from alembic import op

revision: str = "101_dpe_scope_national"
down_revision: str = "100_dpe_source_columns"
branch_labels: str | None = None
depends_on: str | None = None

# Copy of app.scope.model.DEPARTEMENTS_HEBERGEABLES (101 codes, 2026-10-13),
# checked equal by tests/test_scope_locks.py.
DEPARTEMENTS_HEBERGEABLES: tuple[str, ...] = (
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
    "11", "12", "13", "14", "15", "16", "17", "18", "19", "2A",
    "2B", "21", "22", "23", "24", "25", "26", "27", "28", "29",
    "30", "31", "32", "33", "34", "35", "36", "37", "38", "39",
    "40", "41", "42", "43", "44", "45", "46", "47", "48", "49",
    "50", "51", "52", "53", "54", "55", "56", "57", "58", "59",
    "60", "61", "62", "63", "64", "65", "66", "67", "68", "69",
    "70", "71", "72", "73", "74", "75", "76", "77", "78", "79",
    "80", "81", "82", "83", "84", "85", "86", "87", "88", "89",
    "90", "91", "92", "93", "94", "95",
    "971", "972", "973", "974", "976",
)  # fmt: skip
DEPARTEMENTS_091: tuple[str, ...] = ("44", "85")

# Waiting behind an import holding the table would block the deploy: fail fast.
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"


def _observations_valid(departements: tuple[str, ...]) -> str:
    """``dc_dpe_observations_valid`` of migration 091 over *departements*."""
    codes = ",".join(f"'{code}'" for code in departements)
    return rf"""
        CREATE OR REPLACE FUNCTION public.dc_dpe_observations_valid(value jsonb)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
        DECLARE item jsonb; field text; pair text; previous text := '';
        BEGIN
            IF jsonb_typeof(value) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
            IF jsonb_array_length(value) > 4 THEN RETURN false; END IF;
            IF jsonb_array_length(value) = 0 THEN RETURN true; END IF;
            FOR item IN SELECT jsonb_array_elements(value) LOOP
                IF NOT public.dc_dpe_json_keys(item, ARRAY[
                    'source_slug','department','source_version','observed_at',
                    'last_seen_at','state','import_id'
                ]) THEN RETURN false; END IF;
                FOREACH field IN ARRAY ARRAY[
                    'source_slug','department','source_version','observed_at',
                    'last_seen_at','state','import_id'
                ] LOOP
                    IF jsonb_typeof(item->field) IS DISTINCT FROM 'string'
                    THEN RETURN false; END IF;
                END LOOP;
                IF item->>'source_slug' NOT IN ('dpe03existant','dpe02neuf')
                    OR item->>'department' NOT IN ({codes})
                    OR item->>'source_version' !~ '^[a-f0-9]{{64}}$'
                    OR btrim(item->>'import_id') = ''
                    OR item->>'state' NOT IN (
                        'present','absent_from_complete_scope','unknown'
                    ) THEN RETURN false; END IF;
                IF NOT public.dc_dpe_utc_timestamp(item->>'observed_at')
                    OR NOT public.dc_dpe_utc_timestamp(item->>'last_seen_at')
                THEN RETURN false; END IF;
                IF (item->>'last_seen_at')::timestamptz >
                    (item->>'observed_at')::timestamptz THEN RETURN false; END IF;
                pair := (item->>'source_slug') || ':' || (item->>'department');
                IF pair COLLATE "C" <= previous COLLATE "C" THEN RETURN false; END IF;
                previous := pair;
            END LOOP;
            RETURN true;
        END $$;
    """


# Count, over every top-level table whose CHECK calls the function, the rows
# observed outside 44/85; refuse the downgrade when any. Partitions are read
# through their parent, never twice.
_REFUSE_OUTSIDE_091 = r"""
    DO $$
    DECLARE relation regclass; found bigint; total bigint := 0;
    BEGIN
        FOR relation IN
            SELECT DISTINCT con.conrelid::regclass
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            WHERE con.contype = 'c' AND NOT rel.relispartition
              AND pg_get_constraintdef(con.oid) LIKE '%dc_dpe_observations_valid(%'
        LOOP
            EXECUTE format(
                'SELECT count(*) FROM %s t WHERE EXISTS (SELECT 1 FROM '
                'jsonb_array_elements(t.source_observations) o '
                'WHERE o->>''department'' NOT IN (''44'', ''85''))',
                relation
            ) INTO found;
            total := total + found;
        END LOOP;
        IF total > 0 THEN
            RAISE EXCEPTION
                'downgrade of 101_dpe_scope_national refused: % DPE rows '
                'observed outside 44/85; withdraw them (44/85 reimport) first',
                total;
        END IF;
    END $$;
"""


def upgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    op.execute(_observations_valid(DEPARTEMENTS_HEBERGEABLES))


def downgrade() -> None:
    op.execute(LOCK_TIMEOUT)
    op.execute(_REFUSE_OUTSIDE_091)
    op.execute(_observations_valid(DEPARTEMENTS_091))
