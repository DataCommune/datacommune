"""Admin — system status (/v1/admin/system).

Single composite endpoint exposing: DB health, rate limit bucket
enumeration, disk usage (/etl_tmp + /), services list (api, db,
scheduler). Read-only, no audit.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.dependencies import AdminUser
from app.admin.queries import get_system_status
from app.admin.schemas import SystemStatus
from app.database import get_db

router = APIRouter(prefix="/v1/admin/system", tags=["admin"])


@router.get("", response_model=SystemStatus)
async def system_status(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemStatus:
    """Snapshot temps reel: DB, rate limit, disk, services."""
    return await get_system_status(db)
