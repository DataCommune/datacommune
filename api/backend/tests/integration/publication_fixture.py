"""Install the real journal migration in bounded legacy-schema test fixtures."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def install_publication_journal(conn):
    if conn.execute("SELECT to_regclass('etl_publications')").fetchone()[0] is not None:
        return
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/094_publication_generations.py"
    )
    spec = importlib.util.spec_from_file_location("fixture_publication_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.op = SimpleNamespace(execute=conn.execute)
    migration.upgrade()
