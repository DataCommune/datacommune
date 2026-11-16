"""Pydantic models for ETL pipeline results."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, PrivateAttr

if TYPE_CHECKING:
    from app.etl.publication import PublicationWork


class EtlStageResult(BaseModel):
    """Result of a single ETL stage execution."""

    stage_name: str
    duration_seconds: float
    rows_affected: int | None = None
    success: bool
    error: str | None = None


class EtlResult(BaseModel):
    """Aggregate result of a full ETL pipeline run."""

    dataset_name: str
    stages: list[EtlStageResult]
    total_rows_loaded: int
    total_duration_seconds: float
    success: bool
    error: str | None = None
    # Pre-swap row count from dataset_metadata, set by runner
    # after _check_row_regression. Used by ValidationHook to
    # persist before/after counts without an extra DB read.
    previous_row_count: int | None = None
    _publication_row_count: int | None = PrivateAttr(default=None)
    _publication_work: PublicationWork | None = PrivateAttr(default=None)
    _failure_cause: BaseException | None = PrivateAttr(default=None)

    @property
    def failure_cause(self) -> BaseException | None:
        """Internal in-process cause; excluded from JSON/schema and persistence."""
        return self._failure_cause

    def remember_failure(self, cause: BaseException) -> None:
        self._failure_cause = cause


class ValidationRunResult(BaseModel):
    """Result of a post-swap validation run."""

    validation_run_id: int
    dataset_name: str
    passed: bool
    row_count_before: int | None = None
    row_count_after: int
    null_rate_findings: list[dict] = []
