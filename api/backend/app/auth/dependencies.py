"""FastAPI dependencies for API key verification, quota, and JWT auth."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth.models import ApiKeyInfo, UserInfo
from app.auth.queries import (
    get_default_key_for_user,
    get_user_by_id,
    lookup_api_key,
    migrate_key_hash,
)
from app.auth.quota import reserve_quota
from app.auth.service import (
    decode_access_token,
    hash_api_key,
    hash_api_key_legacy,
    session_matches_credential,
)
from app.billing.plans import (
    UNLIMITED_QUOTA,
    get_quota_for_plan,
    get_rate_limit_for_plan,
    get_rate_limit_for_quota,
    is_internal_unlimited_plan,
)
from app.billing.queries import get_active_subscription
from app.common.exceptions import ErreurApiError
from app.config import settings
from app.database import get_db
from app.mcp.canal import est_appel_mcp
from app.middleware import _get_client_ip, cle_visiteur
from app.rate_limit import _check_rate

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Public key shipped in the frontend bundle (scripts/rotate_anon_key.py).
ANON_KEY_PREFIX = "dc_anon_"
ANON_IP_MINUTE_BUCKET_PREFIX = "anon:ip:"
ANON_IP_DAY_BUCKET_PREFIX = "anon:ip-day:"
# What a keyless request carries through the protected router: no row behind it.
KEYLESS_ACCESS = ApiKeyInfo(
    id=0,
    name="keyless",
    quota_monthly=0,
    revoked=False,
    created_at=datetime(2026, 11, 12, tzinfo=UTC),
)


# ---------------------------------------------------------------------------
# Private helpers (shared logic for verify_api_key / check_quota_hybrid)
# ---------------------------------------------------------------------------


async def _resolve_api_key(api_key: str, db: AsyncSession) -> ApiKeyInfo:
    """Validate a raw API key string and return ApiKeyInfo."""
    key_hash = hash_api_key(api_key)
    row = await lookup_api_key(db, key_hash)

    # Fallback: try legacy SHA-256 hash for keys created before HMAC migration
    if row is None:
        legacy_hash = hash_api_key_legacy(api_key)
        row = await lookup_api_key(db, legacy_hash)
        if row is not None:
            # Transparently migrate to HMAC hash (one-time per key)
            await migrate_key_hash(db, legacy_hash, key_hash)

    if row is None:
        raise ErreurApiError(403, "Clé API invalide")

    return ApiKeyInfo(**row)


async def _enforce_quota(
    request: Request, key_info: ApiKeyInfo, db: AsyncSession
) -> ApiKeyInfo:
    """Rate limit + monthly quota enforcement. Sets request.state.api_key_id."""
    request.state.api_key_id = key_info.id

    sub = None
    if key_info.user_id is not None:
        sub = await get_active_subscription(db, key_info.user_id)
        if sub is not None and is_internal_unlimited_plan(sub["plan_name"]):
            if not await _is_verified_admin(key_info.user_id, db):
                raise ErreurApiError(403, "Clé interne invalide")
            current_month = datetime.now(UTC).strftime("%Y-%m")
            await reserve_quota(
                db,
                key_id=key_info.id,
                user_id=key_info.user_id,
                month=current_month,
                limit=UNLIMITED_QUOTA,
            )
            return key_info

    # The sentinel is storage-only. Without the internal account entitlement it
    # must fail closed instead of becoming a negative quota or a bypass.
    if key_info.quota_monthly == UNLIMITED_QUOTA:
        raise ErreurApiError(403, "Clé interne invalide")

    if is_public_anonymous_key(key_info):
        await _enforce_anonymous_visitor_limits(request, key_info, db)
        return key_info

    # Per-plan rate limit (req/min)
    plan_limit = (
        get_rate_limit_for_quota(key_info.quota_monthly)
        if key_info.user_id is None
        else get_rate_limit_for_plan(sub["plan_name"] if sub else "free")
    )
    allowed, _remaining, reset = _check_rate(f"plan:{key_info.id}", plan_limit)
    if not allowed:
        raise ErreurApiError(
            429,
            "Limite de debit atteinte",
            f"Limite: {plan_limit} req/min. Reessayez dans {reset}s.",
        )

    current_month = datetime.now(UTC).strftime("%Y-%m")

    plan_name = sub["plan_name"] if sub else "free"
    monthly_limit = (
        key_info.quota_monthly
        if key_info.user_id is None
        else get_quota_for_plan(plan_name)
    )
    count = await reserve_quota(
        db,
        key_id=key_info.id,
        user_id=key_info.user_id,
        month=current_month,
        limit=monthly_limit,
    )
    if count is None:
        raise ErreurApiError(
            429, "Quota mensuel epuise", f"Limite: {monthly_limit}/mois."
        )

    return key_info


def is_public_anonymous_key(key_info: ApiKeyInfo) -> bool:
    """Whether the key is the public key shipped in the frontend bundle.

    Identified by the prefix stored at creation (``dc_anon_``) on an ownerless
    key: the stored prefix is derived from the plaintext whose hash matched, so
    a ``dc_live_`` key can never pass for it.
    """
    return key_info.user_id is None and (key_info.key_prefix or "").startswith(
        ANON_KEY_PREFIX
    )


async def _enforce_anonymous_visitor_limits(
    request: Request, key_info: ApiKeyInfo, db: AsyncSession
) -> None:
    """Per visitor IP allowance of the public key, never a per-key bucket.

    Every visitor of the site sends the same key: a per-key rate or monthly
    quota would be shared by the whole world and the first burst of traffic
    would shut the site for everyone. The key's monthly row is still counted,
    without a limit, for supervision.
    """
    _enforce_visitor_ip_limits(request)

    await reserve_quota(
        db,
        key_id=key_info.id,
        user_id=None,
        month=datetime.now(UTC).strftime("%Y-%m"),
        limit=UNLIMITED_QUOTA,
    )


def _enforce_visitor_ip_limits(request: Request) -> None:
    """Per-minute and per-day ceilings of one visitor (public key or no key).

    The visitor is its address, or its /64 in IPv6 (``cle_visiteur``): counting
    each IPv6 address would let one machine rotate out of its buckets.
    """
    client_ip = cle_visiteur(_get_client_ip(request))
    checks = (
        (
            f"{ANON_IP_MINUTE_BUCKET_PREFIX}{client_ip}",
            settings.anon_rate_limit_per_ip_minute,
            60,
            "Limite de debit atteinte",
            "req/min",
        ),
        (
            f"{ANON_IP_DAY_BUCKET_PREFIX}{client_ip}",
            settings.anon_rate_limit_per_ip_day,
            86_400,
            "Limite quotidienne atteinte",
            "req/jour",
        ),
    )
    for bucket, limit, window, title, unit in checks:
        allowed, _remaining, reset = _check_rate(bucket, limit, window)
        if not allowed:
            raise ErreurApiError(
                429,
                title,
                f"Limite: {limit} {unit}. Reessayez dans {reset}s.",
                headers={"Cache-Control": "no-store", "Retry-After": str(reset)},
            )


async def _is_verified_admin(
    user_id: int,
    db: AsyncSession,
) -> bool:
    """Return whether the internal account owner is verified and admin."""
    user = await get_user_by_id(db, user_id)
    return bool(user and user["is_verified"] and user["is_admin"])


# ---------------------------------------------------------------------------
# Public dependencies
# ---------------------------------------------------------------------------


async def verify_api_key(
    api_key: Annotated[str | None, Depends(_api_key_header)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiKeyInfo:
    """Validate the X-API-Key header and return key info.

    Raises:
        ErreurApiError: 401 if header missing, 403 if key invalid/revoked.
    """
    if api_key is None:
        raise ErreurApiError(401, "Clé API manquante", "Header X-API-Key requis")

    return await _resolve_api_key(api_key, db)


async def check_quota(
    request: Request,
    key_info: Annotated[ApiKeyInfo, Depends(verify_api_key)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiKeyInfo:
    """Enforce per-plan rate limit and monthly quota after verifying the API key.

    Also sets request.state.api_key_id for middleware logging.

    Raises:
        ErreurApiError: 429 if rate limit or monthly quota is exhausted.
    """
    return await _enforce_quota(request, key_info, db)


async def check_quota_hybrid(
    request: Request,
    api_key: Annotated[str | None, Depends(_api_key_header)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiKeyInfo:
    """Hybrid auth: X-API-Key header OR JWT cookie -> ApiKeyInfo for quota.

    Priority:
      1. X-API-Key header present -> standard API key verification
      2. JWT cookie present -> resolve user's default key for quota tracking
      3. Neither -> 401
    """
    # Path 1: explicit API key header
    if api_key is not None:
        key_info = await _resolve_api_key(api_key, db)
        return await _enforce_quota(request, key_info, db)

    # Path 2: JWT cookie fallback
    token = request.cookies.get(settings.auth_cookie_name, "")
    if token.startswith("Bearer "):
        token = token[7:]

    if token:
        payload = decode_access_token(token)
        if payload is not None:
            try:
                user_id = int(payload.get("sub", ""))
            except (ValueError, TypeError):
                raise ErreurApiError(401, "Token invalide") from None

            user = await get_user_by_id(db, user_id)
            if user is None or not user["is_verified"]:
                raise ErreurApiError(401, "Compte invalide ou non vérifié")
            if not session_matches_credential(payload, user):
                raise ErreurApiError(401, "Session révoquée, reconnectez-vous")

            default_key = await get_default_key_for_user(db, user_id)
            if default_key is None:
                raise ErreurApiError(
                    401,
                    "Clé API par défaut introuvable",
                    "Créez une clé API dans votre tableau de bord.",
                )

            key_info = ApiKeyInfo(**default_key)
            return await _enforce_quota(request, key_info, db)

    # Path 3: nothing provided. Open only by configuration (Ianis, 2026-11-12):
    # the visitor's IP buckets are the whole allowance, no key row is counted.
    # A read of the MCP server (D-143) is keyless by design, gated by its own
    # MCP_ENABLED, and spends the same buckets.
    if settings.keyless_access_enabled or est_appel_mcp(request):
        request.state.api_key_id = None
        _enforce_visitor_ip_limits(request)
        return KEYLESS_ACCESS

    raise ErreurApiError(
        401, "Clé API manquante", "Header X-API-Key ou cookie JWT requis"
    )


# ---------------------------------------------------------------------------
# JWT cookie-based auth (for dashboard pages)
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserInfo:
    """Extract and validate JWT from HttpOnly cookie.

    This dependency is for dashboard pages (cookie auth).
    API endpoints use check_quota_hybrid (X-API-Key or JWT) instead.

    Raises:
        ErreurApiError: 401 if not authenticated or token invalid.
    """
    token = request.cookies.get(settings.auth_cookie_name, "")
    # Strip "Bearer " prefix if present
    if token.startswith("Bearer "):
        token = token[7:]

    if not token:
        raise ErreurApiError(401, "Non authentifié", "Connexion requise")

    payload = decode_access_token(token)
    if payload is None:
        raise ErreurApiError(401, "Token invalide ou expiré")

    try:
        user_id = int(payload.get("sub", ""))
    except (ValueError, TypeError):
        raise ErreurApiError(401, "Token invalide") from None

    user = await get_user_by_id(db, user_id)
    if user is None or not user["is_verified"]:
        raise ErreurApiError(401, "Compte invalide ou non vérifié")
    if not session_matches_credential(payload, user):
        raise ErreurApiError(401, "Session révoquée, reconnectez-vous")

    return UserInfo(**user)
