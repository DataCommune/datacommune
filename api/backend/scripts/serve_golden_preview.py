#!/usr/bin/env python3
"""CI-only preview of the CHECKED-OUT commit, on loopback, read-only.

``cd-api.yml`` deploys ``master`` only. The golden replay used to target the
deployed API through ``DC_GOLDEN_API_BASE_URL``, so it exercised a commit that
was not the ``dev`` checkout being verified: a green replay proved nothing about
the code under review (T-51-28, repudiation). This entrypoint removes that gap
without deploying anything -- it serves the checkout itself and proves which
commit it serves.

Hard invariants, each locked by ``tests/test_golden_preview.py``:

* binds ``127.0.0.1`` only, never a wildcard, and disables the access log (the
  replayed ``/v1/resolve`` query string IS a private address);
* the protected DSN is read from a NAMED environment variable, converted to its
  asyncpg form in memory, and never logged or serialized;
* every connection carries ``default_transaction_read_only=on`` and startup
  refuses to serve unless PostgreSQL confirms it (T-51-27);
* authentication is a constant-time comparison against the protected preview
  key -- no database lookup, no quota accounting, no key-hash migration;
* the request-log INSERT is unreachable: the preview declares the smoke marker
  the verifier sends, which the middleware uses to skip it;
* ``GET /__golden/checkout-sha`` returns ``git rev-parse HEAD``, compared to the
  expected commit before the process is allowed to serve anything.

It is NOT importable by the application and is never collected by pytest (it
lives under ``scripts/`` and exposes no ``test_*``).
"""

from __future__ import annotations

import argparse
import hmac
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import Header, HTTPException

# Support the documented direct invocation from ``api/backend`` while keeping
# package imports identical when the module is loaded by pytest.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# See the matching comment in scripts/verify_golden_cases.py (57-08 deviation):
# the deployed Docker image flattens api/backend/ to /app (3 parents, not 4),
# so parents[3] would raise IndexError when this module is imported there --
# harmless since serve_golden_preview() (the only reader of _REPO_ROOT) never
# runs outside the CI-only golden-live preview job.
_FILE_PARENTS = Path(__file__).resolve().parents
_REPO_ROOT = _FILE_PARENTS[3] if len(_FILE_PARENTS) > 3 else _FILE_PARENTS[-1]

PREVIEW_HOST = "127.0.0.1"
PREVIEW_SHA_PATH = "/__golden/checkout-sha"
PREVIEW_SMOKE_MARKER = "golden-preview-loopback-marker"
DSN_ENV = "DC_GOLDEN_READONLY_DSN"
API_KEY_ENV = "DC_GOLDEN_API_KEY"
EXPECTED_SHA_ENV = "GITHUB_SHA"

# Applied to EVERY pooled connection. ``default_transaction_read_only=on`` makes
# a write fail at the server, not at a Python guard that a future refactor could
# route around; the timeout mirrors the application engine.
ENGINE_SERVER_SETTINGS: dict[str, str] = {
    "default_transaction_read_only": "on",
    "statement_timeout": "15000",
}

_PLACEHOLDER = "golden-preview-local-placeholder"
# ``app.config`` fail-fast refuses to boot with DEBUG=false and insecure
# defaults. These are LOCAL placeholders, not production identities: none of
# them authenticates anything, and the preview reaches no external service.
# DEBUG stays false on purpose -- DEBUG=true makes SQLAlchemy echo every
# statement AND its bound parameters, which would print the replayed address.
PREVIEW_PLACEHOLDER_ENV: dict[str, str] = {
    "DEBUG": "false",
    "JWT_SECRET_KEY": _PLACEHOLDER,
    "ADMIN_PASSWORD": _PLACEHOLDER,
    "RESEND_API_KEY": _PLACEHOLDER,
    "STRIPE_WEBHOOK_SECRET": _PLACEHOLDER,
    "API_KEY_HMAC_SECRET": _PLACEHOLDER,
    "API_BASE_URL": "https://golden-preview.invalid",
    "SMOKE_TEST_API_KEY": PREVIEW_SMOKE_MARKER,
}

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ASYNC_PREFIX = "postgresql+asyncpg://"


class GoldenPreviewError(RuntimeError):
    """Raised when a preview guard fails closed."""


def async_dsn(raw: str) -> str:
    """Convert a protected PostgreSQL DSN to its asyncpg form, in memory only.

    The value is never logged, echoed or written anywhere: it is read by
    environment-variable NAME and handed straight to the engine factory.
    """
    if not raw or "://" not in raw:
        raise GoldenPreviewError("preview DSN is missing or is not a URL")
    scheme, _, rest = raw.partition("://")
    if scheme.split("+", 1)[0] not in {"postgresql", "postgres"}:
        raise GoldenPreviewError("preview DSN must be a PostgreSQL URL")
    return f"{_ASYNC_PREFIX}{rest}"


def assert_read_only_state(state: Any) -> None:
    """Refuse to serve unless PostgreSQL itself confirms the read-only state."""
    if state != "on":
        raise GoldenPreviewError("preview transaction is not read-only")


