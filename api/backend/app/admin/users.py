"""Admin users router: list, detail, grant-plan, toggle is_admin."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.admin.dependencies import AdminUser
from app.admin.queries import (
    admin_grant_plan,
    admin_resend_verification,
    admin_reset_quota,
    admin_revoke_all_keys,
    admin_toggle_is_admin,
    get_admin_user_detail,
    list_admin_users,
)
from app.admin.schemas import (
    AdminUserDetailResponse,
    AdminUsersListResponse,
    BulkActionRequest,
    BulkActionResponse,
    BulkActionResult,
    GrantPlanRequest,
    ResendVerifRequest,
    ResetQuotaRequest,
    RevokeKeysRequest,
    ToggleAdminRequest,
)
from app.common.exceptions import ErreurApiError
from app.database import get_db

router = APIRouter(prefix="/v1/admin/users", tags=["admin"])


_SORT_PATTERN = r"^(email|plan|quota|api_keys_count|created_at|last_login)_(asc|desc)$"


@router.get("", response_model=AdminUsersListResponse)
async def list_users(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(None),
    plan: str | None = Query(None, pattern="^(free|pro|business|enterprise)$"),
    is_admin: bool | None = Query(None),
    email_verified: bool | None = Query(None),
    sort: str | None = Query(None, pattern=_SORT_PATTERN),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> AdminUsersListResponse:
    """List all users with search, filter, sort, and offset pagination.

    sort: `{field}_{asc|desc}` — field in (email, plan, quota, api_keys_count,
    created_at, last_login). Invalid values are rejected by the regex pattern.
    """
    offset = (page - 1) * page_size
    rows, total = await list_admin_users(
        db, search, plan, is_admin, email_verified, sort, offset, page_size
    )
    return AdminUsersListResponse(
        rows=rows, total=total, page=page, page_size=page_size
    )


@router.post("/bulk", response_model=BulkActionResponse)
async def bulk_action(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    body: BulkActionRequest,
) -> BulkActionResponse:
    """Apply an admin action to a batch of users.

    Per-user transactions: each target user is processed by the single-user
    helper (which owns its commit). A failure on one user does NOT roll back
    successful users — we surface per-user status in `results`.

    Self-protection: if the caller's own id appears in `user_ids`, the whole
    batch is rejected with 422 (clearer than silent drop).
    """
    # Self-protection: reject upfront rather than per-user error
    if admin.id in body.user_ids:
        raise ErreurApiError(
            422,
            "Action interdite",
            "cannot target yourself in bulk action",
        )

    results: list[BulkActionResult] = []

    for uid in body.user_ids:
        try:
            if body.action == "reset_quota":
                await admin_reset_quota(
                    db=db,
                    admin=admin,
                    target_user_id=uid,
                    reason=body.reason,
                    request=request,
                )
            elif body.action == "revoke_keys":
                await admin_revoke_all_keys(
                    db=db,
                    admin=admin,
                    target_user_id=uid,
                    reason=body.reason,
                    request=request,
                )
            elif body.action == "resend_verification":
                await admin_resend_verification(
                    db=db,
                    admin=admin,
                    target_user_id=uid,
                    reason=body.reason,
                    request=request,
                )
            results.append(BulkActionResult(user_id=uid, status="ok"))
        except ErreurApiError as e:
            # Roll back the current per-user attempt so the next iteration
            # gets a clean session. The helpers commit on success, so
            # rollback here only undoes the failed partial work.
            await db.rollback()
            results.append(
                BulkActionResult(user_id=uid, status="error", detail=e.detail)
            )
        # Defensive: never let a single user break the batch.
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            results.append(
                BulkActionResult(
                    user_id=uid, status="error", detail=f"Erreur interne: {e!r}"
                )
            )

    failed = sum(1 for r in results if r.status == "error")
    return BulkActionResponse(results=results, total=len(results), failed=failed)


@router.get("/{user_id}", response_model=AdminUserDetailResponse)
async def get_user_detail(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[int, Path(..., ge=1)],
) -> AdminUserDetailResponse:
    """Return full user detail: info, API keys, subscription, recent activity."""
    detail = await get_admin_user_detail(db, user_id)
    if not detail or not detail.get("id"):
        raise ErreurApiError(404, "Introuvable", f"User {user_id} inexistant")
    return AdminUserDetailResponse(**detail)


@router.post("/{user_id}/grant-plan")
async def grant_plan(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    user_id: Annotated[int, Path(..., ge=1)],
    body: GrantPlanRequest,
) -> dict:
    """Grant a plan manually to a user (writes subscription + audit log atomically).

    Returns 422 if admin attempts to grant a plan to themselves.
    """
    if user_id == admin.id:
        raise ErreurApiError(
            422,
            "Action interdite",
            "cannot grant plan to yourself",
        )
    after = await admin_grant_plan(
        db=db,
        admin=admin,
        target_user_id=user_id,
        plan_name=body.plan,
        end_date=body.end_date,
        reason=body.reason,
        request=request,
        force_downgrade=body.force_downgrade,
    )
    return {"status": "ok", "user_id": user_id, "plan": body.plan, "after": after}


@router.patch("/{user_id}/admin")
async def toggle_admin(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    user_id: Annotated[int, Path(..., ge=1)],
    body: ToggleAdminRequest,
) -> dict:
    """Toggle is_admin for a user (writes is_admin update + audit log atomically).

    Returns 422 if admin attempts to modify their own admin status (lockout risk).
    """
    if user_id == admin.id:
        raise ErreurApiError(
            422,
            "Action interdite",
            "cannot modify own admin status",
        )
    after = await admin_toggle_is_admin(
        db=db,
        admin=admin,
        target_user_id=user_id,
        new_is_admin=body.is_admin,
        reason=body.reason,
        request=request,
    )
    return {
        "status": "ok",
        "user_id": user_id,
        "is_admin": body.is_admin,
        "after": after,
    }


@router.post("/{user_id}/reset-quota")
async def reset_quota(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    user_id: Annotated[int, Path(..., ge=1)],
    body: ResetQuotaRequest,
) -> dict:
    """Reset target user's monthly quota counter to 0 (account-level).

    Returns 422 if admin targets themselves (makes no operational sense).
    """
    if user_id == admin.id:
        raise ErreurApiError(
            422,
            "Action interdite",
            "cannot reset own quota",
        )
    after = await admin_reset_quota(
        db=db,
        admin=admin,
        target_user_id=user_id,
        reason=body.reason,
        request=request,
    )
    return {"status": "ok", "user_id": user_id, "after": after}


@router.post("/{user_id}/revoke-keys")
async def revoke_keys(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    user_id: Annotated[int, Path(..., ge=1)],
    body: RevokeKeysRequest,
) -> dict:
    """Revoke ALL non-revoked api_keys of the target user (including default).

    Returns 422 if admin targets themselves (lockout risk).
    """
    if user_id == admin.id:
        raise ErreurApiError(
            422,
            "Action interdite",
            "cannot revoke own keys",
        )
    after = await admin_revoke_all_keys(
        db=db,
        admin=admin,
        target_user_id=user_id,
        reason=body.reason,
        request=request,
    )
    return {"status": "ok", "user_id": user_id, "after": after}


@router.post("/{user_id}/resend-verification")
async def resend_verification(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
    user_id: Annotated[int, Path(..., ge=1)],
    body: ResendVerifRequest,
) -> dict:
    """Resend verification email to an unverified user.

    422 if already verified, 429 if within 5-minute cooldown for this user.
    """
    # No self-protection guard: admin.id should already be verified so
    # admin_resend_verification will raise 422 if they are — matches
    # research recommendation (individual-level guard is sufficient).
    after = await admin_resend_verification(
        db=db,
        admin=admin,
        target_user_id=user_id,
        reason=body.reason,
        request=request,
    )
    return {"status": "ok", "user_id": user_id, "after": after}
