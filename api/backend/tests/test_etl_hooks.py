"""Tests for ETL monitoring hooks."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.demographie.backfill import BackfillPreconditionError
from app.etl.base import BasePipeline
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.registry import registry
from app.etl.schemas import EtlResult


@pytest.mark.parametrize(
    "positions", [("view", "validation"), ("validation", "view", "validation")]
)
def test_required_validation_order_is_refused_before_publication(positions):
    from app.etl.runner import PipelineRunner

    classes = {"validation": ValidationHook, "view": DependentMatviewHook}
    runner = PipelineRunner([classes[name]() for name in positions])
    with pytest.raises(RuntimeError, match="must precede"):
        runner._require_publication_checks()


def test_private_work_is_not_a_serializable_or_forgeable_hook_permission():
    result = EtlResult(
        dataset_name="dpe",
        stages=[],
        total_rows_loaded=1,
        total_duration_seconds=1,
        success=True,
    )
    result._publication_work = object()
    assert "_publication_work" not in result.model_dump()
    assert "_publication_work" not in result.model_dump_json()
    assert "_publication_work" not in str(EtlResult.model_json_schema())
    with (
        patch("app.etl.hooks.psycopg.connect") as connect,
        pytest.raises(RuntimeError, match="Invalid publication work"),
    ):
        DependentMatviewHook().on_run_complete("dpe", result)
    connect.assert_not_called()


class _MappedPipeline(BasePipeline):
    @property
    def dataset_name(self) -> str:
        return "hook_test"

    @property
    def expected_columns(self) -> list[str]:
        return []

    @property
    def staging_table_name(self) -> str:
        return "actual_table_staging"

    @property
    def production_table_name(self) -> str:
        return "actual_table"

    def download(self) -> list[Path]:
        return []

    def validate_schema(self, file_path: Path) -> None:
        pass

    def transform(self, file_path: Path) -> Path:
        return file_path

    def stage(self, file_path: Path) -> int:
        return 0

    def swap(self) -> None:
        pass


def test_validation_hook_uses_pipeline_production_table() -> None:
    registry.register("hook_test", _MappedPipeline)
    result = EtlResult(
        dataset_name="hook_test",
        stages=[],
        total_rows_loaded=42,
        total_duration_seconds=1.0,
        success=True,
        previous_row_count=40,
    )

    with patch("app.etl.publication_validation.validate_publication") as run_validation:
        ValidationHook().on_run_complete("hook_test", result)

    assert run_validation.call_args.args[1:3] == ("hook_test", "actual_table")


def test_dependent_matview_hook_rebuilds_scoring_after_dpe() -> None:
    result = EtlResult(
        dataset_name="dpe",
        stages=[],
        total_rows_loaded=42,
        total_duration_seconds=1.0,
        success=True,
    )
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    recreate = MagicMock()

    with (
        patch("app.etl.hooks.psycopg.connect", return_value=conn),
        patch.object(
            DependentMatviewHook,
            "_DEPENDENCIES",
            {"dpe": (("scoring_renov_target", recreate),)},
        ),
    ):
        DependentMatviewHook().on_run_complete("dpe", result)

    recreate.assert_called_once_with(conn)
    assert any(
        "INSERT INTO matview_metadata" in str(call.args[0])
        for call in conn.execute.call_args_list
    )
    conn.commit.assert_called_once()


def test_dependent_matview_hook_ignores_failed_etl() -> None:
    result = EtlResult(
        dataset_name="dpe",
        stages=[],
        total_rows_loaded=0,
        total_duration_seconds=1.0,
        success=False,
        error="failed",
    )

    with patch("app.etl.hooks.psycopg.connect") as connect:
        DependentMatviewHook().on_run_complete("dpe", result)

    connect.assert_not_called()


def test_dependent_matview_hook_demographie_backfills_before_rebuild() -> None:
    """DATA-03/D-08: demographie's real (unpatched) _DEPENDENCIES entry
    backfills superficie/densite BEFORE rebuilding v_commune_synthese,
    sharing the same connection -- never the reverse order (43-PATTERNS.md
    landmine #7: refreshing the view before the backfill publishes stale
    values)."""
    result = EtlResult(
        dataset_name="demographie",
        stages=[],
        total_rows_loaded=42,
        total_duration_seconds=1.0,
        success=True,
    )
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False

    call_order: list[str] = []

    def _backfill(passed_conn):
        assert passed_conn is conn
        call_order.append("backfill")

    def _rebuild(passed_conn):
        assert passed_conn is conn
        call_order.append("rebuild")

    with (
        patch("app.etl.hooks.psycopg.connect", return_value=conn),
        patch("app.etl.hooks.backfill_superficie_densite", side_effect=_backfill),
        patch("app.etl.hooks.recreate_commune_synthese", side_effect=_rebuild),
    ):
        DependentMatviewHook().on_run_complete("demographie", result)

    assert call_order == ["backfill", "rebuild"]
    conn.commit.assert_called_once()


def test_dependent_matview_hook_demographie_precondition_failure_skips_commit() -> None:
    """A BackfillPreconditionError aborts before the rebuild AND before the
    success commit -- the caller's transaction never lands a partial
    state. The failure is still recorded on a *separate* connection (the
    hook's existing best-effort observability path), which the exception
    re-raise leaves untouched."""
    result = EtlResult(
        dataset_name="demographie",
        stages=[],
        total_rows_loaded=42,
        total_duration_seconds=1.0,
        success=True,
    )
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    failure_conn = MagicMock()
    failure_conn.__enter__.return_value = failure_conn
    failure_conn.__exit__.return_value = False

    rebuild = MagicMock()

    with (
        patch(
            "app.etl.hooks.psycopg.connect",
            side_effect=[conn, failure_conn],
        ),
        patch(
            "app.etl.hooks.backfill_superficie_densite",
            side_effect=BackfillPreconditionError("boom"),
        ),
        patch("app.etl.hooks.recreate_commune_synthese", rebuild),
        pytest.raises(BackfillPreconditionError),
    ):
        DependentMatviewHook().on_run_complete("demographie", result)

    rebuild.assert_not_called()
    conn.commit.assert_not_called()
