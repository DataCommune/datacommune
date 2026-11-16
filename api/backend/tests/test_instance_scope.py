"""Instance scope model, validation and runner refusal, without a database."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.common.source_catalog import DATASET_CATALOG
from app.etl.base import DEPARTEMENTS
from app.etl.errors import DatasetNotHostedError, EmptyScopeError
from app.etl.runner import PipelineRunner
from app.etl_loyers.pipeline import LoyersPipeline
from app.etl_vacance.pipeline import VacancePipeline
from app.scope import (
    SCOPE_EXCLUDED_KEYS,
    InstanceScope,
    departement_from_insee,
    parse_departements,
    replace_scope,
)

pytestmark = pytest.mark.unit

MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations/versions/097_instance_scope.py"
)


def _scope(datasets, departements):
    return InstanceScope(frozenset(datasets), tuple(departements))


def test_parse_departements_normalises_order_case_and_duplicates():
    assert parse_departements("85, 44,2a,44") == ["2A", "44", "85"]
    assert parse_departements("976,01") == ["01", "976"]


def test_parse_departements_rejects_unknown_codes():
    with pytest.raises(ValueError, match=r"\['20', '99'\]"):
        parse_departements("44,99,20")


@pytest.mark.parametrize(
    ("code", "expected"),
    [("44109", "44"), ("2A004", "2A"), ("2B033", "2B"), ("97411", "974")],
)
def test_departement_from_insee(code, expected):
    assert departement_from_insee(code) == expected


def test_scope_hosts_and_sorted_json():
    scope = _scope({"vacance", "dvf"}, ("85", "44"))
    assert scope.hosts("vacance") and not scope.hosts("dpe")
    assert not scope.is_empty and not scope.covers_all_departements
    assert scope.as_json() == {
        "datasets": ["dvf", "vacance"],
        "departements": ["44", "85"],
    }
    assert _scope(set(), ()).is_empty
    assert _scope({"dvf"}, DEPARTEMENTS).covers_all_departements


@pytest.mark.parametrize("key", ["unknown_dataset", "commune"])
def test_replace_scope_refuses_keys_outside_hostable_catalogue(key):
    conn = MagicMock()
    with pytest.raises(ValueError, match=key):
        replace_scope(conn, ["vacance", key], ["44"])
    conn.transaction.assert_not_called()
    conn.execute.assert_not_called()


def test_replace_scope_refuses_unknown_departement():
    conn = MagicMock()
    with pytest.raises(ValueError, match="99"):
        replace_scope(conn, ["vacance"], ["44", "99"])
    conn.execute.assert_not_called()


def test_commune_is_the_only_excluded_catalogue_key():
    assert {"commune"} == SCOPE_EXCLUDED_KEYS
    assert set(DATASET_CATALOG) >= SCOPE_EXCLUDED_KEYS


def test_migration_departement_check_matches_application_list():
    spec = importlib.util.spec_from_file_location("scope_097", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.DEPARTEMENTS == DEPARTEMENTS
    assert len(DEPARTEMENTS) == 102


def test_vacance_floor_follows_bound_scope():
    pipeline = VacancePipeline()
    assert pipeline.min_staging_rows == 28_000  # nothing bound: national floor
    pipeline.bind_scope(_scope({"vacance"}, ("01", "44")))
    assert pipeline.is_department_scope_limited
    # 01: no reference, proportional share x 0.5; 44: 80 % of 207 communes.
    assert pipeline.departement_min_rows("01") == int(28_000 / 102 * 0.5) == 137
    assert pipeline.departement_min_rows("44") == int(207 * 0.8) == 165
    assert pipeline.min_staging_rows == 137 + 165
    assert pipeline.expected_department_coverage == ["01", "44"]
    pipeline.bind_scope(_scope({"vacance"}, DEPARTEMENTS))
    assert not pipeline.is_department_scope_limited
    assert pipeline.min_staging_rows == 28_000


def _frozen_44_85_rows(dataset):
    from collections import Counter

    from app.scope import departement_from_insee

    if dataset == "vacance":
        from tests.oracles.lovac_full import FIELDS, expected_rows, frozen_bytes

        rows = list(expected_rows(frozen_bytes()[0]).values())
        column = FIELDS.index("code_commune")
        codes = Counter(departement_from_insee(row[column]) for row in rows)
    else:
        import tempfile

        from tests.oracles.loyers_full import FIELDS, expected, frozen_files

        with tempfile.TemporaryDirectory() as folder:
            frozen_files(Path(folder) / "loyers")
            _, rows, _ = expected(Path(folder) / "loyers")
        codes = Counter(row[FIELDS.index("code_departement")] for row in rows)
    return {code: codes[code] for code in ("44", "85")}


@pytest.mark.parametrize(
    "dataset,pipeline_cls,observed",
    [
        ("vacance", VacancePipeline, {"44": 207, "85": 253}),
        ("loyers", LoyersPipeline, {"44": 828, "85": 1012}),
    ],
)
def test_complete_44_85_extract_clears_its_scoped_national_floor(
    dataset, pipeline_cls, observed
):
    # Official frozen national generations: 44+85 are sparser than the mean
    # departement, so a purely proportional floor would refuse them.
    assert _frozen_44_85_rows(dataset) == observed
    pipeline = pipeline_cls()
    national = pipeline.min_staging_rows
    pipeline.bind_scope(_scope({dataset}, ("44", "85")))
    assert int(national * 2 / 102) > sum(observed.values())
    assert 0 < pipeline.min_staging_rows <= sum(observed.values())
    for code, rows in observed.items():
        assert 0 < pipeline.departement_min_rows(code) <= rows


class _Counts:
    """Connection stub: departement counts of one staging table."""

    def __init__(self, counts):
        self.counts = counts

    def execute(self, query, params):
        rows = [] if "DISTINCT" in query else list(self.counts.items())
        return MagicMock(fetchall=MagicMock(return_value=rows))


@pytest.mark.parametrize(
    "counts,refused",
    [
        ({"44": 828, "85": 1012}, None),  # complete 2025 extract
        # 44 truncated to 10 rows, 85 inflated: the total (2 010) clears the
        # total floor, the departement floor of 44 does not.
        ({"44": 10, "85": 2000}, "Department volume: 44 has 10 rows"),
        ({"44": 10, "85": 1012}, "Department volume: 44 has 10 rows"),
        ({"85": 1012}, "missing scoped departments ['44']"),
    ],
)
def test_truncated_departement_is_refused_even_when_the_total_passes(counts, refused):
    pipeline = LoyersPipeline()
    pipeline.bind_scope(_scope({"loyers"}, ("44", "85")))
    assert pipeline.min_staging_rows == 662 + 809
    error = PipelineRunner._scoped_coverage_error(
        _Counts(counts), pipeline, "_loyers_staging_flat", MagicMock()
    )
    if refused is None:
        assert error is None
    else:
        assert refused in error and "swap aborted" in error


def test_unbound_scope_is_an_explicit_error():
    with pytest.raises(RuntimeError, match="No instance scope bound"):
        _ = VacancePipeline().scope


class _Tracked(VacancePipeline):
    downloads = 0

    def download(self):
        type(self).downloads += 1
        return []


@pytest.mark.parametrize(
    ("scope", "error"),
    [
        (_scope({"dvf"}, ("44",)), DatasetNotHostedError),
        (_scope({"vacance"}, ()), EmptyScopeError),
    ],
)
def test_runner_refuses_before_journal_and_download(monkeypatch, scope, error):
    monkeypatch.setattr(
        _Tracked, "get_sync_dsn", staticmethod(lambda: "postgresql://x")
    )
    insert = MagicMock()
    monkeypatch.setattr(PipelineRunner, "_insert_etl_run", insert)
    monkeypatch.setattr(
        "app.etl.runner.dataset_execution_lock", MagicMock(), raising=True
    )
    _Tracked.downloads = 0
    with pytest.raises(error):
        PipelineRunner(scope_loader=lambda _dsn: scope).run(_Tracked())
    insert.assert_not_called()
    assert _Tracked.downloads == 0


def test_transport_and_rnb_links_declare_their_catalogue_keys():
    from app.etl_bdnb.pipeline import BdnbBatimentParcellePipeline
    from app.etl_rnb_liens.pipeline import RnbLiensPipeline
    from app.etl_transport.pipeline import TransportPipeline

    assert TransportPipeline().scope_dataset_keys == ("transports", "gares")
    assert RnbLiensPipeline().scope_dataset_keys == ("rnb",)
    # The BDNB import is hosted under the key of its proxy (04.7-19, D2).
    assert BdnbBatimentParcellePipeline().scope_dataset_keys == ("bdnb",)
    assert VacancePipeline().scope_dataset_keys == ("vacance",)


@pytest.mark.parametrize(
    ("pipeline_cls", "absent"),
    [
        # Measured on the frozen oracles of test_frozen_source_cli.py:
        # LOVAC communes26 has 101 departements (no 975); the 2025 rent map
        # files have neither 975 nor 976.
        (VacancePipeline, {"975"}),
        (LoyersPipeline, {"975", "976"}),
    ],
)
def test_full_scope_expects_every_departement_but_declared_absences(
    pipeline_cls, absent
):
    pipeline = pipeline_cls()
    pipeline.bind_scope(_scope({pipeline.dataset_name}, DEPARTEMENTS))
    expected = set(pipeline.expected_department_coverage)
    assert set(DEPARTEMENTS) - expected == absent


def test_permis_expects_only_the_departements_its_source_covers():
    # Source coverage is read from the catalogue (tests/test_source_coverage.py).
    from app.etl_permis.pipeline import PermisPipeline

    pipeline = PermisPipeline()
    pipeline.bind_scope(_scope({"permis"}, DEPARTEMENTS))
    assert pipeline.expected_department_coverage == ["93"]
    assert DATASET_CATALOG["permis"].source_coverage.departements_inclus == ("93",)
    pipeline.bind_scope(_scope({"permis"}, ("44", "85")))
    assert pipeline.expected_department_coverage == []


def _bound_vacance(departements):
    pipeline = VacancePipeline()
    pipeline.bind_scope(_scope({"vacance"}, departements))
    return pipeline


@pytest.mark.parametrize("departements", [("44",), tuple(DEPARTEMENTS)])
def test_unreadable_coverage_guard_fails_closed_when_scope_bound(
    monkeypatch, departements
):
    monkeypatch.setattr(
        "app.etl.runner.psycopg.connect", MagicMock(side_effect=OSError("down"))
    )
    error = PipelineRunner._check_department_coverage(
        "postgresql://x", _bound_vacance(departements), MagicMock()
    )
    assert error == "Department coverage check unreadable -- swap aborted"


def test_unreadable_scoped_regression_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "app.etl.runner.psycopg.connect", MagicMock(side_effect=OSError("down"))
    )
    error, previous = PipelineRunner._check_row_regression(
        "postgresql://x", _bound_vacance(("44",)), 1, MagicMock()
    )
    assert error == "Scoped row regression check unreadable -- swap aborted"
    assert previous is None


class _TableCounts:
    """Connection stub: departement counts per staging table (name in the SQL)."""

    def __init__(self, by_table):
        self.by_table = by_table

    def execute(self, query, params):
        table = next(t for t in self.by_table if f"FROM {t} " in query)
        rows = [] if "DISTINCT" in query else list(self.by_table[table].items())
        return MagicMock(fetchall=MagicMock(return_value=rows))


def test_each_transport_table_expects_only_its_own_source_coverage():
    # First national import (2026-10-29): stops cover the overseas departements
    # but not Mayotte; SNCF stations cover neither them nor Corsica. Each table
    # is checked against its own source, not the union of both.
    from app.etl_transport.pipeline import TransportPipeline
    from app.scope.model import DEPARTEMENTS_HEBERGEABLES

    pipeline = TransportPipeline()
    pipeline.bind_scope(_scope({"transports", "gares"}, DEPARTEMENTS_HEBERGEABLES))
    stops = {d: 10 for d in DEPARTEMENTS_HEBERGEABLES if d != "976"}
    stations = {
        d: 3 for d in DEPARTEMENTS_HEBERGEABLES if d not in ("2A", "2B") and len(d) == 2
    }
    error = PipelineRunner._scoped_coverage_error(
        _TableCounts({"_arrets_transport_staging": stops, "_gares_staging": stations}),
        pipeline,
        "_arrets_transport_staging",
        MagicMock(),
    )
    assert error is None
    # A station table missing a mainland departement is still refused.
    del stations["44"]
    error = PipelineRunner._scoped_coverage_error(
        _TableCounts({"_arrets_transport_staging": stops, "_gares_staging": stations}),
        pipeline,
        "_arrets_transport_staging",
        MagicMock(),
    )
    assert error and "['44']" in error and "_gares_staging" in error


# First national import (2026-10-29, runs 70, 73, 74): departements each
# national file publishes no row for, measured on the archived source objects
# (scolaire ca22305f, Filosofi DEC_COM 902044e0, demographie merged 4dc40807).
# Scolaire also holds a handful of border rows for 22, 65 and 2A, sectorised
# to a college of the neighbouring departement: they stay in staging.
_MEASURED_ABSENT = {
    "scolaire": (
        "_carte_scolaire_staging",
        ("09", "12", "17", "31", "32", "46", "81", "82", "971", "972", "976"),
        {"22": 5, "65": 1, "2A": 1},
    ),
    "revenus": ("_revenus_staging_flat", ("971", "973", "976"), {}),
    "demographie": ("_demographie_staging_flat", ("976",), {}),
}


@pytest.mark.parametrize("dataset", sorted(_MEASURED_ABSENT))
def test_national_file_absences_pass_the_coverage_guard(dataset):
    import run_etl
    from app.common.schemas import OutOfCoverageReason
    from app.scope.coverage import hosting_for
    from app.scope.model import DEPARTEMENTS_HEBERGEABLES

    table, absent, border = _MEASURED_ABSENT[dataset]
    scope = _scope({dataset}, DEPARTEMENTS_HEBERGEABLES)
    pipeline = run_etl.PIPELINE_MAP[dataset]()
    pipeline.bind_scope(scope)
    counts = {d: 100 for d in DEPARTEMENTS_HEBERGEABLES if d not in absent}
    counts.update(border)
    error = PipelineRunner._scoped_coverage_error(
        _TableCounts({table: counts}), pipeline, table, MagicMock()
    )
    assert error is None
    # The visitor reads « hors couverture » for the source, never a zero.
    for departement in absent:
        hosting = hosting_for(dataset, scope, departement)
        assert hosting.etat == "hors_couverture", departement
        assert hosting.motif == OutOfCoverageReason.SOURCE_SANS_COUVERTURE
    # A departement the source publishes is still required.
    del counts["44"]
    error = PipelineRunner._scoped_coverage_error(
        _TableCounts({table: counts}), pipeline, table, MagicMock()
    )
    assert error and "['44']" in error
