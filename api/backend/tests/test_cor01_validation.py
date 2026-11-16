"""COR-01 regression oracles adapted from the frozen audit counterexamples."""

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from tests.data_quality.scopes import FULL_FRANCE_SCOPE


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "exception",
        "empty",
        "mixed",
        "drift_error",
        "okay",
        "optional",
        "no_baseline",
    ],
)
def test_validation_process_fails_closed(case):
    code = """
import runpy, sys
from app.validation.base import registry, ValidationResult
from app.validation.validators import ban, cadastre, dpe, dvf
from app.validation import drift
class Broken:
    def run(self, dsn): raise RuntimeError("synthetic unavailable")
class Okay:
    def run(self, dsn):
        if case in ("optional", "no_baseline"):
            return ValidationResult("okay", 3, None, None, False,
                                    status="not_applicable")
        return ValidationResult("okay", 3, 3, 0, True)
registry._validators = ({} if sys.argv[1] == "empty"
                        else {"broken": Broken, "okay": Okay})
case = sys.argv[1]
def fake_drift(*args, **kwargs):
    if case == "drift_error": raise RuntimeError("synthetic drift failure")
    return {"status": "not_applicable" if case == "no_baseline" else "passed"}
drift.detect_drift = fake_drift
names = {"empty": [], "unknown": ["absent"], "exception": ["broken"],
         "mixed": ["okay", "broken"], "drift_error": ["okay"],
         "okay": ["okay"], "optional": ["okay"], "no_baseline": ["okay"]}[case]
sys.argv = ["app.validation"] + names
runpy.run_module("app.validation", run_name="__main__")
"""
    # The child prints non-ASCII text: a Windows codec (cp1252) fails it without UTF-8.
    proc = subprocess.run(
        [sys.executable, "-c", code, case],
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout=20,
    )
    assert (proc.returncode == 0) is (case in ("okay", "optional")), proc.stdout
    assert "No drift detected" not in proc.stdout
    summary = json.loads(
        next(
            line.removeprefix("VALIDATION_SUMMARY=")
            for line in proc.stdout.splitlines()
            if line.startswith("VALIDATION_SUMMARY=")
        )
    )
    expected = 1 if case == "empty" else 4 if case == "mixed" else 2
    assert summary["expected"] == len(summary["checks"]) == expected
    assert summary["passed"] is (case in ("okay", "optional"))


@pytest.mark.parametrize(
    "source,local,status,passed",
    [
        (None, 7, "unavailable", False),
        (0, 7, "failed", False),
        (0, 0, "passed", True),
        (7, 7, "passed", True),
    ],
)
def test_source_counts_are_not_conflated(source, local, status, passed):
    from app.validation.base import BaseValidator

    class Validator(BaseValidator):
        dataset_name = production_table_name = "synthetic"

        def get_local_row_count(self, dsn):
            return local

        def get_source_row_count(self):
            return source

    with patch("app.etl.validation.persist_validation_run"):
        result = Validator().run("forbidden")
    assert result.passed is passed
    assert result.status == status


def test_null_subscan_failure_is_reported_and_cli_fails(tmp_path):
    import argparse

    from app.data_quality import run
    from app.data_quality.profiles import DatasetProfile, NullSpec
    from app.data_quality.report import _cell_verdict

    profile = DatasetProfile(
        dataset="audit",
        table="audit",
        nulls=NullSpec(required_cols=("id",), expected_cols=("value",)),
    )
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (3, 0)
    with patch(
        "app.data_quality.profiles.check_null_rates",
        side_effect=RuntimeError("synthetic scan unavailable"),
    ):
        result, result.dimensions_run = run._audit_profile(
            conn, profile, FULL_FRANCE_SCOPE
        )
        with (
            patch.object(run, "PROFILES", {"audit": profile}),
            patch.object(run.psycopg, "connect") as connect,
            patch.object(run, "load_scope_sync", return_value=FULL_FRANCE_SCOPE),
        ):
            connect.return_value.__enter__.return_value = conn
            assert (
                run._run_generic(
                    argparse.Namespace(datasets=["audit"], out=str(tmp_path))
                )
                == 1
            )
    assert result.errors
    assert _cell_verdict(result, "nulls") != "🟢"
    assert result.stats["null_scans"]["completed"] == 1
    assert result.stats["null_scans"]["expected"] == 2
    assert "🔴" in (tmp_path / "MATRIX.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "rows,status",
    [
        ([], "not_applicable"),
        ([(2, 0), (1, 0)], "passed"),
        ([(2, 7), (1, 0)], "failed"),
        ([(2, 7), (1, 7)], "passed"),
    ],
)
def test_drift_distinguishes_no_baseline_zero_and_known_counts(rows, status):
    from app.validation.drift import detect_drift

    with (
        patch("psycopg.connect") as connect,
        patch("app.validation.drift.persist_validation_run"),
    ):
        conn = connect.return_value.__enter__.return_value
        conn.execute.return_value.fetchall.return_value = rows
        result = detect_drift("forbidden", "audit", detailed=True)
    assert result["status"] == status


def test_optional_source_is_explicitly_not_applicable():
    from app.validation.validators.ban import BanValidator

    with (
        patch.object(BanValidator, "get_local_row_count", return_value=7),
        patch("app.etl.validation.persist_validation_run"),
    ):
        result = BanValidator().run("forbidden")
    assert result.status == "not_applicable"
    assert result.passed is False
