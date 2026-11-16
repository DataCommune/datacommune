"""Unit tests for data_quality report: _cell_verdict + render_matrix + write_matrix.

Covers DQ-CORE-03: matrix datasets x dimensions with verdicts 🟢/🟠/🔴/—.
"""

import pytest

pytestmark = pytest.mark.unit

from app.data_quality.base import AuditResult, Finding
from app.data_quality.report import _cell_verdict, render_matrix, write_matrix

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    dataset: str,
    dimensions_run: list[str],
    findings: list[Finding] | None = None,
) -> AuditResult:
    r = AuditResult(dataset=dataset, table=f"{dataset}_table", row_count=1000)
    r.dimensions_run = dimensions_run
    if findings:
        r.findings.extend(findings)
    return r


# ---------------------------------------------------------------------------
# _cell_verdict — D-06/D-07
# ---------------------------------------------------------------------------


def test_cell_verdict_dimension_not_in_dimensions_run() -> None:
    """Dimension absent from dimensions_run → '—' (N/A)."""
    r = _make_result("demo", dimensions_run=["volume"])
    assert _cell_verdict(r, "geo") == "—"


def test_cell_verdict_error_in_category() -> None:
    """Dimension in dimensions_run + ≥1 Finding ERROR of same category → '🔴'."""
    finding = Finding(
        severity="ERROR",
        category="volume",
        title="Table vide",
        description="0 rows",
    )
    r = _make_result("demo", dimensions_run=["volume"], findings=[finding])
    assert _cell_verdict(r, "volume") == "🔴"


def test_cell_verdict_warn_only() -> None:
    """Dimension in dimensions_run + WARN only → '🟠'."""
    finding = Finding(
        severity="WARN",
        category="geo",
        title="Partial coverage",
        description="Some rows outside dept",
    )
    r = _make_result("equip", dimensions_run=["geo"], findings=[finding])
    assert _cell_verdict(r, "geo") == "🟠"


def test_cell_verdict_no_finding() -> None:
    """Dimension in dimensions_run + 0 findings of that category → '🟢'."""
    r = _make_result("demo", dimensions_run=["volume"])
    assert _cell_verdict(r, "volume") == "🟢"


def test_cell_verdict_volume_warn_severity() -> None:
    """Dimension in dimensions_run + WARN Finding with category='volume' → '🟠'.

    Covers permis volume severity=WARN path (D-11/D-12 P29).
    check_volume emits a WARN volume Finding via spec.severity.upper().
    _cell_verdict must route this to 🟠, not 🟢.
    """
    finding = Finding(
        severity="WARN",
        category="volume",
        title="Volume SITADEL partiel",
        description="727 rows < floor (quarantaine SITADEL, severity=warn D-11 P29)",
    )
    r = _make_result("permis", dimensions_run=["volume"], findings=[finding])
    assert _cell_verdict(r, "volume") == "🟠", (
        "volume WARN Finding must produce 🟠, not 🟢 or 🔴. "
        "check_volume must emit severity='WARN' from spec.severity.upper() "
        "(D-11/D-12 P29)."
    )


def test_cell_verdict_isolation_by_category() -> None:
    """ERROR in another category does NOT redden the cell (category isolation)."""
    error_volume = Finding(
        severity="ERROR",
        category="volume",
        title="Volume trop bas",
        description="500 rows < 1000 floor",
    )
    r = _make_result("equip", dimensions_run=["geo", "volume"], findings=[error_volume])
    # geo has no findings → should be 🟢, not contaminated by volume ERROR
    assert _cell_verdict(r, "geo") == "🟢"
    # volume has the error → 🔴
    assert _cell_verdict(r, "volume") == "🔴"


# ---------------------------------------------------------------------------
# render_matrix — columns union + cell values
# ---------------------------------------------------------------------------


def test_matrix_verdicts() -> None:
    """render_matrix produces correct emoji cells per findings."""
    error_finding = Finding(
        severity="ERROR",
        category="geo",
        title="Lambert-93 hors dept",
        description="97% des points hors département",
    )
    results = [
        _make_result(
            "equipements", dimensions_run=["geo", "volume"], findings=[error_finding]
        ),
        _make_result("demographie", dimensions_run=["volume"]),
    ]
    output = render_matrix(results)

    # equipements/geo → 🔴 (ERROR finding)
    assert "🔴" in output
    # demographie/volume → 🟢 (run + 0 finding)
    assert "🟢" in output
    # demographie/geo → — (not in dimensions_run)
    assert "—" in output
    # Header contains column names
    assert "| Dataset |" in output
    assert "geo" in output
    assert "volume" in output


def test_matrix_na_cell() -> None:
    """demographie with dimensions_run=['volume'] renders '—' for geo column."""
    results = [
        _make_result("equipements", dimensions_run=["geo", "volume"]),
        _make_result("demographie", dimensions_run=["volume"]),
    ]
    output = render_matrix(results)

    lines = output.splitlines()
    demo_line = next((line for line in lines if "demographie" in line), None)
    assert demo_line is not None, "No row for demographie in matrix"
    # demographie's geo cell must be "—"
    assert "—" in demo_line


def test_matrix_columns_are_sorted_union() -> None:
    """All columns = sorted union of all dimensions_run across results."""
    results = [
        _make_result("a", dimensions_run=["volume", "geo"]),
        _make_result("b", dimensions_run=["nulls"]),
    ]
    output = render_matrix(results)

    # Header row must contain all three dims
    header_line = next(line for line in output.splitlines() if "| Dataset |" in line)
    assert "geo" in header_line
    assert "nulls" in header_line
    assert "volume" in header_line

    # Columns must appear in alphabetical order in header
    geo_pos = header_line.index("geo")
    nulls_pos = header_line.index("nulls")
    volume_pos = header_line.index("volume")
    assert geo_pos < nulls_pos < volume_pos


def test_matrix_empty_results_no_crash() -> None:
    """render_matrix([]) must not raise even with no results."""
    output = render_matrix([])
    assert "# Matrice data-quality" in output


def test_matrix_empty_dimensions_no_crash() -> None:
    """Result with no dimensions_run renders gracefully (no crash)."""
    r = _make_result("demo", dimensions_run=[])
    output = render_matrix([r])
    assert "# Matrice data-quality" in output
    assert "demo" in output


# ---------------------------------------------------------------------------
# write_matrix — file creation
# ---------------------------------------------------------------------------


def test_write_matrix_creates_matrix_md(tmp_path) -> None:
    """write_matrix creates exactly MATRIX.md and returns its Path."""
    r = _make_result("demo", dimensions_run=["volume"])
    path = write_matrix([r], tmp_path)
    assert path.name == "MATRIX.md"
    assert path.exists()
    assert "# Matrice data-quality" in path.read_text(encoding="utf-8")


def test_write_matrix_creates_output_dir(tmp_path) -> None:
    """write_matrix creates out_dir if it doesn't exist."""
    out = tmp_path / "nested" / "output"
    r = _make_result("demo", dimensions_run=["volume"])
    path = write_matrix([r], out)
    assert path.exists()
    assert path.parent == out
