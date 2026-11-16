"""Snapshot regression tests for the P23 matrix verdicts (GOAL, D-01).

Wave 0 — these tests use _cell_verdict (already exists in report.py) with
synthetically constructed AuditResult objects. They are GREEN for the 6 P23
cells listed below because _cell_verdict derives verdicts from AuditResult
findings and dimensions_run — no DB connection needed.

Purpose: prove that the P24 implementation does not change the expected
verdicts for the 3 pilot datasets after freshness/nulls are added.

Expected P24 matrix (from CONTEXT.md D-01):
  demographie  : volume 🟢 | geo —
  equipements  : volume 🟢 | geo 🔴  (Lambert-93 bug → out_of_dept ~97%)
  cadastre     : volume 🟢 | geo 🟠  (34 invalids → WARN after D-02 banding)
"""

import pytest

from app.data_quality.base import AuditResult, Finding
from app.data_quality.report import _cell_verdict  # already exists

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper: build a synthetic AuditResult for each (dataset, state) scenario
# ---------------------------------------------------------------------------


def _build_p24_expected_result(dataset: str) -> AuditResult:
    """Build a synthetic AuditResult matching the expected P24 matrix state.

    This represents what each dataset's result looks like after all P24
    dimensions are implemented and run. Used to snapshot-test _cell_verdict.
    """
    if dataset == "demographie":
        # Non-spatial: geo N/A, volume 🟢 (no findings)
        result = AuditResult(
            dataset="demographie", table="demographie", row_count=34_963
        )
        result.dimensions_run = ["volume"]  # geo not run (non-spatial)
        # No findings → volume 🟢
        return result

    if dataset == "equipements":
        # Spatial with Lambert-93 bug: volume 🟢, geo 🔴
        result = AuditResult(
            dataset="equipements", table="equipements", row_count=2_722_677
        )
        result.dimensions_run = ["volume", "geo"]
        # Volume: no findings → 🟢
        # Geo: out_of_dept ERROR (Lambert-93) → 🔴
        result.findings.append(
            Finding(
                severity="ERROR",
                category="geo",
                title="Geometries hors departement : 97.0% (2 522 000 lignes)",
                description="Lambert-93 positive control.",
                count=2_522_000,
            )
        )
        return result

    if dataset == "cadastre":
        # Spatial with 34 invalid geoms: volume 🟢, geo 🟠 (WARN after D-02 banding)
        result = AuditResult(dataset="cadastre", table="parcelles", row_count=1_279_746)
        result.dimensions_run = ["volume", "geo"]
        # Volume: no findings → 🟢
        # Geo: invalid WARN (34 invalids = 0.003%) → 🟠
        result.findings.append(
            Finding(
                severity="WARN",
                category="geo",
                title="Geometries invalides : 34 lignes",
                description="ST_IsValid FALSE (0.003%).",
                count=34,
            )
        )
        return result

    raise ValueError(f"Unknown dataset for matrix regression: {dataset!r}")


# ---------------------------------------------------------------------------
# Parametrized snapshot: 6 cells from the P24 expected matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dataset,dimension,expected_verdict",
    [
        ("equipements", "geo", "\U0001f534"),  # 🔴 Lambert ERROR
        ("equipements", "volume", "\U0001f7e2"),  # 🟢 volume OK
        ("cadastre", "geo", "\U0001f7e0"),  # 🟠 34 invalids WARN
        ("cadastre", "volume", "\U0001f7e2"),  # 🟢 volume OK
        ("demographie", "volume", "\U0001f7e2"),  # 🟢 volume OK
        ("demographie", "geo", "—"),  # — non-spatial N/A
    ],
)
def test_matrix_cell_regression(
    dataset: str, dimension: str, expected_verdict: str
) -> None:
    """Snapshot: _cell_verdict returns expected verdict for each P24 cell.

    These cells represent the truth matrix after P24: demographie all green,
    equipements geo red (Lambert), cadastre geo orange (34 invalids banded WARN).
    """
    result = _build_p24_expected_result(dataset)
    actual = _cell_verdict(result, dimension)
    assert actual == expected_verdict, (
        f"Matrix cell ({dataset}, {dimension}): "
        f"expected {expected_verdict!r}, got {actual!r}. "
        "Regression detected — P24 must not change these verdicts."
    )
