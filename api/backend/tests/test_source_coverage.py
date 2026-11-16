"""Source coverage in the catalogue: the only territorial authority per source.

The catalogue (``DatasetCatalogEntry.source_coverage``) decides which
departements a source publishes; the import selection, the runner, the
departements a pipeline expects in staging and the ``source_sans_couverture``
reason all read it (D-22, D-23, D-24).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.common.schemas import OutOfCoverageReason
from app.common.source_catalog import (
    DATASET_CATALOG,
    SourceCoverage,
    covered_departements,
    source_covers,
)
from app.datasets.provenance import ProvenanceFacts, assemble_provenance
from app.etl.base import DEPARTEMENTS
from app.etl.errors import SourceWithoutCoverageError
from app.etl.runner import PipelineRunner
from app.etl_dvf.pipeline import DVF_NO_DATA_DEPARTEMENTS, DvfPipeline
from app.etl_loyers.pipeline import LoyersPipeline
from app.etl_permis.pipeline import PermisPipeline
from app.etl_vacance.pipeline import VacancePipeline
from app.resolve.router import _refine_dataset_states
from app.resolve.schemas import resolution_state
from app.scope.model import InstanceScope

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


def _scope(datasets, departements):
    return InstanceScope(frozenset(datasets), tuple(departements))


@pytest.mark.parametrize(
    ("key", "departement", "covered"),
    [
        ("dvf", "57", False),
        ("dvf", "976", False),
        ("dvf", "44", True),
        ("permis", "44", False),
        ("permis", "93", True),
        ("loyers", "975", False),
        ("loyers", "976", False),
        ("loyers", "44", True),
        ("vacance", "975", False),
        ("vacance", "976", True),
        ("ban", "976", True),
        # Border rows only (5 for 22, 1 for 65 and 2A): not the producer's
        # sectorisation of these departements.
        ("scolaire", "22", False),
        ("scolaire", "65", False),
        ("scolaire", "2A", False),
        ("scolaire", "2B", True),
        ("revenus", "972", True),
        ("demographie", "971", True),
    ],
)
def test_source_covers(key, departement, covered):
    assert source_covers(DATASET_CATALOG[key], departement) is covered


def test_coverage_names_only_real_departements():
    for key, entry in DATASET_CATALOG.items():
        coverage = entry.source_coverage
        named = set(coverage.departements_exclus) | set(
            coverage.departements_inclus or ()
        )
        assert named <= set(DEPARTEMENTS), key


def test_every_pipeline_key_is_a_catalogue_entry():
    # The lenient default of BasePipeline (no absence outside the catalogue)
    # only serves test doubles: every real pipeline is in the catalogue.
    import run_etl

    for name, cls in run_etl.PIPELINE_MAP.items():
        assert set(cls().scope_dataset_keys) <= set(DATASET_CATALOG), name


def test_coverage_default_is_national():
    assert SourceCoverage().covers("2A")
    assert DATASET_CATALOG["cadastre"].source_coverage == SourceCoverage()


def test_covered_departements_keeps_the_scope_order():
    # Migrated from test_scope_floors.py (source_covers of app.scope.floors).
    assert covered_departements(("permis",), ("44", "85")) == []
    assert covered_departements(("permis",), ("44", "93")) == ["93"]
    assert covered_departements(("loyers",), ("44",)) == ["44"]
    # A pipeline with two sources expects a departement either publishes.
    assert covered_departements(("transports", "gares"), ("85", "44")) == ["85", "44"]


@pytest.mark.parametrize(
    ("pipeline_cls", "absent"),
    [
        (DvfPipeline, {"57", "67", "68", "976"}),
        (LoyersPipeline, {"975", "976"}),
        (VacancePipeline, {"975"}),
        (PermisPipeline, set(DEPARTEMENTS) - {"93"}),
    ],
)
def test_pipeline_absences_are_derived_from_the_catalogue(pipeline_cls, absent):
    pipeline = pipeline_cls()
    assert pipeline.departements_outside_source == absent
    pipeline.bind_scope(_scope({pipeline.dataset_name}, DEPARTEMENTS))
    assert set(DEPARTEMENTS) - set(pipeline.expected_department_coverage) == absent


def test_dvf_constant_is_the_catalogue_exclusion():
    assert (
        frozenset(DATASET_CATALOG["dvf"].source_coverage.departements_exclus)
        == DVF_NO_DATA_DEPARTEMENTS
    )


def test_permis_expects_nothing_on_44_85():
    pipeline = PermisPipeline()
    pipeline.bind_scope(_scope({"permis"}, ("44", "85")))
    assert pipeline.expected_department_coverage == []
    pipeline.bind_scope(_scope({"permis"}, ("44", "93")))
    assert pipeline.expected_department_coverage == ["93"]


# -- import guard ------------------------------------------------------------


class _HttpClientCounter:
    """Stands for httpx.AsyncClient: counts the clients a download opens."""

    calls = 0

    def __init__(self, *args, **kwargs):
        type(self).calls += 1

    async def __aenter__(self):
        raise RuntimeError("download attempted")

    async def __aexit__(self, *exc):
        return False


def _run_etl(monkeypatch, scope, args):
    """Run run_etl.main with a substituted scope and HTTP client.

    The runner is replaced by a call to ``permis.download()``: a permis that
    passes the selection reaches the (substituted) HTTP client. Other
    pipelines are only recorded (their downloads use the same httpx module).
    """
    import run_etl
    from app.etl.schemas import EtlResult

    started = []

    def run(_runner, pipeline, *, dry_run):
        started.append(pipeline.dataset_name)
        if isinstance(pipeline, PermisPipeline):
            with pytest.raises(RuntimeError, match="download attempted"):
                pipeline.download()
        return EtlResult(
            dataset_name=pipeline.dataset_name,
            stages=[],
            total_rows_loaded=0,
            total_duration_seconds=0,
            success=True,
        )

    _HttpClientCounter.calls = 0
    monkeypatch.setattr("app.etl_permis.pipeline.httpx.AsyncClient", _HttpClientCounter)
    monkeypatch.setattr("app.etl.runner.default_scope_loader", lambda _dsn: scope)
    monkeypatch.setattr(run_etl.PipelineRunner, "run", run)
    monkeypatch.setattr(run_etl, "build_default_hooks", lambda: [])
    monkeypatch.setattr(run_etl.sys, "argv", ["run_etl", *args])
    run_etl.main()
    return started


def test_run_etl_default_skips_a_source_without_coverage(monkeypatch, caplog):
    scope = _scope({"permis", "loyers"}, ("44", "85"))
    assert _run_etl(monkeypatch, scope, []) == ["loyers"]
    assert "source sans couverture sur le périmètre: permis" in caplog.text
    assert _HttpClientCounter.calls == 0


def test_run_etl_refuses_a_named_source_without_coverage(monkeypatch, capsys):
    scope = _scope({"permis", "loyers"}, ("44", "85"))
    with pytest.raises(SystemExit) as exit_info:
        _run_etl(monkeypatch, scope, ["permis"])
    assert exit_info.value.code == 2
    assert "source sans couverture sur le périmètre: permis" in capsys.readouterr().err
    assert _HttpClientCounter.calls == 0


def test_named_source_with_coverage_reaches_the_download(monkeypatch):
    # Witness of the counter: with 93 hosted, the same selection downloads.
    scope = _scope({"permis"}, ("44", "93"))
    assert _run_etl(monkeypatch, scope, ["permis"]) == ["permis"]
    assert _HttpClientCounter.calls == 1


class _TrackedPermis(PermisPipeline):
    downloads = 0

    def download(self):
        type(self).downloads += 1
        return []


def test_runner_refuses_before_journal_and_download(monkeypatch):
    monkeypatch.setattr(
        _TrackedPermis, "get_sync_dsn", staticmethod(lambda: "postgresql://x")
    )
    insert = MagicMock()
    monkeypatch.setattr(PipelineRunner, "_insert_etl_run", insert)
    monkeypatch.setattr(
        "app.etl.runner.dataset_execution_lock", MagicMock(), raising=True
    )
    _TrackedPermis.downloads = 0
    scope = _scope({"permis"}, ("44", "85"))
    with pytest.raises(SourceWithoutCoverageError, match="permis"):
        PipelineRunner(scope_loader=lambda _dsn: scope).run(_TrackedPermis())
    insert.assert_not_called()
    assert _TrackedPermis.downloads == 0


# -- Corsican postcode on the effective coverage (D-15 note of 02-07) --------


def _loyers_covering(departements):
    facts = ProvenanceFacts(effective_departments=departements)
    return {"loyers": assemble_provenance("loyers", facts, _NOW)}


def test_corsican_postcode_is_not_promoted_when_the_instance_hosts_2a():
    scope = _scope({"loyers"}, ("2A", "44"))
    states = {"loyers": resolution_state("loyers", None, is_error=False)}
    _refine_dataset_states(states, _loyers_covering(["2A", "44"]), "20000", scope=scope)
    assert states["loyers"].absence_reason == "not_reported"
    assert states["loyers"].motif is None


def test_corsican_postcode_is_out_of_the_territory_on_44_85():
    scope = _scope({"loyers"}, ("44", "85"))
    states = {"loyers": resolution_state("loyers", None, is_error=False)}
    _refine_dataset_states(states, _loyers_covering(["44", "85"]), "20000", scope=scope)
    assert states["loyers"].absence_reason == "out_of_coverage"
    assert states["loyers"].motif == OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE


# -- overseas postcodes (WR-02 of the 02-09 review) --------------------------


@pytest.mark.parametrize("postcode", ["97100", "97110"])
def test_overseas_postcode_is_not_promoted_when_its_departement_is_covered(postcode):
    scope = _scope({"loyers"}, ("971", "44"))
    states = {"loyers": resolution_state("loyers", None, is_error=False)}
    _refine_dataset_states(
        states, _loyers_covering(["971", "44"]), postcode, scope=scope
    )
    assert states["loyers"].absence_reason == "not_reported"


def test_overseas_postcode_is_out_of_the_territory_on_44_85():
    scope = _scope({"loyers"}, ("44", "85"))
    states = {"loyers": resolution_state("loyers", None, is_error=False)}
    _refine_dataset_states(states, _loyers_covering(["44", "85"]), "97100", scope=scope)
    assert states["loyers"].absence_reason == "out_of_coverage"
    assert states["loyers"].motif == OutOfCoverageReason.TERRITOIRE_HORS_PERIMETRE


def test_postcode_naming_no_departement_is_no_proof():
    scope = _scope({"loyers"}, ("44", "85"))
    states = {"loyers": resolution_state("loyers", None, is_error=False)}
    _refine_dataset_states(states, _loyers_covering(["44", "85"]), "98000", scope=scope)
    assert states["loyers"].absence_reason == "not_reported"
