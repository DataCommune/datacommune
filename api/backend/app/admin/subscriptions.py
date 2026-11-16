"""Admin -- subscriptions cross-user management (/v1/admin/subscriptions).

Two endpoints:
  - GET /v1/admin/subscriptions              -- paginated list + filters
  - POST /v1/admin/subscriptions/{sub_id}/sync  -- force-sync from Stripe

Both endpoints gated by AdminUser (403 non-admin, 429 >50 req/min).
Sync action rejects source='manual_grant' with 422.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.dependencies import AdminUser
from app.admin.queries import (
    admin_sync_subscription_from_stripe,
    list_admin_subscriptions,
)
from app.admin.schemas import (
    AdminSubscriptionsListResponse,
    SyncSubscriptionRequest,
)
from app.database import get_db

router = APIRouter(prefix="/v1/admin/subscriptions", tags=["admin"])


@router.get("", response_model=AdminSubscriptionsListResponse)
async def list_subscriptions(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    source: Annotated[Literal["stripe", "manual_grant"] | None, Query()] = None,
    plan_name: Annotated[str | None, Query(max_length=50)] = None,
    status: Annotated[str | None, Query(max_length=50)] = None,
    user_email: Annotated[str | None, Query(max_length=255)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminSubscriptionsListResponse:
    """Liste paginee cross-user avec filtres source/plan/status/user_email."""
    rows, total = await list_admin_subscriptions(
        db=db,
        source=source,
        plan_name=plan_name,
        status=status,
        user_email=user_email,
        page=page,
        page_size=page_size,
    )
    return AdminSubscriptionsListResponse(
        rows=rows,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{sub_id}/sync")
async def sync_subscription(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    sub_id: Annotated[int, Path(ge=1)],
    body: SyncSubscriptionRequest,
) -> dict:
    """Force-sync une subscription depuis Stripe.

    422 si source='manual_grant' (detail 'Impossible de sync une subscription
    manuelle'). 502 si Stripe API erreur. 404 si sub_id inexistante.
    """
    after = await admin_sync_subscription_from_stripe(
        db=db,
        admin=admin,
        sub_id=sub_id,
        reason=body.reason,
        request=request,
    )
    return {"status": "ok", "sub_id": sub_id, "after": after}
