"""DPE beyond 44/85, proven on a disposable PostGIS base (plan 04.1-07).

ROADMAP 4.1 criterion 4, D-122: a DPE import takes every hostable departement
(``DEPARTEMENTS_HEBERGEABLES``, DROM included, overseas collectivities never).

1. Migrated to ``101_dpe_scope_national`` (pinned: later revisions such as
   102 do not touch DPE), the observation constraint
   accepts the 101 hostable codes and still refuses ``99``, ``975`` and the
   empty string: a closed list was widened, no free pattern opened.
2. A synthetic import of {44, 85, 971} (local paginated API, official shape)
   is published; the 971 document carries its 971 observation and lies in
   Guadeloupe (source coordinates in the territory's own projection). A second
   generation reconciles against the previous 971 rows: the withdrawn document
   becomes a tombstone, the other stays present.
3. The downgrade of 101 refuses, naming the count, while any row of any table
   carrying the constraint is observed outside 44/85; once none is left, it
   gives the 44/85 constraint back.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_dpe_scope_national.py
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.config import settings
from app.etl.runner import PipelineRunner
from app.etl_dpe import config as dpe_config
from app.etl_dpe.pipeline import DpePipeline
from app.etl_dpe.sources import DPE_SOURCES
from app.scope.model import DEPARTEMENTS_HEBERGEABLES
from tests.integration.test_dpe_source_staging import RAW_ADDRESS_MIGRATION, _migrate

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
HEAD = "101_dpe_scope_national"  # this module's revision, not the script head
PREVIOUS = "100_dpe_source_columns"
SNAPSHOT = "2026-10-13T00:00:00Z"
PROBE_TABLE = "diagnostics_dpe_downgrade_probe"
_REAL_ASYNC_CLIENT = httpx.AsyncClient

# Source coordinates as ADEME publishes them (measured 2026-10-13): Lambert 93
# in metropolitan France, RGAF09 / UTM 20N in Guadeloupe (97107, Basse-Terre).
NANTES = (354780.58, 6690061.58)
LA_ROCHE = (338000.0, 6629000.0)
BASSE_TERRE = (653431.02, 1774592.38)


def _alembic(dsn: str, action: str, revision: str) -> None:
    """``alembic upgrade|downgrade`` in process, on *dsn* only."""
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", dsn)
        context.setattr(settings, "database_url", dsn)
        cfg = Config()
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", dsn)
        getattr(command, action)(cfg, revision)


def _revision(dsn: str) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    _alembic(isolated_pg_dsn, "upgrade", HEAD)
    return isolated_pg_dsn


@pytest.fixture
def raw_address_column(migrated):
    """Migration 104 alone around an import: the DPE import fills its column
    (plan 04.1-13). The module otherwise stays at its own revision (101)."""
    engine = create_engine(migrated.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with engine.connect() as connection:
            _migrate(connection, name=RAW_ADDRESS_MIGRATION)
            connection.commit()
        yield
        with engine.connect() as connection:
            _migrate(connection, "downgrade", name=RAW_ADDRESS_MIGRATION)
            connection.commit()
    finally:
        engine.dispose()


def _observation(department: str, state: str = "present") -> dict:
    return {
        "source_slug": "dpe03existant",
        "department": department,
        "source_version": "a" * 64,
        "observed_at": SNAPSHOT,
        "last_seen_at": SNAPSHOT,
        "state": state,
        "import_id": "probe-generation",
    }


def _valid(conn, department: str) -> bool:
    return conn.execute(
        "SELECT public.dc_dpe_observations_valid(%s::jsonb)",
        (json.dumps([_observation(department)]),),
    ).fetchone()[0]


def _insert(conn, table: str, row_id: int, department: str, insee: str) -> None:
    conn.execute(
        f"INSERT INTO {table} (id, numero_dpe, code_departement, code_insee, "
        "source_observations, source_state, source_snapshot_at) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, 'present', %s)",
        (
            row_id,
            f"probe-{table}-{row_id}",
            department,
            insee,
            json.dumps([_observation(department)]),
            SNAPSHOT,
        ),
    )


def test_head_constraint_accepts_every_hostable_code_and_refuses_the_rest(migrated):
    script = ScriptDirectory.from_config(_config(migrated))
    assert HEAD in {revision.revision for revision in script.walk_revisions()}
    assert script.get_revision(HEAD).down_revision == PREVIOUS
    assert _revision(migrated) == HEAD
    assert len(DEPARTEMENTS_HEBERGEABLES) == 101
    with psycopg.connect(migrated) as conn:
        refused = [code for code in DEPARTEMENTS_HEBERGEABLES if not _valid(conn, code)]
        assert refused == []
        for code in ("99", "975", "977", "", "2a", "44 ", "9710"):
            assert _valid(conn, code) is False, code
        _insert(conn, "diagnostics_dpe", 9_100_001, "971", "97107")
        conn.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, "diagnostics_dpe", 9_100_002, "975", "97502")
        conn.rollback()
        conn.execute("DELETE FROM diagnostics_dpe WHERE id = 9100001")
        conn.commit()


def _config(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


def _source_row(number: str, department: str, insee: str, point) -> dict:
    row = dict.fromkeys(dpe_config.EXPECTED_COLUMNS, None)
    row.update(
        {
            "numero_dpe": number,
            "code_departement_ban": department,
            "code_insee_ban": insee,
            "coordonnee_cartographique_x_ban": point[0],
            "coordonnee_cartographique_y_ban": point[1],
            "adresse_brut": "3 RUE DES LILAS",
            "version_dpe": "2.6",
            "conso_5_usages_ep": 12345.67,
            "emission_ges_5_usages": 1500,
            "etiquette_dpe": "C",
            "date_reception_dpe": "2026-09-01",
        }
    )
    return row


def _metadata(slug: str) -> dict:
    return {
        "id": DPE_SOURCES[slug].dataset_id,
        "slug": slug,
        "href": DPE_SOURCES[slug].endpoint.removesuffix("/lines"),
        "dataUpdatedAt": "2026-10-07T19:09:24.311Z",
        "schema": [
            {
                "key": key,
                "type": dpe_config.SOURCE_FIELD_TYPES[key],
                **(
                    {"format": dpe_config.SOURCE_FIELD_FORMATS[key]}
                    if key in dpe_config.SOURCE_FIELD_FORMATS
                    else {}
                ),
            }
            for key in dpe_config.EXPECTED_COLUMNS
        ],
        "virtual": {"filters": [{"key": "dpe_desactive", "values": [0]}]},
    }


def _install_source_api(monkeypatch, state: dict) -> list[str]:
    """A local official-shaped API serving ``state['rows']`` per (slug, dept)."""
    requested = []

    def respond(request):
        slug = request.url.path.split("/")[5]
        if not request.url.path.endswith("/lines"):
            return httpx.Response(200, json=_metadata(slug))
        match = re.fullmatch(
            r'code_departement_ban:"([^"]+)"', request.url.params["qs"]
        )
        assert match, request.url.params["qs"]
        department = match.group(1)
        requested.append(f"{slug}:{department}")
        rows = state["rows"].get((slug, department), [])
        if request.url.params.get("select") == "numero_dpe":
            return httpx.Response(200, json={"total": len(rows)})
        return httpx.Response(200, json={"total": len(rows), "results": rows})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _REAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    return requested


def _import(dsn: str, monkeypatch, directory: Path) -> DpePipeline:
    """Download, stage and publish one generation, as the runner does."""
    monkeypatch.setattr(settings, "etl_temp_dir", str(directory))
    pipeline = DpePipeline()
    try:
        files = pipeline.download()
        pipeline.create_staging_table(dsn)
        for path in files:
            PipelineRunner()._process_file(
                pipeline, path, [], logging.getLogger(__name__)
            )
        pipeline.swap()
    finally:
        pipeline.close()
    return pipeline


def _served(dsn: str) -> dict[str, dict]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT numero_dpe, code_departement, source_state, is_tombstone, "
            "source_observations, ST_X(position), ST_Y(position) "
            "FROM diagnostics_dpe WHERE numero_dpe LIKE 'nat-%' ORDER BY numero_dpe"
        ).fetchall()
    return {
        number: {
            "departement": departement,
            "state": state,
            "tombstone": tombstone,
            "observations": observations,
            "point": (lon, lat),
        }
        for number, departement, state, tombstone, observations, lon, lat in rows
    }


def test_import_44_85_971_is_published_and_reconciled_on_971(
    migrated, raw_address_column, monkeypatch, tmp_path, bind_departements
):
    bind_departements(("44", "85", "971"), DpePipeline)
    monkeypatch.setattr(DpePipeline, "get_sync_dsn", staticmethod(lambda: migrated))
    monkeypatch.setattr(DpePipeline, "min_staging_rows_final", property(lambda _: 1))
    first = {
        ("dpe03existant", "44"): [_source_row("nat-44-a", "44", "44109", NANTES)],
        ("dpe02neuf", "44"): [_source_row("nat-44-b", "44", "44109", NANTES)],
        ("dpe03existant", "85"): [_source_row("nat-85-a", "85", "85191", LA_ROCHE)],
        ("dpe02neuf", "85"): [_source_row("nat-85-b", "85", "85191", LA_ROCHE)],
        ("dpe03existant", "971"): [
            _source_row("nat-971-a", "971", "97107", BASSE_TERRE),
            _source_row("nat-971-b", "971", "97107", BASSE_TERRE),
        ],
        # (dpe02neuf, 971) publishes nothing: a verified zero, not a gap.
    }
    state = {"rows": first}
    requested = _install_source_api(monkeypatch, state)

    pipeline = _import(migrated, monkeypatch, tmp_path / "first")

    assert pipeline.department_scope == ["44", "85", "971"]
    assert {entry.pair for entry in pipeline.import_manifest.files} == {
        (slug, code) for slug in DPE_SOURCES for code in ("44", "85", "971")
    }
    assert "dpe02neuf:971" in requested
    served = _served(migrated)
    assert set(served) == {
        "nat-44-a",
        "nat-44-b",
        "nat-85-a",
        "nat-85-b",
        "nat-971-a",
        "nat-971-b",
    }
    guadeloupe = served["nat-971-a"]
    assert guadeloupe["departement"] == "971"
    assert guadeloupe["state"] == "present"
    assert [
        (o["source_slug"], o["department"], o["state"])
        for o in guadeloupe["observations"]
    ] == [("dpe03existant", "971", "present")]
    lon, lat = guadeloupe["point"]
    # Basse-Terre, not the Gulf of Guinea a Lambert 93 reading would give.
    assert -62.0 < lon < -61.0 and 15.8 < lat < 16.6, guadeloupe["point"]
    lon, lat = served["nat-44-a"]["point"]
    assert -2.0 < lon < -1.0 and 47.0 < lat < 47.5, served["nat-44-a"]["point"]

    # Second generation: 971-b withdrawn by the producer, everything else kept.
    second = dict(first)
    second[("dpe03existant", "971")] = [
        _source_row("nat-971-a", "971", "97107", BASSE_TERRE)
    ]
    state["rows"] = second
    _import(migrated, monkeypatch, tmp_path / "second")

    served = _served(migrated)
    kept = served["nat-971-a"]
    assert kept["state"] == "present" and kept["tombstone"] is False
    retired = served["nat-971-b"]
    assert retired["tombstone"] is True
    assert retired["state"] == "absent_from_complete_scope"
    assert [
        (o["source_slug"], o["department"], o["state"]) for o in retired["observations"]
    ] == [("dpe03existant", "971", "absent_from_complete_scope")]
    assert retired["point"] == (None, None)


def _outside_44_85(conn) -> int:
    return conn.execute(
        "SELECT count(*) FROM diagnostics_dpe t WHERE EXISTS ("
        "SELECT 1 FROM jsonb_array_elements(t.source_observations) o "
        "WHERE o->>'department' NOT IN ('44', '85'))"
    ).fetchone()[0]


def test_downgrade_of_101_is_refused_while_rows_lie_outside_44_85(migrated):
    with psycopg.connect(migrated) as conn:
        # Start from rows of 44/85 only in every table carrying the constraint
        # (served, candidate, _old), whatever the previous tests left.
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT con.conrelid::regclass::text FROM pg_constraint con "
                "WHERE con.contype = 'c' AND con.conname = 'ck_dpe_source_observations'"
            )
        ]
        assert "diagnostics_dpe" in tables
        for table in tables:
            conn.execute(
                f"DELETE FROM {table} t WHERE EXISTS ("
                "SELECT 1 FROM jsonb_array_elements(t.source_observations) o "
                "WHERE o->>'department' NOT IN ('44', '85'))"
            )
        conn.execute(
            f"CREATE TABLE {PROBE_TABLE} "
            "(LIKE diagnostics_dpe INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
        )
        _insert(conn, "diagnostics_dpe", 9_200_001, "971", "97107")
        _insert(conn, PROBE_TABLE, 9_200_002, "974", "97411")
        _insert(conn, "diagnostics_dpe", 9_200_003, "44", "44109")
        conn.commit()
        before = conn.execute("SELECT count(*) FROM diagnostics_dpe").fetchone()[0]

    # Every table carrying the constraint is counted: 2 rows outside 44/85.
    with pytest.raises(Exception, match=r"refused: 2 DPE rows") as refused:
        _alembic(migrated, "downgrade", PREVIOUS)
    assert "101_dpe_scope_national" in str(refused.value)
    assert _revision(migrated) == HEAD
    with psycopg.connect(migrated) as conn:
        assert (
            conn.execute("SELECT count(*) FROM diagnostics_dpe").fetchone()[0] == before
        )
        assert _outside_44_85(conn) == 1
        conn.execute("DELETE FROM diagnostics_dpe WHERE id = 9200001")
        conn.commit()

    # The copy alone still blocks: nothing is deleted nor rewritten.
    with pytest.raises(Exception, match=r"refused: 1 DPE rows"):
        _alembic(migrated, "downgrade", PREVIOUS)
    assert _revision(migrated) == HEAD
    with psycopg.connect(migrated) as conn:
        assert conn.execute(f"SELECT count(*) FROM {PROBE_TABLE}").fetchone()[0] == 1
        conn.execute(f"DROP TABLE {PROBE_TABLE}")
        conn.commit()

    _alembic(migrated, "downgrade", PREVIOUS)
    assert _revision(migrated) == PREVIOUS
    with psycopg.connect(migrated) as conn:
        assert _valid(conn, "44") is True
        assert _valid(conn, "971") is False
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, "diagnostics_dpe", 9_200_004, "971", "97107")
        conn.rollback()
        # The 44 row survived the downgrade untouched.
        assert (
            conn.execute(
                "SELECT count(*) FROM diagnostics_dpe WHERE id = 9200003"
            ).fetchone()[0]
            == 1
        )

    _alembic(migrated, "upgrade", HEAD)
    assert _revision(migrated) == HEAD
    with psycopg.connect(migrated) as conn:
        assert _valid(conn, "971") is True
