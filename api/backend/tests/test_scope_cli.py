"""Operator scope command and hosted-only ETL selection, without a database."""

import pytest

from app.common.source_catalog import DATASET_CATALOG
from app.etl.base import DEPARTEMENTS
from app.etl.errors import DatasetNotHostedError
from app.scope import SCOPE_EXCLUDED_KEYS, InstanceScope
from app.scope.__main__ import main as scope_main


def _scope(datasets, departements):
    return InstanceScope(frozenset(datasets), tuple(departements))


def _no_database(*args, **kwargs):
    raise AssertionError("refused input must not open a database connection")


@pytest.mark.parametrize(
    "argv,named",
    [
        (["--datasets", "loyers", "--departements", "44,99"], "99"),
        (["--datasets", "loyers,nope", "--departements", "44"], "nope"),
        (["--datasets", "commune", "--departements", "44"], "commune"),
    ],
)
def test_set_refuses_unknown_values_before_connecting(monkeypatch, capsys, argv, named):
    monkeypatch.setattr("app.scope.__main__.psycopg.connect", _no_database)
    assert scope_main(["set", *argv]) == 2
    assert named in capsys.readouterr().err


# No schema lock since migration 102 (04.1-08): no warning on any scope.
_VIEW_WARNING = ""


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_set_warns_without_refusing_when_departements_exceed_a_lock(
    monkeypatch, capsys
):
    written = []

    def replace(conn, datasets, departements):
        written.append((datasets, departements))
        return _scope(datasets, departements)

    monkeypatch.setattr("app.scope.__main__.psycopg.connect", lambda *a, **k: _Conn())
    monkeypatch.setattr("app.scope.__main__.replace_scope", replace)
    argv = ["set", "--datasets", "dpe,dvf", "--departements", "44,85,33"]
    assert scope_main(argv) == 0
    # Written as requested. No lock left (DPE: migration 101, 04.1-07; scoring
    # view: migration 102, 04.1-08): no warning.
    assert written == [(["dpe", "dvf"], ["33", "44", "85"])]
    assert capsys.readouterr().out == (
        "SCOPE set datasets=2 departements=33,44,85\n" + _VIEW_WARNING
    )


def test_set_prints_no_warning_within_every_lock(monkeypatch, capsys):
    monkeypatch.setattr("app.scope.__main__.psycopg.connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(
        "app.scope.__main__.replace_scope",
        lambda conn, datasets, departements: _scope(datasets, departements),
    )
    assert scope_main(["set", "--datasets", "dpe", "--departements", "44"]) == 0
    assert capsys.readouterr().out == "SCOPE set datasets=1 departements=44\n"


def test_set_normalises_then_replaces(monkeypatch, capsys):
    written = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def replace(conn, datasets, departements):
        written.append((datasets, departements))
        return _scope(datasets, departements)

    monkeypatch.setattr("app.scope.__main__.psycopg.connect", lambda *a, **k: Conn())
    monkeypatch.setattr("app.scope.__main__.replace_scope", replace)
    argv = ["set", "--datasets", "vacance, dvf,dvf", "--departements", "85,44,2a"]
    assert scope_main(argv) == 0
    assert written == [(["dvf", "vacance"], ["2A", "44", "85"])]
    # 2A and 85 used to exceed the scoring view lock (44/33), lifted by 102.
    assert capsys.readouterr().out == (
        "SCOPE set datasets=2 departements=2A,44,85\n" + _VIEW_WARNING
    )


def test_show_prints_summary_then_one_key_per_line(monkeypatch, capsys):
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    scope = _scope({"vacance", "dvf"}, ("44", "85"))
    monkeypatch.setattr("app.scope.__main__.psycopg.connect", lambda *a, **k: Conn())
    monkeypatch.setattr("app.scope.__main__.load_scope_sync", lambda conn: scope)
    assert scope_main(["show"]) == 0
    assert capsys.readouterr().out == (
        "SCOPE datasets=2 departements=44,85\ndvf\nvacance\n"
        "SCOPE lock: aucun verrou de schéma\n"
    )
    # JSON stays the stored scope only: locks are an operator report.
    assert scope_main(["show", "--json"]) == 0
    assert capsys.readouterr().out == (
        '{"datasets": ["dvf", "vacance"], "departements": ["44", "85"]}\n'
    )


def _run_etl(monkeypatch, scope, args):
    import run_etl

    observed = []

    def run(_runner, pipeline, *, dry_run):
        from app.etl.schemas import EtlResult

        observed.append(pipeline.dataset_name)
        return EtlResult(
            dataset_name=pipeline.dataset_name,
            stages=[],
            total_rows_loaded=0,
            total_duration_seconds=0,
            success=True,
        )

    monkeypatch.setattr("app.etl.runner.default_scope_loader", lambda _dsn: scope)
    monkeypatch.setattr(run_etl.PipelineRunner, "run", run)
    monkeypatch.setattr(run_etl, "build_default_hooks", lambda: [])
    monkeypatch.setattr(run_etl.sys, "argv", ["run_etl", *args])
    run_etl.main()
    return observed


def test_run_etl_without_names_runs_only_hosted_pipelines(monkeypatch):
    # transports needs both of its keys; gares alone does not select it.
    scope = _scope({"vacance", "loyers", "gares", "dvf"}, ("44", "85"))
    assert _run_etl(monkeypatch, scope, []) == ["loyers", "vacance", "dvf"]


