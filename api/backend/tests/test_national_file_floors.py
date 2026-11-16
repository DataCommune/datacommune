"""Per-departement floors of a national file filtered to the scope (261020-fpm).

Witness 75/67/971 (04.1-21): Paris holds 68,135 foncier_pm rows while the
fallback floor (mean national share x 0.5) asked for 82,352 and refused the
swap. Once the scope filter has read the rows of each hosted departement in
the import's own national file, a departement without a reference count is
checked for presence; reference counts (44/85, legacy prod base) keep their 80 % floor.
"""

from unittest.mock import MagicMock

from app.etl.runner import PipelineRunner
from app.etl_majic_pm.pipeline import MajicPmPipeline
from app.scope import InstanceScope


def _pipeline(*departements):
    pipeline = MajicPmPipeline()
    pipeline.bind_scope(InstanceScope(frozenset({"foncier_pm"}), departements))
    return pipeline


def test_without_file_counts_the_former_floors_apply():
    pipeline = _pipeline("75", "44")
    assert pipeline.departement_min_rows("75") == 82_352  # mean share x 0.5
    assert pipeline.departement_min_rows("44") == int(359_855 * 0.8)


def test_file_counts_make_a_departement_without_reference_a_presence_check():
    pipeline = _pipeline("75", "44")
    pipeline.record_scope_file_rows({"75": 68_135, "44": 359_855})
    assert pipeline.departement_min_rows("75") == 1
    assert pipeline.departement_min_rows("44") == int(359_855 * 0.8)
    assert pipeline.min_staging_rows == 1 + int(359_855 * 0.8)
    assert pipeline.scope_file_rows == {"75": 68_135, "44": 359_855}


class _Counts:
    """Connection stub: departement counts of one staging table."""

    def __init__(self, counts):
        self.counts = counts

    def execute(self, query, params):
        rows = [] if "DISTINCT" in query else list(self.counts.items())
        return MagicMock(fetchall=MagicMock(return_value=rows))


def test_paris_of_the_witness_passes_and_a_truncated_reference_still_fails():
    pipeline = _pipeline("75", "44")
    pipeline.record_scope_file_rows({"75": 68_135, "44": 359_855})
    table = "_foncier_pm_staging_flat"
    log = MagicMock()
    ok = PipelineRunner._scoped_coverage_error(
        _Counts({"75": 68_135, "44": 359_855}), pipeline, table, log
    )
    assert ok is None
    truncated = PipelineRunner._scoped_coverage_error(
        _Counts({"75": 68_135, "44": 10}), pipeline, table, log
    )
    assert "Department volume: 44 has 10 rows" in truncated
    missing = PipelineRunner._scoped_coverage_error(
        _Counts({"44": 359_855}), pipeline, table, log
    )
    assert "missing scoped departments ['75']" in missing
