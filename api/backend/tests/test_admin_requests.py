"""Phase 17 — /admin/requests tests (list + CSV export).

Covers: 403 non-admin, 200 list shape, 422 endpoint-without-date_from,
status_code range 422, CSV BOM+header, CSV 422 passthrough,
CSV TRUNCATED marker, no-commit-on-read.

Pattern: AsyncMock + dependency_overrides + ASGITransport — no real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_ADMIN_ID = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_admin(uid: int = _ADMIN_ID, is_admin: bool = True) -> UserInfo:
    return UserInfo(
        id=uid,
        email=f"admin{uid}@example.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=is_admin,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _DictMapping:
    """Minimal mapping that supports dict() via keys() + __getitem__."""

    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)


def _make_row(**kwargs) -> dict:
    """Default api_request_log row with overrideable fields."""
    defaults = {
        "id": 1,
        "ts": _NOW,
        "method": "GET",
        "path": "/v1/dpe/xxx",
        "status_code": 200,
        "duration_ms": 12.5,
        "client_ip": "1.2.3.4",
        "api_key_id": 7,
        "user_id": 2,
        "user_email": "u2@x.com",
    }
    defaults.update(kwargs)
    return defaults


def _db_for_list(rows: list[dict], total: int) -> AsyncMock:
    """DB mock: first execute() returns rows, second returns count."""
    db = AsyncMock()

    # rows result: .mappings().all() returns [_DictMapping, ...]
    rows_result = MagicMock()
    rows_result.mappings.return_value.all.return_value = [_DictMapping(r) for r in rows]

    # count result: .scalar_one() returns int
    count_result = MagicMock()
    count_result.scalar_one.return_value = total

    db.execute.side_effect = [rows_result, count_result]
    db.commit = AsyncMock()
    return db


def _db_for_csv(batches: list[list[dict]]) -> AsyncMock:
    """DB mock for CSV export: each execute call returns one batch of rows.

    _stream_requests_csv calls _fetch_batch repeatedly via db.execute.
    Each call returns rows via .mappings().all(). An empty batch signals EOF.
    """
    db = AsyncMock()

    def _batch_result(rows: list[dict]) -> MagicMock:
        r = MagicMock()
        r.mappings.return_value.all.return_value = [_DictMapping(row) for row in rows]
        return r

    db.execute.side_effect = [_batch_result(b) for b in batches]
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests — list endpoint
# ---------------------------------------------------------------------------


async def test_admin_requests_403_non_admin():
    """Non-admin user gets 403 on GET /admin/requests."""
    app.dependency_overrides[get_current_user] = lambda: _make_admin(is_admin=False)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/requests")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


async def test_admin_requests_200_shape():
    """GET /admin/requests returns total, page, page_size, rows with correct fields."""
    row = _make_row()
    db = _db_for_list([row], total=5)

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/requests?page=1&page_size=50")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 50
        assert len(data["rows"]) == 1
        assert data["rows"][0]["path"] == "/v1/dpe/xxx"
        assert data["rows"][0]["status_code"] == 200
    finally:
        app.dependency_overrides.clear()


async def test_admin_requests_endpoint_filter_requires_date_from():
    """Pitfall 7 — endpoint filter without date_from returns 422."""
    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: _db_for_list([], 0)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/requests?endpoint=/v1/dpe")
        assert r.status_code == 422
        assert "date_from" in r.text.lower()
    finally:
        app.dependency_overrides.clear()


async def test_admin_requests_status_code_out_of_range_422():
    """status_code=700 is outside [100, 599] — Pydantic rejects with 422."""
    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: _db_for_list([], 0)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/requests?status_code=700")
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


async def test_admin_requests_no_audit_on_read():
    """GET /admin/requests must NOT call db.commit (reads never audit-log)."""
    db = _db_for_list([], 0)
    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/requests")
        assert r.status_code == 200
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests — CSV export
# ---------------------------------------------------------------------------


async def test_admin_requests_export_csv_has_bom_and_header():
    """CSV response starts with UTF-8 BOM and header line matching _CSV_FIELDNAMES."""
    row = _make_row()
    # First batch has 1 row, second batch is empty (EOF signal)
    db = _db_for_csv([[row], []])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/requests/export.csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")
        assert "attachment" in r.headers.get("content-disposition", "")
        body = r.content
        assert body.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM missing from CSV response"
        text_content = body.decode("utf-8-sig")
        header_line = text_content.splitlines()[0]
        assert header_line.startswith("ts,method,path,status_code")
    finally:
        app.dependency_overrides.clear()


async def test_admin_requests_export_csv_422_endpoint_without_date_from():
    """CSV export returns 422 when endpoint is set but date_from is missing."""
    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: _db_for_csv([])
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/requests/export.csv?endpoint=/v1/dpe")
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


async def test_admin_requests_export_csv_truncation_marker():
    """When >= _CSV_MAX_ROWS rows emitted, final CSV line contains 'TRUNCATED'."""
    from app.admin import requests as mod

    original_max = mod._CSV_MAX_ROWS
    mod._CSV_MAX_ROWS = 3  # Lower the cap to avoid generating thousands of mock rows

    try:
        # Provide 3 rows in first batch (= cap), then would emit TRUNCATED
        rows = [_make_row(id=i) for i in range(1, 4)]
        # After cap is hit, loop exits — empty second batch would never be called
        db = _db_for_csv([rows, []])

        app.dependency_overrides[get_current_user] = lambda: _make_admin()
        app.dependency_overrides[get_db] = lambda: db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.get("/v1/admin/requests/export.csv")
            assert r.status_code == 200
            text_content = r.content.decode("utf-8-sig")
            assert "TRUNCATED" in text_content
        finally:
            app.dependency_overrides.clear()
    finally:
        mod._CSV_MAX_ROWS = original_max


async def test_admin_requests_export_csv_neutralizes_formula_cells():
    """Audit S13 / FA-02: attacker-controlled cells never open as spreadsheet formulas.

    `path` and `user_email` are user-fed (a request to a forged path lands in
    api_request_log.path verbatim), so a leading `=` would execute when the
    admin opens the export in Excel.
    """
    row = _make_row(
        path="=cmd|' /C calc'!A0",
        user_email="@SUM(1+1)",
        method="+HYPERLINK(1)",
    )
    db = _db_for_csv([[row], []])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/requests/export.csv")
        assert r.status_code == 200
        data_line = r.content.decode("utf-8-sig").splitlines()[1]
        assert "'=cmd" in data_line, f"path formula not neutralized: {data_line}"
        assert "'@SUM(1+1)" in data_line, f"email formula not neutralized: {data_line}"
        assert "'+HYPERLINK(1)" in data_line, (
            f"method formula not neutralized: {data_line}"
        )
    finally:
        app.dependency_overrides.clear()
