"""Contract tests for the protected checkout preview (Phase 51, Plan 07).

``cd-api.yml`` deploys ``master`` only, so ``DC_GOLDEN_API_BASE_URL`` pointed at
an API built from a DIFFERENT commit than the ``dev`` checkout being replayed:
a green golden replay proved nothing about the code under test (T-51-28). The
preview closes that repudiation gap by serving the checkout itself, on loopback,
against the production database forced read-only (T-51-27).

Everything here is offline: pure functions, constants and source reads. The
preview app is never built and no engine is ever created -- constructing it
would require the protected DSN and would mutate the process-wide settings that
the rest of the suite depends on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from scripts.serve_golden_preview import (
    API_KEY_ENV,
    DSN_ENV,
    ENGINE_SERVER_SETTINGS,
    EXPECTED_SHA_ENV,
    PREVIEW_HOST,
    PREVIEW_PLACEHOLDER_ENV,
    PREVIEW_SHA_PATH,
    PREVIEW_SMOKE_MARKER,
    GoldenPreviewError,
    _preview_auth_dependency,
    assert_expected_sha,
    assert_read_only_state,
    async_dsn,
    preview_api_key_guard,
    uvicorn_config,
)
from scripts.verify_golden_cases import (
    SMOKE_HEADER,
    GoldenSecurityError,
    _assert_loopback_base_url,
    _assert_served_checkout_sha,
)

_BACKEND = Path(__file__).resolve().parents[1]
_PREVIEW_PATH = _BACKEND / "scripts" / "serve_golden_preview.py"
_PREVIEW_SOURCE = _PREVIEW_PATH.read_text(encoding="utf-8")
_MIDDLEWARE_SOURCE = (_BACKEND / "app" / "middleware.py").read_text(encoding="utf-8")


def _executable_source(path: Path) -> str:
    """Return the source with comments and string literals removed.

    The forbidden-literal scans below are about what the preview DOES, not
    about the prose explaining why. Naming ``api_request_log`` in a comment
    that documents why it is unreachable must not fail the guard that proves
    it IS unreachable -- and a comment can never execute anything.
    """
    import io
    import tokenize

    kept: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in {tokenize.COMMENT, tokenize.STRING}:
                continue
            kept.append(token.string)
    return " ".join(kept)


_PREVIEW_CODE = _executable_source(_PREVIEW_PATH)

_SHA = "a" * 40
_OTHER_SHA = "b" * 40
_DSN_SENTINEL = "postgresql://golden_reader:preview-sentinel@192.0.2.9:5432/datacommune"


# --- loopback bind, no access log (T-51-27) -----------------------------------


def test_preview_binds_loopback_only() -> None:
    assert PREVIEW_HOST == "127.0.0.1"
    # A wildcard bind would publish the production database behind an
    # unauthenticated-by-design preview on the self-hosted runner's network.
    assert "0.0.0.0" not in _PREVIEW_SOURCE


def test_preview_uvicorn_config_is_loopback_without_access_log() -> None:
    async def _app(scope, receive, send) -> None:  # pragma: no cover - ASGI stub
        return None

    config = uvicorn_config(_app, port=8811)
    assert config.host == PREVIEW_HOST
    assert config.port == 8811
    # The access log would print every replayed /v1/resolve query string --
    # that is the private address of a golden case.
    assert config.access_log is False


# --- read-only engine + startup assertion (T-51-27) ---------------------------


def test_preview_engine_forces_read_only_on_every_connection() -> None:
    assert ENGINE_SERVER_SETTINGS["default_transaction_read_only"] == "on"
    assert ENGINE_SERVER_SETTINGS["statement_timeout"] == "15000"


def test_preview_startup_refuses_a_writable_transaction() -> None:
    assert_read_only_state("on")
    for state in ("off", "", None, "ON "):
        with pytest.raises(GoldenPreviewError, match="read-only"):
            assert_read_only_state(state)


# --- protected DSN handling ---------------------------------------------------


def test_preview_dsn_is_converted_in_memory_without_rendering_the_url() -> None:
    converted = async_dsn(_DSN_SENTINEL)
    assert converted.startswith("postgresql+asyncpg://")
    assert converted.endswith(
        "golden_reader:preview-sentinel@192.0.2.9:5432/datacommune"
    )
    # The DSN is read from the environment by NAME and never hard-coded.
    assert "preview-sentinel" not in _PREVIEW_SOURCE
    assert DSN_ENV == "DC_GOLDEN_READONLY_DSN"


def test_preview_dsn_rejects_anything_but_postgresql() -> None:
    for raw in ("", "sqlite:///tmp.db", "not-a-url", "mysql://host/db"):
        with pytest.raises(GoldenPreviewError):
            async_dsn(raw)


def test_preview_accepts_an_already_async_dsn() -> None:
    assert async_dsn("postgresql+asyncpg://u:p@h/db").startswith(
        "postgresql+asyncpg://"
    )


# --- constant-time key, no DB lookup, no quota, no request log ----------------


def test_preview_api_key_is_compared_without_a_database_lookup() -> None:
    preview_api_key_guard("expected-key", "expected-key")
    with pytest.raises(GoldenPreviewError):
        preview_api_key_guard("wrong-key", "expected-key")
    with pytest.raises(GoldenPreviewError):
        preview_api_key_guard(None, "expected-key")
    # An empty expected key must never authorize everything.
    with pytest.raises(GoldenPreviewError):
        preview_api_key_guard("", "")
    assert API_KEY_ENV == "DC_GOLDEN_API_KEY"


def test_preview_auth_uses_a_timing_safe_comparison() -> None:
    assert "compare_digest" in _PREVIEW_CODE


def test_preview_never_reaches_quota_migration_or_request_log() -> None:
    for forbidden in (
        "_enforce_quota",
        "_resolve_api_key",
        "api_request_log",
        "INSERT",
        "UPDATE",
    ):
        assert forbidden not in _PREVIEW_CODE


def test_preview_smoke_marker_suppresses_the_request_log_insert() -> None:
    # The middleware skips the api_request_log INSERT when the constant-time
    # smoke header matches settings.smoke_test_api_key. The preview sets that
    # setting to the marker the verifier sends, so no write is ever attempted.
    assert PREVIEW_PLACEHOLDER_ENV["SMOKE_TEST_API_KEY"] == PREVIEW_SMOKE_MARKER
    assert SMOKE_HEADER == ("X-Smoke-Test", PREVIEW_SMOKE_MARKER)
    assert "x-smoke-test" in _MIDDLEWARE_SOURCE
    assert "smoke_test_api_key" in _MIDDLEWARE_SOURCE


def test_preview_auth_reads_the_key_from_the_header_not_the_query() -> None:
    """The override must authorize on ``X-API-Key`` and on nothing else.

    Regression (run 32369100409, 9/9 cases ``401``): ``Annotated`` and ``Header``
    were imported INSIDE ``_preview_auth_dependency``, so under the module's
    ``from __future__ import annotations`` FastAPI could not resolve the string
    annotation and silently degraded ``x_api_key`` to an optional QUERY
    parameter. The header was never read, the guard always saw ``None`` and every
    protected route answered 401 -- with the correct key too. The query-parameter
    assertion below is the one that fails on the broken build.

    The probe app is a bare FastAPI instance: no ``app.main``, no engine, no DSN
    and no process-wide settings mutation, exactly as this module requires.
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    probe = FastAPI()

    @probe.get(
        "/probe", dependencies=[Depends(_preview_auth_dependency("expected-key"))]
    )
    async def _probe() -> dict[str, bool]:
        return {"ok": True}

    def status(**kwargs: Any) -> int:
        with TestClient(probe) as client:
            return client.get("/probe", **kwargs).status_code

    assert status(headers={"X-API-Key": "expected-key"}) == 200
    assert status(headers={"X-API-Key": "wrong-key"}) == 401
    assert status() == 401
    # A key smuggled through the query string must never authorize: that is
    # precisely what the broken build accepted while rejecting the header.
    assert status(params={"x_api_key": "expected-key"}) == 401


