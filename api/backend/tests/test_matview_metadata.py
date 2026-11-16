"""Unit tests for matview_metadata scheduler writer + health reader (review #1 #14).

Tests verify that:
- refresh_commune_synthese_view writes last_refresh_at + status='success' on success
- refresh_commune_synthese_view writes status='failed' + last_error on failure
- refresh_communes_lookup has the same write behavior
- /v1/health matviews block returns non-null derniere_maj from matview_metadata
- /v1/health degrades when a matview is older than freshness_threshold_days

All tests run without a real DB (CI-friendly, uses monkeypatching).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.commune.matview_ddl import CREATE_SQL, INTERMEDIATE_MATVIEWS
from app.scheduler.jobs import (
    initialize_matview_metadata,
    refresh_commune_synthese_view,
    refresh_communes_lookup,
    refresh_scoring_renov_target,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Commune synthese DDL regression guards
# ---------------------------------------------------------------------------


def test_commune_dvf_reads_mutations_grain_already_qualified() -> None:
    """D-14 (Phase 57): v_commune_dvf reads mutations_grain, filters AFTER
    qualification -- never the pre-grouping filter that caused the
    Pornichet bug (a mixed mutation losing its commercial lines while
    keeping its full sale price)."""
    dvf_sql = dict(INTERMEDIATE_MATVIEWS)["v_commune_dvf"]

    assert "FROM mutations_grain" in dvf_sql
    assert "FROM mutations\n" not in dvf_sql
    assert "mutation_level" not in dvf_sql
    assert "code_type_local" not in dvf_sql
    assert "ORDER BY valeur_fonciere / surface_bati_totale" in dvf_sql
    assert "GROUP BY code_commune" in dvf_sql


def test_commune_dvf_distinguishes_maison_et_appartement() -> None:
    """D-14: the matview publishes a maison/appartement breakdown, not just
    the pooled median."""
    dvf_sql = dict(INTERMEDIATE_MATVIEWS)["v_commune_dvf"]

    assert "prix_m2_median_maison" in dvf_sql
    assert "nb_mutations_12m_maison" in dvf_sql
    assert "prix_m2_median_appartement" in dvf_sql
    assert "nb_mutations_12m_appartement" in dvf_sql


def test_commune_dvf_excludes_multi_commune_mutations() -> None:
    """A deterministic MIN(code_commune) is not proof of attribution.

    Multi-commune mutations stay in history but must not influence a
    commune's published price statistics.
    """
    dvf_sql = dict(INTERMEDIATE_MATVIEWS)["v_commune_dvf"]

    assert "AND nb_communes = 1" in dvf_sql


def test_commune_dvf_hides_median_below_seuil_nd() -> None:
    """C-01: SEUIL_ND_COMMUNE is imported from app.dvf.couverture (single
    source of truth), never a literal recopied in the matview SQL."""
    from app.dvf.couverture import SEUIL_ND_COMMUNE

    dvf_sql = dict(INTERMEDIATE_MATVIEWS)["v_commune_dvf"]

    assert f">= {SEUIL_ND_COMMUNE}" in dvf_sql
    assert "CASE WHEN count(*)" in dvf_sql


def test_commune_synthese_absent_source_groups_do_not_invent_zero() -> None:
    """COR-14: absence is unknown for every count, including existing DVF guards."""
    assert "dvf.nb_mutations_12m::int AS nb_mutations_12m" in CREATE_SQL
    assert "mon.nb_monuments::int AS nb_monuments" in CREATE_SQL
    assert "fpm.nb_proprietaires_pm::int AS nb_proprietaires_pm" in CREATE_SQL
    for prefix in ("eq", "sc", "mon", "fpm", "dvf"):
        assert f"COALESCE({prefix}." not in CREATE_SQL
    assert "dvf.nb_mutations_12m_maison::int AS nb_mutations_12m_maison" in CREATE_SQL
    assert "dvf.nb_mutations_12m_appartement::int" in CREATE_SQL


def test_commune_synthese_select_and_group_by_stay_consistent_on_dvf_fields() -> None:
    """The DVF breakdown fields must appear in both SELECT and GROUP BY (the
    most frequent bug class on this file, per module docstring)."""
    for field in (
        "dvf.prix_m2_median_maison",
        "dvf.nb_mutations_12m_maison",
        "dvf.prix_m2_median_appartement",
        "dvf.nb_mutations_12m_appartement",
    ):
        assert field in CREATE_SQL
    group_by_clause = CREATE_SQL.split("GROUP BY")[1]
    assert "dvf.prix_m2_median_maison" in group_by_clause
    assert "dvf.nb_mutations_12m_maison" in group_by_clause
    assert "dvf.prix_m2_median_appartement" in group_by_clause
    assert "dvf.nb_mutations_12m_appartement" in group_by_clause


# ---------------------------------------------------------------------------
# Fixtures shared by scheduler tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def _mock_psycopg_conn(monkeypatch):
    """Provide a fake psycopg connection that captures execute() calls.

    The context manager yields a MagicMock that has .execute() and .commit()
    as MagicMock. Tests inspect .execute.call_args_list to verify SQL.
    """
    fake_conn = MagicMock()
    fake_conn.__enter__ = MagicMock(return_value=fake_conn)
    fake_conn.__exit__ = MagicMock(return_value=False)

    monkeypatch.setattr(
        "app.scheduler.jobs.psycopg.connect",
        MagicMock(return_value=fake_conn),
    )
    monkeypatch.setattr(
        "app.scheduler.jobs.settings",
        MagicMock(database_url="postgresql+asyncpg://user:pw@localhost/test_db"),
    )
    return fake_conn


# ---------------------------------------------------------------------------
# refresh_commune_synthese_view — success path
# ---------------------------------------------------------------------------


class TestRefreshCommuneSyntheseSuccess:
    def test_writes_success_status_to_metadata(self, _mock_psycopg_conn):
        """On success, matview_metadata must be updated with status='success'."""
        refresh_commune_synthese_view()

        all_calls = _mock_psycopg_conn.execute.call_args_list
        sql_calls = [str(c.args[0]) for c in all_calls if c.args]

        # Verify the matview was rebuilt via DROP + CREATE (not REFRESH —
        # REFRESH cannot re-bind after the ETL rename swap; see
        # app.commune.matview_ddl).
        assert any(
            "DROP MATERIALIZED VIEW IF EXISTS v_commune_synthese" in sql
            for sql in sql_calls
        ), "DROP MATERIALIZED VIEW not called"
        assert any(
            "CREATE MATERIALIZED VIEW v_commune_synthese" in sql for sql in sql_calls
        ), "CREATE MATERIALIZED VIEW not called"

        # Verify the metadata UPDATE with success was called
        success_update = [
            sql
            for sql in sql_calls
            if "UPDATE matview_metadata" in sql
            and "last_refresh_status = 'success'" in sql
        ]
        assert success_update, (
            "Expected UPDATE matview_metadata SET last_refresh_status='success'; "
            f"got sql calls: {sql_calls}"
        )

    def test_commit_called_on_success(self, _mock_psycopg_conn):
        """conn.commit() must be called after a successful refresh."""
        refresh_commune_synthese_view()
        _mock_psycopg_conn.commit.assert_called_once()

    def test_no_exception_raised_on_success(self, _mock_psycopg_conn):
        """Successful refresh must not propagate any exception."""
        refresh_commune_synthese_view()  # no raise


# ---------------------------------------------------------------------------
# refresh_commune_synthese_view — failure path
# ---------------------------------------------------------------------------


class TestRefreshCommuneSyntheseFailure:
    @pytest.fixture()
    def _conn_raises(self, monkeypatch):
        """Rebuild and failure metadata share the session holding exclusion."""
        refresh_conn = MagicMock()
        refresh_conn.__enter__ = MagicMock(return_value=refresh_conn)
        refresh_conn.__exit__ = MagicMock(return_value=False)

        # Make execute raise on the DROP (first statement of the rebuild)
        def raise_on_rebuild(sql, *args, **kwargs):
            if "DROP MATERIALIZED VIEW" in str(sql):
                raise RuntimeError("boom: pg connection lost")
            return MagicMock()

        refresh_conn.execute.side_effect = raise_on_rebuild

        monkeypatch.setattr(
            "app.scheduler.jobs.psycopg.connect",
            MagicMock(return_value=refresh_conn),
        )
        monkeypatch.setattr(
            "app.scheduler.jobs.settings",
            MagicMock(database_url="postgresql+asyncpg://user:pw@localhost/test_db"),
        )
        return refresh_conn, refresh_conn

    def test_failure_path_writes_failed_status(self, _conn_raises):
        """When the rebuild fails, matview_metadata must record status='failed'."""
        _, meta_conn = _conn_raises
        with pytest.raises(RuntimeError, match="boom"):
            refresh_commune_synthese_view()

        meta_conn.rollback.assert_called_once()

        meta_sql_calls = [
            str(c.args[0]) for c in meta_conn.execute.call_args_list if c.args
        ]
        failure_updates = [
            sql
            for sql in meta_sql_calls
            if "UPDATE matview_metadata" in sql
            and "last_refresh_status = 'failed'" in sql
        ]
        assert failure_updates, (
            "Expected UPDATE matview_metadata SET last_refresh_status='failed' "
            f"on error; got: {meta_sql_calls}"
        )

    def test_failure_reraises_exception(self, _conn_raises):
        """Exception must be re-raised so the scheduler sees the failure."""
        with pytest.raises(RuntimeError, match="boom"):
            refresh_commune_synthese_view()

    def test_failure_records_error_message(self, _conn_raises):
        """last_error must contain the exception message (truncated to 500 chars)."""
        _, meta_conn = _conn_raises
        with pytest.raises(RuntimeError):
            refresh_commune_synthese_view()

        # Find the execute call that writes the error
        for c in meta_conn.execute.call_args_list:
            if c.args and "last_error" in str(c.args[0]):
                params = c.args[1] if len(c.args) > 1 else ()
                # params should be (error_str, matview_name)
                if params:
                    assert "boom" in params[0], (
                        f"last_error should contain 'boom'; got {params[0]!r}"
                    )
                return
        pytest.fail("No execute call found that writes last_error to matview_metadata")


# ---------------------------------------------------------------------------
# refresh_communes_lookup — smoke tests (same behavior as commune_synthese)
# ---------------------------------------------------------------------------


class TestRefreshCommunesLookupBasic:
    def test_writes_success_to_metadata(self, _mock_psycopg_conn):
        """communes_lookup refresh writes status='success' to matview_metadata."""
        refresh_communes_lookup()
        all_calls = _mock_psycopg_conn.execute.call_args_list
        sql_calls = [str(c.args[0]) for c in all_calls if c.args]
        assert any(
            "DROP MATERIALIZED VIEW IF EXISTS communes_lookup" in sql
            for sql in sql_calls
        ), "Expected DROP before rebuilding communes_lookup"
        assert any(
            "CREATE MATERIALIZED VIEW communes_lookup" in sql for sql in sql_calls
        ), "Expected CREATE for communes_lookup"
        success_updates = [
            sql
            for sql in sql_calls
            if "UPDATE matview_metadata" in sql
            and "last_refresh_status = 'success'" in sql
        ]
        assert success_updates, "Expected success metadata update for communes_lookup"


class TestRefreshScoringRenovBasic:
    def test_quarantine_never_rebuilds_or_writes_success(self, _mock_psycopg_conn):
        from app.common.unqualified_surfaces import SurfaceQuarantinedError

        with pytest.raises(SurfaceQuarantinedError):
            refresh_scoring_renov_target()
        _mock_psycopg_conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Startup initialization
# ---------------------------------------------------------------------------


def test_startup_initializes_only_matviews_without_refresh_timestamp(monkeypatch):
    query_conn = MagicMock()
    query_conn.__enter__ = MagicMock(return_value=query_conn)
    query_conn.__exit__ = MagicMock(return_value=False)
    query_conn.execute.return_value.fetchall.return_value = [
        ("communes_lookup",),
        ("scoring_renov_target",),
        ("v_commune_synthese",),
    ]
    refresh_lookup = MagicMock()
    refresh_scoring = MagicMock()
    refresh_synthese = MagicMock()

    monkeypatch.setattr(
        "app.scheduler.jobs.psycopg.connect",
        MagicMock(return_value=query_conn),
    )
    monkeypatch.setattr(
        "app.scheduler.jobs.settings",
        MagicMock(database_url="postgresql+asyncpg://user:pw@localhost/test_db"),
    )
    monkeypatch.setattr(
        "app.scheduler.jobs.refresh_communes_lookup",
        refresh_lookup,
    )
    monkeypatch.setattr(
        "app.scheduler.jobs.refresh_scoring_renov_target",
        refresh_scoring,
    )
    monkeypatch.setattr(
        "app.scheduler.jobs.refresh_commune_synthese_view",
        refresh_synthese,
    )

    initialize_matview_metadata()

    refresh_lookup.assert_called_once_with()
    refresh_scoring.assert_not_called()
    refresh_synthese.assert_called_once_with()
    sql = str(query_conn.execute.call_args.args[0])
    assert "WHERE last_refresh_at IS NULL" in sql


# ---------------------------------------------------------------------------
# /v1/health — matviews block uses matview_metadata
# ---------------------------------------------------------------------------


class TestHealthMatviewsBlock:
    """Verify /v1/health reads matview_metadata, not pg_matviews."""

    @pytest.fixture()
    def _mock_health_fresh_matview(self, monkeypatch):
        """Return a fresh matview from matview_metadata (1h old, threshold 2d)."""
        matview_row = type(
            "Row",
            (),
            {
                "_mapping": {
                    "matview_name": "v_commune_synthese",
                    "last_refresh_at": _NOW - timedelta(hours=1),
                    "last_refresh_status": "success",
                    "last_error": None,
                    "freshness_threshold_days": 2,
                }
            },
        )()

        async def _execute(query, *args, **kwargs):
            sql = str(query)
            if "PostGIS_Version" in sql:
                r = MagicMock()
                r.scalar = MagicMock(return_value="3.4 USE_GEOS=1")
                return r
            if "matview_metadata" in sql:
                r = MagicMock()
                r.__iter__ = lambda s: iter([matview_row])
                return r
            # Default: no datasets (to isolate matviews check)
            r = MagicMock()
            r.__iter__ = lambda s: iter([])
            return r

        from app.database import get_db
        from app.main import app

        async def _fake_db():
            db = AsyncMock()
            db.execute = _execute
            return db

        app.dependency_overrides[get_db] = _fake_db
        yield
        app.dependency_overrides.pop(get_db, None)

    @pytest.fixture()
    def _mock_health_stale_matview(self, monkeypatch):
        """Return a stale matview (5 days old, threshold 2 days)."""
        matview_row = type(
            "Row",
            (),
            {
                "_mapping": {
                    "matview_name": "v_commune_synthese",
                    "last_refresh_at": _NOW - timedelta(days=5),
                    "last_refresh_status": "success",
                    "last_error": None,
                    "freshness_threshold_days": 2,
                }
            },
        )()

        async def _execute(query, *args, **kwargs):
            sql = str(query)
            if "PostGIS_Version" in sql:
                r = MagicMock()
                r.scalar = MagicMock(return_value="3.4 USE_GEOS=1")
                return r
            if "matview_metadata" in sql:
                r = MagicMock()
                r.__iter__ = lambda s: iter([matview_row])
                return r
            r = MagicMock()
            r.__iter__ = lambda s: iter([])
            return r

        from app.database import get_db
        from app.main import app

        async def _fake_db():
            db = AsyncMock()
            db.execute = _execute
            return db

        app.dependency_overrides[get_db] = _fake_db
        yield
        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.asyncio
    async def test_health_matviews_block_has_real_derniere_maj(
        self, client, _mock_health_fresh_matview
    ):
        """matviews[].derniere_maj must not be null (real timestamp from metadata)."""
        resp = await client.get("/v1/health")
        # Phase 44-03 (D-07): a fresh matview is nominal → exactly HTTP 200.
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("matviews"), "Expected matviews list in health response"
        for mv in data["matviews"]:
            assert mv["derniere_maj"] is not None, (
                f"matview {mv['nom']} has null derniere_maj — "
                "should read from matview_metadata"
            )

    @pytest.mark.asyncio
    async def test_health_degrades_on_stale_matview(
        self, client, _mock_health_stale_matview
    ):
        """A matview older than freshness_threshold_days must degrade global statut."""
        resp = await client.get("/v1/health")
        # Phase 44-03 (D-07): an aged matview is editorial → HTTP 200, degraded.
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["statut"] == "degradee", (
            f"Expected 'degradee' when matview is 5d old (threshold 2d); "
            f"got {data['statut']!r}"
        )
        assert data["technical_status"] == "available"
        assert data["editorial_status"] == "degraded"
