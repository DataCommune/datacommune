"""Unit tests for protected internal service-account provisioning."""

from unittest.mock import AsyncMock

import pytest

from scripts import provision_internal_account as provisioner
from scripts.provision_internal_account import (
    Credentials,
    ProvisioningError,
    _get_or_create_user,
    _upsert_internal_key,
    _upsert_internal_subscription,
    load_credentials,
    provision,
    verify,
)

pytestmark = pytest.mark.unit

# `_REPO_ROOT` / `_WORKFLOW_PATH` and the workflow test that used them are gone
# since plan 01-07: `provision-internal-account.yml` created a service account on
# the LIVE API through the stopped `datacommune-prod` runner, and was removed with
# it (D-05, `docs/REPO-STATE.md`). What stays below tests
# `api/backend/scripts/provision_internal_account.py`, still in the tree -- and
# now without any caller (KI-P5).


def _environment() -> dict[str, str]:
    return {
        "DC_INTERNAL_ACCOUNT_EMAIL": "codex-agent@datacommune.fr",
        "DC_INTERNAL_ACCOUNT_PASSWORD": "p" * 40,
        "DC_INTERNAL_ACCOUNT_API_KEY": "dc_live_" + "k" * 48,
    }


def _credentials() -> Credentials:
    environment = _environment()
    return Credentials(
        email=environment["DC_INTERNAL_ACCOUNT_EMAIL"],
        password=environment["DC_INTERNAL_ACCOUNT_PASSWORD"],
        api_key=environment["DC_INTERNAL_ACCOUNT_API_KEY"],
    )


class _Mappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _Result:
    def __init__(
        self,
        *,
        rows: list[dict[str, object]] | None = None,
        scalar: object = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)

    def scalar_one(self) -> object:
        return self._scalar

    def scalar_one_or_none(self) -> object:
        return self._scalar


def _statement_text(call: object) -> str:
    statement = call.args[0]  # type: ignore[attr-defined]
    return " ".join(str(statement).split())


class _TrackingTransaction:
    def __init__(self) -> None:
        self.entered = False
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "_TrackingTransaction":
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None
        return False


class _TrackingDb:
    def __init__(self) -> None:
        self.transaction = _TrackingTransaction()
        self.execute = AsyncMock(return_value=_Result(scalar=None))

    def begin(self) -> _TrackingTransaction:
        return self.transaction


def test_load_credentials_accepts_strong_internal_values():
    credentials = load_credentials(_environment())

    assert credentials.email == "codex-agent@datacommune.fr"
    assert credentials.password == "p" * 40
    assert credentials.api_key.startswith("dc_live_")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DC_INTERNAL_ACCOUNT_EMAIL", "person@datacommune.fr"),
        ("DC_INTERNAL_ACCOUNT_EMAIL", "codex-agent@example.com"),
        ("DC_INTERNAL_ACCOUNT_PASSWORD", "too-short"),
        ("DC_INTERNAL_ACCOUNT_API_KEY", "dc_test_" + "k" * 48),
        ("DC_INTERNAL_ACCOUNT_API_KEY", "dc_live_short"),
    ],
)
def test_load_credentials_rejects_unsafe_values(name: str, value: str):
    environment = _environment()
    environment[name] = value

    with pytest.raises(ProvisioningError):
        load_credentials(environment)


def test_load_credentials_never_embeds_values_in_errors():
    environment = _environment()
    secret = "sensitive-password"
    environment["DC_INTERNAL_ACCOUNT_PASSWORD"] = secret

    with pytest.raises(ProvisioningError) as exc_info:
        load_credentials(environment)

    assert secret not in str(exc_info.value)


async def test_get_or_create_user_creates_marked_admin_account(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(rows=[]),
            _Result(scalar=41),
        ]
    )
    monkeypatch.setattr(
        provisioner, "hash_password", lambda password: "stored-password-hash"
    )

    user_id, created = await _get_or_create_user(db, _credentials())

    assert (user_id, created) == (41, True)
    assert db.execute.await_count == 2
    assert "SELECT id, name, company FROM users" in _statement_text(
        db.execute.await_args_list[0]
    )
    assert "INSERT INTO users" in _statement_text(db.execute.await_args_list[1])
    insert_params = db.execute.await_args_list[1].args[1]
    assert insert_params == {
        "email": "codex-agent@datacommune.fr",
        "password_hash": "stored-password-hash",
        "name": "Codex Service Account",
        "company": "DataCommune",
    }


async def test_get_or_create_user_adopts_only_existing_marked_account(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(
                rows=[
                    {
                        "id": 41,
                        "name": "Codex Service Account",
                        "company": "DataCommune",
                    }
                ]
            ),
            _Result(scalar=1),
            _Result(),
        ]
    )
    monkeypatch.setattr(
        provisioner, "hash_password", lambda password: "rotated-password-hash"
    )

    user_id, created = await _get_or_create_user(db, _credentials())

    assert (user_id, created) == (41, False)
    assert db.execute.await_count == 3
    marker_params = db.execute.await_args_list[1].args[1]
    assert marker_params == {
        "user_id": 41,
        "marker": "manual_grant_41",
        "plan_name": "internal",
    }
    update_sql = _statement_text(db.execute.await_args_list[2])
    update_params = db.execute.await_args_list[2].args[1]
    assert "UPDATE users" in update_sql
    assert "is_verified = TRUE" in update_sql
    assert "is_admin = TRUE" in update_sql
    assert update_params == {
        "password_hash": "rotated-password-hash",
        "user_id": 41,
    }


