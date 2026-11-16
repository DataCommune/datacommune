"""Unit tests: boot-time schema drift gate (ADR-0003 D2).

The gate is asymmetric — DB behind the code fails fast, DB ahead only warns.
These tests drive the pure decision function against the REAL embedded
ScriptDirectory (no DB needed): only the "DB revision" input varies. A fifth
test proves the ALLOW_SCHEMA_DRIFT escape hatch short-circuits before any DB
access.
"""

import logging
from unittest.mock import MagicMock

import pytest

from app.common.migration_check import (
    _evaluate_revisions,
    _load_script_directory,
    check_migrations_current,
)

pytestmark = pytest.mark.unit


def _head_and_parent() -> tuple[str, str]:
    script = _load_script_directory()
    head = script.get_current_head()
    parent = script.get_revision(head).down_revision
    if isinstance(parent, tuple):  # merge revision
        parent = parent[0]
    assert head and parent, "expected a linear history with a real parent"
    return head, parent


def test_db_at_head_passes():
    """DB revision == embedded head -> nominal boot, no raise."""
    script = _load_script_directory()
    head = script.get_current_head()
    _evaluate_revisions({head}, script)


def test_db_behind_raises():
    """DB at a known-but-older revision -> blocking RuntimeError."""
    _, parent = _head_and_parent()
    script = _load_script_directory()
    with pytest.raises(RuntimeError, match="BLOQUANTE"):
        _evaluate_revisions({parent}, script)


def test_alembic_version_absent_raises():
    """No alembic_version row (fresh/incomplete DB) is treated as behind."""
    script = _load_script_directory()
    with pytest.raises(RuntimeError, match="BLOQUANTE"):
        _evaluate_revisions(set(), script)


def test_db_ahead_unknown_warns_and_boots(caplog):
    """DB revision unknown to the embedded history -> warn, boot (rollback path)."""
    script = _load_script_directory()
    with caplog.at_level(logging.WARNING):
        _evaluate_revisions({"deadbeefcafe00"}, script)  # must not raise
    assert "TOLEREE" in caplog.text


async def test_allow_schema_drift_bypasses(monkeypatch):
    """ALLOW_SCHEMA_DRIFT=true returns before touching the engine."""
    from app.config import settings

    monkeypatch.setattr(settings, "allow_schema_drift", True)
    engine = MagicMock()
    await check_migrations_current(engine)
    engine.connect.assert_not_called()
