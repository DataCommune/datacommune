"""HTTP decision matrix for /v1/health (Phase 44-03, HEALTH-01, D-07/D-08).

Only a real technical unavailability (DB/PostGIS) returns 503. An editorial
degradation (delayed source, failed ETL run, aged matview) stays HTTP 200 with
technical_status=available and editorial_status=degraded. All cases run without
a real DB (CI-friendly).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


def _row(mapping: dict):
    return type("Row", (), {"_mapping": mapping})()


def _install_db(
    monkeypatch,
    *,
    dataset_row=None,
    dataset_rows=None,
    matview_row=None,
    raise_postgis=False,
    now=_NOW,
):
    """Override get_db with a fake session dispatching on SQL text."""

    async def _execute(query, *args, **kwargs):
        sql = str(query)
        if "PostGIS_Version" in sql:
            if raise_postgis:
                raise RuntimeError("database failure carrying private SQL parameters")
            r = MagicMock()
            r.scalar = MagicMock(return_value="3.5 USE_GEOS=1")
            return r
        if "matview_metadata" in sql:
            r = MagicMock()
            r.__iter__ = lambda s: iter([matview_row] if matview_row else [])
            return r
        rows = (
            dataset_rows
            if dataset_rows is not None
            else ([dataset_row] if dataset_row else [])
        )
        r = MagicMock()
        r.__iter__ = lambda s: iter(rows)
        return r

    from app.database import get_db
    from app.main import app

    async def _fake_db():
        db = AsyncMock()
        db.execute = _execute
        return db

    app.dependency_overrides[get_db] = _fake_db
    monkeypatch.setattr("app.datasets.freshness._now", lambda: now)
    return app


@pytest.fixture()
def _teardown_overrides():
    yield
    from app.database import get_db
    from app.main import app

    app.dependency_overrides.pop(get_db, None)


class TestHealthDecisionMatrix:
    @pytest.mark.asyncio
    async def test_version_endpoint_exposes_actual_and_expected_schema(
        self, client, monkeypatch
    ):
        from app.database import get_db
        from app.main import app

        result = MagicMock()
        result.scalar_one_or_none.return_value = "actual-123"
        db = AsyncMock()
        db.execute.return_value = result

        async def _fake_db():
            return db

        script = MagicMock()
        script.get_heads.return_value = ["expected-456"]
        app.dependency_overrides[get_db] = _fake_db
        monkeypatch.setattr("app.health.router._load_script_directory", lambda: script)
        try:
            resp = await client.get("/v1/health/version")
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert resp.status_code == 200
        assert resp.json() == {
            "environment": "production",
            "deployment_sha": "unknown",
            "schema_actual": "actual-123",
            "schema_expected": "expected-456",
        }

    @pytest.mark.asyncio
    async def test_nominal_is_available_current_200(
        self, client, monkeypatch, _teardown_overrides
    ):
        fresh_matview = _row(
            {
                "matview_name": "v_commune_synthese",
                "last_refresh_at": _NOW - timedelta(hours=1),
                "last_refresh_status": "success",
                "last_error": None,
                "freshness_threshold_days": 2,
            }
        )
        _install_db(monkeypatch, dataset_row=None, matview_row=fresh_matview)
        resp = await client.get("/v1/health")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["technical_status"] == "available"
        assert data["editorial_status"] == "current"
        assert data["statut"] == "ok"
        assert data["environment"] == "production"
        assert data["deployment_sha"] == "unknown"

    @pytest.mark.asyncio
    async def test_exposes_configured_non_secret_deployment_markers(
        self, client, monkeypatch, _teardown_overrides
    ):
        _install_db(monkeypatch)
        monkeypatch.setattr(
            "app.health.router.settings.app_environment", "preproduction"
        )
        monkeypatch.setattr("app.health.router.settings.deployment_sha", "abc1234")
        resp = await client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json()["environment"] == "preproduction"
        assert resp.json()["deployment_sha"] == "abc1234"

    @pytest.mark.asyncio
    async def test_failed_etl_run_stays_200_degraded(
        self, client, monkeypatch, _teardown_overrides
    ):
        last_success = _NOW - timedelta(hours=4)
        dataset_row = _row(
            {
                "dataset_name": "dvf",
                "last_updated_at": last_success,
                "freshness_threshold_days": 1,
                "cron_schedule": None,
                "last_run_status": "failed",
                "last_run_started_at": _NOW - timedelta(hours=1),
                "last_success_at": last_success,
            }
        )
        _install_db(monkeypatch, dataset_row=dataset_row, matview_row=None)
        resp = await client.get("/v1/health")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["technical_status"] == "available"
        assert data["editorial_status"] == "degraded"
        assert data["statut"] == "degradee"
        # etat_pipeline=echec still visible on the dataset entry
        dvf = next(d for d in data["datasets_status"] if d["nom"] == "dvf")
        assert dvf["etat_pipeline"] == "echec"

    @pytest.mark.asyncio
    async def test_stale_matview_stays_200_degraded(
        self, client, monkeypatch, _teardown_overrides
    ):
        stale_matview = _row(
            {
                "matview_name": "v_commune_synthese",
                "last_refresh_at": _NOW - timedelta(days=5),
                "last_refresh_status": "success",
                "last_error": None,
                "freshness_threshold_days": 2,
            }
        )
        _install_db(monkeypatch, dataset_row=None, matview_row=stale_matview)
        resp = await client.get("/v1/health")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["technical_status"] == "available"
        assert data["editorial_status"] == "degraded"
        assert data["statut"] == "degradee"

    @pytest.mark.asyncio
    async def test_db_failure_is_unavailable_503_without_stacktrace(
        self, client, monkeypatch, _teardown_overrides
    ):
        _install_db(monkeypatch, raise_postgis=True)
        resp = await client.get("/v1/health")
        assert resp.status_code == 503, resp.text
        data = resp.json()
        assert data["technical_status"] == "unavailable"
        assert data["editorial_status"] == "unknown"
        assert data["base_de_donnees"] == "deconnectee"
        # No stack trace / private SQL leaked into the JSON body
        assert data["erreur"] == "database unavailable"
        assert "private SQL parameters" not in resp.text
        assert "Traceback" not in resp.text

    @pytest.mark.asyncio
    async def test_live_probe_unchanged(self, client):
        resp = await client.get("/v1/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"statut": "ok"}

    @pytest.mark.asyncio
    async def test_exact_postgis_version_is_not_exposed(
        self, client, monkeypatch, _teardown_overrides
    ):
        """Audit S5: /v1/health is public — expose the major version, never the build.

        PostGIS_Version() returns e.g. "3.5 USE_GEOS=1 USE_PROJ=1", handing an
        unauthenticated caller the exact version to match against a CVE plus the
        build configuration. The field stays in the contract (backward compat)
        but carries the major version only.
        """
        _install_db(monkeypatch)
        resp = await client.get("/v1/health")
        assert resp.status_code == 200, resp.text
        assert resp.json()["version_postgis"] == "3"
        assert "USE_GEOS" not in resp.text


# State served by production on 2026-11-13 (4dccdba1): metadata rows as stored in
# the public base (threshold and cron of the time of the scheduler), last successes
# of the national imports of late October. Seven datasets were reported "degrade"
# although each had its upstream release imported: five only because a cron date
# passed (no scheduler runs since D-12), two quarantined sources still carrying a
# stored threshold.
_PROD_NOW = datetime(2026, 11, 13, 15, 0, 0, tzinfo=UTC)
_PROD_ROWS = [
    # name, stored threshold, stored cron, last success
    ("antennes", 45, "0 3 5 * *", datetime(2026, 10, 29, 23, 27, tzinfo=UTC)),
    ("ban", 10, "0 2 * * 0", datetime(2026, 10, 29, 19, 59, tzinfo=UTC)),
    ("dpe", 21, "0 3 1,15 * *", datetime(2026, 10, 31, 2, 12, tzinfo=UTC)),
    ("dvf", 270, "0 2 1 5,11 *", datetime(2026, 10, 31, 21, 18, tzinfo=UTC)),
    ("encadrement_loyers", 545, "0 2 1 2 *", None),
    ("permis", 45, "0 3 1 * *", datetime(2026, 10, 30, 19, 48, tzinfo=UTC)),
    ("transports", 45, "0 4 1 * *", datetime(2026, 10, 29, 23, 17, tzinfo=UTC)),
]


def _prod_rows(age_shift: timedelta = timedelta(0)):
    return [
        _row(
            {
                "dataset_name": name,
                "last_updated_at": last,
                "freshness_threshold_days": threshold,
                "cron_schedule": cron,
                "last_run_status": "success" if last else None,
                "last_run_started_at": last,
                "last_success_at": None if last is None else last - age_shift,
            }
        )
        for name, threshold, cron, last in _PROD_ROWS
    ]


class TestManualUpdateFreshness:
    """D-12: updates are manual, no scheduler runs; thresholds are the promise of a
    manual refresh sized on the upstream cadence (app.scheduler.config)."""

    @pytest.mark.asyncio
    async def test_production_state_of_2026_11_13_is_current(
        self, client, monkeypatch, _teardown_overrides
    ):
        _install_db(monkeypatch, dataset_rows=_prod_rows(), now=_PROD_NOW)
        resp = await client.get("/v1/health")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        states = {d["nom"]: d["etat_pipeline"] for d in data["datasets_status"]}
        assert "permis" not in states and "encadrement_loyers" not in states
        assert states == dict.fromkeys(
            ["antennes", "ban", "dpe", "dvf", "transports"], "sain"
        )
        assert data["statut"] == "ok"
        assert data["editorial_status"] == "current"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("name", "days_fresh", "days_stale"),
        [
            ("ban", 44, 46),
            ("dpe", 44, 46),
            ("antennes", 134, 136),
            ("transports", 544, 546),
            ("dvf", 269, 271),
        ],
    )
    async def test_thresholds_still_degrade_a_late_manual_refresh(
        self, client, monkeypatch, _teardown_overrides, name, days_fresh, days_stale
    ):
        for days, expected in ((days_fresh, "sain"), (days_stale, "degrade")):
            row = _row(
                {
                    "dataset_name": name,
                    "last_updated_at": _PROD_NOW,
                    "freshness_threshold_days": 1,  # stored value is not the policy
                    "cron_schedule": "0 2 * * *",
                    "last_run_status": "success",
                    "last_run_started_at": _PROD_NOW - timedelta(days=days),
                    "last_success_at": _PROD_NOW - timedelta(days=days),
                }
            )
            _install_db(monkeypatch, dataset_row=row, now=_PROD_NOW)
            resp = await client.get("/v1/health")
            entry = resp.json()["datasets_status"][0]
            assert entry["etat_pipeline"] == expected, (name, days, entry)
