"""Schema locks of the instance scope.

Migration 101 lifted the DPE lock of migration 091: a DPE import takes every
hostable departement (plan 04.1-07). Migration 102 lifted the last one, the
``scoring_renov_target`` view frozen on 44/33 by migration 066 (plan 04.1-08):
no schema lock is left, the mechanism stays.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.etl.errors import ScopeLockError, ScopePrerequisiteError
from app.etl.runner import PipelineRunner
from app.etl_dpe.pipeline import DpePipeline
from app.etl_dpe.sources import expected_pairs
from app.scope.model import (
    DEPARTEMENTS_HEBERGEABLES,
    SCOPE_LOCKS,
    InstanceScope,
    locked_departements,
)

pytestmark = pytest.mark.unit

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
MIGRATION_101 = VERSIONS / "101_dpe_scope_national.py"


def _scope(*departements):
    return InstanceScope(frozenset({"dpe"}), departements)


def _migration(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_101():
    return _migration(MIGRATION_101)


def _sql(statement: str) -> str:
    return " ".join(statement.split())


def test_no_schema_lock_is_left():
    assert SCOPE_LOCKS == {}
    # Without a lock, the departements are kept as given, in canonical order.
    assert locked_departements("dpe", ["971", "33", "44"]) == ["33", "44", "971"]
    assert locked_departements("scoring_renov_target", ["75", "44", "971"]) == [
        "44",
        "75",
        "971",
    ]


def test_migration_102_copies_the_canonical_view_and_gives_066_back():
    from app.scoring import matview_ddl

    module = _migration(VERSIONS / "102_scoring_scope_national.py")
    assert module.revision == "102_scoring_scope_national"
    assert module.down_revision == "101_dpe_scope_national"
    # A migration never imports the application: its copy must stay equal.
    assert module.CREATE_SQL == matview_ddl.CREATE_SQL
    assert module.INDEX_SQLS == matview_ddl.INDEX_SQLS
    assert module.DROP_SQL == matview_ddl.DROP_SQL
    assert "code_departement IN" not in module.CREATE_SQL
    # Downgrade: exactly the view of migration 066 (whitespace aside).
    source_066 = (VERSIONS / "066_restore_scoring_matview.py").read_text("utf-8")
    view_066 = source_066.split('op.execute("""', 2)[1].split('""")', 1)[0]
    assert _sql(module.CREATE_SQL_066) == _sql(view_066)
    assert "code_departement IN ('44', '33')" in module.CREATE_SQL_066


def test_migration_101_lists_exactly_the_hostable_departements():
    # The migration copies the list (it never imports the application).
    module = _migration_101()
    assert module.DEPARTEMENTS_HEBERGEABLES == DEPARTEMENTS_HEBERGEABLES
    assert len(module.DEPARTEMENTS_HEBERGEABLES) == 101
    assert "975" not in module.DEPARTEMENTS_HEBERGEABLES
    assert module.down_revision == "100_dpe_source_columns"
    assert module.DEPARTEMENTS_091 == ("44", "85")


def test_migration_103_lists_exactly_the_hostable_departements():
    from app.commune.indicator_schemas import (
        HOSTED_COMMUNE_PATTERN,
        commune_departement,
        hosted_commune,
    )
    from app.scope.model import departement_from_insee

    module = _migration(VERSIONS / "103_commune_indicator_cells_national.py")
    assert module.revision == "103_commune_indicator_cells_national"
    assert module.down_revision == "102_scoring_scope_national"
    assert module.DEPARTEMENTS_HEBERGEABLES == DEPARTEMENTS_HEBERGEABLES
    assert module.CHECK_095 == "code_insee ~ '^(44|85)[0-9]{3}$'"
    # The SQL and Python rules read the departement of a code the same way.
    check = module.hosted_check(DEPARTEMENTS_HEBERGEABLES)
    assert "left(code_insee, 2) = '97' THEN left(code_insee, 3)" in check
    for code in ("44109", "75056", "2A004", "2B033", "97411", "97608", "01001"):
        assert commune_departement(code) == departement_from_insee(code)
        assert hosted_commune(code, DEPARTEMENTS_HEBERGEABLES), code
    for code in ("97501", "98735", "20001", "99001", "4410", "441090", "2C004"):
        assert not hosted_commune(code, DEPARTEMENTS_HEBERGEABLES), code
    assert HOSTED_COMMUNE_PATTERN.startswith("^(?:(?:01|02|")


def test_expected_pairs_follow_the_departements_given():
    assert expected_pairs(("44", "85", "971")) == {
        (slug, code)
        for slug in ("dpe03existant", "dpe02neuf")
        for code in ("44", "85", "971")
    }
    assert len(expected_pairs(DEPARTEMENTS_HEBERGEABLES)) == 202
    for refused in (("975",), ("44", "99"), ("",)):
        with pytest.raises(ValueError, match="outside the hostable list"):
            expected_pairs(refused)


def test_dpe_beyond_44_85_runs_without_lock_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    requested = []

    async def record(self, client, sem, source, department, directory):
        requested.append((source.slug, department))
        raise RuntimeError("stop after the matrix")

    monkeypatch.setattr(DpePipeline, "_download_source", record)
    pipeline = DpePipeline()
    pipeline.bind_scope(_scope("44", "85", "33", "971"))

    assert pipeline.department_scope == ["44", "85", "33", "971"]
    pipeline.check_scope_prerequisites("unused")
    assert pipeline.expected_file_count == 8
    with pytest.raises(RuntimeError, match="stop after the matrix"):
        pipeline.download()
    assert sorted(requested) == sorted(expected_pairs(("33", "44", "85", "971")))


def test_dpe_on_one_departement_builds_its_two_pairs_matrix(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path))
    requested = []

    async def record(self, client, sem, source, department, directory):
        requested.append((source.slug, department))
        raise RuntimeError("stop after the matrix")

    monkeypatch.setattr(DpePipeline, "_download_source", record)
    pipeline = DpePipeline()
    pipeline.bind_scope(_scope("44"))

    assert pipeline.department_scope == ["44"]
    assert pipeline.expected_file_count == 2
    with pytest.raises(RuntimeError, match="stop after the matrix"):
        pipeline.download()
    assert sorted(requested) == [("dpe02neuf", "44"), ("dpe03existant", "44")]


def test_dpe_never_hosts_an_overseas_collectivity_named_by_code():
    pipeline = DpePipeline()
    pipeline.bind_scope(_scope("44", "975", "988"))
    # D-123: a collectivity is out of coverage, never downloaded.
    assert pipeline.department_scope == ["44"]
    assert pipeline.expected_file_count == 2


def test_dpe_without_hostable_departement_is_refused_before_download(monkeypatch):
    monkeypatch.setattr(PipelineRunner, "_insert_etl_run", MagicMock())
    monkeypatch.setattr("app.etl.runner.dataset_execution_lock", MagicMock())
    monkeypatch.setattr(DpePipeline, "download", MagicMock(side_effect=AssertionError))
    runner = PipelineRunner(scope_loader=lambda _dsn: _scope("975"))

    with pytest.raises(ScopePrerequisiteError, match="hostable") as refused:
        runner.run(DpePipeline())

    assert not isinstance(refused.value, ScopeLockError)
    DpePipeline.download.assert_not_called()


def test_lock_refusal_names_every_code_outside_it(monkeypatch):
    # The mechanism outlives the last lock: a future one still refuses.
    monkeypatch.setitem(SCOPE_LOCKS, "probe_view", (("44", "33"), "probe migration"))
    with pytest.raises(ScopeLockError, match=r"\['2A', '85'\]"):
        locked_departements("probe_view", ["33", "44", "2A", "85"])
    assert locked_departements("probe_view", ["33", "44"]) == ["33", "44"]
    # A dataset without a lock keeps its configured departements.
    assert locked_departements("dvf", ["85", "01"]) == ["01", "85"]
