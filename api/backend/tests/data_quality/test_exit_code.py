"""Unit tests for data_quality run.py: exit codes + mode routing (DQ-CORE-04).

Covers:
- _run_generic returns 1 when ≥1 result has ERROR findings
- _run_generic returns 0 when all results are OK/WARN
- Unknown dataset → exit 2
- --legacy flag routes to _run_legacy (not PROFILES)
"""

import argparse
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from app.data_quality.base import AuditResult, Finding
from tests.data_quality.scopes import FULL_FRANCE_SCOPE, scope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(
    datasets: list[str] | None = None,
    out: str = ".planning/audit-dq",
    legacy: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(datasets=datasets or [], out=out, legacy=legacy)


def _make_result_with_error(dataset: str = "equipements") -> AuditResult:
    r = AuditResult(dataset=dataset, table=dataset, row_count=100)
    r.dimensions_run = ["geo"]
    r.findings.append(
        Finding(
            severity="ERROR",
            category="geo",
            title="Lambert-93 hors dept",
            description="97% des points hors département.",
        )
    )
    return r


def _make_result_ok(dataset: str = "demographie") -> AuditResult:
    r = AuditResult(dataset=dataset, table=dataset, row_count=34_963)
    r.dimensions_run = ["volume"]
    return r


def _make_result_warn(dataset: str = "cadastre") -> AuditResult:
    r = AuditResult(dataset=dataset, table="parcelles", row_count=1_000_000)
    r.dimensions_run = ["geo", "volume"]
    r.findings.append(
        Finding(
            severity="WARN",
            category="geo",
            title="Léger débordement",
            description="0.3% hors département.",
        )
    )
    return r


# ---------------------------------------------------------------------------
# exit code 1 — ERROR in results
# ---------------------------------------------------------------------------


def test_run_generic_returns_1_when_result_has_error() -> None:
    """_run_generic → 1 when ≥1 result contains an ERROR finding (DQ-CORE-04)."""
    from app.data_quality.run import _run_generic

    error_result = _make_result_with_error("equipements")
    ok_result = _make_result_ok("demographie")

    args = _make_args(datasets=["equipements", "demographie"])

    with (
        patch("app.data_quality.run.psycopg.connect") as mock_connect,
        patch("app.data_quality.run.load_scope_sync", return_value=FULL_FRANCE_SCOPE),
        patch("app.data_quality.run._audit_profile") as mock_audit,
        patch("app.data_quality.run.write_report"),
        patch("app.data_quality.run.write_matrix"),
    ):
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        # Return alternating results per dataset
        mock_audit.side_effect = [
            (error_result, ["geo"]),
            (ok_result, ["volume"]),
        ]

        code = _run_generic(args)

    assert code == 1, f"Expected exit 1 (ERROR present), got {code}"


# ---------------------------------------------------------------------------
# exit code 0 — no ERROR
# ---------------------------------------------------------------------------


def test_run_generic_returns_0_when_no_error() -> None:
    """_run_generic → 0 when all results are OK or WARN (DQ-CORE-04)."""
    from app.data_quality.run import _run_generic

    ok_result = _make_result_ok("demographie")
    warn_result = _make_result_warn("cadastre")

    args = _make_args(datasets=["demographie", "cadastre"])

    with (
        patch("app.data_quality.run.psycopg.connect") as mock_connect,
        patch("app.data_quality.run.load_scope_sync", return_value=FULL_FRANCE_SCOPE),
        patch("app.data_quality.run._audit_profile") as mock_audit,
        patch("app.data_quality.run.write_report"),
        patch("app.data_quality.run.write_matrix"),
    ):
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        mock_audit.side_effect = [
            (ok_result, ["volume"]),
            (warn_result, ["geo", "volume"]),
        ]

        code = _run_generic(args)

    assert code == 0, f"Expected exit 0 (no ERROR), got {code}"


# ---------------------------------------------------------------------------
# exit code 2 — unknown dataset
# ---------------------------------------------------------------------------


def test_run_generic_returns_2_for_unknown_dataset() -> None:
    """_run_generic → 2 for any unknown dataset name (T-23-05 validation gate)."""
    from app.data_quality.run import _run_generic

    args = _make_args(datasets=["not_a_real_dataset"])
    code = _run_generic(args)
    assert code == 2, f"Expected exit 2 (unknown dataset), got {code}"


def test_run_generic_returns_2_for_mixed_known_unknown() -> None:
    """_run_generic → 2 even if only 1 of N datasets is unknown."""
    from app.data_quality.run import _run_generic

    args = _make_args(datasets=["demographie", "nonexistent"])
    code = _run_generic(args)
    assert code == 2


# ---------------------------------------------------------------------------
# --legacy routing
# ---------------------------------------------------------------------------


def test_main_routes_to_run_legacy_when_legacy_flag_set() -> None:
    """main() with --legacy routes to _run_legacy, not _run_generic (D-04)."""
    from app.data_quality import run as run_module

    with (
        patch.object(run_module, "_run_legacy", return_value=0) as mock_legacy,
        patch.object(run_module, "_run_generic") as mock_generic,
        patch(
            "argparse.ArgumentParser.parse_args",
            return_value=argparse.Namespace(
                datasets=[], out=".planning/audit-dq", legacy=True
            ),
        ),
    ):
        run_module.main()

    mock_legacy.assert_called_once()
    mock_generic.assert_not_called()


def test_main_routes_to_run_generic_by_default() -> None:
    """main() without --legacy routes to _run_generic (D-03)."""
    from app.data_quality import run as run_module

    with (
        patch.object(run_module, "_run_generic", return_value=0) as mock_generic,
        patch.object(run_module, "_run_legacy") as mock_legacy,
        patch(
            "argparse.ArgumentParser.parse_args",
            return_value=argparse.Namespace(
                datasets=[], out=".planning/audit-dq", legacy=False
            ),
        ),
    ):
        run_module.main()

    mock_generic.assert_called_once()
    mock_legacy.assert_not_called()


# ---------------------------------------------------------------------------
# _run_generic bug fix verification: no "return 0" hardcoded path
# ---------------------------------------------------------------------------


def test_run_generic_exit_logic_is_error_driven() -> None:
    """_run_generic exit code must depend on errors, not be hardcoded 0.

    Verify by running with an ERROR result and asserting exit 1.
    This is the regression guard for the original run.py:78 bug.
    """
    from app.data_quality.run import _run_generic

    error_result = _make_result_with_error("equipements")
    args = _make_args(datasets=["equipements"])
    audit_ret = (error_result, ["geo"])

    with (
        patch("app.data_quality.run.psycopg.connect") as mock_connect,
        patch("app.data_quality.run.load_scope_sync", return_value=FULL_FRANCE_SCOPE),
        patch("app.data_quality.run._audit_profile", return_value=audit_ret),
        patch("app.data_quality.run.write_report"),
        patch("app.data_quality.run.write_matrix"),
    ):
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        code = _run_generic(args)

    assert code == 1, (
        f"Expected exit 1 (ERROR result), got {code}. "
        "Possible regression: return 0 hardcoded in _run_generic."
    )


def test_run_generic_refuses_an_empty_scope() -> None:
    """No departement hosted: every floor would be 0 -- refuse (exit 2)."""
    from app.data_quality.run import _run_generic

    args = argparse.Namespace(datasets=["vacance"], out="unused")
    with (
        patch("app.data_quality.run.psycopg.connect"),
        patch("app.data_quality.run.load_scope_sync", return_value=scope()),
        patch("app.data_quality.run._audit_profile") as mock_audit,
    ):
        assert _run_generic(args) == 2
    mock_audit.assert_not_called()
