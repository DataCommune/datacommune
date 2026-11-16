"""Admin — overview counters (/admin/overview) + per-user heatmap.

Two read-only endpoints, no audit log.

Data sources:
  - users_count: SELECT COUNT(*) FROM users (cheap, indexed PK).
  - active_keys_count: COUNT(*) FROM api_keys WHERE NOT revoked.
  - requests_24h: COUNT(*) FROM api_request_log WHERE ts > NOW() - INTERVAL '24 hours'
    — uses existing idx_api_log_ts (ts DESC).
  - MRR: unavailable. Subscription rights and catalog prices are not billing amounts.
  - heatmap days: DATE(ts) GROUP BY on api_request_log JOIN api_keys, 30-day window.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.dependencies import AdminUser
from app.admin.schemas import (
    AdminOverviewResponse,
    AdminUserHeatmapResponse,
    HeatmapDay,
)
from app.common.exceptions import ErreurApiError
from app.database import get_db

# api_request_log retention is enforced by the weekly cleanup job in
# scheduler/jobs.py lines 185-201. Keep this in sync if the retention changes.
_API_LOG_RETENTION_DAYS = 7
_HEATMAP_WINDOW_DAYS = 30

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/overview", response_model=AdminOverviewResponse)
async def get_overview(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminOverviewResponse:
    """Operational counters and explicit revenue unavailability."""
    users_count = int(
        (await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one() or 0
    )

    active_keys_count = int(
        (
            await db.execute(text("SELECT COUNT(*) FROM api_keys WHERE NOT revoked"))
        ).scalar_one()
        or 0
    )

    requests_24h = int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM api_request_log"
                    " WHERE ts > NOW() - INTERVAL '24 hours'"
                )
            )
        ).scalar_one()
        or 0
    )

    # No persisted billed amount, currency or cadence supports a monetary total.
    # Neither effective access (including free grants/internal) nor the latest
    # subscription row proves revenue. Empty local history does not prove zero.
    return AdminOverviewResponse(
        users_count=users_count,
        active_keys_count=active_keys_count,
        mrr_eur_estimated=None,
        mrr_status="unavailable",
        mrr_reason="billing_amounts_not_persisted",
        requests_24h=requests_24h,
    )


@router.get("/users/{user_id}/heatmap", response_model=AdminUserHeatmapResponse)
async def get_user_heatmap(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int, Path(ge=1)],
) -> AdminUserHeatmapResponse:
    """Heatmap 30 jours x 1 user — compteurs quotidiens via api_request_log.

    JOIN sur api_keys pour filtrer par user_id (api_request_log n'a pas de
    colonne user_id — cf. research pitfall 3).

    api_request_log retention = 7 jours. La reponse couvre la fenetre 30 jours
    demandee par OBS-07 mais ne sera populee que pour les 7 derniers jours
    (les cellules 8-30 arriveront vides cote API — l'UI les grise avec une note).
    """
    # Validate user exists — 404 instead of returning empty days (clearer contract)
    user_row = await db.execute(
        text("SELECT id FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    if user_row.scalar_one_or_none() is None:
        raise ErreurApiError(404, "Introuvable", f"User {user_id} inexistant")

    rows = await db.execute(
        text(
            """
            SELECT
                DATE(r.ts) AS day,
                COUNT(*)   AS count
            FROM api_request_log r
            JOIN api_keys k ON k.id = r.api_key_id
            WHERE k.user_id = :uid
              AND r.ts > NOW() - INTERVAL '30 days'
            GROUP BY DATE(r.ts)
            ORDER BY DATE(r.ts) DESC
            """
        ),
        {"uid": user_id},
    )

    days = [
        HeatmapDay(day=row["day"].isoformat(), count=int(row["count"]))
        for row in rows.mappings().all()
    ]
    return AdminUserHeatmapResponse(
        days=days,
        retention_days=_API_LOG_RETENTION_DAYS,
    )
