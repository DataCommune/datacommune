"""The latest snapshot migration carries the application DDL of the chain.

Migrations cannot import ``app.commune.matview_ddl`` (migrations never import
app code, tests/test_migrations_never_import_app.py): each one that changes
the chain ships a frozen copy (098, 106 for plan 04.1-16, 111 for plan 04.7-09,
112 for plan 04.7-10, 115 for the yearly price series).
This test keeps the latest copy and the application DDL equal, so a migrated
base and a rebuilt one never serve different values again (KI-D4, plan 02-09).
"""

import importlib.util
from pathlib import Path

import pytest

from app.commune.matview_ddl import (
    COMMUNE_VIEW_NAMES,
    annuel_statements,
    commune_synthese_statements,
)

pytestmark = pytest.mark.unit

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "115_commune_dvf_annuel.py"
)
_NEW_MIGRATION = (
    "The application DDL of the commune view chain "
    "(app/commune/matview_ddl.py) differs from the frozen copy of migration "
    "115. Every change of that DDL needs a NEW migration shipping the new "
    "snapshot (see the matview_ddl.py docstring); never edit 098, 106, 111, "
    "112 nor 115."
)


def _migration():
    spec = importlib.util.spec_from_file_location("migration_snapshot", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_statements_equal_the_application_ddl():
    frozen = _migration().STATEMENTS
    assert tuple(commune_synthese_statements()) == frozen, _NEW_MIGRATION


def test_frozen_view_names_equal_the_application_chain():
    assert _migration().VIEW_NAMES == COMMUNE_VIEW_NAMES, _NEW_MIGRATION


def test_frozen_publication_lock_equals_the_application_lock():
    # WR-06: 098 takes the commune publication lock under a copied literal.
    from app.commune.dependencies import PUBLICATION_LOCK_ID

    assert _migration().PUBLICATION_LOCK_ID == PUBLICATION_LOCK_ID, (
        "migration 098 must lock the commune publication lock "
        "(app.commune.dependencies.PUBLICATION_LOCK_ID)"
    )


def test_the_upgrade_creates_only_the_yearly_view():
    # No rebuild of the national chain by the migration: the other views of
    # the chain are those of migration 112, text for text (next test).
    migration = _migration()
    frozen = migration.ANNUEL_STATEMENTS
    assert frozen == tuple(annuel_statements()), _NEW_MIGRATION
    assert migration.down_revision == "114_signalements"


def test_the_other_views_of_the_chain_are_those_of_migration_112():
    previous = MIGRATION.with_name("112_departement_synthese.py")
    spec = importlib.util.spec_from_file_location("migration_112", previous)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migration = _migration()
    assert migration.STATEMENTS == module.STATEMENTS + migration.ANNUEL_STATEMENTS


class _Recorder:
    """``op`` and its bind: every statement a migration sends, in order."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def execute(self, statement) -> None:
        self.sent.append(str(statement))

    def get_bind(self) -> "_Recorder":
        return self

    def exec_driver_sql(self, statement, *_parameters) -> None:
        self.sent.append(statement)


def _sent(migration, step) -> list[str]:
    """Statements *step* (a function of *migration*) sends to the database."""
    recorder = _Recorder()
    migration.op = recorder
    step()
    return recorder.sent


def _locked(migration, *statements: str) -> list[str]:
    from app.commune.dependencies import PUBLICATION_LOCK_ID

    return [
        migration.LOCK_TIMEOUT,
        f"SELECT pg_advisory_xact_lock({PUBLICATION_LOCK_ID})",
        *statements,
    ]


def test_upgrade_sends_the_lock_then_the_yearly_view_only():
    # Review Codex 04.7-10 n. 4: the statements upgrade() really executes.
    migration = _migration()
    assert _sent(migration, migration.upgrade) == _locked(
        migration, *annuel_statements()
    )


def test_downgrade_sends_the_lock_then_drops_the_yearly_view_only():
    migration = _migration()
    assert _sent(migration, migration.downgrade) == _locked(
        migration, "DROP MATERIALIZED VIEW IF EXISTS v_commune_dvf_annuel"
    )
