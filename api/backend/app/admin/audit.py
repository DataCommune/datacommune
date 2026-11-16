# ruff: noqa: E501
"""Admin audit log viewer: GET /admin/audit (read-only, filterable)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.dependencies import AdminUser
from app.admin.schemas import AdminAuditEntry, AdminAuditListResponse
from app.database import get_db

router = APIRouter(prefix="/v1/admin/audit", tags=["admin"])


@router.get("", response_model=AdminAuditListResponse)
async def list_audit(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_user_id: int | None = Query(
        None, description="Filter by admin who performed the action"
    ),
    action: str | None = Query(None, description="e.g. grant_plan, toggle_is_admin"),
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    date_from: datetime | None = Query(None),  # noqa: B008
    date_to: datetime | None = Query(None),  # noqa: B008
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> AdminAuditListResponse:
    """Return paginated audit log entries with optional filters.

    Each filter axis aligns with one of the three composite indexes from
    migration 058: (admin_user_id, created_at), (action, created_at),
    (target_type, target_id, created_at).

    Reads are never logged back to admin_audit_log.
    """
    offset = (page - 1) * page_size

    # Frontend `<input type="date">` + `new Date(...).toISOString()` yields
    # midnight of the selected day, which would exclude the rest of that day
    # under `created_at <= :date_to`. Normalize a pure-midnight bound to the
    # very end of the same day so the filter becomes "up to end of day".
    date_to_inclusive = _end_of_day_if_midnight(date_to)

    # Filter clause shared between list and count queries.
    # Each condition maps to an index-backed column.
    filters_sql = """
        AND (CAST(:admin_user_id AS INTEGER) IS NULL OR a.admin_user_id = CAST(:admin_user_id AS INTEGER))
        AND (CAST(:action AS TEXT) IS NULL OR a.action = CAST(:action AS TEXT))
        AND (CAST(:target_type AS TEXT) IS NULL OR a.target_type = CAST(:target_type AS TEXT))
        AND (CAST(:target_id AS TEXT) IS NULL OR a.target_id = CAST(:target_id AS TEXT))
        AND (CAST(:date_from AS TIMESTAMPTZ) IS NULL OR a.created_at >= CAST(:date_from AS TIMESTAMPTZ))
        AND (CAST(:date_to AS TIMESTAMPTZ) IS NULL OR a.created_at <= CAST(:date_to AS TIMESTAMPTZ))
    """
    params: dict = {
        "admin_user_id": admin_user_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "date_from": date_from,
        "date_to": date_to_inclusive,
    }

    list_rs = await db.execute(
        text(
            f"""
            SELECT a.id, a.admin_user_id, u.email AS admin_email,
                   a.action, a.target_type, a.target_id,
                   a.before, a.after, a.reason,
                   HOST(a.ip) AS ip, a.user_agent, a.created_at
            FROM admin_audit_log a
            LEFT JOIN users u ON u.id = a.admin_user_id
            WHERE 1=1 {filters_sql}
            ORDER BY a.created_at DESC
            OFFSET :offset LIMIT :limit
            """
        ),
        {**params, "offset": offset, "limit": page_size},
    )
    rows = [dict(r) for r in list_rs.mappings().all()]

    count_rs = await db.execute(
        text(f"SELECT COUNT(*) FROM admin_audit_log a WHERE 1=1 {filters_sql}"),
        params,
    )
    total = count_rs.scalar_one()

    return AdminAuditListResponse(
        rows=[AdminAuditEntry(**r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


def _end_of_day_if_midnight(dt: datetime | None) -> datetime | None:
    """Bump a midnight-precision datetime to the last microsecond of the same day.

    `<input type="date">` clients send `YYYY-MM-DDT00:00:00`; under an inclusive
    `<=` filter this would silently drop every event recorded later in the day.
    When the time component is exactly midnight we assume the caller meant
    "the whole day" and extend the bound to 23:59:59.999999.
    """
    if dt is None:
        return None
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt + timedelta(days=1) - timedelta(microseconds=1)
    return dt
