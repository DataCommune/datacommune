"""Admin dependencies: require_admin + per-admin rate limit."""

from typing import Annotated

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.auth.models import UserInfo
from app.common.exceptions import ErreurApiError
from app.rate_limit import _check_rate

ADMIN_RATE_LIMIT_PER_MIN = 50


async def require_admin(
    current_user: Annotated[UserInfo, Depends(get_current_user)],
) -> UserInfo:
    """Require authenticated user with is_admin=True.

    Returns 403 if not admin.
    Applies a 50 req/min rate limit keyed on admin:{user_id}.
    Note: RateLimitMiddleware explicitly SKIPS /admin paths (see rate_limit.py),
    so this per-dependency check is the only admin rate limit in effect.
    """
    if not current_user.is_admin:
        raise ErreurApiError(403, "Accès refusé", "Droits administrateur requis")
    allowed, _remaining, reset = _check_rate(
        f"admin:{current_user.id}", ADMIN_RATE_LIMIT_PER_MIN
    )
    if not allowed:
        raise ErreurApiError(
            429, "Limite de débit atteinte", f"Reessayez dans {reset}s."
        )
    return current_user


AdminUser = Annotated[UserInfo, Depends(require_admin)]
