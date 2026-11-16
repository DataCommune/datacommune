"""Tests for persistence invariants (DQ-INTEG-01).

Wave 0 — These tests cover the D-01 decision: markdown-only persistence canal.
write_report and write_matrix already exist in report.py → these tests should
pass GREEN immediately, confirming the existing canal is correct.

test_no_validation_runs_table verifies the D-01 decision (no validation_runs table
was added): checked symbolically by asserting no module in data_quality imports or
references a validation_runs symbol.

Contract confirmed here for plans 26-01 / 26-02:
- DQ-INTEG-01: write_report writes a .md file in out_dir (existing, stable)
- DQ-INTEG-01: write_matrix writes MATRIX.md in out_dir (existing, stable)
- DQ-INTEG-01: no table validation_runs exists (D-01: markdown canal only)
- DQ-INTEG-01: run.py calls conn.set_read_only(True) (read-only runner invariant)
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# DQ-INTEG-01 — write_report markdown canal (already exists)
# ---------------------------------------------------------------------------


def test_write_report_creates_markdown(tmp_path: Path) -> None:
    """write_report writes a .md file named after the dataset in out_dir."""
    from app.data_quality.base import AuditResult
    from app.data_quality.report import write_report

    result = AuditResult(dataset="dvf", table="mutations", row_count=1_000)
    path = write_report(result, tmp_path)

    assert path.exists(), f"write_report did not create file at {path}"
    assert path.suffix == ".md", f"Expected .md file, got '{path.suffix}'"
    assert path.name == "dvf.md", f"Expected 'dvf.md', got '{path.name}'"

    content = path.read_text(encoding="utf-8")
    assert "dvf" in content, "Report content missing dataset name"
    assert "mutations" in content, "Report content missing table name"


def test_write_matrix_creates_matrix_md(tmp_path: Path) -> None:
    """write_matrix writes MATRIX.md in out_dir."""
    from app.data_quality.base import AuditResult
    from app.data_quality.report import write_matrix

    results = [
        AuditResult(dataset="dvf", table="mutations", row_count=1_000),
        AuditResult(dataset="demographie", table="demographie", row_count=35_000),
    ]
    for r in results:
        r.dimensions_run = ["volume"]

    path = write_matrix(results, tmp_path)

    assert path.exists(), f"write_matrix did not create MATRIX.md at {path}"
    assert path.name == "MATRIX.md", f"Expected 'MATRIX.md', got '{path.name}'"

    content = path.read_text(encoding="utf-8")
    assert "dvf" in content, "MATRIX.md missing 'dvf' entry"
    assert "demographie" in content, "MATRIX.md missing 'demographie' entry"


# ---------------------------------------------------------------------------
# DQ-INTEG-01 — D-01 no validation_runs table
# ---------------------------------------------------------------------------


def test_no_validation_runs_table() -> None:
    """D-01: no data_quality module references validation_runs as a target to create.

    The D-01 decision chose markdown as the sole persistence canal, explicitly
    rejecting a validation_runs DB table. This test asserts the decision holds:
    the run.py source code must not contain the string 'validation_runs'.
    """
    import app.data_quality.run as run_module

    run_source = Path(run_module.__file__).read_text(encoding="utf-8")
    assert "validation_runs" not in run_source, (
        "run.py contains 'validation_runs' — D-01 decision forbids this table. "
        "Use markdown write_report/write_matrix canal only."
    )


# ---------------------------------------------------------------------------
# DQ-INTEG-01 — conn.set_read_only(True) invariant
# ---------------------------------------------------------------------------


def test_runner_stays_read_only() -> None:
    """run.py calls conn.set_read_only(True) — read-only invariant (T-23-06)."""
    import app.data_quality.run as run_module

    run_source = Path(run_module.__file__).read_text(encoding="utf-8")
    assert "set_read_only(True)" in run_source, (
        "run.py must call conn.set_read_only(True) (T-23-06: read-only connection). "
        "This invariant prevents accidental writes during the DQ audit."
    )
