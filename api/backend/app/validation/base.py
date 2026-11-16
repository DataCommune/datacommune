"""BaseValidator pattern with ValidatorRegistry for per-dataset validation.

Provides:
- ValidationResult dataclass: structured output of a validator run
- BaseValidator: abstract base class for all dataset validators
- ValidatorRegistry: singleton registry mapping dataset names to validator classes
- register_validator: decorator to register a validator at import time
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Structured output of a single validator run."""

    dataset_name: str
    local_row_count: int | None
    source_row_count: int | None  # None if source API unavailable
    discrepancy_pct: float | None
    passed: bool
    notes: str | None = None
    status: str | None = None

    def __post_init__(self):
        if self.status is None:
            self.status = "passed" if self.passed else "failed"


class BaseValidator(ABC):
    """Abstract base class for dataset validators.

    Subclasses must implement:
    - dataset_name: canonical name of the dataset (e.g. "dvf")
    - production_table_name: actual DB table name (may differ from dataset_name)
    - discrepancy_threshold: max acceptable % divergence from source row count
    - get_source_row_count(): fetch authoritative row count from upstream API
    """

    @property
    @abstractmethod
    def dataset_name(self) -> str:
        """Canonical dataset name used in validation_runs."""
        ...

    @property
    @abstractmethod
    def production_table_name(self) -> str:
        """Actual PostgreSQL table name in the public schema."""
        ...

    @property
    def discrepancy_threshold(self) -> float:
        """Maximum acceptable % discrepancy between local and source row counts.

        Default is 5.0%. Override per validator when the source API or the
        ETL pipeline is known to filter rows; DVF, which filters none, uses 0.
        """
        return 5.0

    source_comparison_applicable: bool = True
    # Delay of the exact count, in ms; None takes the delay of the validation
    # scans (``etl_validation_scan_timeout_s``): a fixed 8 s does not count
    # the national parcelles (94.8 M rows), mutations, DPE nor addresses.
    statement_timeout_ms: int | None = None

    @property
    def delai_comptage_ms(self) -> int:
        from app.config import settings

        if self.statement_timeout_ms is not None:
            return self.statement_timeout_ms
        return settings.etl_validation_scan_timeout_s * 1000

    @abstractmethod
    def get_source_row_count(self) -> int | None:
        """Fetch the authoritative row count from the upstream source API.

        Return None if the source is unavailable. Declare source comparison
        non-applicable explicitly when no count API exists.
        """
        ...

    def get_local_row_count(self, dsn: str) -> int:
        """Return an exact, bounded public-table count, including partitions.

        Catalog estimates never certify completeness. Missing tables, SQL timeout
        and stale statistics cannot be mistaken for a known zero.

        The count may last as long as a validation scan: it joins the readers
        of the publication (shared try-lock, held by its transaction) and is
        refused at once while a publication runs, never waiting for it. A
        publication that starts during a count waits for its end: this path is
        run by hand (``run_all_validators``), outside the imports.
        """
        from app.commune.dependencies import PUBLICATION_LOCK_ID
        from app.etl.validation import ValidationUnavailableError

        with psycopg.connect(dsn, connect_timeout=5) as conn:
            conn.set_read_only(True)
            conn.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(self.delai_comptage_ms),),
            )
            admis = conn.execute(
                "SELECT pg_try_advisory_xact_lock_shared(%s)", (PUBLICATION_LOCK_ID,)
            ).fetchone()
            if not admis or not admis[0]:
                raise ValidationUnavailableError(
                    f"{self.production_table_name}: publication in progress"
                )
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(
                    sql.Identifier("public", self.production_table_name)
                )
            ).fetchone()
        return int(row[0])

    def run(self, dsn: str) -> ValidationResult:
        """Execute the validator and persist the result.

        Computes discrepancy between local and source row counts (when available),
        applies the threshold, builds a notes string, persists to validation_runs,
        and returns a ValidationResult.
        """
        from app.etl.validation import persist_validation_run

        local_count = self.get_local_row_count(dsn)
        source_count = self.get_source_row_count()
        status = None

        discrepancy_pct: float | None = None
        passed: bool
        notes_str: str | None = None

        if source_count is not None and source_count > 0:
            discrepancy_pct = abs(local_count - source_count) / source_count * 100
            passed = discrepancy_pct <= self.discrepancy_threshold
            src_info = (
                f"source_count={source_count:,}, discrepancy={discrepancy_pct:.1f}%"
            )
            if not passed:
                notes_str = (
                    f"Local {local_count:,} vs source {source_count:,} "
                    f"({discrepancy_pct:.1f}% discrepancy, "
                    f"threshold {self.discrepancy_threshold}%). {src_info}"
                )
            else:
                notes_str = src_info
        elif source_count == 0:
            passed = local_count == 0
            discrepancy_pct = 0.0 if passed else None
            notes_str = (
                f"source_count=0, local_count={local_count}; same-scope comparison"
            )
        elif not self.source_comparison_applicable:
            passed = False
            status = "not_applicable"
            notes_str = "No source count API; source comparison not applicable"
        else:
            passed = False
            status = "unavailable"
            notes_str = "Source count unavailable; comparison not executed"

        logger.info(
            "%s: passed=%s local=%d source=%s discrepancy=%s",
            self.dataset_name,
            passed,
            local_count,
            source_count,
            f"{discrepancy_pct:.1f}%" if discrepancy_pct is not None else "N/A",
        )

        persist_validation_run(
            dsn,
            self.dataset_name,
            row_count_before=None,
            row_count_after=local_count,
            null_rate_findings=[],
            passed=passed,
            blocked_swap=False,
            notes=notes_str,
        )

        return ValidationResult(
            dataset_name=self.dataset_name,
            local_row_count=local_count,
            source_row_count=source_count,
            discrepancy_pct=discrepancy_pct,
            passed=passed,
            notes=notes_str,
            status=status,
        )


class ValidatorRegistry:
    """Registry mapping dataset names to validator classes.

    Mirrors PipelineRegistry in app.etl.registry but for validators.
    Validators self-register at import time via @register_validator.
    """

    def __init__(self) -> None:
        self._validators: dict[str, type[BaseValidator]] = {}

    def register(self, name: str, cls: type[BaseValidator]) -> None:
        """Register *cls* under *name*."""
        self._validators[name] = cls
        logger.debug("ValidatorRegistry: registered '%s' -> %s", name, cls.__name__)

    def get(self, name: str) -> type[BaseValidator] | None:
        """Return the validator class for *name*, or None."""
        return self._validators.get(name)

    def list_validators(self) -> list[str]:
        """Return all registered validator names."""
        return list(self._validators.keys())

    def get_all(self) -> dict[str, type[BaseValidator]]:
        """Return the full validator map."""
        return dict(self._validators)


# Module-level singleton used across the application.
registry = ValidatorRegistry()


def register_validator(name: str):
    """Class decorator that auto-registers a validator in the global registry.

    Usage:
        @register_validator("dvf")
        class DvfValidator(BaseValidator):
            ...
    """

    def decorator(cls: type[BaseValidator]) -> type[BaseValidator]:
        registry.register(name, cls)
        return cls

    return decorator