@pytest.mark.parametrize(
    ("existing", "expected_calls"),
    [
        (
            {"id": 41, "name": "Personal account", "company": "DataCommune"},
            1,
        ),
        (
            {"id": 41, "name": "Codex Service Account", "company": "Other"},
            1,
        ),
    ],
)
async def test_get_or_create_user_refuses_unmarked_identity(
    monkeypatch, existing: dict[str, object], expected_calls: int
):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(rows=[existing]))
    monkeypatch.setattr(provisioner, "hash_password", lambda password: "hash")

    with pytest.raises(ProvisioningError, match="unmarked user"):
        await _get_or_create_user(db, _credentials())

    assert db.execute.await_count == expected_calls


async def test_get_or_create_user_refuses_matching_identity_without_marker(
    monkeypatch,
):
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(
                rows=[
                    {
                        "id": 41,
                        "name": "Codex Service Account",
                        "company": "DataCommune",
                    }
                ]
            ),
            _Result(scalar=0),
        ]
    )
    monkeypatch.setattr(provisioner, "hash_password", lambda password: "hash")

    with pytest.raises(ProvisioningError, match="without marker"):
        await _get_or_create_user(db, _credentials())

    assert db.execute.await_count == 2


async def test_upsert_subscription_refuses_marker_owned_by_another_user():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(scalar=99))

    with pytest.raises(ProvisioningError, match="belongs to another user"):
        await _upsert_internal_subscription(db, user_id=41)

    assert db.execute.await_count == 1
    assert db.execute.await_args.args[1] == {"marker": "manual_grant_41"}


async def test_upsert_subscription_creates_internal_manual_grant():
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(scalar=None),
            _Result(scalar=0),
            _Result(),
        ]
    )

    await _upsert_internal_subscription(db, user_id=41)

    assert db.execute.await_count == 3
    insert_sql = _statement_text(db.execute.await_args_list[2])
    insert_params = db.execute.await_args_list[2].args[1]
    assert "INSERT INTO subscriptions" in insert_sql
    assert "ON CONFLICT (stripe_subscription_id) DO UPDATE" in insert_sql
    assert insert_params == {
        "user_id": 41,
        "marker": "manual_grant_41",
        "plan_name": "internal",
    }


async def test_upsert_subscription_refuses_unexpected_active_subscription():
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(scalar=41),
            _Result(scalar=1),
        ]
    )

    with pytest.raises(ProvisioningError, match="Unexpected active subscription"):
        await _upsert_internal_subscription(db, user_id=41)

    assert db.execute.await_count == 2
    assert all(
        "INSERT INTO subscriptions" not in _statement_text(call)
        for call in db.execute.await_args_list
    )


@pytest.mark.parametrize(
    "keys",
    [
        [
            {"id": 7, "name": "Codex internal service key"},
            {"id": 8, "name": "Codex internal service key"},
        ],
        [{"id": 7, "name": "Personal key"}],
    ],
)
async def test_upsert_key_refuses_unexpected_key_state(
    keys: list[dict[str, object]],
):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(rows=keys))

    with pytest.raises(ProvisioningError, match="Unexpected API keys"):
        await _upsert_internal_key(db, _credentials(), user_id=41)

    assert db.execute.await_count == 1


async def test_upsert_key_rotates_existing_marked_key_in_place(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(rows=[{"id": 7, "name": "Codex internal service key"}]),
            _Result(),
        ]
    )
    monkeypatch.setattr(provisioner, "hash_api_key", lambda key: "new-key-hash")

    key_id = await _upsert_internal_key(db, _credentials(), user_id=41)

    assert key_id == 7
    assert db.execute.await_count == 2
    update_sql = _statement_text(db.execute.await_args_list[1])
    update_params = db.execute.await_args_list[1].args[1]
    assert "UPDATE api_keys" in update_sql
    assert "status = 'active'" in update_sql
    assert "revoked = FALSE" in update_sql
    assert update_params == {
        "key_hash": "new-key-hash",
        "quota": -1,
        "key_prefix": "dc_live_kkkkkkkk...",
        "key_id": 7,
    }


async def test_upsert_key_creates_single_unlimited_live_key(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(rows=[]),
            _Result(scalar=7),
        ]
    )
    monkeypatch.setattr(provisioner, "hash_api_key", lambda key: "new-key-hash")

    key_id = await _upsert_internal_key(db, _credentials(), user_id=41)

    assert key_id == 7
    assert db.execute.await_count == 2
    insert_sql = _statement_text(db.execute.await_args_list[1])
    insert_params = db.execute.await_args_list[1].args[1]
    assert "INSERT INTO api_keys" in insert_sql
    assert "'live', TRUE, 'active', FALSE" in insert_sql
    assert insert_params == {
        "key_hash": "new-key-hash",
        "name": "Codex internal service key",
        "quota": -1,
        "user_id": 41,
        "key_prefix": "dc_live_kkkkkkkk...",
    }


