"""Shared test fixtures -- auth bypass, rate limit bypass, async HTTP client."""

import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

# No upstream download at boot in the tests (set before the settings load).
os.environ.setdefault("PRECHAUFFAGE_AMONTS", "false")

from app.auth.dependencies import check_quota, check_quota_hybrid
from app.auth.models import ApiKeyInfo
from app.main import app
from app.rate_limit import _buckets

_DESTRUCTIVE_DB_OPT_IN = "ALLOW_DESTRUCTIVE_DB_TESTS"
_TESTS_DIR = Path(__file__).resolve().parent
_DESTRUCTIVE_DB_ITEMS: dict[str, frozenset[str] | None] = {
    "test_equipements_reprojection.py": None,
    "test_geo_gate.py": frozenset(
        {
            "test_gate_smoke_clean_staging_returns_none",
            "test_gate_smoke_polluted_staging_returns_abort",
        }
    ),
    "test_transport_geo.py": frozenset({"test_lambert_fake_coords_produce_error"}),
}


def _is_destructive_db_test(item: pytest.Item) -> bool:
    """Identify tests that write to a real PostgreSQL connection."""
    if item.get_closest_marker("destructive") is not None:
        return True
    try:
        relative_path = Path(item.path).resolve().relative_to(_TESTS_DIR).as_posix()
    except ValueError:
        return False

    if relative_path.startswith("integration/"):
        return True
    if relative_path not in _DESTRUCTIVE_DB_ITEMS:
        return False

    selected_items = _DESTRUCTIVE_DB_ITEMS[relative_path]
    return selected_items is None or item.name in selected_items


def _install_offline_database_guard(item: pytest.Item) -> pytest.MonkeyPatch | None:
    """Block native connections while allowing tests to install explicit doubles."""
    if _is_destructive_db_test(item):
        return None

    def refuse_connection(*args, **kwargs):
        # Failed inherits BaseException: application recovery must not hide this.
        pytest.fail(
            "Offline test attempted a PostgreSQL connection; install an explicit "
            "connection double or use the declared destructive test harness.",
            pytrace=False,
        )

    async def refuse_at_once(*args, **kwargs):
        # The app's own engine (asyncpg) finds no database in an offline test, by
        # design: its reads degrade or the test expects the refusal. Linux (CI)
        # refuses a closed loopback port at once; Windows takes 2 s per address,
        # 4.1 s for ``localhost`` (::1 then 127.0.0.1), which made about sixty
        # tests of ``/v1/resolve`` wait 4.1 s each (measured 2026-11-13). Refuse
        # the way CI does, with the same error.
        raise ConnectionRefusedError(111, "Connect call failed (offline test)")

    guard = pytest.MonkeyPatch()
    for target in (psycopg, psycopg.Connection, psycopg.AsyncConnection):
        guard.setattr(target, "connect", refuse_connection)
    guard.setattr(asyncpg, "connect", refuse_at_once)
    return guard


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    """Enclose higher-scope fixture setup and teardown, not only test calls."""
    guard = _install_offline_database_guard(item)
    try:
        yield
    finally:
        if guard is not None:
            guard.undo()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply both destructive DB gates without changing test behavior."""
    opted_in = os.environ.get(_DESTRUCTIVE_DB_OPT_IN) == "1"
    opt_in_guard = pytest.mark.skipif(
        not opted_in,
        reason=f"set {_DESTRUCTIVE_DB_OPT_IN}=1 to run destructive DB tests",
    )
    for item in items:
        if _is_destructive_db_test(item):
            item.add_marker(pytest.mark.destructive)
            item.add_marker(opt_in_guard)


def _fake_key_info() -> ApiKeyInfo:
    """Return a fake API key info for testing (bypasses DB lookup)."""
    return ApiKeyInfo(
        id=1,
        name="test-key",
        quota_monthly=999999,
        revoked=False,
        created_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Reset rate limiter state between tests to prevent 429s."""
    _buckets.clear()


