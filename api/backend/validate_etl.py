"""Validate ETL row counts against expected thresholds.

Queries dataset_metadata for actual row counts and compares against
minimum thresholds per dataset. Also reports the latest etl_runs status.

Usage:
    python -m validate_etl                  # Validate all datasets
    python -m validate_etl ban cadastre     # Validate specific datasets only

Exit codes:
    0 -- all checked datasets meet thresholds
    1 -- one or more datasets below threshold or missing
"""

import sys

import psycopg

from app.config import settings

# Minimum expected rows per dataset (order of magnitude safety net).
# These are intentionally conservative -- real counts should be higher.
EXPECTED_THRESHOLDS: dict[str, int] = {
    "monuments": 30_000,
    "loyers": 2_000,
    "transports": 100_000,
    "scolaire": 50_000,
    "dvf": 15_000_000,
    "dpe": 10_000_000,
    "ban": 20_000_000,
    "cadastre": 70_000_000,
    "batiments": 500_000,
    "connectivite": 30_000_000,
}


def _get_sync_dsn() -> str:
    """Derive a sync psycopg DSN from the async SQLAlchemy URL."""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _fetch_metadata(dsn: str) -> dict[str, tuple[int, str]]:
    """Return {dataset_name: (row_count, status)} from dataset_metadata."""
    result: dict[str, tuple[int, str]] = {}
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT dataset_name, row_count, status FROM dataset_metadata"
        ).fetchall()
        for name, row_count, status in rows:
            result[name] = (row_count or 0, status or "unknown")
    return result


def _fetch_latest_runs(dsn: str) -> dict[str, tuple[str, str | None]]:
    """Return {dataset_name: (status, error_message)} for latest run per dataset."""
    result: dict[str, tuple[str, str | None]] = {}
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (dataset_name)
                   dataset_name, status, error_message
            FROM etl_runs
            ORDER BY dataset_name, started_at DESC
            """
        ).fetchall()
        for name, status, error_msg in rows:
            result[name] = (status, error_msg)
    return result


def _check_thresholds(
    metadata: dict[str, tuple[int, str]],
    datasets: list[str],
) -> list[str]:
    """Compare row counts against thresholds. Print table. Return failures."""
    failures: list[str] = []

    print("\n--- Row Count Validation ---\n")
    for name in datasets:
        threshold = EXPECTED_THRESHOLDS[name]
        actual, _status = metadata.get(name, (0, "missing"))
        passed = actual >= threshold

        tag = "  OK" if passed else "FAIL"
        print(f"[{tag}] {name:<15s} {actual:>14,} rows  (min: {threshold:>14,})")

        if not passed:
            failures.append(name)

    return failures


def _check_runs(
    runs: dict[str, tuple[str, str | None]],
    datasets: list[str],
) -> None:
    """Print warnings for datasets whose last run is not 'success'."""
    print("\n--- Latest ETL Run Status ---\n")
    has_warnings = False

    for name in datasets:
        if name not in runs:
            print(f"[WARN] {name}: no ETL run found")
            has_warnings = True
            continue

        status, error_msg = runs[name]
        if status != "success":
            msg = f"[WARN] {name}: last run status = {status}"
            if error_msg:
                msg += f" -- {error_msg}"
            print(msg)
            has_warnings = True

    if not has_warnings:
        print("All datasets have a successful last run.")


def main() -> None:
    """Entry point for validate_etl."""
    # Parse optional dataset names from CLI args
    args = sys.argv[1:]
    if args:
        unknown = [a for a in args if a not in EXPECTED_THRESHOLDS]
        if unknown:
            print(
                f"Unknown dataset(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(EXPECTED_THRESHOLDS))}"
            )
            sys.exit(2)
        datasets = args
    else:
        datasets = list(EXPECTED_THRESHOLDS.keys())

    dsn = _get_sync_dsn()

    # Fetch data
    metadata = _fetch_metadata(dsn)
    runs = _fetch_latest_runs(dsn)

    # Validate thresholds
    failures = _check_thresholds(metadata, datasets)

    # Report latest run status
    _check_runs(runs, datasets)

    # Summary
    passing = len(datasets) - len(failures)
    print(f"\n{passing}/{len(datasets)} datasets passing thresholds")

    if failures:
        print(f"\nFailing: {', '.join(failures)}")
        sys.exit(1)
    else:
        print("\nAll checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