# Skip and refusal of a source without coverage: tests/test_source_coverage.py.


@pytest.mark.parametrize(
    "argv",
    [
        ["--datasets", "", "--departements", "44"],
        ["--datasets", "loyers", "--departements", ""],
        ["--datasets", "loyers"],
        ["--clear", "--datasets", "loyers"],
    ],
)
def test_set_refuses_an_implicit_empty_scope(monkeypatch, capsys, argv):
    monkeypatch.setattr("app.scope.__main__.psycopg.connect", _no_database)
    assert scope_main(["set", *argv]) == 2
    assert "SCOPE refused" in capsys.readouterr().err


def test_set_clear_empties_the_scope_explicitly(monkeypatch, capsys):
    written = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def replace(conn, datasets, departements):
        written.append((datasets, departements))
        return _scope(datasets, departements)

    monkeypatch.setattr("app.scope.__main__.psycopg.connect", lambda *a, **k: Conn())
    monkeypatch.setattr("app.scope.__main__.replace_scope", replace)
    assert scope_main(["set", "--clear"]) == 0
    assert written == [([], [])]
    assert capsys.readouterr().out == "SCOPE set datasets=0 departements=\n"


def test_run_etl_full_scope_selects_every_pipeline_but_quarantine(monkeypatch):
    import run_etl

    scope = _scope(set(DATASET_CATALOG) - SCOPE_EXCLUDED_KEYS, tuple(DEPARTEMENTS))
    observed = _run_etl(monkeypatch, scope, [])
    assert observed == [n for n in run_etl.PIPELINE_MAP if n not in run_etl.NAMED_ONLY]
    assert "encadrement_loyers" not in observed
    assert "bdnb_batiment_parcelle" not in observed


def test_run_etl_named_only_pipeline_runs_when_named(monkeypatch):
    scope = _scope({"bdnb"}, ("44",))
    assert _run_etl(monkeypatch, scope, ["bdnb_batiment_parcelle"]) == [
        "bdnb_batiment_parcelle"
    ]


@pytest.mark.parametrize(
    "args,scope,message",
    [
        (["loyers"], _scope({"vacance"}, ("44",)), "dataset non hébergé: loyers"),
        (["transports"], _scope({"transports"}, ("44",)), "dataset non hébergé: gares"),
        ([], _scope({"loyers"}, ()), "périmètre vide"),
        # Departements but no importable dataset: nothing to run is refused.
        # (bdnb has its import pipeline since 04.7-19: a live proxy instead.)
        ([], _scope({"georisques"}, ("44",)), "aucun dataset à importer"),
        ([], _scope({"permis"}, ("44", "85")), "aucun dataset à importer"),
        (["loyers"], _scope({"loyers"}, ()), "périmètre vide"),
    ],
)
def test_run_etl_refuses_before_any_pipeline(monkeypatch, capsys, args, scope, message):
    with pytest.raises(SystemExit) as refused:
        _run_etl(monkeypatch, scope, args)
    assert refused.value.code == 2
    assert message in capsys.readouterr().err


def test_run_etl_scope_change_after_selection_fails_only_that_dataset(monkeypatch):
    import run_etl

    observed = []

    def run(_runner, pipeline, *, dry_run):
        from app.etl.schemas import EtlResult

        observed.append(pipeline.dataset_name)
        if pipeline.dataset_name == "loyers":
            raise DatasetNotHostedError("loyers")
        return EtlResult(
            dataset_name=pipeline.dataset_name,
            stages=[],
            total_rows_loaded=0,
            total_duration_seconds=0,
            success=True,
        )

    scope = _scope({"vacance", "loyers"}, ("44",))
    monkeypatch.setattr("app.etl.runner.default_scope_loader", lambda _dsn: scope)
    monkeypatch.setattr(run_etl.PipelineRunner, "run", run)
    monkeypatch.setattr(run_etl, "build_default_hooks", lambda: [])
    monkeypatch.setattr(run_etl.sys, "argv", ["run_etl"])
    with pytest.raises(SystemExit) as failed:
        run_etl.main()
    assert failed.value.code == 1
    assert observed == ["loyers", "vacance"]


def test_scheduler_skips_a_source_without_coverage(monkeypatch, caplog):
    # WR-03: the runner refuses permis on 44/85; the job skips it, no error.
    from app.etl.errors import SourceWithoutCoverageError
    from app.scheduler import jobs

    def refuse(_runner, pipeline, **kwargs):
        raise SourceWithoutCoverageError(pipeline.dataset_name)

    monkeypatch.setattr(jobs.PipelineRunner, "run", refuse)
    monkeypatch.setattr(jobs, "build_default_hooks", lambda: [])
    assert jobs.run_scheduled_pipeline("dvf") is None
    assert "skipped: source without coverage" in caplog.text
    assert all(record.levelname != "ERROR" for record in caplog.records)


def test_scheduler_skips_a_dataset_outside_the_scope(monkeypatch, caplog):
    from app.scheduler import jobs

    def refuse(_runner, pipeline, **kwargs):
        raise DatasetNotHostedError(pipeline.dataset_name)

    monkeypatch.setattr(jobs.PipelineRunner, "run", refuse)
    monkeypatch.setattr(jobs, "build_default_hooks", lambda: [])
    assert jobs.run_scheduled_pipeline("loyers") is None
    assert "skipped: dataset not hosted" in caplog.text
    assert any(record.levelname == "WARNING" for record in caplog.records)