@pytest.fixture(autouse=True)
def _clear_resend_verif_cooldown(monkeypatch):
    """Reset admin resend-verification cooldown dict between tests.

    Use monkeypatch to install a FRESH empty dict before each test. This is
    more robust than .clear() because:
    - any live reference to the old dict still behaves as expected within
      the test (monkeypatch will undo at teardown);
    - it isolates tests against any late-import path that might have
      captured a stale reference during collection.
    """
    from app.admin import queries as _q

    monkeypatch.setattr(_q, "_resend_verif_last_sent", {})


@pytest.fixture()
def full_scope_loader():
    """Scope loader hosting the whole catalogue (minus ``commune``) on 102 depts.

    Opens no connection. Same scope as the ``full_instance_scope`` factory of
    ``tests/integration/conftest.py`` writes into a database.
    """
    from app.common.source_catalog import DATASET_CATALOG
    from app.etl.base import DEPARTEMENTS
    from app.scope import SCOPE_EXCLUDED_KEYS, InstanceScope

    scope = InstanceScope(
        datasets=frozenset(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS,
        departements=tuple(DEPARTEMENTS),
    )
    return lambda _dsn: scope


@pytest.fixture(autouse=True)
def _explicit_test_instance_scope(monkeypatch, full_scope_loader):
    """Give every in-process PipelineRunner a declared test scope (D-21).

    Production has no scope default: the runner reads the database and an
    empty scope imports nothing. Tests declare theirs here instead of in
    ``app/``. The real database read is proven by
    ``tests/integration/test_instance_scope.py`` (explicit loader) and by
    tests that launch ``run_etl.py`` in a subprocess, which this substitution
    does not reach and which write their scope with ``full_instance_scope``.
    """
    monkeypatch.setattr("app.etl.runner.default_scope_loader", full_scope_loader)


@pytest.fixture()
def host_test_datasets(monkeypatch, full_scope_loader):
    """Factory: extend the test scope with synthetic runner datasets.

    Runner tests use fake pipelines whose names are not catalogue keys; each
    such module declares the names it hosts instead of weakening the check.
    """
    from app.scope import InstanceScope

    def host(*names: str) -> None:
        base = full_scope_loader("")
        scope = InstanceScope(base.datasets | frozenset(names), base.departements)
        monkeypatch.setattr("app.etl.runner.default_scope_loader", lambda _dsn: scope)

    return host


@pytest.fixture()
def bind_departements(monkeypatch, full_scope_loader):
    """Factory: declare the departements of the test scope (D-22, D-23).

    Departmental pipelines read their departements from the bound scope and
    have no default. The scope (catalogue x *departements*) is bound to the
    given pipeline classes, for tests calling their methods without a
    runner, and served to every in-process runner.
    """
    from app.scope import InstanceScope

    def bind(departements, *classes, datasets=()):
        base = full_scope_loader("")
        scope = InstanceScope(base.datasets | frozenset(datasets), tuple(departements))
        monkeypatch.setattr("app.etl.runner.default_scope_loader", lambda _dsn: scope)
        for cls in classes:
            monkeypatch.setattr(cls, "_bound_scope", scope, raising=False)
        return scope

    return bind


@pytest.fixture()
async def client(request, full_scope_loader):
    """Async HTTP client with auth bypassed.

    Uses FastAPI dependency_overrides to skip DB-backed API key
    verification. Tests hit the real app code but without auth.
    """
    # DB-free HTTP unit tests bypass the publication DB check explicitly.
    # PostGIS tests must remove this override to exercise the actual guard.
    from app.common.publication_readiness import require_publication
    from app.scope.dependencies import get_instance_scope

    if "integration" not in Path(request.node.path).parts:
        app.dependency_overrides[require_publication] = lambda: None
        # DB-free HTTP unit tests host the whole catalogue; PostGIS tests read
        # the scope their fixtures write into the database (D-23).
        scope = full_scope_loader("")
        app.dependency_overrides[get_instance_scope] = lambda: scope
    app.dependency_overrides[check_quota] = _fake_key_info
    app.dependency_overrides[check_quota_hybrid] = _fake_key_info
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
