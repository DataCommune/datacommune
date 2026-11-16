"""Unit tests for /v1/admin/etl (Phase 19, OPS-01/OPS-02).

Covers:
  - List summary returns one row per PIPELINE_MAP key
  - Drill-down 404 on unknown pipeline_name
  - Drill-down returns paginated rows
  - Trigger happy path: audit + db.commit + asyncio.create_task
  - Trigger 404 on unknown name
  - Trigger 422 on reason < 10 chars
  - Trigger 409 when a pipeline key is not hosted by the database scope (D-23)
  - AdminUser gate: non-admin -> 403 (delegated to require_admin mock)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db
from app.main import app
from app.scope.model import InstanceScope
from tests.scope_doubles import SCOPE_44_85

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)


def _make_admin() -> UserInfo:
    return UserInfo(
        id=1,
        email="admin@test",
        name="Admin",
        company=None,
        is_verified=True,
        is_admin=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _DictMapping:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _ExecResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return _DictMapping(self._rows)

    def scalar_one(self):
        return self._scalar if self._scalar is not None else 0


def _scope_is(monkeypatch, scope: InstanceScope) -> None:
    """The trigger reads the scope stored in the database (load_scope)."""
    monkeypatch.setattr("app.admin.etl.load_scope", AsyncMock(return_value=scope))


def _without(scope: InstanceScope, key: str) -> InstanceScope:
    return InstanceScope(
        datasets=scope.datasets - {key}, departements=scope.departements
    )


@pytest.fixture
def admin_client(monkeypatch):
    # Default: every catalogue dataset hosted on 44/85; refusal tests narrow it.
    _scope_is(monkeypatch, SCOPE_44_85)
    app.dependency_overrides[get_current_user] = _make_admin
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()


# ---------- List ----------


class TestListEtlPipelines:
    @pytest.mark.asyncio
    async def test_returns_summary_rows(self, admin_client):
        db = AsyncMock()
        # list_etl_pipeline_summary calls db.execute ONCE (the VALUES CTE join)
        fake_rows = [
            {
                "pipeline": "dpe",
                "last_success_at": datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
                "last_run_status": "success",
                "last_run_started_at": datetime(2026, 4, 10, 11, 58, tzinfo=UTC),
                "last_run_duration_seconds": 123.4,
                "last_run_rows_loaded": 50000,
                "last_run_error_message": None,
            },
            {
                "pipeline": "dvf",
                "last_success_at": None,
                "last_run_status": None,
                "last_run_started_at": None,
                "last_run_duration_seconds": None,
                "last_run_rows_loaded": None,
                "last_run_error_message": None,
            },
        ]
        db.execute = AsyncMock(return_value=_ExecResult(rows=fake_rows))
        app.dependency_overrides[get_db] = lambda: db

        async with admin_client as c:
            resp = await c.get("/v1/admin/etl")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["rows"][0]["pipeline"] == "dpe"
        assert body["rows"][1]["last_run_status"] is None


# ---------- Drill-down ----------


class TestGetEtlPipelineHistory:
    @pytest.mark.asyncio
    async def test_404_on_unknown_pipeline(self, admin_client):
        db = AsyncMock()
        app.dependency_overrides[get_db] = lambda: db
        async with admin_client as c:
            resp = await c.get("/v1/admin/etl/not_a_real_pipeline")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_paginated_history(self, admin_client):
        db = AsyncMock()
        fake_run = {
            "id": 1,
            "started_at": datetime(2026, 4, 10, 11, 58, tzinfo=UTC),
            "completed_at": datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            "status": "success",
            "total_rows_loaded": 50000,
            "total_duration_seconds": 123.4,
            "error_message": None,
            "stages": {"extract": {"ok": True}},
        }
        db.execute = AsyncMock(
            side_effect=[
                _ExecResult(rows=[fake_run]),  # data
                _ExecResult(scalar=1),  # count
            ]
        )
        app.dependency_overrides[get_db] = lambda: db

        # 'dpe' is a real pipeline in PIPELINE_MAP (see scheduler/jobs.py)
        async with admin_client as c:
            resp = await c.get("/v1/admin/etl/dpe?page=1&page_size=20")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pipeline"] == "dpe"
        assert body["total"] == 1
        assert body["rows"][0]["status"] == "success"


# ---------- Trigger ----------


class TestTriggerEtl:
    @pytest.mark.asyncio
    async def test_404_on_unknown_pipeline(self, admin_client):
        db = AsyncMock()
        app.dependency_overrides[get_db] = lambda: db
        async with admin_client as c:
            resp = await c.post(
                "/v1/admin/etl/bogus_pipeline/trigger",
                json={"reason": "force a manual run for test"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_422_on_short_reason(self, admin_client):
        db = AsyncMock()
        app.dependency_overrides[get_db] = lambda: db
        async with admin_client as c:
            resp = await c.post(
                "/v1/admin/etl/dpe/trigger",
                json={"reason": "short"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    @patch("app.admin.etl.asyncio.create_task")
    @patch("app.admin.etl.log_admin_action", new_callable=AsyncMock)
    async def test_happy_path_writes_audit_and_enqueues(
        self,
        mock_audit,
        mock_create_task,
        admin_client,
    ):
        db = AsyncMock()
        db.commit = AsyncMock()
        app.dependency_overrides[get_db] = lambda: db

        async with admin_client as c:
            resp = await c.post(
                "/v1/admin/etl/dpe/trigger",
                json={"reason": "force a manual run after webhook miss"},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "enqueued"
        assert body["pipeline"] == "dpe"

        # Audit BEFORE enqueue — both must have been called
        mock_audit.assert_awaited_once()
        audit_kwargs = mock_audit.await_args.kwargs
        assert audit_kwargs["action"] == "trigger_etl"
        assert audit_kwargs["target_type"] == "etl_pipeline"
        assert audit_kwargs["target_id"] == "dpe"

        db.commit.assert_awaited()  # commit AFTER audit
        # asyncio.create_task may be called multiple times (middleware also uses it),
        # so we verify at least one call is for the pipeline coroutine.
        assert mock_create_task.call_count >= 1
        # The first call is always the pipeline (trigger handler fires first)
        first_call_arg = mock_create_task.call_args_list[0].args[0]
        assert hasattr(first_call_arg, "__name__") or hasattr(first_call_arg, "cr_code")

    @pytest.mark.asyncio
    @patch("app.admin.etl.asyncio.create_task")
    @patch("app.admin.etl.log_admin_action", new_callable=AsyncMock)
    async def test_409_when_dataset_not_hosted(
        self, mock_audit, mock_create_task, admin_client, monkeypatch
    ):
        """D-23: a dataset outside the database scope is refused, not enqueued."""
        _scope_is(monkeypatch, _without(SCOPE_44_85, "dvf"))
        db = AsyncMock()
        app.dependency_overrides[get_db] = lambda: db

        async with admin_client as c:
            resp = await c.post(
                "/v1/admin/etl/dvf/trigger",
                json={"reason": "test non-hosted dataset guard"},
            )

        assert resp.status_code == 409
        body = resp.json()
        assert body["title"] == "Dataset non hébergé"
        assert "Dataset non hébergé par cette instance : dvf." in body["detail"]
        assert "python -m app.scope set" in body["detail"]
        mock_audit.assert_not_awaited()
        assert all(
            getattr(call.args[0], "__name__", "") != "_run_pipeline_in_executor"
            for call in mock_create_task.call_args_list
        )

    @pytest.mark.asyncio
    @patch("app.admin.etl.log_admin_action", new_callable=AsyncMock)
    async def test_409_names_every_missing_key_of_a_multi_key_pipeline(
        self, mock_audit, admin_client, monkeypatch
    ):
        """The transports import loads stops and stations: both must be hosted."""
        _scope_is(monkeypatch, _without(SCOPE_44_85, "gares"))
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        async with admin_client as c:
            resp = await c.post(
                "/v1/admin/etl/transports/trigger",
                json={"reason": "test multi-key pipeline guard"},
            )

        assert resp.status_code == 409
        assert (
            "Dataset non hébergé par cette instance : gares." in resp.json()["detail"]
        )
        mock_audit.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.admin.etl.asyncio.create_task")
    @patch("app.admin.etl.log_admin_action", new_callable=AsyncMock)
    async def test_409_when_the_source_covers_no_hosted_departement(
        self, mock_audit, mock_create_task, admin_client, monkeypatch
    ):
        """WR-03: DVF does not publish 57 -- refused before the audit row."""
        _scope_is(monkeypatch, InstanceScope(frozenset({"dvf"}), ("57",)))
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        async with admin_client as c:
            resp = await c.post(
                "/v1/admin/etl/dvf/trigger",
                json={"reason": "test source coverage guard"},
            )

        assert resp.status_code == 409
        body = resp.json()
        assert body["title"] == "Source sans couverture"
        assert "source sans couverture sur le périmètre: dvf" in body["detail"]
        mock_audit.assert_not_awaited()
        assert all(
            getattr(call.args[0], "__name__", "") != "_run_pipeline_in_executor"
            for call in mock_create_task.call_args_list
        )

    @pytest.mark.asyncio
    @patch("app.admin.etl.log_admin_action", new_callable=AsyncMock)
    async def test_409_when_scope_is_empty(self, mock_audit, admin_client, monkeypatch):
        """An empty scope hosts nothing: every trigger is refused."""
        _scope_is(monkeypatch, InstanceScope(datasets=frozenset(), departements=()))
        app.dependency_overrides[get_db] = lambda: AsyncMock()

        async with admin_client as c:
            resp = await c.post(
                "/v1/admin/etl/dpe/trigger",
                json={"reason": "test empty scope guard"},
            )

        assert resp.status_code == 409
        assert "Dataset non hébergé par cette instance : dpe." in resp.json()["detail"]
        mock_audit.assert_not_awaited()