# --- placeholders keep DEBUG false and carry no production identity -----------


def test_preview_placeholders_keep_debug_false() -> None:
    assert PREVIEW_PLACEHOLDER_ENV["DEBUG"] == "false"
    # DEBUG=true would make SQLAlchemy echo every statement AND its bound
    # parameters -- the replayed private address among them.
    assert set(PREVIEW_PLACEHOLDER_ENV) == {
        "DEBUG",
        "JWT_SECRET_KEY",
        "ADMIN_PASSWORD",
        "RESEND_API_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "API_KEY_HMAC_SECRET",
        "API_BASE_URL",
        "SMOKE_TEST_API_KEY",
    }


def test_preview_placeholders_are_not_production_identities() -> None:
    assert PREVIEW_PLACEHOLDER_ENV["API_BASE_URL"] == "https://golden-preview.invalid"
    for name, value in PREVIEW_PLACEHOLDER_ENV.items():
        if name in {"DEBUG", "API_BASE_URL", "SMOKE_TEST_API_KEY"}:
            continue
        assert "golden-preview" in value


def test_preview_deploys_nothing() -> None:
    for forbidden in ("docker", "ssh", "caddy", "compose", "deploy"):
        assert forbidden not in _PREVIEW_CODE.lower()


# --- served checkout SHA (T-51-28) --------------------------------------------


