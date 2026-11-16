"""Tests for auth module: service, router endpoints, dependencies.

Covers: register, login, verify, reset, profile, quick-key, account deletion,
JWT tokens, password hashing, API key generation, email tokens, check_quota.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import app.auth.router as auth_router_module
from app.auth.dependencies import check_quota, get_current_user
from app.auth.email import send_api_key_email
from app.auth.models import ApiKeyInfo, UserInfo
from app.auth.service import (
    create_access_token,
    create_reset_token,
    create_verification_token,
    decode_access_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_email_token,
    verify_password,
    verify_reset_token,
)
from app.common.exceptions import ErreurApiError
from app.config import settings
from app.database import get_db
from app.main import app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_user(
    uid: int = 1,
    email: str = "test@example.com",
    verified: bool = True,
    password: str = "hashed",
) -> dict:
    now = datetime.now(UTC)
    return {
        "id": uid,
        "email": email,
        "password_hash": password,
        "name": None,
        "company": None,
        "is_verified": verified,
        "created_at": now,
        "updated_at": now,
    }


def _fake_user_info() -> UserInfo:
    now = datetime.now(UTC)
    return UserInfo(
        id=1,
        email="test@example.com",
        name="Test",
        company=None,
        is_verified=True,
        created_at=now,
        updated_at=now,
    )


def _mock_db():
    return AsyncMock()


class _SessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _session_factory(db):
    return lambda: _SessionContext(db)


@pytest.fixture()
async def auth_client():
    """Async client WITHOUT auth override — for testing auth endpoints directly."""
    app.dependency_overrides[get_db] = _mock_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
async def authed_client():
    """Client with authenticated user injected."""
    app.dependency_overrides[get_db] = _mock_db
    app.dependency_overrides[get_current_user] = _fake_user_info
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _use_secure_test_jwt_secret(monkeypatch):
    """Keep JWT tests independent from local developer secrets."""
    monkeypatch.setattr(
        settings,
        "jwt_secret_key",
        "test-only-jwt-secret-with-at-least-32-bytes",
    )


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """Per-route rate limiters share in-memory state across tests.

    The per-route email buckets (register, forgot-password) are keyed by
    client IP and persist for the whole pytest process — reset them so each
    test starts from a clean limiter.
    """
    from app.rate_limit import _buckets

    _buckets.clear()
    yield
    _buckets.clear()


# ===========================================================================
# 1. Password hashing
# ===========================================================================


class TestPasswordHashing:
    def test_hash_and_verify(self):
        hashed = hash_password("securepass123")
        assert verify_password("securepass123", hashed)

    def test_wrong_password(self):
        hashed = hash_password("securepass123")
        assert not verify_password("wrongpass", hashed)

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("securepass123")
        assert hashed != "securepass123"


# ===========================================================================
# 2. JWT tokens
# ===========================================================================


class TestJWT:
    def test_create_and_decode(self):
        token = create_access_token({"sub": "42"})
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "42"

    def test_expired_token(self):
        token = create_access_token(
            {"sub": "42"},
            expires_delta=timedelta(seconds=-1),
        )
        assert decode_access_token(token) is None

    def test_invalid_token(self):
        assert decode_access_token("not.a.real.token") is None

    def test_tampered_token(self):
        token = create_access_token({"sub": "42"})
        # Flip the second-to-last signature char: the last base64 char has
        # unused padding bits, so flipping it may leave the signature intact
        pos = len(token) - 2
        tampered = token[:pos] + ("a" if token[pos] != "a" else "b") + token[pos + 1 :]
        assert decode_access_token(tampered) is None


# ===========================================================================
# 3. Email verification tokens
# ===========================================================================


class TestEmailTokens:
    def test_verification_roundtrip(self):
        token = create_verification_token("user@example.com")
        email = verify_email_token(token)
        assert email == "user@example.com"

    def test_verification_expired(self):
        from itsdangerous import URLSafeTimedSerializer

        # Use a serializer that produces tokens with old timestamps
        s = URLSafeTimedSerializer("test-secret")
        token = s.dumps("user@example.com", salt="email-verify")
        # Token signed with wrong secret should fail verification
        assert verify_email_token(token) is None

    def test_verification_invalid(self):
        assert verify_email_token("garbage") is None

    def test_reset_roundtrip(self):
        token = create_reset_token("user@example.com", "hashed")
        email = verify_reset_token(token)
        assert email["email"] == "user@example.com"
        assert email["nonce"]

    def test_reset_wrong_secret(self):
        from itsdangerous import URLSafeTimedSerializer

        s = URLSafeTimedSerializer("wrong-secret")
        token = s.dumps("user@example.com", salt="password-reset")
        assert verify_reset_token(token) is None

    def test_reset_invalid(self):
        assert verify_reset_token("garbage") is None


async def test_api_key_email_uses_configured_api_url(monkeypatch):
    monkeypatch.setattr(settings, "api_base_url", "https://demo.example/")

    with patch(
        "app.auth.email._send_with_retry",
        new_callable=AsyncMock,
    ) as send:
        await send_api_key_email("user@example.com", "dc_test_example")

    html = send.await_args.args[0]["html"]
    assert "https://demo.example/v1/datasets" in html
    assert "/v1/communes" not in html


# ===========================================================================
# 4. API key generation
# ===========================================================================


class TestApiKeyGeneration:
    def test_generate_live(self):
        plaintext, key_hash, prefix = generate_api_key("live")
        assert plaintext.startswith("dc_live_")
        assert prefix.startswith("dc_live_")
        assert prefix.endswith("...")
        assert len(key_hash) == 64  # SHA-256 hex

    def test_generate_test(self):
        plaintext, _key_hash, _prefix = generate_api_key("test")
        assert plaintext.startswith("dc_test_")

    def test_hash_matches(self):
        plaintext, key_hash, _ = generate_api_key("live")
        assert hash_api_key(plaintext) == key_hash

    def test_hash_deterministic(self):
        assert hash_api_key("dc_live_abc") == hash_api_key("dc_live_abc")


# ===========================================================================
# 5. Register endpoint
# ===========================================================================


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_success(self, auth_client, monkeypatch):
        monkeypatch.setattr(settings, "resend_api_key", "re_test")
        with (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.auth.router.create_user", new_callable=AsyncMock, return_value=1
            ),
            patch("app.auth.router.send_verification_email", new_callable=AsyncMock),
        ):
            resp = await auth_client.post(
                "/auth/register",
                json={"email": "new@example.com", "password": "longpassword"},
            )
        assert resp.status_code == 202
        assert "verification" in resp.json()["message"]
        assert resp.json()["requires_verification"] is True

    @pytest.mark.asyncio
    async def test_register_email_disabled_auto_verifies(
        self,
        auth_client,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "resend_api_key", "resend-disabled-demo")
        monkeypatch.setattr(settings, "app_environment", "preproduction")
        with (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.auth.router.create_user",
                new_callable=AsyncMock,
                return_value=42,
            ),
            patch(
                "app.auth.router.verify_user_email",
                new_callable=AsyncMock,
                return_value=True,
            ) as verify_email,
            patch(
                "app.auth.router.get_default_key_for_user",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.auth.router.insert_default_api_key",
                new_callable=AsyncMock,
            ) as insert_key,
            patch(
                "app.auth.router.send_verification_email",
                new_callable=AsyncMock,
            ) as send_email,
        ):
            resp = await auth_client.post(
                "/auth/register",
                json={"email": "new@example.com", "password": "longpassword"},
            )
        assert resp.status_code == 202
        assert resp.json()["requires_verification"] is False
        verify_email.assert_awaited_once()
        assert verify_email.await_args.args[1] == "new@example.com"
        insert_key.assert_awaited_once()
        send_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, auth_client, monkeypatch):
        """Anti-enumeration: same response whether email exists or not."""
        monkeypatch.setattr(settings, "resend_api_key", "re_test")
        with patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=_make_user(),
        ):
            resp = await auth_client.post(
                "/auth/register",
                json={"email": "exists@example.com", "password": "longpassword"},
            )
        assert resp.status_code == 202
        assert resp.json()["requires_verification"] is True

    @pytest.mark.asyncio
    async def test_register_existing_unverified_resends(self, auth_client, monkeypatch):
        """Existing unverified account retrying register gets a fresh link."""
        monkeypatch.setattr(settings, "resend_api_key", "re_test")
        monkeypatch.setattr(auth_router_module, "_VERIF_RESEND_COOLDOWN", {})
        user = _make_user(email="exists@example.com", verified=False)
        with (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch(
                "app.auth.router.send_verification_email",
                new_callable=AsyncMock,
            ) as send_email,
        ):
            resp = await auth_client.post(
                "/auth/register",
                json={"email": "exists@example.com", "password": "longpassword"},
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert resp.status_code == 202
        assert resp.json()["requires_verification"] is True
        send_email.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_register_short_password(self, auth_client):
        resp = await auth_client.post(
            "/auth/register",
            json={"email": "new@example.com", "password": "short"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_register_invalid_email(self, auth_client):
        resp = await auth_client.post(
            "/auth/register",
            json={"email": "not-an-email", "password": "longpassword"},
        )
        assert resp.status_code == 422


# ===========================================================================
# 5b. Per-route rate limit on email-sending endpoints (AB-01)
# ===========================================================================


def _stateful_limiter(
    limit: int, seen: dict[str, int], windows: dict[str, int] | None = None
):
    """Fake _check_rate allowing `limit` calls per bucket, rejecting beyond.

    Records the `window_seconds` each route asks for when `windows` is provided —
    the two email routes deliberately use different windows (see T6).
    """

    def _check(bucket: str, _limit: int, window_seconds: int = 60):
        seen[bucket] = seen.get(bucket, 0) + 1
        if windows is not None:
            windows[bucket] = window_seconds
        if seen[bucket] > limit:
            return (False, 0, 30)
        return (True, 1, window_seconds)

    return _check


class TestPerRouteEmailRateLimit:
    """AB-01: /auth/register and /auth/forgot-password can trigger outbound
    email without authentication — they need their own per-IP ceiling,
    below the global middleware one."""

    @pytest.mark.asyncio
    async def test_register_rejects_request_beyond_route_limit(
        self, auth_client, monkeypatch
    ):
        monkeypatch.setattr(settings, "resend_api_key", "re_test")
        seen: dict[str, int] = {}
        with (
            patch(
                "app.auth.router._check_rate",
                side_effect=_stateful_limiter(5, seen),
            ),
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.auth.router.create_user", new_callable=AsyncMock, return_value=1
            ),
            patch("app.auth.router.send_verification_email", new_callable=AsyncMock),
        ):
            statuses = []
            for _ in range(6):
                resp = await auth_client.post(
                    "/auth/register",
                    json={"email": "new@example.com", "password": "longpassword"},
                )
                statuses.append(resp.status_code)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert statuses == [202, 202, 202, 202, 202, 429]
        # The bucket is route-specific, not the global middleware bucket.
        assert list(seen) == ["register:ip:127.0.0.1"]

    @pytest.mark.asyncio
    async def test_forgot_password_rejects_request_beyond_route_limit(
        self, auth_client
    ):
        seen: dict[str, int] = {}
        with (
            patch(
                "app.auth.router._check_rate",
                side_effect=_stateful_limiter(5, seen),
            ),
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            statuses = []
            for _ in range(6):
                resp = await auth_client.post(
                    "/auth/forgot-password",
                    json={"email": "nobody@example.com"},
                )
                statuses.append(resp.status_code)

        assert statuses == [202, 202, 202, 202, 202, 429]
        assert list(seen) == ["forgotpw:ip:127.0.0.1"]

    @pytest.mark.asyncio
    async def test_route_buckets_are_independent(self, auth_client):
        """Exhausting the register bucket must not block forgot-password."""
        seen: dict[str, int] = {}
        windows: dict[str, int] = {}
        with (
            patch(
                "app.auth.router._check_rate",
                side_effect=_stateful_limiter(2, seen, windows),
            ),
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.auth.router.create_user", new_callable=AsyncMock, return_value=1
            ),
            patch("app.auth.router.send_verification_email", new_callable=AsyncMock),
        ):
            for _ in range(3):
                await auth_client.post(
                    "/auth/register",
                    json={"email": "new@example.com", "password": "longpassword"},
                )
            resp = await auth_client.post(
                "/auth/forgot-password",
                json={"email": "nobody@example.com"},
            )

        assert resp.status_code == 202
        assert set(seen) == {"register:ip:127.0.0.1", "forgotpw:ip:127.0.0.1"}

        # T6 — the two routes deliberately carry DIFFERENT windows, and the choice
        # is load-bearing: /forgot-password is the one that can mail a third party
        # on demand, so it gets the hourly cap; /register only mails the address
        # being registered and stays per-minute so a shared NAT is not locked out.
        # Without this assertion the two silently drift back to the same value.
        assert windows["forgotpw:ip:127.0.0.1"] == 3600
        assert windows["register:ip:127.0.0.1"] == 60


# ===========================================================================
# 6. Login endpoint
# ===========================================================================


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success(self, auth_client):
        pw_hash = hash_password("securepass")
        user = _make_user(password=pw_hash)
        with patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=user,
        ):
            resp = await auth_client.post(
                "/auth/login",
                json={"email": "test@example.com", "password": "securepass"},
            )
        assert resp.status_code == 200
        assert "access_token" in resp.json()
        # Check HttpOnly cookie is set
        assert "access_token=" in resp.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_preproduction_cookie_is_host_only_and_named_separately(
        self, auth_client, monkeypatch
    ):
        monkeypatch.setattr(settings, "auth_cookie_name", "dev_access_token")
        monkeypatch.setattr(settings, "auth_cookie_domain", None)
        pw_hash = hash_password("securepass")
        with patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=_make_user(password=pw_hash),
        ):
            resp = await auth_client.post(
                "/auth/login",
                json={"email": "test@example.com", "password": "securepass"},
                cookies={"access_token": "Bearer production-token"},
            )
        cookie = resp.headers["set-cookie"]
        assert cookie.startswith("dev_access_token=")
        assert "Domain=" not in cookie
        assert "access_token=" not in cookie.removeprefix("dev_access_token=")

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, auth_client):
        pw_hash = hash_password("correct")
        user = _make_user(password=pw_hash)
        with patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=user,
        ):
            resp = await auth_client.post(
                "/auth/login",
                json={"email": "test@example.com", "password": "wrong"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, auth_client):
        with patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = await auth_client.post(
                "/auth/login",
                json={"email": "nobody@example.com", "password": "whatever"},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_unverified_user_resends_link(self, auth_client, monkeypatch):
        """Correct password on unverified account: 403 + fresh verification link."""
        monkeypatch.setattr(settings, "resend_api_key", "re_test")
        monkeypatch.setattr(auth_router_module, "_VERIF_RESEND_COOLDOWN", {})
        pw_hash = hash_password("securepass")
        user = _make_user(password=pw_hash, verified=False)
        with (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch(
                "app.auth.router.send_verification_email",
                new_callable=AsyncMock,
            ) as send_email,
        ):
            resp = await auth_client.post(
                "/auth/login",
                json={"email": "test@example.com", "password": "securepass"},
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert resp.status_code == 403
        send_email.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_unverified_resend_cooldown(self, auth_client, monkeypatch):
        """Second attempt within the cooldown window does not resend."""
        monkeypatch.setattr(settings, "resend_api_key", "re_test")
        monkeypatch.setattr(auth_router_module, "_VERIF_RESEND_COOLDOWN", {})
        pw_hash = hash_password("securepass")
        user = _make_user(password=pw_hash, verified=False)
        with (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch(
                "app.auth.router.send_verification_email",
                new_callable=AsyncMock,
            ) as send_email,
        ):
            for _ in range(2):
                resp = await auth_client.post(
                    "/auth/login",
                    json={"email": "test@example.com", "password": "securepass"},
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
        assert resp.status_code == 403
        send_email.assert_awaited_once()


# ===========================================================================
# 7. Verify email endpoint
# ===========================================================================


class TestVerifyEmail:
    @pytest.mark.asyncio
    async def test_verify_valid_token(self, auth_client):
        token = create_verification_token("user@example.com")
        with (
            patch(
                "app.auth.router.verify_user_email",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=_make_user(email="user@example.com"),
            ),
            patch(
                "app.auth.router.get_default_key_for_user",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.auth.router.insert_default_api_key", new_callable=AsyncMock),
        ):
            resp = await auth_client.get(f"/auth/verify?token={token}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_verify_invalid_token(self, auth_client):
        resp = await auth_client.get("/auth/verify?token=bad-token")
        assert resp.status_code == 400


# ===========================================================================
# 7b. Resend verification endpoint (T2)
# ===========================================================================


class TestResendVerification:
    """Public resend endpoint: neutral 202 regardless of account state."""

    def _patches(self, user, monkeypatch):
        monkeypatch.setattr(settings, "resend_api_key", "re_test")
        return (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch(
                "app.auth.router.send_verification_email",
                new_callable=AsyncMock,
            ),
            patch(
                "app.auth.router._check_rate",
                return_value=(True, 4, 60),
            ),
        )

    async def _post(self, client, email="test@example.com"):
        resp = await client.post(
            "/auth/resend-verification",
            json={"email": email},
        )
        # Let fire_and_forget tasks run
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return resp

    @pytest.mark.asyncio
    async def test_unverified_user_gets_fresh_link(self, auth_client, monkeypatch):
        monkeypatch.setattr(auth_router_module, "_VERIF_RESEND_COOLDOWN", {})
        user = _make_user(verified=False)
        get_user, send_email, check_rate = self._patches(user, monkeypatch)
        with get_user, send_email as send, check_rate:
            resp = await self._post(auth_client)
        assert resp.status_code == 202
        send.assert_awaited_once()
        # No token or sensitive state in the response body
        body = resp.json()
        assert body == {"message": auth_router_module._RESEND_VERIFICATION_MSG}

    @pytest.mark.asyncio
    async def test_nonexistent_email_same_response(self, auth_client, monkeypatch):
        """Anti-enumeration: identical 202 body when no account exists."""
        monkeypatch.setattr(auth_router_module, "_VERIF_RESEND_COOLDOWN", {})
        get_user, send_email, check_rate = self._patches(None, monkeypatch)
        with get_user, send_email as send, check_rate:
            resp = await self._post(auth_client, "nobody@example.com")
        assert resp.status_code == 202
        assert resp.json() == {"message": auth_router_module._RESEND_VERIFICATION_MSG}
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_verified_same_response(self, auth_client, monkeypatch):
        """Anti-enumeration: identical 202 body, no email sent."""
        monkeypatch.setattr(auth_router_module, "_VERIF_RESEND_COOLDOWN", {})
        user = _make_user(verified=True)
        get_user, send_email, check_rate = self._patches(user, monkeypatch)
        with get_user, send_email as send, check_rate:
            resp = await self._post(auth_client)
        assert resp.status_code == 202
        assert resp.json() == {"message": auth_router_module._RESEND_VERIFICATION_MSG}
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_throttle_active_same_response(self, auth_client, monkeypatch):
        """Within the per-email cooldown: silent no-op, identical 202 body."""
        import time as _time

        monkeypatch.setattr(
            auth_router_module,
            "_VERIF_RESEND_COOLDOWN",
            {"test@example.com": _time.monotonic()},
        )
        user = _make_user(verified=False)
        get_user, send_email, check_rate = self._patches(user, monkeypatch)
        with get_user, send_email as send, check_rate:
            resp = await self._post(auth_client)
        assert resp.status_code == 202
        assert resp.json() == {"message": auth_router_module._RESEND_VERIFICATION_MSG}
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_quickkey_sentinel_not_emailed(self, auth_client, monkeypatch):
        """Sentinel-hash accounts never receive a self-service link."""
        monkeypatch.setattr(auth_router_module, "_VERIF_RESEND_COOLDOWN", {})
        user = _make_user(verified=False, password="!quickkey:sentinel")
        get_user, send_email, check_rate = self._patches(user, monkeypatch)
        with get_user, send_email as send, check_rate:
            resp = await self._post(auth_client)
        assert resp.status_code == 202
        assert resp.json() == {"message": auth_router_module._RESEND_VERIFICATION_MSG}
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_ip_rate_limited_returns_429(self, auth_client, monkeypatch):
        """Per-route IP ceiling (same pattern as quick-key) returns 429."""
        monkeypatch.setattr(settings, "resend_api_key", "re_test")
        with patch(
            "app.auth.router._check_rate",
            return_value=(False, 0, 30),
        ):
            resp = await auth_client.post(
                "/auth/resend-verification",
                json={"email": "test@example.com"},
            )
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_invalid_email_schema(self, auth_client):
        with patch(
            "app.auth.router._check_rate",
            return_value=(True, 4, 60),
        ):
            resp = await auth_client.post(
                "/auth/resend-verification",
                json={"email": "not-an-email"},
            )
        assert resp.status_code == 422


# ===========================================================================
# 8. Forgot password / reset password
# ===========================================================================


class TestPasswordReset:
    @pytest.mark.asyncio
    async def test_forgot_password_existing(self, auth_client):
        user = _make_user()
        with (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch("app.auth.router.send_reset_email", new_callable=AsyncMock),
        ):
            resp = await auth_client.post(
                "/auth/forgot-password",
                json={"email": "test@example.com"},
            )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_forgot_password_nonexistent(self, auth_client):
        """Anti-enumeration: same 202 response."""
        with patch(
            "app.auth.router.get_user_by_email",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = await auth_client.post(
                "/auth/forgot-password",
                json={"email": "nobody@example.com"},
            )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_reset_password_valid(self, auth_client):
        token = create_reset_token("test@example.com", "hashed")
        user = _make_user()
        with (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.auth.router.update_user_password", new_callable=AsyncMock),
        ):
            resp = await auth_client.post(
                "/auth/reset-password",
                json={"token": token, "new_password": "newsecurepass"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_internal_account_cannot_reset_password(self, auth_client):
        token = create_reset_token("internal@example.com", "hashed")
        user = _make_user(email="internal@example.com")
        with (
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value={"plan_name": "internal"},
            ),
            patch(
                "app.auth.router.update_user_password", new_callable=AsyncMock
            ) as update_password,
        ):
            resp = await auth_client.post(
                "/auth/reset-password",
                json={"token": token, "new_password": "newsecurepass"},
            )

        assert resp.status_code == 403
        update_password.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reset_password_invalid_token(self, auth_client):
        resp = await auth_client.post(
            "/auth/reset-password",
            json={"token": "bad-token", "new_password": "newsecurepass"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_reset_password_too_short(self, auth_client):
        token = create_reset_token("test@example.com", "hashed")
        resp = await auth_client.post(
            "/auth/reset-password",
            json={"token": token, "new_password": "short"},
        )
        assert resp.status_code == 400


# ===========================================================================
# 9. Quick-key flow
# ===========================================================================


class TestQuickKey:
    def setup_method(self):
        auth_router_module._QUICKKEY_COOLDOWN.clear()
        auth_router_module._QUICKKEY_PENDING.clear()

    @pytest.mark.asyncio
    async def test_quick_key_endpoint_schedules_background_delivery(self, auth_client):
        with (
            patch(
                "app.auth.router._check_rate",
                return_value=(True, 0, 3600),
            ),
            patch("app.auth.router.fire_and_forget") as mock_ff,
        ):
            resp = await auth_client.post(
                "/auth/quick-key",
                json={"email": "quick@example.com"},
            )
            # Close the coroutine leaked by the mock to avoid RuntimeWarning
            if mock_ff.call_args is not None:
                coro = mock_ff.call_args.args[0]
                if hasattr(coro, "close"):
                    coro.close()

        assert resp.status_code == 202
        assert mock_ff.call_count == 1
        assert "quick@example.com" in auth_router_module._QUICKKEY_PENDING

    @pytest.mark.asyncio
    async def test_quick_key_endpoint_deduplicates_pending_email(self, auth_client):
        with (
            patch(
                "app.auth.router._check_rate",
                return_value=(True, 0, 3600),
            ),
            patch("app.auth.router.fire_and_forget") as mock_ff,
        ):
            first = await auth_client.post(
                "/auth/quick-key",
                json={"email": "quick@example.com"},
            )
            # Close coroutine from first call to avoid RuntimeWarning
            if mock_ff.call_args is not None:
                coro = mock_ff.call_args.args[0]
                if hasattr(coro, "close"):
                    coro.close()
            second = await auth_client.post(
                "/auth/quick-key",
                json={"email": "quick@example.com"},
            )

        assert first.status_code == 202
        assert second.status_code == 202
        assert mock_ff.call_count == 1

    @pytest.mark.asyncio
    async def test_deliver_quick_key_revokes_key_when_email_send_fails(self):
        db = AsyncMock()
        quick_user = _make_user(password="!quickkey:existing")

        with (
            patch("app.auth.router.async_session", new=_session_factory(db)),
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=quick_user,
            ),
            patch(
                "app.auth.router.count_user_active_keys",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "app.auth.router.insert_user_api_key",
                new_callable=AsyncMock,
                return_value=99,
            ),
            patch(
                "app.auth.router.send_api_key_email",
                new_callable=AsyncMock,
                side_effect=RuntimeError("smtp down"),
            ),
            patch(
                "app.auth.router.update_api_key_status",
                new_callable=AsyncMock,
            ) as update_status,
        ):
            auth_router_module._QUICKKEY_PENDING.add("quick@example.com")
            await auth_router_module._deliver_quick_key("quick@example.com")

        update_status.assert_awaited_once_with(db, 99, "revoked")
        assert "quick@example.com" not in auth_router_module._QUICKKEY_PENDING
        assert "quick@example.com" not in auth_router_module._QUICKKEY_COOLDOWN

    @pytest.mark.asyncio
    async def test_deliver_quick_key_sets_cooldown_on_success(self):
        db = AsyncMock()
        quick_user = _make_user(password="!quickkey:existing")

        with (
            patch("app.auth.router.async_session", new=_session_factory(db)),
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=quick_user,
            ),
            patch(
                "app.auth.router.count_user_active_keys",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "app.auth.router.insert_user_api_key",
                new_callable=AsyncMock,
                return_value=99,
            ),
            patch("app.auth.router.send_api_key_email", new_callable=AsyncMock),
            patch(
                "app.auth.router.verify_user_email", new_callable=AsyncMock
            ) as verify,
            patch(
                "app.auth.router.revoke_user_key", new_callable=AsyncMock
            ) as revoke_key,
        ):
            auth_router_module._QUICKKEY_PENDING.add("quick@example.com")
            await auth_router_module._deliver_quick_key("quick@example.com")

        revoke_key.assert_not_awaited()
        assert "quick@example.com" not in auth_router_module._QUICKKEY_PENDING
        assert "quick@example.com" in auth_router_module._QUICKKEY_COOLDOWN
        verify.assert_awaited_once()


# ===========================================================================
# 10. Profile update (authenticated)
# ===========================================================================


class TestProfile:
    @pytest.mark.asyncio
    async def test_get_me(self, authed_client):
        with patch(
            "app.auth.router.get_active_subscription",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = await authed_client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == "test@example.com"
        assert resp.json()["plan"] == "free"

    @pytest.mark.asyncio
    async def test_update_profile(self, authed_client):
        with (
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.auth.router.update_user_profile", new_callable=AsyncMock),
        ):
            resp = await authed_client.put(
                "/auth/profile",
                json={"name": "New Name", "company": "Corp"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_internal_account_cannot_update_profile(self, authed_client):
        with (
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value={"plan_name": "internal"},
            ),
            patch(
                "app.auth.router.update_user_profile", new_callable=AsyncMock
            ) as update_profile,
        ):
            resp = await authed_client.put(
                "/auth/profile",
                json={"name": "Changed", "company": "Changed"},
            )

        assert resp.status_code == 403
        update_profile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_change_password(self, authed_client):
        pw_hash = hash_password("oldpass1234")
        user = _make_user(password=pw_hash)
        with (
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
            patch("app.auth.router.update_user_password", new_callable=AsyncMock),
        ):
            resp = await authed_client.put(
                "/auth/password",
                json={"current_password": "oldpass1234", "new_password": "newpass5678"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, authed_client):
        pw_hash = hash_password("correct")
        user = _make_user(password=pw_hash)
        with (
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.auth.router.get_user_by_email",
                new_callable=AsyncMock,
                return_value=user,
            ),
        ):
            resp = await authed_client.put(
                "/auth/password",
                json={
                    "current_password": "wrong",
                    "new_password": "newpass5678",
                },
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_internal_account_cannot_change_password(self, authed_client):
        with patch(
            "app.auth.router.get_active_subscription",
            new_callable=AsyncMock,
            return_value={"plan_name": "internal"},
        ):
            resp = await authed_client.put(
                "/auth/password",
                json={
                    "current_password": "oldpass1234",
                    "new_password": "newpass5678",
                },
            )

        assert resp.status_code == 403


# ===========================================================================
# 11. Account deletion
# ===========================================================================


class TestAccountDeletion:
    @pytest.mark.asyncio
    async def test_delete_without_confirm(self, authed_client):
        resp = await authed_client.delete("/auth/account")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_with_confirm(self, authed_client):
        with (
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.auth.router.delete_user", new_callable=AsyncMock),
        ):
            resp = await authed_client.delete("/auth/account?confirm=true")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_internal_account_cannot_be_deleted(self, authed_client):
        with (
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value={"plan_name": "internal"},
            ),
            patch("app.auth.router.delete_user", new_callable=AsyncMock) as delete_user,
        ):
            resp = await authed_client.delete("/auth/account?confirm=true")

        assert resp.status_code == 403
        delete_user.assert_not_awaited()


class TestInternalKeyManagement:
    @pytest.mark.asyncio
    async def test_internal_account_cannot_create_key(self, authed_client):
        with patch(
            "app.auth.router.get_active_subscription",
            new_callable=AsyncMock,
            return_value={"plan_name": "internal"},
        ):
            resp = await authed_client.post(
                "/auth/keys",
                json={"name": "Extra key", "environment": "live"},
            )

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_internal_account_cannot_revoke_key(self, authed_client):
        with (
            patch(
                "app.auth.router.get_active_subscription",
                new_callable=AsyncMock,
                return_value={"plan_name": "internal"},
            ),
            patch("app.auth.router.revoke_user_key", new_callable=AsyncMock) as revoke,
        ):
            resp = await authed_client.delete("/auth/keys/42")

        assert resp.status_code == 403
        revoke.assert_not_awaited()


# ===========================================================================
# 12. Logout
# ===========================================================================


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout(self, auth_client):
        resp = await auth_client.post("/auth/logout")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_preproduction_logout_only_expires_dev_cookie(
        self, auth_client, monkeypatch
    ):
        monkeypatch.setattr(settings, "auth_cookie_name", "dev_access_token")
        monkeypatch.setattr(settings, "auth_cookie_domain", None)
        resp = await auth_client.post(
            "/auth/logout", cookies={"access_token": "Bearer production-token"}
        )
        cookie = resp.headers["set-cookie"]
        assert cookie.startswith("dev_access_token=")
        assert "Domain=" not in cookie
        assert not cookie.startswith("access_token=")


# ===========================================================================
# 13. check_quota dependency (TST-04)
# ===========================================================================


def _make_key_info(
    quota_monthly: int = 500,
    key_id: int = 42,
    user_id: int | None = None,
) -> ApiKeyInfo:
    """Return a minimal ApiKeyInfo for check_quota tests."""
    return ApiKeyInfo(
        id=key_id,
        name="test-key",
        quota_monthly=quota_monthly,
        revoked=False,
        created_at=datetime.now(UTC),
        user_id=user_id,
    )


def _make_request() -> MagicMock:
    """Return a minimal mock Request with the attributes check_quota accesses."""
    req = MagicMock()
    req.state = MagicMock()
    return req


class TestCheckQuota:
    @pytest.mark.asyncio
    async def test_internal_admin_bypasses_rate_and_monthly_quotas(self):
        # Entitlement comes from the account, not the denormalized key quota.
        key_info = _make_key_info(quota_monthly=500, user_id=99)
        request = _make_request()
        db = AsyncMock()

        with (
            patch(
                "app.auth.dependencies.get_active_subscription",
                new_callable=AsyncMock,
                return_value={"plan_name": "internal"},
            ),
            patch(
                "app.auth.dependencies.get_user_by_id",
                new_callable=AsyncMock,
                return_value={"is_verified": True, "is_admin": True},
            ),
            patch("app.auth.dependencies._check_rate") as check_rate,
            patch(
                "app.auth.dependencies.reserve_quota",
                new_callable=AsyncMock,
            ) as reserve,
        ):
            result = await check_quota(request, key_info, db)

        assert result == key_info
        check_rate.assert_not_called()
        reserve.assert_awaited_once()
        assert reserve.call_args.kwargs["limit"] == -1

    @pytest.mark.asyncio
    async def test_internal_sentinel_rejected_for_non_admin(self):
        key_info = _make_key_info(quota_monthly=-1, user_id=99)
        request = _make_request()
        db = AsyncMock()

        with (
            patch(
                "app.auth.dependencies.get_active_subscription",
                new_callable=AsyncMock,
                return_value={"plan_name": "internal"},
            ),
            patch(
                "app.auth.dependencies.get_user_by_id",
                new_callable=AsyncMock,
                return_value={"is_verified": True, "is_admin": False},
            ),
            pytest.raises(ErreurApiError) as exc_info,
        ):
            await check_quota(request, key_info, db)

        assert exc_info.value.code == 403

    @pytest.mark.asyncio
    async def test_internal_plan_rejected_for_unverified_admin(self):
        key_info = _make_key_info(quota_monthly=-1, user_id=99)
        request = _make_request()
        db = AsyncMock()

        with (
            patch(
                "app.auth.dependencies.get_active_subscription",
                new_callable=AsyncMock,
                return_value={"plan_name": "internal"},
            ),
            patch(
                "app.auth.dependencies.get_user_by_id",
                new_callable=AsyncMock,
                return_value={"is_verified": False, "is_admin": True},
            ),
            pytest.raises(ErreurApiError) as exc_info,
        ):
            await check_quota(request, key_info, db)

        assert exc_info.value.code == 403

    @pytest.mark.asyncio
    async def test_internal_sentinel_rejected_without_internal_plan(self):
        key_info = _make_key_info(quota_monthly=-1, user_id=99)
        request = _make_request()
        db = AsyncMock()

        with (
            patch(
                "app.auth.dependencies.get_active_subscription",
                new_callable=AsyncMock,
                return_value={"plan_name": "enterprise"},
            ),
            pytest.raises(ErreurApiError) as exc_info,
        ):
            await check_quota(request, key_info, db)

        assert exc_info.value.code == 403

    @pytest.mark.asyncio
    async def test_internal_sentinel_rejected_for_anonymous_key(self):
        key_info = _make_key_info(quota_monthly=-1)
        request = _make_request()
        db = AsyncMock()

        with pytest.raises(ErreurApiError) as exc_info:
            await check_quota(request, key_info, db)

        assert exc_info.value.code == 403

    @pytest.mark.asyncio
    async def test_under_quota_returns_key_info(self):
        """Happy path: rate limit OK, quota not exceeded — returns key_info."""
        key_info = _make_key_info(quota_monthly=500)
        request = _make_request()
        db = AsyncMock()

        # Rate limiter allows the request; increment_quota returns a count under limit
        with (
            patch(
                "app.auth.dependencies._check_rate",
                return_value=(True, 49, 60),
            ),
            patch(
                "app.auth.dependencies.reserve_quota",
                new_callable=AsyncMock,
                return_value=1,
            ),
        ):
            result = await check_quota(request, key_info, db)

        assert result == key_info
        assert request.state.api_key_id == key_info.id

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_raises_429(self):
        """Rate limiter denies the request — must raise ErreurApiError with code 429."""
        key_info = _make_key_info(quota_monthly=500)
        request = _make_request()
        db = AsyncMock()

        # Rate limiter blocks the request (allowed=False)
        with (
            patch(
                "app.auth.dependencies._check_rate",
                return_value=(False, 0, 5),
            ),
            pytest.raises(ErreurApiError) as exc_info,
        ):
            await check_quota(request, key_info, db)

        assert exc_info.value.code == 429
        # The frontend words this 429 as a request limit, not a quota: its title
        # must not start with "Quota mensuel" (frontend/src/lib/data/panne.ts, G1).
        assert exc_info.value.message == "Limite de debit atteinte"
        assert exc_info.value.headers is None

    @pytest.mark.asyncio
    async def test_quota_exceeded_raises_429(self):
        """Monthly quota exhausted — reservation refuses the exhausted quota."""
        key_info = _make_key_info(quota_monthly=500)
        request = _make_request()
        db = AsyncMock()

        # Rate limit passes, but quota is already maxed out (reservation returns None)
        with (
            patch(
                "app.auth.dependencies._check_rate",
                return_value=(True, 49, 60),
            ),
            patch(
                "app.auth.dependencies.reserve_quota",
                new_callable=AsyncMock,
                return_value=None,
            ),
            pytest.raises(ErreurApiError) as exc_info,
        ):
            await check_quota(request, key_info, db)

        assert exc_info.value.code == 429
        # The frontend names the monthly quota only from this problem title
        # (frontend/src/lib/data/panne.ts, 03-17 G1): renaming it needs both sides.
        assert exc_info.value.message == "Quota mensuel epuise"

    # -- D-06 identity isolation: two identities share neither bucket nor quota

    @pytest.mark.asyncio
    async def test_rate_limit_bucket_is_per_key_identity(self):
        """D-06: distinct keys use distinct `plan:{id}` rate-limit buckets, so
        one identity's traffic never consumes another's per-plan allowance.
        """
        captured_buckets: list[str] = []

        def _fake_check_rate(bucket: str, limit: int):
            captured_buckets.append(bucket)
            return (True, 1, 60)

        for key_id in (11, 22):
            key_info = _make_key_info(quota_monthly=500, key_id=key_id)
            request = _make_request()
            db = AsyncMock()
            with (
                patch(
                    "app.auth.dependencies._check_rate",
                    side_effect=_fake_check_rate,
                ),
                patch(
                    "app.auth.dependencies.reserve_quota",
                    new_callable=AsyncMock,
                    return_value=1,
                ),
            ):
                await check_quota(request, key_info, db)

        assert captured_buckets == ["plan:11", "plan:22"]

    @pytest.mark.asyncio
    async def test_monthly_quota_counted_per_key_identity(self):
        """D-06: the monthly quota counter is incremented under each key's own
        id -- two identities do not share a single quota row.
        """
        seen_key_ids: list[int] = []

        async def _fake_increment(db, *, key_id, user_id, month, limit):
            seen_key_ids.append(key_id)
            return 1

        for key_id in (11, 22):
            key_info = _make_key_info(quota_monthly=500, key_id=key_id)
            request = _make_request()
            db = AsyncMock()
            with (
                patch(
                    "app.auth.dependencies._check_rate",
                    return_value=(True, 49, 60),
                ),
                patch(
                    "app.auth.dependencies.reserve_quota",
                    side_effect=_fake_increment,
                ),
            ):
                await check_quota(request, key_info, db)

        assert seen_key_ids == [11, 22]