async def test_provision_commits_complete_idempotent_second_run(monkeypatch):
    db = _TrackingDb()
    get_user = AsyncMock(return_value=(41, False))
    upsert_subscription = AsyncMock()
    upsert_key = AsyncMock(return_value=7)
    write_audit = AsyncMock()
    monkeypatch.setattr(provisioner, "_get_or_create_user", get_user)
    monkeypatch.setattr(
        provisioner, "_upsert_internal_subscription", upsert_subscription
    )
    monkeypatch.setattr(provisioner, "_upsert_internal_key", upsert_key)
    monkeypatch.setattr(provisioner, "_write_audit", write_audit)

    result = await provision(db, _credentials())

    assert result == {"user_id": 41, "key_id": 7, "created": False}
    assert db.transaction.entered is True
    assert db.transaction.committed is True
    assert db.transaction.rolled_back is False
    assert "pg_advisory_xact_lock" in _statement_text(db.execute.await_args_list[0])
    get_user.assert_awaited_once_with(db, _credentials())
    upsert_subscription.assert_awaited_once_with(db, 41)
    upsert_key.assert_awaited_once_with(db, _credentials(), 41)
    write_audit.assert_awaited_once_with(db, 41, False)


async def test_provision_rolls_back_and_skips_audit_on_collision(monkeypatch):
    db = _TrackingDb()
    get_user = AsyncMock(return_value=(41, True))
    upsert_subscription = AsyncMock()
    collision = ProvisioningError("Unexpected API keys on internal account")
    upsert_key = AsyncMock(side_effect=collision)
    write_audit = AsyncMock()
    monkeypatch.setattr(provisioner, "_get_or_create_user", get_user)
    monkeypatch.setattr(
        provisioner, "_upsert_internal_subscription", upsert_subscription
    )
    monkeypatch.setattr(provisioner, "_upsert_internal_key", upsert_key)
    monkeypatch.setattr(provisioner, "_write_audit", write_audit)

    with pytest.raises(ProvisioningError) as exc_info:
        await provision(db, _credentials())

    assert exc_info.value is collision
    assert db.transaction.entered is True
    assert db.transaction.committed is False
    assert db.transaction.rolled_back is True
    write_audit.assert_not_awaited()


def _verified_account_row() -> dict[str, object]:
    return {
        "id": 41,
        "name": "Codex Service Account",
        "company": "DataCommune",
        "password_hash": "stored-password-hash",
        "is_verified": True,
        "is_admin": True,
        "plan_name": "internal",
        "subscription_status": "active",
        "subscription_source": "manual_grant",
        "stripe_price_id": "",
        "active_subscription_count": 1,
        "key_id": 7,
        "key_name": "Codex internal service key",
        "key_hash": "expected-key-hash",
        "quota_monthly": -1,
        "environment": "live",
        "is_default": True,
        "key_status": "active",
        "revoked": False,
        "key_count": 1,
    }


async def test_verify_checks_hash_loaded_by_restarted_container(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(rows=[_verified_account_row()]))
    monkeypatch.setattr(provisioner, "hash_api_key", lambda key: "expected-key-hash")
    monkeypatch.setattr(provisioner, "verify_password", lambda password, stored: True)
    monkeypatch.setattr(
        provisioner.settings,
        "internal_unlimited_key_hash",
        "expected-key-hash",
    )

    result = await verify(db, _credentials())

    assert result == {"user_id": 41, "key_id": 7, "valid": True}


async def test_verify_rejects_stale_container_hash(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(rows=[_verified_account_row()]))
    monkeypatch.setattr(provisioner, "hash_api_key", lambda key: "expected-key-hash")
    monkeypatch.setattr(provisioner, "verify_password", lambda password, stored: True)
    monkeypatch.setattr(
        provisioner.settings,
        "internal_unlimited_key_hash",
        "stale-container-hash",
    )

    with pytest.raises(ProvisioningError, match="verification failed"):
        await verify(db, _credentials())


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("active_subscription_count", 2),
        ("subscription_source", "stripe"),
        ("key_name", "Unexpected key"),
        ("environment", "test"),
    ],
)
async def test_verify_rejects_drifted_persisted_invariants(
    monkeypatch, field: str, invalid_value: object
):
    row = _verified_account_row()
    row[field] = invalid_value
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(rows=[row]))
    monkeypatch.setattr(provisioner, "hash_api_key", lambda key: "expected-key-hash")
    monkeypatch.setattr(provisioner, "verify_password", lambda password, stored: True)
    monkeypatch.setattr(
        provisioner.settings,
        "internal_unlimited_key_hash",
        "expected-key-hash",
    )

    with pytest.raises(ProvisioningError, match="verification failed"):
        await verify(db, _credentials())
