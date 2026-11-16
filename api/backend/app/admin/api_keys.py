"""Admin — cross-user API keys management (/admin/api-keys).

Three endpoints:
  - GET /admin/api-keys           — paginated list cross-user
  - POST /admin/api-keys/{key_id}/revoke   — revoke one key (audit-logged)
  - PATCH /admin/api-keys/{key_id}/quota   — edit quota_monthly (audit-logged)

Both mutations enforce self-protection (admin cannot target their own keys)
and a reason (min_length=10) via Pydantic schemas from Plan 17-02.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.dependencies import AdminUser
from app.admin.queries import (
    admin_revoke_single_key,
    admin_update_key_quota,
    list_admin_api_keys,
)
from app.admin.schemas import (
    AdminApiKeysListResponse,
    RevokeKeyRequest,
    UpdateKeyQuotaRequest,
)
from app.database import get_db

router = APIRouter(prefix="/v1/admin/api-keys", tags=["admin"])


@router.get("", response_model=AdminApiKeysListResponse)
async def list_api_keys(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    owner_email: Annotated[str | None, Query(max_length=255)] = None,
    revoked: Annotated[bool | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminApiKeysListResponse:
    """Liste paginée cross-user avec filtres owner_email (ILIKE) + revoked."""
    rows, total = await list_admin_api_keys(
        db=db,
        owner_email=owner_email,
        revoked=revoked,
        page=page,
        page_size=page_size,
    )
    return AdminApiKeysListResponse(
        rows=rows,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/{key_id}/revoke")
async def revoke_single_key(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    key_id: Annotated[int, Path(ge=1)],
    body: RevokeKeyRequest,
) -> dict:
    """Révoque une clé API (loggé, intentionnel y compris is_default=TRUE).

    422 self-protection si la clé appartient à l'admin
    (détail 'cannot revoke own keys'). 422 si déjà révoquée. 404 si inexistante.
    """
    after = await admin_revoke_single_key(
        db=db,
        admin=admin,
        key_id=key_id,
        reason=body.reason,
        request=request,
    )
    return {"status": "ok", "key_id": key_id, "after": after}


@router.patch("/{key_id}/quota")
async def update_key_quota(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    key_id: Annotated[int, Path(ge=1)],
    body: UpdateKeyQuotaRequest,
) -> dict:
    """Modifie quota_monthly d'une clé API (loggé).

    422 self-protection ('cannot modify own keys quota'),
    422 si révoquée, 404 si inexistante.
    """
    after = await admin_update_key_quota(
        db=db,
        admin=admin,
        key_id=key_id,
        new_quota=body.quota_monthly,
        reason=body.reason,
        request=request,
    )
    return {"status": "ok", "key_id": key_id, "after": after}
