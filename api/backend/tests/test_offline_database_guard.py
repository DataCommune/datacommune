"""Offline database guard proofs using native sentinels, never a database."""

from types import SimpleNamespace

import psycopg
import pytest

from tests import conftest as shared


def _item(path="test_synthetic_offline.py", *, destructive=False):
    marks = []
    return SimpleNamespace(
        path=shared._TESTS_DIR / path,
        name="test_synthetic",
        get_closest_marker=lambda name: (
            pytest.mark.destructive if name == "destructive" and destructive else None
        ),
        add_marker=marks.append,
        marks=marks,
    )


@pytest.fixture
def native_sentinels(monkeypatch):
    calls = []

    def native(*args, **kwargs):
        calls.append((args, kwargs))
        return "native-sentinel"

    for target in (psycopg, psycopg.Connection, psycopg.AsyncConnection):
        monkeypatch.setattr(target, "connect", native)
    return calls


def _install(item):
    installer = getattr(shared, "_install_offline_database_guard", lambda item: None)
    return installer(item)


@pytest.mark.parametrize(
    "target", [psycopg, psycopg.Connection, psycopg.AsyncConnection]
)
def test_offline_connections_fail_before_native_call(target, native_sentinels):
    guard = _install(_item())
    try:
        with pytest.raises(pytest.fail.Exception, match="Offline test"):
            target.connect("sentinel-only")
        assert native_sentinels == []
    finally:
        if guard is not None:
            guard.undo()


def test_application_exception_handler_cannot_hide_attempt(native_sentinels):
    guard = _install(_item())
    swallowed = []
    try:
        with pytest.raises(pytest.fail.Exception):
            try:
                psycopg.connect("sentinel-only")
            except Exception:  # noqa: BLE001
                swallowed.append(True)
        assert native_sentinels == []
        assert swallowed == []
    finally:
        if guard is not None:
            guard.undo()


def test_explicit_fake_connection_remains_allowed(monkeypatch, native_sentinels):
    guard = _install(_item())
    try:
        with monkeypatch.context() as fake:
            fake.setattr(psycopg, "connect", lambda *args, **kwargs: "fake-connection")
            assert psycopg.connect() == "fake-connection"
        assert native_sentinels == []
    finally:
        if guard is not None:
            guard.undo()
    assert psycopg.connect() == "native-sentinel"


@pytest.mark.parametrize(
    "item", [_item("integration/test_synthetic.py"), _item(destructive=True)]
)
def test_declared_database_tests_preserve_native_path(item, native_sentinels):
    assert shared._is_destructive_db_test(item)
    assert _install(item) is None
    assert psycopg.connect() == "native-sentinel"
    assert len(native_sentinels) == 1


@pytest.mark.parametrize("opt_in", [False, True])
def test_existing_destructive_collection_gate_is_preserved(monkeypatch, opt_in):
    monkeypatch.setenv(shared._DESTRUCTIVE_DB_OPT_IN, "1" if opt_in else "0")
    item = _item("integration/test_synthetic.py")
    shared.pytest_collection_modifyitems([item])
    assert [mark.name for mark in item.marks] == ["destructive", "skipif"]
    assert item.marks[1].args == (not opt_in,)


def test_protocol_encloses_fixture_setup_and_teardown(native_sentinels):
    protocol = getattr(shared, "pytest_runtest_protocol", None)
    assert protocol is not None, "offline guard must enclose fixture setup"
    lifecycle = protocol(_item(), None)
    next(lifecycle)
    try:
        for phase in ("setup", "teardown"):
            with pytest.raises(pytest.fail.Exception):
                psycopg.connect(phase)
        assert native_sentinels == []
    finally:
        lifecycle.close()
    assert psycopg.connect() == "native-sentinel"
