"""Unit tests for /v1/admin/system (Phase 19, OPS-04).

Covers each branch of the composite status:
  - DB up + DB down (execute raises)
  - Scheduler up / stale / unknown (etl_runs.MAX branches)
  - Rate limit bucket enumeration filters to window
  - Disk fallback when /etl_tmp missing
  - Services list includes api/db/scheduler
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.admin.queries import (
    get_db_health,
    get_disk_usage,
    get_rate_limit_buckets,
    get_scheduler_last_activity,
)
from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db
from app.main import app

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
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return _DictMapping(self._rows)


@pytest.fixture
def admin_client():
    app.dependency_overrides[get_current_user] = _make_admin
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.clear()


# ---------- DB health ----------


class TestGetDbHealth:
    @pytest.mark.asyncio
    async def test_up_path(self):
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _ExecResult(
                    rows=[
                        {"state": "active", "cnt": 3},
                        {"state": "idle", "cnt": 7},
                    ]
                ),
                _ExecResult(rows=[{"bytes": 1_234_567, "pretty": "1205 KB"}]),
            ]
        )
        h = await get_db_health(db)
        assert h.status == "up"
        assert h.connections_active == 3
        assert h.connections_idle == 7
        assert h.connections_total == 10
        assert h.db_size_bytes == 1_234_567

    @pytest.mark.asyncio
    async def test_down_path_does_not_raise(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
        h = await get_db_health(db)
        assert h.status == "down"
        assert h.error and "connection refused" in h.error


# ---------- Scheduler health ----------


class TestGetSchedulerLastActivity:
    @pytest.mark.asyncio
    async def test_up_when_recent(self):
        db = AsyncMock()
        recent = datetime.now(UTC) - timedelta(hours=2)
        db.execute = AsyncMock(return_value=_ExecResult(rows=[{"last_ts": recent}]))
        s = await get_scheduler_last_activity(db)
        assert s.status == "up"

    @pytest.mark.asyncio
    async def test_stale_when_older_than_2d(self):
        db = AsyncMock()
        old = datetime.now(UTC) - timedelta(days=5)
        db.execute = AsyncMock(return_value=_ExecResult(rows=[{"last_ts": old}]))
        s = await get_scheduler_last_activity(db)
        assert s.status == "stale"

    @pytest.mark.asyncio
    async def test_unknown_when_null(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ExecResult(rows=[{"last_ts": None}]))
        s = await get_scheduler_last_activity(db)
        assert s.status == "unknown"


# ---------- Rate limit enumeration ----------


class TestGetRateLimitBuckets:
    def test_filters_to_window_and_acquires_lock(self):
        from app.rate_limit import _WINDOW_SECONDS, _buckets, _lock

        # Seed fake buckets directly (module-level dict)
        now = time.monotonic()
        recent = now - 5  # inside window
        expired = now - (_WINDOW_SECONDS + 10)
        with _lock:
            _buckets.clear()
            _buckets["ip:1.1.1.1"] = [recent, recent, recent]
            _buckets["ip:2.2.2.2"] = [expired]  # all expired
            _buckets["admin:42"] = [recent]

        active, total = get_rate_limit_buckets()

        assert total == 3
        keys = {b.bucket for b in active}
        assert "ip:1.1.1.1" in keys
        assert "admin:42" in keys
        assert "ip:2.2.2.2" not in keys  # filtered out
        assert next(b for b in active if b.bucket == "ip:1.1.1.1").count == 3

        # Cleanup to avoid leaking to other tests
        with _lock:
            _buckets.clear()


# ---------- Disk ----------


class TestGetDiskUsage:
    def test_happy_path(self):
        fake = MagicMock(total=1000, used=250, free=750)
        with patch("app.admin.queries.shutil.disk_usage", return_value=fake):
            rows = get_disk_usage()
        assert len(rows) == 2
        assert rows[0].status == "ok"
        assert rows[0].percent_used == 25.0

    def test_unavailable_mount(self):
        with patch(
            "app.admin.queries.shutil.disk_usage",
            side_effect=FileNotFoundError("/etl_tmp"),
        ):
            rows = get_disk_usage()
        assert all(r.status == "unavailable" for r in rows)
        assert rows[0].detail


# ---------- Composite endpoint ----------


class TestSystemStatusEndpoint:
    @pytest.mark.asyncio
    @patch("app.admin.queries.get_disk_usage")
    @patch("app.admin.queries.get_rate_limit_buckets")
    async def test_returns_all_sections(self, mock_rl, mock_disk, admin_client):
        mock_rl.return_value = ([], 0)
        mock_disk.return_value = []

        db = AsyncMock()
        recent = datetime.now(UTC) - timedelta(hours=1)
        # Order: get_db_health -> 2 execute calls (states, size)
        # Then get_scheduler_last_activity -> 1 execute (MAX)
        db.execute = AsyncMock(
            side_effect=[
                _ExecResult(rows=[{"state": "active", "cnt": 1}]),
                _ExecResult(rows=[{"bytes": 100, "pretty": "100 B"}]),
                _ExecResult(rows=[{"last_ts": recent}]),
            ]
        )
        app.dependency_overrides[get_db] = lambda: db

        async with admin_client as c:
            resp = await c.get("/v1/admin/system")
        assert resp.status_code == 200
        body = resp.json()
        assert body["db"]["status"] == "up"
        svc_names = {s["name"] for s in body["services"]}
        assert svc_names == {"api", "db", "scheduler"}
        assert body["rate_limit"]["total_buckets"] == 0
