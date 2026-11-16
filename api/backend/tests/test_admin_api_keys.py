"""Phase 17 — /admin/api-keys tests (list + revoke + quota mutations).

Covers: 403 non-admin, list shape, revoke happy path + audit write,
revoke 404/422/self-protection, quota happy path + audit write,
quota 422/self-protection, Pydantic 422 bounds, atomicity via _TrackingDB.

Pattern: AsyncMock + _TrackingDB + dependency_overrides — no real DB.
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
_OTHER_USER_ID = 99
_KEY_ID = 42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_admin(uid: int = _ADMIN_ID) -> UserInfo:
    return UserInfo(
        id=uid,
        email=f"admin{uid}@example.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=True,
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


class _TrackingDB:
    """DB mock that tracks execute calls and commit. Supports fault injection.

    responses: list of items returned sequentially by execute().
      - list[dict] items → wrapped in _DictMapping for .mappings().all()/.first()
      - MagicMock items → returned as-is
    raise_on_execute_n: if set, raises RuntimeError on the Nth execute call.
    """

    def __init__(
        self,
        responses: list,
        raise_on_execute_n: int | None = None,
    ):
        self._responses = list(responses)
        self._n = 0
        self.raise_on = raise_on_execute_n
        self.execute_args: list = []
        self.commit_called = False

    async def execute(self, *args, **kwargs):
        self._n += 1
        self.execute_args.append(args)
        if self.raise_on is not None and self._n == self.raise_on:
            raise RuntimeError(f"forced failure at execute #{self._n}")
        item = self._responses.pop(0)
        if isinstance(item, list):
            result = MagicMock()
            mapped = [_DictMapping(r) for r in item]
            result.mappings.return_value.all.return_value = mapped
            result.mappings.return_value.first.return_value = (
                mapped[0] if mapped else None
            )
            result.scalar_one.return_value = (
                item[0] if item and not isinstance(item[0], dict) else 0
            )
            return result
        # Raw MagicMock — caller uses it directly
        return item

    async def commit(self):
        self.commit_called = True

    async def rollback(self):
        pass

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _key_row(
    key_id: int = _KEY_ID,
    user_id: int = _OTHER_USER_ID,
    revoked: bool = False,
    is_default: bool = False,
    quota_monthly: int = 500,
    key_prefix: str = "dc_live_abc",
    owner_email: str = "target@x.com",
    owner_plan: str = "free",
) -> dict:
    return {
        "id": key_id,
        "key_prefix": key_prefix,
        "user_id": user_id,
        "revoked": revoked,
        "is_default": is_default,
        "quota_monthly": quota_monthly,
        "owner_email": owner_email,
        "owner_plan": owner_plan,
    }


def _api_key_row(key_id: int = 1, user_id: int = _OTHER_USER_ID) -> dict:
    """Full AdminApiKeyRow-compatible dict for the list endpoint."""
    return {
        "id": key_id,
        "prefix": f"dc_live_{key_id:03d}",
        "label": f"Key {key_id}",
        "user_id": user_id,
        "owner_email": f"u{user_id}@x.com",
        "quota_monthly": 500,
        "quota_used": 42,
        "revoked": False,
        "is_default": False,
        "created_at": _NOW,
        "last_used_at": None,
    }


def _scalar_result(value) -> MagicMock:
    """MagicMock result whose .scalar_one() returns value."""
    r = MagicMock()
    r.scalar_one.return_value = value
    return r


def _mappings_result(rows: list[dict]) -> MagicMock:
    """MagicMock result whose .mappings().all() returns [_DictMapping, ...]."""
    r = MagicMock()
    r.mappings.return_value.all.return_value = [_DictMapping(row) for row in rows]
    r.mappings.return_value.first.return_value = _DictMapping(rows[0]) if rows else None
    return r


# ---------------------------------------------------------------------------
# Tests — GET /admin/api-keys list
# ---------------------------------------------------------------------------


async def test_admin_api_keys_list_403_non_admin():
    """Non-admin user gets 403 on GET /admin/api-keys."""
    non_admin = UserInfo(
        id=99,
        email="u@x.com",
        name=None,
        company=None,
        is_verified=True,
        is_admin=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    app.dependency_overrides[get_current_user] = lambda: non_admin
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/api-keys")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


async def test_admin_api_keys_list_shape():
    """GET /admin/api-keys returns rows/total/page/page_size."""
    rows = [_api_key_row(key_id=1), _api_key_row(key_id=2)]
    db = AsyncMock()
    db.execute.side_effect = [
        _mappings_result(rows),  # data query
        _scalar_result(2),  # count query
    ]

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/api-keys?page=1&page_size=50")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert data["page"] == 1
        assert data["page_size"] == 50
        assert len(data["rows"]) == 2
        assert data["rows"][0]["id"] == 1
        assert data["rows"][0]["last_used_at"] is None  # NULL valid for inactive keys
        assert "owner_email" in data["rows"][0]
    finally:
        app.dependency_overrides.clear()


async def test_admin_api_keys_list_no_commit():
    """GET /admin/api-keys must NOT call db.commit (read-only)."""
    db = AsyncMock()
    db.commit = AsyncMock()
    db.execute.side_effect = [
        _mappings_result([]),
        _scalar_result(0),
    ]

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/v1/admin/api-keys")
        assert r.status_code == 200
        db.commit.assert_not_called()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests — POST /admin/api-keys/{key_id}/revoke
# ---------------------------------------------------------------------------


async def test_admin_revoke_single_key_happy_path():
    """POST /{key_id}/revoke returns {status,key_id,after} and commits once."""
    key = _key_row()
    # Sequence: (1) SELECT key, (2) UPDATE api_keys, (3) INSERT audit_log
    db = _TrackingDB([[key], MagicMock(), MagicMock()])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/v1/admin/api-keys/{_KEY_ID}/revoke",
                json={"reason": "suspicious activity detected"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["key_id"] == _KEY_ID
        assert data["after"]["revoked"] is True
        assert db.commit_called is True
    finally:
        app.dependency_overrides.clear()


async def test_admin_revoke_single_key_404_missing():
    """POST /{key_id}/revoke returns 404 when key not found."""
    # SELECT returns no row
    db = _TrackingDB([[]])  # empty list = no rows

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/v1/admin/api-keys/9999/revoke",
                json={"reason": "revoking non-existent key"},
            )
        assert r.status_code == 404
        assert db.commit_called is False
    finally:
        app.dependency_overrides.clear()


async def test_admin_revoke_single_key_422_already_revoked():
    """POST /{key_id}/revoke returns 422 'Déjà révoquée' when key already revoked."""
    key = _key_row(revoked=True)
    db = _TrackingDB([[key]])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/v1/admin/api-keys/{_KEY_ID}/revoke",
                json={"reason": "already revoked test reason"},
            )
        assert r.status_code == 422
        assert "Déjà révoquée" in r.text or "d" in r.text.lower()
        assert db.commit_called is False
    finally:
        app.dependency_overrides.clear()


async def test_admin_revoke_single_key_422_self_protection():
    """Admin cannot revoke their own key — 422 'cannot revoke own keys'."""
    key = _key_row(user_id=_ADMIN_ID)  # key belongs to the admin
    db = _TrackingDB([[key]])

    app.dependency_overrides[get_current_user] = lambda: _make_admin(uid=_ADMIN_ID)
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/v1/admin/api-keys/{_KEY_ID}/revoke",
                json={"reason": "testing self protection rule"},
            )
        assert r.status_code == 422
        assert "cannot revoke own keys" in r.text
        assert db.commit_called is False
    finally:
        app.dependency_overrides.clear()


async def test_admin_revoke_single_key_422_short_reason():
    """Pydantic rejects reason shorter than 10 chars — 422 before DB."""
    db = _TrackingDB([])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                f"/v1/admin/api-keys/{_KEY_ID}/revoke",
                json={"reason": "short"},
            )
        assert r.status_code == 422
        # Must not hit the DB at all (Pydantic rejects before handler runs)
        assert db._n == 0
    finally:
        app.dependency_overrides.clear()


async def test_admin_revoke_single_key_atomic_on_audit_failure():
    """_TrackingDB raises at the 3rd execute (audit INSERT). Commit must NOT run.

    Execute sequence for admin_revoke_single_key:
      (1) SELECT key — OK
      (2) UPDATE api_keys SET revoked = TRUE, status = 'revoked' — OK
      (3) INSERT admin_audit_log — RAISES (forced)
    Result: db.commit_called must be False (mutation not durably committed).
    """
    key = _key_row()
    # Third execute raises — simulates audit log insert failure
    db = _TrackingDB([[key], MagicMock(), MagicMock()], raise_on_execute_n=3)

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            await ac.post(
                f"/v1/admin/api-keys/{_KEY_ID}/revoke",
                json={"reason": "atomicity test: audit failure"},
            )
        # 500 is expected — the forced RuntimeError propagates
        # The CRITICAL invariant: commit was NOT called
        assert db.commit_called is False, (
            "db.commit() was called despite audit failure — revoke is NOT atomic"
        )
    finally:
        app.dependency_overrides.clear()


async def test_admin_revoke_single_key_rejects_internal_account():
    key = _key_row(quota_monthly=-1, owner_plan="internal")
    db = _TrackingDB([[key]])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.post(
                f"/v1/admin/api-keys/{_KEY_ID}/revoke",
                json={"reason": "must remain managed by provisioner"},
            )
        assert response.status_code == 422
        assert db.commit_called is False
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests — PATCH /admin/api-keys/{key_id}/quota
# ---------------------------------------------------------------------------


async def test_admin_update_key_quota_happy_path():
    """PATCH /{key_id}/quota returns {status,key_id,after} with new quota."""
    key = _key_row(quota_monthly=500)
    db = _TrackingDB([[key], MagicMock(), MagicMock()])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.patch(
                f"/v1/admin/api-keys/{_KEY_ID}/quota",
                json={"quota_monthly": 1000, "reason": "upgrading quota for user"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["key_id"] == _KEY_ID
        assert data["after"]["quota_monthly"] == 1000
        assert db.commit_called is True
    finally:
        app.dependency_overrides.clear()


async def test_admin_update_key_quota_rejects_internal_account():
    """The internal entitlement cannot be altered through the generic quota API."""
    key = _key_row(quota_monthly=-1, owner_plan="internal")
    db = _TrackingDB([[key]])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            response = await ac.patch(
                f"/v1/admin/api-keys/{_KEY_ID}/quota",
                json={"quota_monthly": 1000, "reason": "must remain internal"},
            )
        assert response.status_code == 422
        assert db.commit_called is False
    finally:
        app.dependency_overrides.clear()


async def test_admin_update_key_quota_422_revoked():
    """PATCH /{key_id}/quota returns 422 'Clé révoquée' when key is revoked."""
    key = _key_row(revoked=True)
    db = _TrackingDB([[key]])

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.patch(
                f"/v1/admin/api-keys/{_KEY_ID}/quota",
                json={"quota_monthly": 1000, "reason": "editing quota on revoked key"},
            )
        assert r.status_code == 422
        assert "Clé révoquée" in r.text
        assert db.commit_called is False
    finally:
        app.dependency_overrides.clear()


async def test_admin_update_key_quota_422_self_protection():
    """Admin cannot edit quota on their own key — 422 'cannot modify own keys quota'."""
    key = _key_row(user_id=_ADMIN_ID)
    db = _TrackingDB([[key]])

    app.dependency_overrides[get_current_user] = lambda: _make_admin(uid=_ADMIN_ID)
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.patch(
                f"/v1/admin/api-keys/{_KEY_ID}/quota",
                json={
                    "quota_monthly": 1000,
                    "reason": "self-targeting quota test",
                },
            )
        assert r.status_code == 422
        assert "cannot modify own keys quota" in r.text
        assert db.commit_called is False
    finally:
        app.dependency_overrides.clear()


async def test_admin_update_key_quota_422_pydantic_bounds():
    """Pydantic rejects invalid quota values before any DB access."""
    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    # No get_db override — ensures no DB is touched on Pydantic failure

    invalid_cases = [
        {"quota_monthly": -1, "reason": "negative quota test reason"},
        {"quota_monthly": 10_000_001, "reason": "over max quota test reason"},
        {"quota_monthly": 1000, "reason": "short"},  # reason too short
    ]
    try:
        for body in invalid_cases:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.patch(f"/v1/admin/api-keys/{_KEY_ID}/quota", json=body)
            assert r.status_code == 422, f"expected 422 for {body}"
    finally:
        app.dependency_overrides.clear()


async def test_admin_update_key_quota_atomic_on_audit_failure():
    """_TrackingDB raises at the 3rd execute (audit INSERT). Commit must NOT run.

    Execute sequence for admin_update_key_quota:
      (1) SELECT key — OK
      (2) UPDATE api_keys SET quota_monthly = :q — OK
      (3) INSERT admin_audit_log — RAISES (forced)
    """
    key = _key_row(quota_monthly=500)
    db = _TrackingDB([[key], MagicMock(), MagicMock()], raise_on_execute_n=3)

    app.dependency_overrides[get_current_user] = lambda: _make_admin()
    app.dependency_overrides[get_db] = lambda: db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            await ac.patch(
                f"/v1/admin/api-keys/{_KEY_ID}/quota",
                json={"quota_monthly": 2000, "reason": "atomicity test quota update"},
            )
        assert db.commit_called is False, (
            "db.commit() was called despite audit failure — quota update is NOT atomic"
        )
    finally:
        app.dependency_overrides.clear()
