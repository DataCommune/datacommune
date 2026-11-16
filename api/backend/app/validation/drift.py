"""Drift detection between successive validation runs.

Compares row_count_after values across the last two validation_runs
for a given dataset. Flags significant deviations and persists a
drift-finding entry when drift exceeds the configured threshold.
"""

import logging

import psycopg

from app.etl.validation import persist_validation_run

logger = logging.getLogger(__name__)


def detect_drift(
    dsn: str,
    dataset_name: str,
    threshold_pct: float = 15.0,
    *,
    detailed: bool = False,
) -> dict | None:
    """Detect row count drift between the two most recent validation runs.

    Queries validation_runs for the last 2 entries with non-null
    row_count_after. If the row count change exceeds *threshold_pct*,
    persists a new validation_runs entry recording the drift finding
    and returns a drift info dict.

    Args:
        dsn: psycopg sync connection string.
        dataset_name: ETL dataset name (matches validation_runs.dataset_name).
        threshold_pct: Percentage change above which drift is flagged. Default 15.0.

    Returns:
        Dict with drift info when drift exceeds threshold, None otherwise.
        Returns None (not an error) when fewer than 2 runs exist or
        when the drift is below threshold. With detailed=True, always returns
        an explicit passed/failed/not_applicable verdict.
    """
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        conn.set_read_only(True)
        conn.execute("SET LOCAL statement_timeout = '8s'")
        rows = conn.execute(
            """
            SELECT id, row_count_after
            FROM validation_runs
            WHERE dataset_name = %s
              AND row_count_after IS NOT NULL
            ORDER BY run_at DESC
            LIMIT 2
            """,
            (dataset_name,),
        ).fetchall()

    if len(rows) < 2:
        logger.info(
            "detect_drift: dataset='%s' — fewer than 2 runs, skipping",
            dataset_name,
        )
        return (
            {"status": "not_applicable", "notes": "Fewer than two observations"}
            if detailed
            else None
        )

    current_id, current_count = rows[0]
    prev_id, prev_count = rows[1]

    # Zero is an observed baseline, not a reason to discard a divergence.
    drift_pct = (
        abs(current_count - prev_count) / prev_count * 100
        if prev_count
        else (100.0 if current_count else 0.0)
    )

    if drift_pct <= threshold_pct:
        logger.info(
            "detect_drift: dataset='%s' drift=%.2f%%"
            " within threshold %.1f%%, no action",
            dataset_name,
            drift_pct,
            threshold_pct,
        )
        return (
            {
                "status": "passed",
                "previous_count": prev_count,
                "current_count": current_count,
                "drift_pct": drift_pct,
            }
            if detailed
            else None
        )

    direction = "down" if current_count < prev_count else "up"

    logger.warning(
        "detect_drift: dataset='%s' drift=%.2f%% (%s) exceeds threshold %.1f%%"
        " — prev=%d current=%d",
        dataset_name,
        drift_pct,
        direction,
        threshold_pct,
        prev_count,
        current_count,
    )

    # Persist a validation_runs entry so drift findings are visible in history
    persist_validation_run(
        dsn,
        dataset_name,
        row_count_before=prev_count,
        row_count_after=current_count,
        null_rate_findings=[],
        passed=False,
        blocked_swap=False,
        notes=(
            f"drift detected: {drift_pct:.1f}% ({direction},"
            f" prev={prev_count:,} current={current_count:,})"
        ),
    )

    return {
        **({"status": "failed"} if detailed else {}),
        "dataset_name": dataset_name,
        "current_run_id": current_id,
        "previous_run_id": prev_id,
        "current_count": current_count,
        "previous_count": prev_count,
        "drift_pct": round(drift_pct, 2),
        "direction": direction,
    }
