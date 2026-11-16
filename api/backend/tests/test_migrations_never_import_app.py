"""Guard: no Alembic migration imports application code.

A migration is frozen at its revision; the app is not. A migration that
imports DDL, constants or helpers from ``app.*`` silently replays HEAD
behaviour at an old revision -- and breaks every fresh chain the day the app
moves on, while already-migrated databases stay green. That is exactly what
happened on 2026-08-25: 073_semantic_contracts imported
``app.commune.matview_ddl.CREATE_SQL``; 57-09 made that DDL read a column
only migration 088 creates; ci-data-contracts went red on
``column dvf.prix_m2_median_maison does not exist`` while production
(already at 087) was unaffected. The rule was already written in 088's
docstring ("Migrations never import app code") -- this test is what makes
it bite.

Static scan (AST), no DB: any ``import app…`` / ``from app… import`` in
migrations/versions/*.py fails here with the offending file and line.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _app_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app" or alias.name.startswith("app."):
                    offenders.append(f"{path.name}:{node.lineno} import {alias.name}")  # noqa: PERF401
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0 and (mod == "app" or mod.startswith("app.")):
                offenders.append(f"{path.name}:{node.lineno} from {mod} import …")
    return offenders


def test_versions_dir_is_where_we_think() -> None:
    files = sorted(_VERSIONS_DIR.glob("0*.py"))
    assert len(files) >= 89, f"{_VERSIONS_DIR} : {len(files)} migrations trouvées"


def test_no_migration_imports_app_code() -> None:
    offenders = [
        line
        for path in sorted(_VERSIONS_DIR.glob("*.py"))
        for line in _app_imports(path)
    ]
    assert not offenders, (
        "Une migration importe du code applicatif (DDL de head rejoué à une "
        "ancienne révision -- casse toute chaîne fraîche) :\n  "
        + "\n  ".join(offenders)
    )


def test_guard_detects_an_app_import(tmp_path: Path) -> None:
    """The guard itself must be able to fail (D-40)."""
    bad = tmp_path / "999_bad.py"
    bad.write_text(
        "from app.commune.matview_ddl import CREATE_SQL\nimport app.config\n",
        encoding="utf-8",
    )
    found = _app_imports(bad)
    assert len(found) == 2 and all("999_bad.py" in f for f in found)