def test_preview_exposes_the_checkout_sha_on_a_private_route() -> None:
    assert PREVIEW_SHA_PATH == "/__golden/checkout-sha"
    assert EXPECTED_SHA_ENV == "GITHUB_SHA"


def test_preview_refuses_to_serve_a_commit_other_than_the_checkout() -> None:
    assert_expected_sha(_SHA, _SHA)
    for served, expected in ((_SHA, _OTHER_SHA), (_SHA, None), (_SHA, ""), ("", _SHA)):
        with pytest.raises(GoldenPreviewError, match="checkout"):
            assert_expected_sha(served, expected)


def test_preview_sha_must_be_a_full_commit_hash() -> None:
    with pytest.raises(GoldenPreviewError):
        assert_expected_sha(_SHA[:7], _SHA[:7])


# --- verifier side: loopback + provenance before any private input ------------


def test_verifier_refuses_a_non_loopback_base_url() -> None:
    _assert_loopback_base_url("http://127.0.0.1:8811")
    for url in (
        "https://api.datacommune.fr",
        "http://localhost:8811",
        "http://127.0.0.1.evil.invalid",
        "http://0.0.0.0:8811",
        "",
    ):
        with pytest.raises(GoldenSecurityError, match="loopback"):
            _assert_loopback_base_url(url)


class _ShaClient:
    def __init__(self, payload: Any) -> None:
        self.calls: list[str] = []
        self._payload = payload

    def get(self, path: str, params: Any = None) -> Any:
        self.calls.append(path)

        class _Response:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> Any:
                return payload

        payload = self._payload
        return _Response()


def test_verifier_reads_the_served_checkout_sha(monkeypatch) -> None:
    monkeypatch.setenv(EXPECTED_SHA_ENV, _SHA)
    client = _ShaClient({"served_checkout_sha": _SHA})
    assert _assert_served_checkout_sha(client, EXPECTED_SHA_ENV) == _SHA
    assert client.calls == [PREVIEW_SHA_PATH]


def test_verifier_blocks_when_the_served_sha_differs(monkeypatch) -> None:
    monkeypatch.setenv(EXPECTED_SHA_ENV, _OTHER_SHA)
    client = _ShaClient({"served_checkout_sha": _SHA})
    with pytest.raises(GoldenSecurityError):
        _assert_served_checkout_sha(client, EXPECTED_SHA_ENV)


def test_verifier_blocks_on_a_malformed_sha_endpoint(monkeypatch) -> None:
    monkeypatch.setenv(EXPECTED_SHA_ENV, _SHA)
    for payload in ({}, {"served_checkout_sha": None}, [], {"sha": _SHA}):
        with pytest.raises(GoldenSecurityError):
            _assert_served_checkout_sha(_ShaClient(payload), EXPECTED_SHA_ENV)


def test_verifier_requires_the_expected_sha_to_be_present(monkeypatch) -> None:
    monkeypatch.delenv(EXPECTED_SHA_ENV, raising=False)
    with pytest.raises(GoldenSecurityError):
        _assert_served_checkout_sha(
            _ShaClient({"served_checkout_sha": _SHA}), EXPECTED_SHA_ENV
        )


def test_verifier_source_no_longer_references_the_deployed_api(monkeypatch) -> None:
    verifier = (_BACKEND / "scripts" / "verify_golden_cases.py").read_text(
        encoding="utf-8"
    )
    assert "DC_GOLDEN_API_BASE_URL" not in verifier
    assert re.search(r"served_checkout_sha", verifier) is not None
