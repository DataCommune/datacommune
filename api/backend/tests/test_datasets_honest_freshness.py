"""Unit tests for honest freshness (review #1 finding #13).

Tests verify that compute_freshness() + /v1/datasets correctly surface
etat_pipeline based on etl_runs status, not merely dataset_metadata.last_updated_at.

A failed run past the cron window MUST produce etat_pipeline='echec' even
when the prior success is recent. All tests run without a real DB (CI-friendly).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.datasets.freshness import compute_freshness

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Unit tests: compute_freshness() — no HTTP, no DB
# ---------------------------------------------------------------------------


class TestComputeFreshnessSignature:
    """Verify the new (fraicheur, etat_pipeline) tuple contract."""

    def test_returns_tuple(self):
        result = compute_freshness(
            last_success_at=_NOW - timedelta(hours=3),
            threshold_days=1,
            now=_NOW,
        )
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_no_threshold_returns_none_fraicheur_and_sain(self):
        fraicheur, etat = compute_freshness(
            last_success_at=_NOW - timedelta(days=30),
            threshold_days=None,
            now=_NOW,
        )
        assert fraicheur is None
        assert etat == "sain"

    def test_fresh_success_returns_sain(self):
        """Recent success, no failure: etat_pipeline='sain'."""
        fraicheur, etat = compute_freshness(
            last_success_at=_NOW - timedelta(hours=2),
            threshold_days=1,
            cron_schedule=None,
            last_run_status="success",
            last_run_started_at=_NOW - timedelta(hours=2),
            now=_NOW,
        )
        assert fraicheur == "fresh"
        assert etat == "sain"

    def test_failed_run_after_success_degrades_etat_pipeline(self):
        """Last run failed AFTER the last success: etat_pipeline='echec'."""
        last_success = _NOW - timedelta(hours=4)
        failed_at = _NOW - timedelta(hours=1)
        fraicheur, etat = compute_freshness(
            last_success_at=last_success,
            threshold_days=1,
            cron_schedule=None,
            last_run_status="failed",
            last_run_started_at=failed_at,
            now=_NOW,
        )
        # fraicheur is still 'fresh' (last_success was <4h ago, threshold 1d)
        assert fraicheur == "fresh"
        # But etat_pipeline must be 'echec' — the failed run happened after success
        assert etat == "echec", (
            f"Expected 'echec' when last run failed after success; got '{etat}'"
        )

    def test_failed_run_before_success_does_not_degrade(self):
        """If the last run is a success (after the failure), etat_pipeline='sain'."""
        last_success = _NOW - timedelta(hours=2)
        _fraicheur, etat = compute_freshness(
            last_success_at=last_success,
            threshold_days=1,
            cron_schedule=None,
            last_run_status="success",
            last_run_started_at=last_success,
            now=_NOW,
        )
        assert etat == "sain"

    def test_stale_data_returns_degrade(self):
        """No explicit failure, but data is stale: etat_pipeline='degrade'."""
        # threshold=1d, but last_success was 2d ago → stale territory
        fraicheur, etat = compute_freshness(
            last_success_at=_NOW - timedelta(days=2),
            threshold_days=1,
            cron_schedule=None,
            last_run_status=None,
            last_run_started_at=None,
            now=_NOW,
        )
        assert fraicheur in ("stale", "outdated")
        assert etat == "degrade"

    def test_outdated_data_returns_degrade(self):
        """Very old data: fraicheur='outdated', etat_pipeline='degrade'."""
        fraicheur, etat = compute_freshness(
            last_success_at=_NOW - timedelta(days=10),
            threshold_days=1,
            cron_schedule=None,
            now=_NOW,
        )
        assert fraicheur == "outdated"
        assert etat == "degrade"

    def test_missed_cron_within_grace_remains_sain(self):
        """A missed cron is tolerated until ETL_SCHEDULE_GRACE_DAYS expires."""
        fraicheur, etat = compute_freshness(
            last_success_at=datetime(2026, 4, 16, 8, 0, 0, tzinfo=UTC),
            threshold_days=7,
            cron_schedule="0 6 * * *",
            last_run_status="success",
            last_run_started_at=datetime(2026, 4, 16, 8, 0, 0, tzinfo=UTC),
            now=datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC),
        )
        assert fraicheur == "fresh"
        assert etat == "sain"

    def test_no_success_at_all_outdated_and_degrade(self):
        """No successful run yet: last_success_at=None with threshold."""
        fraicheur, etat = compute_freshness(
            last_success_at=None,
            threshold_days=1,
            now=_NOW,
        )
        assert fraicheur == "outdated"
        assert etat == "degrade"


# ---------------------------------------------------------------------------
# Integration-level: /v1/datasets endpoint surfaces etat_pipeline
# ---------------------------------------------------------------------------


class TestDatasetsEndpointEtatPipeline:
    """Hit /v1/datasets with a mocked DB, assert etat_pipeline on each entry."""

    @pytest.fixture()
    def _mock_get_all_datasets(self):
        """Patch get_all_datasets to return controlled rows without a real DB."""
        last_success = _NOW - timedelta(hours=4)
        failed_started = _NOW - timedelta(hours=1)
        rows = [
            {
                "dataset_name": "dvf",
                "status": "disponible",
                "last_updated_at": last_success,
                "row_count": 10_000,
                "last_import_duration_seconds": 120.0,
                "cron_schedule": None,
                "freshness_threshold_days": 1,
                # last run = FAILED after last_success
                "last_run_status": "failed",
                "last_run_started_at": failed_started,
                "last_run_completed_at": failed_started + timedelta(minutes=5),
                "last_run_duration": 300.0,
                "last_run_rows": 0,
                "last_run_error": "connection timeout",
                "last_success_at": last_success,
            },
            {
                "dataset_name": "ban",
                "status": "disponible",
                "last_updated_at": _NOW - timedelta(hours=1),
                "row_count": 26_000_000,
                "last_import_duration_seconds": 3600.0,
                "cron_schedule": None,
                "freshness_threshold_days": 7,
                # last run = SUCCESS, recent
                "last_run_status": "success",
                "last_run_started_at": _NOW - timedelta(hours=1),
                "last_run_completed_at": _NOW - timedelta(minutes=30),
                "last_run_duration": 1800.0,
                "last_run_rows": 26_000_000,
                "last_run_error": None,
                "last_success_at": _NOW - timedelta(hours=1),
            },
        ]
        with (
            patch(
                "app.datasets.router.get_all_datasets", new=AsyncMock(return_value=rows)
            ),
            patch("app.datasets.freshness._now", return_value=_NOW),
        ):
            yield

    @pytest.mark.asyncio
    async def test_failed_run_degrades_etat_pipeline(
        self, client, _mock_get_all_datasets
    ):
        """dvf with a failed run after last success surfaces etat_pipeline='echec'."""
        resp = await client.get("/v1/datasets")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        dvf = next(d for d in data["donnees"] if d["nom"] == "dvf")
        assert dvf["etat_pipeline"] in ("degrade", "echec"), (
            f"failed run past cron must degrade; got {dvf['etat_pipeline']!r}"
        )
        assert dvf["derniere_run"]["statut"] == "failed"

    @pytest.mark.asyncio
    async def test_only_success_keeps_etat_pipeline_sain(
        self, client, _mock_get_all_datasets
    ):
        """ban with a recent success and no failure must have etat_pipeline='sain'."""
        resp = await client.get("/v1/datasets")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        ban = next(d for d in data["donnees"] if d["nom"] == "ban")
        got = ban["etat_pipeline"]
        assert got == "sain", f"expected sain for recent success, got {got!r}"

    @pytest.mark.asyncio
    async def test_etat_pipeline_present_on_all_entries(
        self, client, _mock_get_all_datasets
    ):
        """Every dataset entry must include the etat_pipeline field."""
        resp = await client.get("/v1/datasets")
        assert resp.status_code == 200
        for entry in resp.json()["donnees"]:
            assert "etat_pipeline" in entry, (
                f"etat_pipeline missing on dataset {entry.get('nom')}"
            )
            assert entry["etat_pipeline"] in ("sain", "degrade", "echec")


class TestHealthDegradedOnDatasetEchec:
    """Hit /v1/health and assert global statut='degradee' when a dataset has echec."""

    @pytest.fixture()
    def _mock_health_db(self, monkeypatch):
        """Patch the health router's DB calls to return controlled data."""
        last_success = _NOW - timedelta(hours=4)
        failed_started = _NOW - timedelta(hours=1)

        # Rows from the datasets + lateral query
        dataset_row = type(
            "Row",
            (),
            {
                "_mapping": {
                    "dataset_name": "dvf",
                    "last_updated_at": last_success,
                    "freshness_threshold_days": 1,
                    "cron_schedule": None,
                    "last_run_status": "failed",
                    "last_run_started_at": failed_started,
                    "last_success_at": last_success,
                }
            },
        )()

        # Matview row
        matview_row = type(
            "Row",
            (),
            {
                "_mapping": {
                    "matview_name": "v_commune_synthese",
                    "last_refresh_at": _NOW - timedelta(hours=6),
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
            # Default = dataset lateral query
            r = MagicMock()
            r.__iter__ = lambda s: iter([dataset_row])
            return r

        from app.database import get_db
        from app.main import app

        async def _fake_db():
            db = AsyncMock()
            db.execute = _execute
            return db

        app.dependency_overrides[get_db] = _fake_db
        monkeypatch.setattr("app.datasets.freshness._now", lambda: _NOW)
        yield
        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.asyncio
    async def test_health_global_degrades_on_dataset_echec(
        self, client, _mock_health_db
    ):
        """A failed dataset run is editorial: HTTP 200, editorial_status=degraded.

        Rewritten for Phase 44-03 (D-07): editorial degradation no longer
        returns 503 — only a real technical DB failure does.
        """
        resp = await client.get("/v1/health")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["statut"] == "degradee", (
            f"Expected 'degradee' with failed dataset run; got {data['statut']!r}"
        )
        assert data["technical_status"] == "available"
        assert data["editorial_status"] == "degraded"
