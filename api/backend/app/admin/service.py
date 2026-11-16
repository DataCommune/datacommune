"""Admin services: log_admin_action + request metadata extraction."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.models import UserInfo
from app.middleware import _get_client_ip


def extract_ip_ua(request: Request) -> tuple[str | None, str]:
    """Extract client IP (CF-aware) and user-agent from request."""
    ip = _get_client_ip(request)
    ua = request.headers.get("user-agent", "")
    return ip, ua


async def log_admin_action(
    db: AsyncSession,
    admin: UserInfo,
    action: str,
    target_type: str,
    target_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str,
    request: Request,
) -> None:
    """Append one row to admin_audit_log.

    CRITICAL: does NOT commit. The caller must commit after both the
    mutation and this audit write to guarantee atomicity (grant + audit
    roll back together on failure).
    """
    ip, ua = extract_ip_ua(request)
    await db.execute(
        text("""
            INSERT INTO admin_audit_log
                (admin_user_id, action, target_type, target_id,
                 before, after, reason, ip, user_agent)
            VALUES
                (:admin_id, :action, :target_type, :target_id,
                 CAST(:before AS JSONB), CAST(:after AS JSONB),
                 :reason, CAST(:ip AS INET), :ua)
        """),
        {
            "admin_id": admin.id,
            "action": action,
            "target_type": target_type,
            "target_id": str(target_id),
            "before": json.dumps(before) if before is not None else None,
            "after": json.dumps(after) if after is not None else None,
            "reason": reason,
            "ip": ip,
            "ua": ua,
        },
    )
