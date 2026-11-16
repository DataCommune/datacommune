"""Admin-only endpoint for querying validation run history.

Exposes GET /v1/validation/reports — returns recent validation_runs
rows for authenticated admin users. Auth is handled at endpoint level
via get_current_user (JWT cookie), not via X-API-Key.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/validation", tags=["validation"])


@router.get("/reports")
async def get_validation_reports(
    current_user: Annotated[UserInfo, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    dataset: str | None = Query(default=None),
) -> list[dict]:
    """Return recent validation_runs rows for admin users.

    Args:
        current_user: Authenticated user (JWT cookie). Must be admin.
        db: Async DB session.
        limit: Max rows to return (1-200, default 50).
        dataset: Optional dataset name filter.

    Returns:
        List of validation run dicts ordered by run_at DESC.

    Raises:
        HTTPException 403: If current user is not admin.
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin requis")

    query = """
        SELECT id, dataset_name, run_at, row_count_before, row_count_after,
               row_drop_pct, null_rate_findings, passed, blocked_swap, notes
        FROM validation_runs
        WHERE (:dataset IS NULL OR dataset_name = :dataset)
        ORDER BY run_at DESC
        LIMIT :limit
    """

    result = await db.execute(
        text(query),
        {"dataset": dataset, "limit": limit},
    )
    rows = result.fetchall()

    logger.info(
        "get_validation_reports: user=%s returned %d rows (dataset=%s, limit=%d)",
        current_user.email,
        len(rows),
        dataset,
        limit,
    )

    return [
        {
            "id": row.id,
            "dataset_name": row.dataset_name,
            "run_at": row.run_at.isoformat(),
            "row_count_before": row.row_count_before,
            "row_count_after": row.row_count_after,
            "row_drop_pct": row.row_drop_pct,
            "null_rate_findings": row.null_rate_findings,
            "passed": row.passed,
            "blocked_swap": row.blocked_swap,
            "notes": row.notes,
        }
        for row in rows
    ]
