"""Scoring view without a frozen departement, on a disposable PostGIS base.

Plan 04.1-08 (ROADMAP 4.1 criterion 4). Migration 066 froze the materialized
view ``scoring_renov_target`` on DPE of 44 and 33; migration
``102_scoring_scope_national`` recreates it without any departement filter,
only when it exists.

1. Upgraded to ``102_scoring_scope_national``, a fresh base has the view
   (created by 066, recreated by 102).
2. F/G DPE of 44 and 75 inside synthetic parcels: the view at head holds both
   departements; downgraded to 101 (DDL of 066), 75 is gone; upgraded back,
   75 returns. A D label and a DPE outside any parcel never enter it.
3. Without the view (dropped, as an ETL swap does with CASCADE), downgrade and
   upgrade leave it absent and its freshness metadata untouched.
4. A relation of that name that is not a materialized view is refused, never
   dropped.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_scoring_scope_national.py
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings
from app.scope.model import SCOPE_LOCKS

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
HEAD = "102_scoring_scope_national"  # this module's revision, not the script head
PREVIOUS = "101_dpe_scope_national"
SNAPSHOT = "2026-10-13T00:00:00Z"

# (id, numero, departement, insee, label, lon, lat): two F/G DPE inside a 44
# parcel and a 75 parcel, a D label inside the 44 parcel, an F label outside
# every parcel.
DPE_ROWS = (
    (9_300_001, "probe-44-g", "44", "44109", "G", -1.5536, 47.2184),
    (9_300_002, "probe-75-f", "75", "75056", "F", 2.3522, 48.8566),
    (9_300_003, "probe-44-d", "44", "44109", "D", -1.5537, 47.2185),
    (9_300_004, "probe-75-out", "75", "75056", "F", 2.4000, 48.9000),
)
PARCELS = (
    ("44", "44109000AB0001", "44109", -1.5600, 47.2100, -1.5500, 47.2200),
    ("75", "75056000AB0001", "75056", 2.3500, 48.8500, 2.3600, 48.8600),
)


def _config(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


def _alembic(dsn: str, action: str, revision: str) -> None:
    """``alembic upgrade|downgrade`` in process, on *dsn* only."""
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", dsn)
        context.setattr(settings, "database_url", dsn)
        getattr(command, action)(_config(dsn), revision)


def _revision(dsn: str) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def _relkind(dsn: str) -> str | None:
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT relkind FROM pg_class "
            "WHERE oid = to_regclass('scoring_renov_target')"
        ).fetchone()
    return None if row is None else row[0]


def _scored(dsn: str) -> list[tuple[str, str, str]]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT code_departement, numero_dpe, id_parcelle "
            "FROM scoring_renov_target ORDER BY numero_dpe"
        ).fetchall()


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    _alembic(isolated_pg_dsn, "upgrade", HEAD)
    return isolated_pg_dsn


def test_revision_102_and_the_fresh_view_exists_without_lock(migrated):
    script = ScriptDirectory.from_config(_config(migrated))
    assert HEAD in {revision.revision for revision in script.walk_revisions()}
    assert script.get_revision(HEAD).down_revision == PREVIOUS
    assert _revision(migrated) == HEAD
    assert _relkind(migrated) == "m"
    assert SCOPE_LOCKS == {}


def _seed(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        for department, parcel, commune, xmin, ymin, xmax, ymax in PARCELS:
            conn.execute(
                "INSERT INTO parcelles (code_departement, id_parcelle, commune, "
                "contenance, geometrie) VALUES (%s, %s, %s, 1000, "
                "ST_Multi(ST_MakeEnvelope(%s, %s, %s, %s, 4326)))",
                (department, parcel, commune, xmin, ymin, xmax, ymax),
            )
        for row_id, number, department, insee, label, lon, lat in DPE_ROWS:
            observation = {
                "source_slug": "dpe03existant",
                "department": department,
                "source_version": "a" * 64,
                "observed_at": SNAPSHOT,
                "last_seen_at": SNAPSHOT,
                "state": "present",
                "import_id": "probe-generation",
            }
            conn.execute(
                "INSERT INTO diagnostics_dpe (id, numero_dpe, code_departement, "
                "code_insee, etiquette_dpe, surface_habitable, position, "
                "source_observations, source_state, source_snapshot_at) "
                "VALUES (%s, %s, %s, %s, %s, 70, "
                "ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s::jsonb, 'present', %s)",
                (
                    row_id,
                    number,
                    department,
                    insee,
                    label,
                    lon,
                    lat,
                    json.dumps([observation]),
                    SNAPSHOT,
                ),
            )
        conn.commit()


def test_view_covers_every_hosted_departement_and_066_did_not(migrated):
    _seed(migrated)
    expected_head = [
        ("44", "probe-44-g", "44109000AB0001"),
        ("75", "probe-75-f", "75056000AB0001"),
    ]

    # Recreated with data by the migration itself: no refresh involved.
    _alembic(migrated, "downgrade", PREVIOUS)
    assert _revision(migrated) == PREVIOUS
    assert _relkind(migrated) == "m"
    assert _scored(migrated) == expected_head[:1]  # 066: 44/33 only

    _alembic(migrated, "upgrade", HEAD)
    assert _revision(migrated) == HEAD
    assert _scored(migrated) == expected_head
    with psycopg.connect(migrated) as conn:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'scoring_renov_target'"
            )
        }
        status = conn.execute(
            "SELECT last_refresh_status FROM matview_metadata "
            "WHERE matview_name = 'scoring_renov_target'"
        ).fetchone()
    assert indexes == {
        "idx_scoring_numero_dpe",
        "idx_scoring_position",
        "idx_scoring_code_departement",
        "idx_scoring_etiquette_dpe",
        "idx_scoring_commune",
    }
    assert status == ("success",)


def test_absent_view_is_left_absent_both_ways(migrated):
    with psycopg.connect(migrated) as conn:
        conn.execute("DROP MATERIALIZED VIEW scoring_renov_target CASCADE")
        conn.execute(
            "UPDATE matview_metadata SET last_error = 'probe-untouched' "
            "WHERE matview_name = 'scoring_renov_target'"
        )
        conn.commit()

    _alembic(migrated, "downgrade", PREVIOUS)
    assert _revision(migrated) == PREVIOUS
    assert _relkind(migrated) is None
    _alembic(migrated, "upgrade", HEAD)
    assert _revision(migrated) == HEAD
    assert _relkind(migrated) is None
    with psycopg.connect(migrated) as conn:
        assert conn.execute(
            "SELECT last_error FROM matview_metadata "
            "WHERE matview_name = 'scoring_renov_target'"
        ).fetchone() == ("probe-untouched",)


def test_a_relation_that_is_not_the_view_is_refused_never_dropped(migrated):
    _alembic(migrated, "downgrade", PREVIOUS)
    with psycopg.connect(migrated) as conn:
        conn.execute("DROP MATERIALIZED VIEW IF EXISTS scoring_renov_target")
        conn.execute("CREATE TABLE scoring_renov_target (probe integer)")
        conn.execute("INSERT INTO scoring_renov_target VALUES (1)")
        conn.commit()

    with pytest.raises(Exception, match="not a materialized view"):
        _alembic(migrated, "upgrade", HEAD)
    assert _revision(migrated) == PREVIOUS
    with psycopg.connect(migrated) as conn:
        assert conn.execute("SELECT probe FROM scoring_renov_target").fetchall() == [
            (1,)
        ]
        conn.execute("DROP TABLE scoring_renov_target")
        conn.commit()

    _alembic(migrated, "upgrade", HEAD)
    assert _revision(migrated) == HEAD
    assert _relkind(migrated) is None