def preview_api_key_guard(header: Any, expected: str) -> None:
    """Compare the preview key in constant time -- no database, no quota.

    An empty expected key must never authorize everything: that would turn the
    preview of a production database into an open endpoint on the runner.
    """
    if not expected:
        raise GoldenPreviewError("preview API key is not configured")
    if not isinstance(header, str) or not hmac.compare_digest(header, expected):
        raise GoldenPreviewError("preview API key rejected")


def checkout_sha() -> str:
    """Return ``git rev-parse HEAD`` for the checkout this process serves."""
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    sha = completed.stdout.strip()
    if _SHA_RE.match(sha) is None:
        raise GoldenPreviewError("checkout commit could not be determined")
    return sha


def assert_expected_sha(served: Any, expected: Any) -> None:
    """Bind the served commit to the expected one -- exact equality, no prefix."""
    if not isinstance(served, str) or _SHA_RE.match(served) is None:
        raise GoldenPreviewError("served checkout commit is malformed")
    if not isinstance(expected, str) or _SHA_RE.match(expected) is None:
        raise GoldenPreviewError("expected checkout commit is missing or malformed")
    if not hmac.compare_digest(served, expected):
        raise GoldenPreviewError("served commit differs from the expected checkout")


def uvicorn_config(app: Any, *, port: int) -> uvicorn.Config:
    """Loopback-only server config with the access log switched off."""
    return uvicorn.Config(
        app,
        host=PREVIEW_HOST,
        port=port,
        access_log=False,
        log_level="warning",
        lifespan="on",
    )


def _apply_placeholder_environment(dsn: str) -> None:
    """Set the local placeholders BEFORE ``app.config`` is ever imported."""
    os.environ["DATABASE_URL"] = async_dsn(dsn)
    for name, value in PREVIEW_PLACEHOLDER_ENV.items():
        os.environ[name] = value


def _preview_engine(dsn: str) -> Any:
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(
        async_dsn(dsn),
        echo=False,
        pool_pre_ping=True,
        # Same privacy setting as app.database (PRIV-01, C58).
        hide_parameters=True,
        connect_args={"server_settings": dict(ENGINE_SERVER_SETTINGS)},
    )


async def _assert_engine_is_read_only(engine: Any) -> None:
    from sqlalchemy import text

    async with engine.connect() as conn:
        result = await conn.execute(text("SHOW transaction_read_only"))
        assert_read_only_state(result.scalar())


def build_preview_app(dsn: str, api_key: str, *, expected_sha: str | None) -> Any:
    """Build the checkout's FastAPI app, rebound to a read-only preview engine.

    Order matters and is the whole point: the placeholders and the DSN are in
    the environment before ``app.config`` loads, and ``app.database`` is rebound
    before ``app.main`` imports it -- ``app.main`` binds ``engine`` at import
    time, so a later replacement would leave the application on its own pool.
    """
    _apply_placeholder_environment(dsn)
    served_sha = checkout_sha()
    assert_expected_sha(served_sha, expected_sha)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.database as database

    engine = _preview_engine(dsn)
    database.engine = engine
    database.async_session = async_sessionmaker(engine, expire_on_commit=False)

    from app.auth.dependencies import check_quota_hybrid
    from app.main import app

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def guarded_lifespan(scoped_app: Any) -> AsyncIterator[None]:
        async with original_lifespan(scoped_app):
            await _assert_engine_is_read_only(engine)
            yield

    app.router.lifespan_context = guarded_lifespan
    app.dependency_overrides[check_quota_hybrid] = _preview_auth_dependency(api_key)
    _mount_sha_route(app, served_sha)
    return app


def _preview_auth_dependency(api_key: str) -> Callable[..., Any]:
    """Replace the quota dependency with a key comparison and nothing else.

    ``Annotated``/``Header`` MUST stay importable from the module globals: this
    module runs under ``from __future__ import annotations``, so FastAPI resolves
    ``preview_auth``'s annotations as strings against ``__globals__``. Imported
    inside this function, they resolve to nothing, FastAPI silently degrades
    ``x_api_key`` to an optional QUERY parameter, the header is never read and
    every protected route answers 401 -- with the correct key too.
    """

    async def preview_auth(
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    ) -> None:
        try:
            preview_api_key_guard(x_api_key, api_key)
        except GoldenPreviewError:
            raise HTTPException(
                status_code=401, detail="preview key rejected"
            ) from None

    return preview_auth


def _mount_sha_route(app: Any, served_sha: str) -> None:
    @app.get(PREVIEW_SHA_PATH, include_in_schema=False)
    async def _served_checkout_sha() -> dict[str, str]:
        return {"served_checkout_sha": served_sha}


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise GoldenPreviewError(f"required environment variable is missing: {name}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Golden checkout preview (CI only)")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        app = build_preview_app(
            _required_env(DSN_ENV),
            _required_env(API_KEY_ENV),
            expected_sha=os.getenv(EXPECTED_SHA_ENV),
        )
    except GoldenPreviewError as exc:
        # Guard messages are static constants: no DSN, no key, no address can
        # transit through them.
        print(f"golden preview blocked: {exc}", file=sys.stderr)
        return 1
    uvicorn.Server(uvicorn_config(app, port=args.port)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
