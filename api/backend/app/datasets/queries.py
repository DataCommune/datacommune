"""SQL queries for dataset metadata with ETL run history."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets.provenance import ProvenanceFacts


async def get_provenance_facts(
    db: AsyncSession,
    names: list[str],
) -> dict[str, ProvenanceFacts]:
    """Load authentic dynamic provenance facts for the given dataset names.

    One batch query (no per-dataset round trip). Sources without a
    ``dataset_metadata`` row (live proxies) are simply absent from the result;
    the caller falls back to empty :class:`ProvenanceFacts` (state ``inconnu``).
    """
    if not names:
        return {}
    result = await db.execute(
        text("""
        SELECT
            dm.dataset_name,
            dm.source_period_label,
            dm.source_updated_at,
            dm.effective_departments,
            ls.last_success_at
        FROM dataset_metadata dm
        LEFT JOIN LATERAL (
            SELECT completed_at AS last_success_at
            FROM etl_runs
            WHERE dataset_name = dm.dataset_name AND status = 'success'
            ORDER BY completed_at DESC
            LIMIT 1
        ) ls ON TRUE
        WHERE dm.dataset_name = ANY(:names)
    """),
        {"names": list(names)},
    )
    facts: dict[str, ProvenanceFacts] = {}
    for row in result:
        m = row._mapping
        eff = m.get("effective_departments")
        facts[m["dataset_name"]] = ProvenanceFacts(
            last_success_at=m.get("last_success_at"),
            source_period_label=m.get("source_period_label"),
            source_updated_at=m.get("source_updated_at"),
            effective_departments=list(eff) if eff else None,
        )
    return facts


async def get_all_datasets(db: AsyncSession) -> list[dict]:
    """Return all dataset metadata with latest ETL run info.

    Uses a LATERAL JOIN to efficiently get the most recent etl_runs
    row per dataset, leveraging the idx_etl_runs_dataset_started index.
    """
    result = await db.execute(
        text("""
        SELECT
            dm.dataset_name,
            dm.status,
            dm.last_updated_at,
            dm.row_count,
            dm.last_import_duration_seconds,
            dm.cron_schedule,
            dm.freshness_threshold_days,
            dm.source_period_label,
            dm.source_updated_at,
            dm.effective_departments,
            lr.status AS last_run_status,
            lr.started_at AS last_run_started_at,
            lr.completed_at AS last_run_completed_at,
            lr.total_duration_seconds AS last_run_duration,
            lr.total_rows_loaded AS last_run_rows,
            lr.error_message AS last_run_error,
            ls.last_success_at
        FROM dataset_metadata dm
        LEFT JOIN LATERAL (
            SELECT status, started_at, completed_at,
                   total_duration_seconds, total_rows_loaded, error_message
            FROM etl_runs
            WHERE dataset_name = dm.dataset_name
            ORDER BY started_at DESC
            LIMIT 1
        ) lr ON TRUE
        LEFT JOIN LATERAL (
            SELECT completed_at AS last_success_at
            FROM etl_runs
            WHERE dataset_name = dm.dataset_name AND status = 'success'
            ORDER BY completed_at DESC
            LIMIT 1
        ) ls ON TRUE
        ORDER BY dm.dataset_name
    """)
    )
    return [dict(row._mapping) for row in result]
